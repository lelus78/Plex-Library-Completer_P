import os
import time
import logging
import threading
import csv
import sys
import concurrent.futures
import queue
from flask import Flask, render_template, redirect, url_for, flash, jsonify, request
from dotenv import load_dotenv
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound
from plexapi.audio import Track

# Carica le variabili dal file .env
load_dotenv()

# --- Configurazione del Logging Centralizzato ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s -[%(levelname)s] - %(message)s",
    handlers=[
        logging.FileHandler("plex_sync.log", mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log.setLevel(logging.DEBUG)
# --- Fine Configurazione ---

# Import dei nostri moduli
from plex_playlist_sync.sync_logic import run_full_sync_cycle, run_cleanup_only, build_library_index, rescan_and_update_missing, force_playlist_scan_and_missing_detection
from plex_playlist_sync.stats_generator import (
    get_plex_tracks_as_df, generate_genre_pie_chart, generate_decade_bar_chart,
    generate_top_artists_chart, generate_duration_distribution, generate_year_trend_chart,
    get_library_statistics
)
from plex_playlist_sync.utils.gemini_ai import list_ai_playlists, generate_on_demand_playlist
from plex_playlist_sync.utils.helperClasses import UserInputs
from plex_playlist_sync.utils.database import (
    initialize_db, get_missing_tracks, update_track_status, get_missing_track_by_id, 
    add_managed_ai_playlist, get_managed_ai_playlists_for_user, delete_managed_ai_playlist, get_managed_playlist_details,
    delete_all_missing_tracks, delete_missing_track, check_track_in_index, comprehensive_track_verification, get_library_index_stats,
    clean_tv_content_from_missing_tracks, clean_resolved_missing_tracks
)
from plex_playlist_sync.utils.downloader import DeezerLinkFinder, download_single_track_with_streamrip

initialize_db()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "una-chiave-segreta-casuale-e-robusta")

app_state = { "status": "In attesa", "last_sync": "Mai eseguito", "is_running": False }

# Coda per i download e ThreadPoolExecutor per l'esecuzione parallela
download_queue = queue.Queue()
download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2) # Ridotto a 2 per evitare sovraccarico

def download_worker():
    while True:
        track_info = download_queue.get()
        if track_info is None: # Sentinella per terminare il worker
            break
        link, track_id = track_info
        try:
            log.info(f"Avvio download per {link} (ID traccia: {track_id})")
            download_single_track_with_streamrip(link) # Ora accetta un singolo link
            update_track_status(track_id, 'downloaded')
            log.info(f"Download completato per {link} (ID traccia: {track_id})")
        except Exception as e:
            log.error(f"Errore durante il download di {link} (ID traccia: {track_id}): {e}", exc_info=True)
        finally:
            download_queue.task_done()

def background_scheduler():
    time.sleep(10)
    wait_seconds = int(os.getenv("SECONDS_TO_WAIT", 86400))
    while True:
        if not app_state["is_running"]:
            log.info(f"Scheduler: Avvio del ciclo di sincronizzazione automatica.")
            run_task_in_background("Automatica", run_full_sync_cycle)
        else:
            log.info("Scheduler: Salto il ciclo automatico, operazione già in corso.")
        log.info(f"Scheduler: In attesa per {wait_seconds} secondi.")
        time.sleep(wait_seconds)

def run_task_in_background(trigger_type, target_function, *args):
    app_state["is_running"] = True
    task_args = (app_state,) + args if target_function == build_library_index else args
    app_state["status"] = f"Operazione ({trigger_type}) in corso..."
    try:
        target_function(*task_args)
        if target_function == run_full_sync_cycle:
            app_state["last_sync"] = time.strftime("%d/%m/%Y %H:%M:%S")
        app_state["status"] = "In attesa"
    except Exception as e:
        log.error(f"Errore critico durante il ciclo '{trigger_type}': {e}", exc_info=True)
        app_state["status"] = "Errore! Controllare i log."
    finally:
        app_state["is_running"] = False

def get_user_aliases():
    return { 'main': os.getenv('USER_ALIAS_MAIN', 'Utente Principale'), 'secondary': os.getenv('USER_ALIAS_SECONDARY', 'Utente Secondario') }

def start_background_task(target_function, flash_message, *args):
    if app_state["is_running"]:
        # Controlla se la task è di indicizzazione e l'indice è vuoto - in tal caso forza
        if target_function == build_library_index:
            index_stats = get_library_index_stats()
            if index_stats['total_tracks_indexed'] == 0:
                # Forza l'arresto per dare priorità all'indicizzazione
                app_state["is_running"] = False
                app_state["status"] = "Operazione fermata per indicizzazione prioritaria"
                flash("⚠️ Operazione fermata automaticamente. Avvio indicizzazione prioritaria...", "warning")
                # Continua con l'indicizzazione
                flash(flash_message, "info")
                task_thread = threading.Thread(target=run_task_in_background, args=("Manuale", target_function, *args))
                task_thread.start()
            else:
                flash("Un'operazione è già in corso. Attendi il completamento.", "warning")
        else:
            flash("Un'operazione è già in corso. Attendi il completamento.", "warning")
    else:
        flash(flash_message, "info")
        task_thread = threading.Thread(target=run_task_in_background, args=("Manuale", target_function, *args))
        task_thread.start()
    return redirect(request.referrer or url_for('index'))

@app.route('/')
def index():
    # Controlla stato indice libreria per avvisi
    index_stats = get_library_index_stats()
    return render_template('index.html', aliases=get_user_aliases(), index_stats=index_stats)

@app.route('/missing_tracks')
def missing_tracks():
    try:
        all_missing_tracks = get_missing_tracks()
        log.info(f"Recuperate {len(all_missing_tracks)} tracce mancanti")
        return render_template('missing_tracks.html', tracks=all_missing_tracks)
    except Exception as e:
        log.error(f"Errore nella pagina missing_tracks: {e}", exc_info=True)
        flash(f"Errore nel recupero delle tracce mancanti: {str(e)}", "error")
        return render_template('missing_tracks.html', tracks=[])

@app.route('/stats')
def stats():
    selected_user = request.args.get('user', 'main')
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    analysis_type = request.args.get('type', 'favorites')  # 'favorites' o 'library'
    
    if force_refresh: 
        flash('Aggiornamento forzato della cache in corso...', 'info')
    
    user_token = os.getenv('PLEX_TOKEN') if selected_user == 'main' else os.getenv('PLEX_TOKEN_USERS')
    plex_url = os.getenv('PLEX_URL')
    favorites_id = os.getenv('PLEX_FAVORITES_PLAYLIST_ID_MAIN') if selected_user == 'main' else os.getenv('PLEX_FAVORITES_PLAYLIST_ID_SECONDARY')
    user_aliases = get_user_aliases()
    
    # Default error state
    error_msg = None
    charts = {
        'genre_chart': "<div class='alert alert-warning'>Dati non disponibili</div>",
        'decade_chart': "<div class='alert alert-warning'>Dati non disponibili</div>",
        'artists_chart': "<div class='alert alert-warning'>Dati non disponibili</div>",
        'duration_chart': "<div class='alert alert-warning'>Dati non disponibili</div>",
        'trend_chart': "<div class='alert alert-warning'>Dati non disponibili</div>"
    }
    library_stats = {}
    
    # Determina cosa analizzare
    target_id = None
    if analysis_type == 'favorites':
        if not favorites_id:
            error_msg = f"ID della playlist dei preferiti non configurato per '{user_aliases.get(selected_user)}'"
        else:
            target_id = favorites_id
    # analysis_type == 'library' usa target_id = None per analizzare tutta la libreria
    
    if user_token and plex_url and (target_id or analysis_type == 'library') and not error_msg:
        try:
            plex = PlexServer(plex_url, user_token)
            log.info(f"Generazione statistiche per {selected_user} - tipo: {analysis_type}")
            
            # Recupera DataFrame con metadati estesi
            df = get_plex_tracks_as_df(plex, playlist_id=target_id, force_refresh=force_refresh)
            
            if not df.empty:
                # Genera tutti i grafici
                charts['genre_chart'] = generate_genre_pie_chart(df)
                charts['decade_chart'] = generate_decade_bar_chart(df)
                charts['artists_chart'] = generate_top_artists_chart(df, top_n=15)
                charts['duration_chart'] = generate_duration_distribution(df)
                charts['trend_chart'] = generate_year_trend_chart(df)
                
                # Statistiche numeriche avanzate
                library_stats = get_library_statistics(df)
                
                log.info(f"Statistiche generate con successo per {len(df)} tracce")
            else:
                error_msg = "Nessuna traccia trovata per l'analisi"
                
        except Exception as e:
            log.error(f"Errore nella generazione statistiche per {selected_user}: {e}", exc_info=True)
            error_msg = f"Errore nel caricamento dei dati: {str(e)}"
    
    if error_msg:
        flash(error_msg, "warning")
    
    # Prepara informazioni sulla fonte dei dati
    data_source_info = {}
    if analysis_type == 'favorites' and target_id:
        # Ottieni nome della playlist dalla configurazione
        try:
            if user_token and plex_url and not error_msg:
                plex = PlexServer(plex_url, user_token)
                playlist = plex.fetchItem(int(target_id))
                data_source_info = {
                    'type': 'playlist',
                    'name': playlist.title,
                    'id': target_id
                }
        except:
            data_source_info = {
                'type': 'playlist',
                'name': 'Playlist Preferiti',
                'id': target_id
            }
    elif analysis_type == 'library':
        data_source_info = {
            'type': 'library',
            'name': 'Intera Libreria Musicale',
            'id': None
        }
    
    return render_template('stats.html', 
                         charts=charts,
                         library_stats=library_stats,
                         aliases=user_aliases, 
                         selected_user=selected_user,
                         analysis_type=analysis_type,
                         data_source=data_source_info,
                         error_message=error_msg)

@app.route('/ai_lab', methods=['GET', 'POST'])
def ai_lab():
    try:
        selected_user_key = request.args.get('user', 'main')
        user_token, plex_url, user_aliases = (os.getenv('PLEX_TOKEN') if selected_user_key == 'main' else os.getenv('PLEX_TOKEN_USERS')), os.getenv('PLEX_URL'), get_user_aliases()
        
        existing_playlists = get_managed_ai_playlists_for_user(selected_user_key)
    except Exception as e:
        log.error(f"Errore nel recupero dati AI Lab: {e}", exc_info=True)
        flash(f"Errore nel caricamento dell'AI Lab: {str(e)}", "error")
        existing_playlists = []
        user_aliases = get_user_aliases()
        selected_user_key = 'main'

    if request.method == 'POST':
        if app_state["is_running"]:
            flash("Un'operazione è già in corso. Attendi il completamento.", "warning")
            return redirect(url_for('ai_lab', user=selected_user_key))
            
        custom_prompt = request.form.get('custom_prompt')
        if not custom_prompt:
            flash("Il prompt per Gemini non può essere vuoto.", "warning")
            return redirect(url_for('ai_lab', user=selected_user_key))
        
        favorites_id = os.getenv('PLEX_FAVORITES_PLAYLIST_ID_MAIN') if selected_user_key == 'main' else os.getenv('PLEX_FAVORITES_PLAYLIST_ID_SECONDARY')
        if not favorites_id:
            flash(f"ID della playlist dei preferiti non configurato.", "warning")
            return redirect(url_for('ai_lab', user=selected_user_key))

        from plex_playlist_sync.sync_logic import run_downloader_only, rescan_and_update_missing
        temp_user_inputs = UserInputs(plex_url=plex_url, plex_token=user_token, plex_min_songs=0, add_playlist_description=True, add_playlist_poster=True, append_service_suffix=False, write_missing_as_csv=False, append_instead_of_sync=False, wait_seconds=0, spotipy_client_id=None, spotipy_client_secret=None, spotify_user_id=None, spotify_playlist_ids=None, spotify_categories=None, country=None, deezer_user_id=None, deezer_playlist_ids=None, plex_token_others=None)
        
        # Verifica se includere dati classifiche (nuovo parametro)
        include_charts = request.form.get('include_charts', 'on') == 'on'
        
        def generate_and_download_task(plex, user_inputs, fav_id, prompt, user_key):
            log.info("FASE 1: Generazione playlist AI on-demand...")
            generate_on_demand_playlist(plex, user_inputs, fav_id, prompt, user_key, include_charts_data=include_charts)
            log.info("FASE 2: Avvio download automatico...")
            download_attempted = run_downloader_only()
            
            if download_attempted:
                log.info("FASE 3: Attesa scansione Plex e verifica tracce...")
                import time
                wait_time = int(os.getenv("PLEX_SCAN_WAIT_TIME", "300"))
                log.info(f"Attendo {wait_time} secondi per dare a Plex il tempo di indicizzare...")
                time.sleep(wait_time)
                log.info("FASE 4: Rescan e aggiornamento playlist AI...")
                rescan_and_update_missing()
            else:
                log.info("Nessun download eseguito, salto la fase di rescan")
        
        return start_background_task(generate_and_download_task, "Generazione playlist e download automatico avviati!", PlexServer(plex_url, user_token), temp_user_inputs, favorites_id, custom_prompt, selected_user_key)

    return render_template('ai_lab.html', aliases=user_aliases, selected_user=selected_user_key, existing_playlists=existing_playlists)

@app.route('/delete_ai_playlist/<int:playlist_db_id>', methods=['POST'])
def delete_ai_playlist_route(playlist_db_id):
    if app_state["is_running"]:
        flash("Impossibile eliminare mentre un'altra operazione è in corso.", "warning")
        return redirect(url_for('ai_lab'))
        
    playlist_details = get_managed_playlist_details(playlist_db_id)
    if not playlist_details:
        flash("Playlist non trovata nel database.", "warning")
        return redirect(url_for('ai_lab'))

    user_key = playlist_details['user']
    user_token = os.getenv('PLEX_TOKEN') if user_key == 'main' else os.getenv('PLEX_TOKEN_USERS')
    plex_url = os.getenv('PLEX_URL')

    try:
        plex = PlexServer(plex_url, user_token)
        plex_playlist = plex.fetchItem(playlist_details['plex_rating_key'])
        log.warning(f"Eliminazione della playlist '{plex_playlist.title}' da Plex...")
        plex_playlist.delete()
        flash(f"Playlist '{plex_playlist.title}' eliminata da Plex.", "info")
    except NotFound:
        log.warning(f"Playlist con ratingKey {playlist_details['plex_rating_key']} non trovata su Plex, probabilmente già eliminata.")
    except Exception as e:
        log.error(f"Errore durante l'eliminazione della playlist da Plex: {e}")
        flash("Errore durante l'eliminazione da Plex, ma procederò a rimuoverla dal database locale.", "warning")

    delete_managed_ai_playlist(playlist_db_id)
    flash(f"Playlist rimossa dal database di gestione.", "info")
    
    return redirect(url_for('ai_lab', user=user_key))


@app.route('/delete_missing_track/<int:track_id>', methods=['POST'])
def delete_missing_track_route(track_id):
    """Endpoint per eliminare permanentemente una traccia dalla lista dei mancanti."""
    try:
        delete_missing_track(track_id)
        flash("Traccia rimossa con successo dalla lista dei mancanti.", "info")
    except Exception as e:
        log.error(f"Errore durante l'eliminazione della traccia mancante ID {track_id}: {e}")
        flash("Errore durante l'eliminazione della traccia.", "warning")
    return redirect(url_for('missing_tracks'))

@app.route('/delete_all_missing_tracks', methods=['POST'])
def delete_all_missing_tracks_route():
    try:
        delete_all_missing_tracks()
        flash("Tutte le tracce mancanti sono state eliminate.", "info")
    except Exception as e:
        log.error(f"Errore durante l'eliminazione di tutte le tracce mancanti: {e}")
        flash("Errore durante l'eliminazione di tutte le tracce mancanti.", "warning")
    return redirect(url_for('missing_tracks'))

@app.route('/emergency_cleanup', methods=['POST'])
def emergency_cleanup():
    """Pulizia di emergenza: ferma operazioni + pulisce DB tracce mancanti."""
    try:
        # Ferma operazioni in corso
        if app_state["is_running"]:
            app_state["is_running"] = False
            app_state["status"] = "Fermato per pulizia emergenza"
        
        # Pulisce tutte le tracce mancanti (probabilmente false positives)
        delete_all_missing_tracks()
        
        flash("🚨 Pulizia emergenza completata: operazioni fermate e tracce mancanti eliminate.", "success")
        flash("ℹ️ Ora puoi procedere con l'indicizzazione della libreria.", "info")
    except Exception as e:
        log.error(f"Errore durante pulizia emergenza: {e}")
        flash("Errore durante la pulizia di emergenza.", "error")
    return redirect(url_for('index'))

@app.route('/test_database', methods=['POST'])
def test_database():
    """Test diagnostico del database per verificare funzionalità."""
    try:
        from plex_playlist_sync.utils.database import initialize_db, get_library_index_stats, DB_PATH
        import os
        
        log.info("🔧 Avvio test diagnostico database...")
        
        # Test 1: Verifica file
        if os.path.exists(DB_PATH):
            db_size = os.path.getsize(DB_PATH)
            log.info(f"✅ Database esiste: {DB_PATH} ({db_size} bytes)")
        else:
            log.warning(f"⚠️ Database non esiste: {DB_PATH}")
        
        # Test 2: Inizializzazione
        initialize_db()
        log.info("✅ Inizializzazione database completata")
        
        # Test 3: Statistiche
        stats = get_library_index_stats()
        log.info(f"✅ Statistiche database: {stats}")
        
        # Test 4: Verifica finale dimensione
        final_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        log.info(f"✅ Dimensione finale database: {final_size} bytes")
        
        flash(f"✅ Test database completato con successo! ({final_size} bytes, {stats['total_tracks_indexed']} tracce)", "success")
        
    except Exception as e:
        log.error(f"❌ Errore test database: {e}", exc_info=True)
        flash(f"❌ Errore test database: {str(e)}", "error")
    
    return redirect(url_for('index'))

@app.route('/test_matching_improvements', methods=['POST'])
def test_matching_improvements_route():
    """Test per verificare i miglioramenti del sistema di matching."""
    try:
        from plex_playlist_sync.utils.database import test_matching_improvements
        
        log.info("🧪 Avvio test miglioramenti matching...")
        
        # Esegui test su 50 tracce per non rallentare troppo
        results = test_matching_improvements(sample_size=50)
        
        if results:
            improvement_pct = (results['improvements'] / results['test_size']) * 100
            old_pct = (results['old_matches'] / results['test_size']) * 100
            new_pct = (results['new_matches'] / results['test_size']) * 100
            
            flash(f"🧪 Test completato su {results['test_size']} tracce:", "info")
            flash(f"📊 Sistema vecchio: {results['old_matches']} trovate ({old_pct:.1f}%)", "info")
            flash(f"📊 Sistema nuovo: {results['new_matches']} trovate ({new_pct:.1f}%)", "success")
            flash(f"🎯 Miglioramento: +{results['improvements']} tracce ({improvement_pct:.1f}%)", "success")
        else:
            flash("❌ Errore durante il test matching", "error")
        
    except Exception as e:
        log.error(f"❌ Errore test matching: {e}", exc_info=True)
        flash(f"❌ Errore test matching: {str(e)}", "error")
    
    return redirect(url_for('missing_tracks'))

@app.route('/clean_tv_content', methods=['POST'])
def clean_tv_content_route():
    """Rimuove contenuti TV/Film dalle tracce mancanti."""
    try:
        clean_tv_content_from_missing_tracks()
        flash("🧹 Pulizia contenuti TV/Film completata!", "success")
    except Exception as e:
        log.error(f"❌ Errore pulizia TV: {e}", exc_info=True)
        flash(f"❌ Errore pulizia TV: {str(e)}", "error")
    
    return redirect(url_for('missing_tracks'))

@app.route('/clean_resolved_tracks', methods=['POST'])
def clean_resolved_tracks_route():
    """Rimuove tutte le tracce risolte (downloaded + resolved_manual) dalla lista."""
    try:
        removed_count, remaining_count = clean_resolved_missing_tracks()
        if removed_count > 0:
            flash(f"🧹 Rimosse {removed_count} tracce risolte! Rimangono {remaining_count} tracce da risolvere.", "success")
        else:
            flash(f"✅ Nessuna traccia risolta da rimuovere. {remaining_count} tracce ancora in lista.", "info")
    except Exception as e:
        log.error(f"❌ Errore pulizia tracce risolte: {e}", exc_info=True)
        flash(f"❌ Errore pulizia tracce risolte: {str(e)}", "error")
    
    return redirect(url_for('missing_tracks'))

@app.route('/find_and_download_missing_tracks_auto', methods=['POST'])
def find_and_download_missing_tracks_auto():
    
    def task():
        log.info("Avvio ricerca e download automatico manuale...")
        all_missing_tracks = get_missing_tracks()
        tracks_to_download = []

        # Fase 1: Filtra le tracce già presenti usando verifica completa
        log.info(f"Controllo completo di {len(all_missing_tracks)} tracce contro indice + filesystem...")
        for track in all_missing_tracks:
            verification_result = comprehensive_track_verification(track[1], track[2])
            if verification_result['exists']:
                log.info(f'La traccia "{track[1]}" - "{track[2]}" è già presente, la rimuovo dalla lista dei mancanti.')
                delete_missing_track(track[0])
            else:
                tracks_to_download.append(track)
        
        if not tracks_to_download:
            log.info("Nessuna traccia valida rimasta da scaricare dopo il controllo.")
            return

        # Fase 2: Cerca i link per le tracce rimanenti in parallelo
        log.info(f"Avvio ricerca parallela di link per {len(tracks_to_download)} tracce...")
        links_found = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_track = {executor.submit(DeezerLinkFinder.find_track_link, {'title': track[1], 'artist': track[2]}): track for track in tracks_to_download}
            
            for future in concurrent.futures.as_completed(future_to_track):
                track_info = future_to_track[future]
                link = future.result()
                if link:
                    links_found.append((link, track_info[0]))

        # Fase 3: Aggiungi i link alla coda di download
        if links_found:
            log.info(f"Aggiungo {len(links_found)} link alla coda di download.")
            for link, track_id in links_found:
                download_queue.put((link, track_id))
        else:
            log.info("Nessun link di download trovato per le tracce rimanenti.")

    if app_state["is_running"]:
        flash("Un'operazione è già in corso. Attendi il completamento.", "warning")
    else:
        flash("Ricerca e download automatici avviati in background.", "info")
        task_thread = threading.Thread(target=run_task_in_background, args=("Ricerca e Download Auto", task))
        task_thread.start()
        
    return redirect(url_for('missing_tracks'))



@app.route('/download_selected_tracks', methods=['POST'])
def download_selected_tracks():
    data = request.json
    tracks_to_download = data.get('tracks', [])
    if not tracks_to_download: return jsonify({"success": False, "error": "Nessuna traccia selezionata per il download."}), 400

    for track_data in tracks_to_download:
        track_id = track_data.get('track_id')
        album_url = track_data.get('album_url')
        if track_id and album_url:
            download_queue.put((album_url, track_id))
            log.info(f"Traccia {track_id} con URL {album_url} aggiunta alla coda di download (selezione multipla).")

    return jsonify({"success": True, "message": f"{len(tracks_to_download)} download aggiunti alla coda."})

@app.route('/sync_now', methods=['POST'])
def sync_now(): return start_background_task(run_full_sync_cycle, "Sincronizzazione completa avviata!")

@app.route('/cleanup_now', methods=['POST'])
def cleanup_now(): return start_background_task(run_cleanup_only, "Pulizia vecchie playlist avviata!")

@app.route('/build_index', methods=['POST'])
def build_index_route(): 
    # Controllo speciale per indicizzazione - può forzare l'arresto di altre operazioni
    index_stats = get_library_index_stats()
    if app_state["is_running"] and index_stats['total_tracks_indexed'] == 0:
        # Se l'indice è vuoto e c'è un'operazione in corso, la fermiamo per dare priorità all'indicizzazione
        app_state["is_running"] = False
        app_state["status"] = "Operazione fermata per dare priorità all'indicizzazione"
        flash("⚠️ Operazione in corso fermata per dare priorità all'indicizzazione della libreria.", "warning")
    return start_background_task(build_library_index, "Avvio indicizzazione libreria...")

@app.route('/restart_indexing', methods=['POST'])
def restart_indexing():
    """Route per riavviare l'indicizzazione ottimizzata quando l'indice è vuoto."""
    index_stats = get_library_index_stats()
    if app_state["is_running"]:
        # Forza l'arresto se l'indice è vuoto (priorità assoluta)
        if index_stats['total_tracks_indexed'] == 0:
            app_state["is_running"] = False
            app_state["status"] = "Operazione fermata per riavvio indicizzazione ottimizzata"
            flash("⚠️ Operazione fermata per dare priorità al riavvio dell'indicizzazione.", "warning")
        else:
            flash("Un'operazione è già in corso. Attendi il completamento.", "warning")
            return redirect(url_for('index'))
    
    flash("🔄 Riavvio indicizzazione ottimizzata per gestire grandi librerie...", "info")
    return start_background_task(build_library_index, "Riavvio indicizzazione libreria ottimizzata...")

@app.route('/rescan_missing', methods=['POST'])
def rescan_missing_route():
    """Nuova rotta per avviare la scansione post-download manualmente."""
    return start_background_task(rescan_and_update_missing, "Avvio scansione per pulire la lista dei brani mancanti...")

@app.route('/force_playlist_scan', methods=['POST'])
def force_playlist_scan_route():
    """Forza la scansione delle playlist per rilevare tracce mancanti."""
    return start_background_task(force_playlist_scan_and_missing_detection, "Avvio scansione forzata playlist per rilevare tracce mancanti...")

@app.route('/force_stop_operations', methods=['POST'])
def force_stop_operations():
    """Forza l'arresto delle operazioni in corso per permettere l'indicizzazione."""
    if app_state["is_running"]:
        app_state["is_running"] = False
        app_state["status"] = "Operazioni fermate manualmente"
        flash("⚠️ Operazioni in corso fermate. Ora puoi indicizzare la libreria.", "warning")
    else:
        flash("ℹ️ Nessuna operazione in corso da fermare.", "info")
    return redirect(url_for('index'))

@app.route('/comprehensive_verify_missing', methods=['POST'])
def comprehensive_verify_missing_route():
    """Rivaluta tutte le tracce mancanti usando verifica completa (fuzzy + filesystem)."""
    
    def comprehensive_verification_task():
        log.info("--- Avvio verifica completa tracce mancanti ---")
        all_missing_tracks = get_missing_tracks()
        
        if not all_missing_tracks:
            log.info("Nessuna traccia mancante da verificare.")
            app_state['status'] = "Nessuna traccia da verificare"
            return
        
        total_tracks = len(all_missing_tracks)
        log.info(f"Verifica completa di {total_tracks} tracce segnalate come mancanti...")
        app_state['status'] = f"Verifica completa: 0/{total_tracks} tracce controllate"
        
        false_positives = []
        truly_missing = []
        verification_stats = {
            'total_checked': total_tracks,
            'exact_matches': 0,
            'fuzzy_matches': 0,
            'filesystem_matches': 0,
            'truly_missing': 0
        }
        
        for i, track in enumerate(all_missing_tracks, 1):
            track_id, title, artist = track[0], track[1], track[2]
            
            # Aggiorna status con progresso
            app_state['status'] = f"Verifica completa: {i}/{total_tracks} - Controllando '{title[:30]}...' di {artist[:20]}..."
            
            try:
                # Usa la verifica completa
                verification_result = comprehensive_track_verification(title, artist)
                
                if verification_result['exists']:
                    false_positives.append((track_id, title, artist))
                    
                    # Aggiorna le statistiche
                    if verification_result['exact_match']:
                        verification_stats['exact_matches'] += 1
                        log.info(f"FALSO POSITIVO (EXACT): '{title}' - '{artist}' trovato nell'indice")
                    elif verification_result['fuzzy_match']:
                        verification_stats['fuzzy_matches'] += 1
                        log.info(f"FALSO POSITIVO (FUZZY): '{title}' - '{artist}' trovato con fuzzy matching")
                    elif verification_result['filesystem_match']:
                        verification_stats['filesystem_matches'] += 1
                        log.info(f"FALSO POSITIVO (FILESYSTEM): '{title}' - '{artist}' trovato nel filesystem")
                    
                    # Rimuovi dalla lista mancanti
                    delete_missing_track(track_id)
                else:
                    truly_missing.append((track_id, title, artist))
                    verification_stats['truly_missing'] += 1
                    log.debug(f"VERAMENTE MANCANTE: '{title}' - '{artist}'")
                    
            except Exception as track_error:
                log.error(f"Errore nella verifica di '{title}' - '{artist}': {track_error}")
                verification_stats['truly_missing'] += 1  # Considera come mancante in caso di errore
        
        # Calcola statistiche finali
        total_removed = len(false_positives)
        reduction_percentage = (total_removed / total_tracks * 100) if total_tracks > 0 else 0
        
        # Log statistiche finali
        log.info(f"=== RISULTATI VERIFICA COMPLETA ===")
        log.info(f"Tracce controllate: {verification_stats['total_checked']}")
        log.info(f"Falsi positivi rimossi: {total_removed} ({reduction_percentage:.1f}%)")
        log.info(f"  - Exact matches (indice): {verification_stats['exact_matches']}")
        log.info(f"  - Fuzzy matches (indice): {verification_stats['fuzzy_matches']}")
        log.info(f"  - Filesystem matches: {verification_stats['filesystem_matches']}")
        log.info(f"Tracce veramente mancanti: {verification_stats['truly_missing']}")
        log.info(f"Riduzione lista missing: {reduction_percentage:.1f}%")
        log.info(f"=== FINE VERIFICA COMPLETA ===")
        
        app_state['status'] = f"Verifica completa: {total_removed} falsi positivi rimossi ({reduction_percentage:.1f}% riduzione)"
    
    if app_state["is_running"]:
        flash("Un'operazione è già in corso. Attendi il completamento.", "warning")
        return redirect(url_for('missing_tracks'))
    else:
        flash("Avvio verifica completa tracce mancanti (fuzzy + filesystem)...", "info")
        task_thread = threading.Thread(target=run_task_in_background, args=("Verifica Completa", comprehensive_verification_task))
        task_thread.start()
        return redirect(url_for('missing_tracks'))


@app.route('/search_plex_manual')
def search_plex_manual():
    query, user_key = request.args.get('query'), request.args.get('user', 'main')
    if not query: return jsonify({"error": "Query di ricerca vuota."}), 400
    user_token, plex_url = (os.getenv('PLEX_TOKEN'), os.getenv('PLEX_URL')) if user_key == 'main' else (os.getenv('PLEX_TOKEN_USERS'), os.getenv('PLEX_URL'))
    if not (user_token and plex_url): return jsonify({"error": "Credenziali Plex non trovate."}), 500
    try:
        plex = PlexServer(plex_url, user_token)
        results = plex.search(query, mediatype='track', limit=15)
        return jsonify([{'title': r.title, 'artist': r.grandparentTitle, 'album': r.parentTitle, 'ratingKey': r.ratingKey} for r in results if isinstance(r, Track)])
    except Exception as e:
        log.error(f"Errore ricerca manuale Plex: {e}")
        return jsonify({"error": "Errore server durante la ricerca."}), 500

@app.route('/associate_track', methods=['POST'])
def associate_track():
    data = request.json
    missing_track_id, plex_track_rating_key, user_key = data.get('missing_track_id'), data.get('plex_track_rating_key'), data.get('user_key', 'main')
    if not all([missing_track_id, plex_track_rating_key, user_key]): return jsonify({"success": False, "error": "Dati incompleti."}), 400
    user_token, plex_url = (os.getenv('PLEX_TOKEN'), os.getenv('PLEX_URL')) if user_key == 'main' else (os.getenv('PLEX_TOKEN_USERS'), os.getenv('PLEX_URL'))
    if not (user_token and plex_url): return jsonify({"success": False, "error": "Credenziali Plex non trovate."}), 500
    try:
        plex = PlexServer(plex_url, user_token)
        missing_track_info = get_missing_track_by_id(missing_track_id)
        if not missing_track_info: return jsonify({"success": False, "error": "Traccia mancante non trovata nel DB."}), 404
        playlist_id = missing_track_info['source_playlist_id']
        playlist_to_update = plex.fetchItem(playlist_id)
        track_to_add = plex.fetchItem(int(plex_track_rating_key))
        log.info(f"Associazione: Aggiunta di '{track_to_add.title}' alla playlist '{playlist_to_update.title}'.")
        playlist_to_update.addItems([track_to_add])
        update_track_status(missing_track_id, 'resolved_manual')
        return jsonify({"success": True, "message": "Traccia associata e aggiunta alla playlist."})
    except NotFound: return jsonify({"success": False, "error": f"Playlist con ID '{playlist_id}' non trovata."}), 404
    except Exception as e:
        log.error(f"Errore associazione traccia: {e}")
        return jsonify({"success": False, "error": "Errore server durante l'associazione."}), 500

@app.route('/search_deezer_manual')
def search_deezer_manual():
    title, artist = request.args.get('title'), request.args.get('artist')
    if not title or not artist: return jsonify({"error": "Titolo e artista richiesti"}), 400
    return jsonify(DeezerLinkFinder.find_potential_tracks(title, artist))

@app.route('/download_track', methods=['POST'])
def download_track():
    data = request.json
    track_id, album_url = data.get('track_id'), data.get('album_url')
    if not track_id or not album_url: return jsonify({"success": False, "error": "Dati incompleti."}), 400
    
    # Aggiungi il download alla coda, non bloccare l'UI
    download_queue.put((album_url, track_id))
    log.info(f"Traccia {track_id} con URL {album_url} aggiunta alla coda di download.")
    return jsonify({"success": True, "message": "Download aggiunto alla coda."})

@app.route('/get_logs')
def get_logs():
    try:
        with open("plex_sync.log", "r", encoding="utf-8") as f:
            log_content = "".join(f.readlines()[-100:])
    except FileNotFoundError:
        log_content = "File di log non ancora creato."
    return jsonify(logs=log_content, status=app_state["status"], last_sync=app_state["last_sync"], is_running=app_state["is_running"])

@app.route('/api/stats')
def api_stats():
    """API endpoint per statistiche in tempo reale"""
    try:
        # Get missing tracks stats
        missing_tracks = get_missing_tracks()
        total_missing = len(missing_tracks)
        
        # Count by status
        status_counts = {}
        for track in missing_tracks:
            status = track[6] if len(track) > 6 else 'missing'
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # Get AI playlists count
        ai_playlists_main = len(get_managed_ai_playlists_for_user('main'))
        ai_playlists_secondary = len(get_managed_ai_playlists_for_user('secondary'))
        total_ai_playlists = ai_playlists_main + ai_playlists_secondary
        
        # Simulate library stats (you can replace with real Plex queries)
        library_stats = {
            'total_tracks': 5000 + len(missing_tracks),  # Mock data
            'sync_health': 'excellent' if total_missing < 10 else 'good' if total_missing < 50 else 'needs_attention'
        }
        
        return jsonify({
            'missing_tracks': {
                'total': total_missing,
                'missing': status_counts.get('missing', 0),
                'downloaded': status_counts.get('downloaded', 0),
                'resolved': status_counts.get('resolved', 0),
                'resolved_manual': status_counts.get('resolved_manual', 0)
            },
            'ai_playlists': {
                'total': total_ai_playlists,
                'main_user': ai_playlists_main,
                'secondary_user': ai_playlists_secondary
            },
            'library': library_stats,
            'system': {
                'status': app_state["status"],
                'last_sync': app_state["last_sync"],
                'is_running': app_state["is_running"]
            }
        })
    except Exception as e:
        log.error(f"Errore nel recupero statistiche API: {e}", exc_info=True)
        return jsonify({'error': 'Errore nel recupero statistiche'}), 500

@app.route('/api/missing_tracks')
def api_missing_tracks():
    """API endpoint per tracce mancanti con filtri"""
    try:
        search = request.args.get('search', '')
        status_filter = request.args.get('status', '')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        all_tracks = get_missing_tracks()
        
        # Filter tracks
        filtered_tracks = []
        for track in all_tracks:
            # Search filter
            if search:
                search_lower = search.lower()
                if not (search_lower in track[1].lower() or  # title
                       search_lower in track[2].lower() or   # artist
                       search_lower in (track[3] or '').lower()):  # album
                    continue
            
            # Status filter
            if status_filter and track[6] != status_filter:
                continue
                
            filtered_tracks.append({
                'id': track[0],
                'title': track[1],
                'artist': track[2],
                'album': track[3],
                'playlist': track[4],
                'playlist_id': track[5],
                'status': track[6]
            })
        
        # Pagination
        total = len(filtered_tracks)
        paginated_tracks = filtered_tracks[offset:offset+limit]
        
        return jsonify({
            'tracks': paginated_tracks,
            'total': total,
            'limit': limit,
            'offset': offset,
            'has_more': offset + limit < total
        })
    except Exception as e:
        log.error(f"Errore nel recupero tracce mancanti API: {e}", exc_info=True)
        return jsonify({'error': 'Errore nel recupero tracce'}), 500

@app.route('/api/music_charts_preview')
def api_music_charts_preview():
    """API endpoint per anteprima dati classifiche musicali"""
    try:
        from plex_playlist_sync.utils.gemini_ai import get_music_charts_preview
        preview_data = get_music_charts_preview()
        return jsonify(preview_data)
    except Exception as e:
        log.error(f"Errore nel recupero anteprima classifiche: {e}", exc_info=True)
        return jsonify({'error': 'Errore nel recupero dati classifiche'}), 500

@app.route('/api/test_music_charts')
def api_test_music_charts():
    """API endpoint per testare integrazione classifiche musicali"""
    try:
        from plex_playlist_sync.utils.gemini_ai import test_music_charts_integration
        test_result = test_music_charts_integration()
        return jsonify({'success': test_result, 'message': 'Test completato con successo' if test_result else 'Test fallito'})
    except Exception as e:
        log.error(f"Errore nel test classifiche: {e}", exc_info=True)
        return jsonify({'error': 'Errore nel test classifiche'}), 500

if __name__ == '__main__':
    log.info("Avvio dell'applicazione Flask...")
    scheduler_thread = threading.Thread(target=background_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Avvia il worker per i download in un thread separato
    download_worker_thread = threading.Thread(target=download_worker, daemon=True)
    download_worker_thread.start()

    app.run(host='0.0.0.0', port=5000)
