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
    level=logging.WARNING,
    format="%(asctime)s - %(name)s -[%(levelname)s] - %(message)s",
    handlers=[
        logging.FileHandler("plex_sync.log", mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log.setLevel(logging.WARNING)
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
from plex_playlist_sync.utils.downloader import DeezerLinkFinder, download_single_track_with_streamrip, find_potential_tracks, find_tracks_free_search
from plex_playlist_sync.utils.i18n import init_i18n_for_app, translate_status
from plex_playlist_sync.utils.soulseek_post_processor import process_soulseek_downloads

initialize_db()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "una-chiave-segreta-casuale-e-robusta")

# Initialize i18n service
init_i18n_for_app(app)

app_state = { "status": "In attesa", "last_sync": "Mai eseguito", "is_running": False }

# Sistema di notifiche per l'interfaccia utente
user_notifications = []

# Numero massimo di tentativi per il download di una traccia
MAX_DOWNLOAD_RETRIES = int(os.getenv("DOWNLOAD_MAX_RETRIES", 3))

# Coda per i download e ThreadPoolExecutor per l'esecuzione parallela
download_queue = queue.Queue()
download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2) # Ridotto a 2 per evitare sovraccarico

def download_worker():
    while True:
        track_info = download_queue.get()
        if track_info is None:  # Sentinella per terminare il worker
            break

        # Gestisce sia tuple a 2 che a 3 elementi per retrocompatibilità
        if len(track_info) == 3:
            link, track_id, attempts = track_info
        else:
            link, track_id = track_info
            attempts = 0

        try:
            source = "Deezer"
            log.info(
                f"Starting download attempt {attempts + 1}/{MAX_DOWNLOAD_RETRIES} "
                f"from {source} for {link} (Track ID: {track_id})"
            )

            # passa 'source' alla funzione di download
            download_result = download_single_track_with_streamrip(link, source=source)
            
            if download_result and download_result.get("success"):
                # aggiorna lo stato
                update_track_status(track_id, "downloaded", source=source)
                
                # Aggiungi notifica per l'utente
                notification = {
                    "type": "success" if download_result.get("files_copied", 0) > 0 else "info",
                    "message": download_result.get("message", "Download completato"),
                    "timestamp": time.time(),
                    "details": {
                        "files_copied": download_result.get("files_copied", 0),
                        "files_found": download_result.get("files_found", 0),
                        "already_existed": download_result.get("already_existed", False)
                    }
                }
                user_notifications.append(notification)
                log.info(f"Added notification to queue: {notification}") # Debug log
                
                # Mantieni solo le ultime 10 notifiche
                if len(user_notifications) > 10:
                    user_notifications.pop(0)
                
                log.info(
                    f"Download completed from {source} for {link} (Track ID: {track_id}): {download_result.get('message')}"
                )
            else:
                error_msg = download_result.get("message", "Download fallito") if download_result else "Errore sconosciuto"
                update_track_status(track_id, "failed", source=source)
                
                # Aggiungi notifica di errore
                notification = {
                    "type": "error",
                    "message": f"Errore download: {error_msg}",
                    "timestamp": time.time()
                }
                user_notifications.append(notification)
                
                if len(user_notifications) > 10:
                    user_notifications.pop(0)
                
                log.error(f"Download failed for {link} (Track ID: {track_id}): {error_msg}")
                return  # Non riprovare se la funzione ha restituito un errore specifico

        except Exception as e:
            attempts += 1
            if attempts < MAX_DOWNLOAD_RETRIES:
                log.error(
                    f"Error downloading {link} (Track ID: {track_id}) attempt {attempts}: {e} - requeuing",
                    exc_info=True,
                )
                download_queue.put((link, track_id, attempts))
            else:
                log.error(
                    f"Failed to download {link} (Track ID: {track_id}) after {attempts} attempts: {e}",
                    exc_info=True,
                )
                update_track_status(track_id, "failed", source="Deezer")
                
                # Aggiungi notifica di fallimento definitivo
                notification = {
                    "type": "error",
                    "message": f"Download fallito dopo {attempts} tentativi",
                    "timestamp": time.time()
                }
                user_notifications.append(notification)
                
                if len(user_notifications) > 10:
                    user_notifications.pop(0)
        finally:
            download_queue.task_done()

def background_scheduler():
    time.sleep(10)
    wait_seconds = int(os.getenv("SECONDS_TO_WAIT", 86400))
    while True:
        if not app_state["is_running"]:
            log.info("Scheduler: Starting automatic synchronization cycle.")
            run_task_in_background("Automatica", run_full_sync_cycle)
        else:
            log.info("Scheduler: Skipping automatic cycle, operation already in progress.")
        log.info(f"Scheduler: Waiting for {wait_seconds} seconds.")
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
        log.error(f"Critical error during '{trigger_type}' cycle: {e}", exc_info=True)
        app_state["status"] = "Error! Check logs."
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
                flash("An operation is already in progress. Please wait for completion.", "warning")
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
        log.info(f"Retrieved {len(all_missing_tracks)} missing tracks")
        return render_template('missing_tracks.html', tracks=all_missing_tracks)
    except Exception as e:
        log.error(f"Error in missing_tracks page: {e}", exc_info=True)
        flash(f"Errore nel recupero delle tracce mancanti: {str(e)}", "error")
        return render_template('missing_tracks.html', tracks=[])

@app.route('/stats')
def stats():
    selected_user = request.args.get('user', 'main')
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    analysis_type = request.args.get('type', 'favorites')  # 'favorites' o 'library'
    
    if force_refresh: 
        flash('Forced cache update in progress...', 'info')
    
    user_token = os.getenv('PLEX_TOKEN') if selected_user == 'main' else os.getenv('PLEX_TOKEN_USERS')
    plex_url = os.getenv('PLEX_URL')
    favorites_id = os.getenv('PLEX_FAVORITES_PLAYLIST_ID_MAIN') if selected_user == 'main' else os.getenv('PLEX_FAVORITES_PLAYLIST_ID_SECONDARY')
    user_aliases = get_user_aliases()
    
    # Default error state
    error_msg = None
    charts = {
        'genre_chart': "<div class='alert alert-warning'>Data not available</div>",
        'decade_chart': "<div class='alert alert-warning'>Data not available</div>",
        'artists_chart': "<div class='alert alert-warning'>Data not available</div>",
        'duration_chart': "<div class='alert alert-warning'>Data not available</div>",
        'trend_chart': "<div class='alert alert-warning'>Data not available</div>"
    }
    library_stats = {}
    
    # Determina cosa analizzare
    target_id = None
    if analysis_type == 'favorites':
        if not favorites_id:
            error_msg = f"Favorites playlist ID not configured for '{user_aliases.get(selected_user)}'"
        else:
            target_id = favorites_id
    # analysis_type == 'library' usa target_id = None per analizzare tutta la libreria
    
    if user_token and plex_url and (target_id or analysis_type == 'library') and not error_msg:
        try:
            plex = PlexServer(plex_url, user_token)
            log.info(f"Generating statistics for {selected_user} - type: {analysis_type}")
            
            # Get current language for data processing and charts
            from plex_playlist_sync.utils.i18n import get_i18n
            current_lang = get_i18n().get_language()
            
            # Retrieve DataFrame with extended metadata
            df = get_plex_tracks_as_df(plex, playlist_id=target_id, force_refresh=force_refresh, language=current_lang)
            
            if not df.empty:
                
                # Generate all charts with language support
                charts['genre_chart'] = generate_genre_pie_chart(df, current_lang)
                charts['decade_chart'] = generate_decade_bar_chart(df, current_lang)
                charts['artists_chart'] = generate_top_artists_chart(df, top_n=15, language=current_lang)
                charts['duration_chart'] = generate_duration_distribution(df, current_lang)
                charts['trend_chart'] = generate_year_trend_chart(df, current_lang)
                
                # Statistiche numeriche avanzate
                library_stats = get_library_statistics(df)
                
                log.info(f"Statistics generated successfully for {len(df)} tracks")
            else:
                error_msg = "No tracks found for analysis"
                
        except Exception as e:
            log.error(f"Error generating statistics for {selected_user}: {e}", exc_info=True)
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
        log.error(f"Error retrieving AI Lab data: {e}", exc_info=True)
        flash(f"Error loading AI Lab: {str(e)}", "error")
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
            log.info("PHASE 1: On-demand AI playlist generation...")
            generate_on_demand_playlist(plex, user_inputs, fav_id, prompt, user_key, include_charts_data=include_charts)
            log.info("PHASE 2: Starting automatic download from Deezer...")
            download_attempted = run_downloader_only(source="Deezer")
            
            if download_attempted:
                log.info("PHASE 3: Waiting for Plex scan and track verification...")
                import time
                wait_time = int(os.getenv("PLEX_SCAN_WAIT_TIME", "300"))
                log.info(f"Waiting {wait_time} seconds to give Plex time to index...")
                time.sleep(wait_time)
                log.info("PHASE 4: Rescan and AI playlist update...")
                rescan_and_update_missing()
            else:
                log.info("Nessun download eseguito, salto la fase di rescan")
        
        return start_background_task(generate_and_download_task, "Generazione playlist e download automatico avviati!", PlexServer(plex_url, user_token), temp_user_inputs, favorites_id, custom_prompt, selected_user_key)

    return render_template('ai_lab.html', aliases=user_aliases, selected_user=selected_user_key, existing_playlists=existing_playlists)

@app.route('/delete_ai_playlist/<int:playlist_db_id>', methods=['POST'])
def delete_ai_playlist_route(playlist_db_id):
    if app_state["is_running"]:
        flash("Cannot delete while another operation is in progress.", "warning")
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
        log.warning(f"Deleting playlist '{plex_playlist.title}' from Plex...")
        plex_playlist.delete()
        flash(f"Playlist '{plex_playlist.title}' deleted from Plex.", "info")
    except NotFound:
        log.warning(f"Playlist with ratingKey {playlist_details['plex_rating_key']} not found on Plex, probably already deleted.")
    except Exception as e:
        log.error(f"Error deleting playlist from Plex: {e}")
        flash("Error deleting from Plex, but will proceed to remove from local database.", "warning")

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
        flash("Error deleting track.", "warning")
    return redirect(url_for('missing_tracks'))

@app.route('/delete_all_missing_tracks', methods=['POST'])
def delete_all_missing_tracks_route():
    try:
        delete_all_missing_tracks()
        flash("Tutte le tracce mancanti sono state eliminate.", "info")
    except Exception as e:
        log.error(f"Errore durante l'eliminazione di tutte le tracce mancanti: {e}")
        flash("Error deleting all missing tracks.", "warning")
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
        flash("ℹ️ You can now proceed with library indexing.", "info")
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
        
        flash(f"✅ Database test completed successfully! ({final_size} bytes, {stats['total_tracks_indexed']} tracks)", "success")
        
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
            
            flash(f"🧪 Test completed on {results['test_size']} tracks:", "info")
            flash(f"📊 Old system: {results['old_matches']} found ({old_pct:.1f}%)", "info")
            flash(f"📊 New system: {results['new_matches']} found ({new_pct:.1f}%)", "success")
            flash(f"🎯 Improvement: +{results['improvements']} tracks ({improvement_pct:.1f}%)", "success")
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
            log.info("No valid tracks left to download after verification.")
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
                download_queue.put((link, track_id, 0))
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
            download_queue.put((album_url, track_id, 0))
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
        flash("⚠️ Operation in progress stopped to prioritize library indexing.", "warning")
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
            flash("⚠️ Operation stopped to prioritize indexing restart.", "warning")
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
            log.info("No missing tracks to verify.")
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
    log.info(f"Associate track endpoint called - Content-Type: {request.content_type}")
    log.info(f"Request data: {request.get_data(as_text=True)}")
    data = request.json
    log.info(f"Parsed JSON data: {data}")
    missing_track_id, plex_track_rating_key, user_key = data.get('missing_track_id'), data.get('plex_track_rating_key'), data.get('user_key', 'main')
    if not all([missing_track_id, plex_track_rating_key, user_key]): return jsonify({"success": False, "error": "Dati incompleti."}), 400
    user_token, plex_url = (os.getenv('PLEX_TOKEN'), os.getenv('PLEX_URL')) if user_key == 'main' else (os.getenv('PLEX_TOKEN_USERS'), os.getenv('PLEX_URL'))
    if not (user_token and plex_url): return jsonify({"success": False, "error": "Credenziali Plex non trovate."}), 500
    try:
        log.info(f"Connecting to Plex server...")
        plex = PlexServer(plex_url, user_token)
        log.info(f"Getting missing track info for ID: {missing_track_id}")
        missing_track_info = get_missing_track_by_id(missing_track_id)
        if not missing_track_info: 
            log.error(f"Missing track ID {missing_track_id} not found in database")
            return jsonify({"success": False, "error": "Traccia mancante non trovata nel DB."}), 404
        playlist_id = missing_track_info['source_playlist_id']
        log.info(f"Getting Plex track with rating key: {plex_track_rating_key}")
        track_to_add = plex.fetchItem(int(plex_track_rating_key))
        
        # Try to add to playlist if it still exists, but don't fail if playlist is gone
        try:
            log.info(f"Getting playlist with ID: {playlist_id}")
            playlist_to_update = plex.fetchItem(playlist_id)
            log.info(f"Associazione: Aggiunta di '{track_to_add.title}' alla playlist '{playlist_to_update.title}'.")
            playlist_to_update.addItems([track_to_add])
            success_message = f"Traccia '{track_to_add.title}' associata e aggiunta alla playlist '{playlist_to_update.title}'."
        except NotFound:
            log.warning(f"Playlist {playlist_id} no longer exists, marking track as found without adding to playlist")
            success_message = f"Traccia '{track_to_add.title}' trovata e marcata come risolta (playlist originale non più disponibile)."
        
        update_track_status(missing_track_id, 'resolved_manual')
        log.info(f"Association completed successfully")
        return jsonify({"success": True, "message": success_message})
    except NotFound as e: 
        log.error(f"Playlist o traccia non trovata: {e}")
        return jsonify({"success": False, "error": "Playlist o traccia non trovata."}), 404
    except Exception as e:
        log.error(f"Errore associazione traccia: {e}")
        return jsonify({"success": False, "error": "Errore server durante l'associazione."}), 500

@app.route('/search_deezer_manual')
def search_deezer_manual():
    title, artist = request.args.get('title'), request.args.get('artist')
    if not title or not artist: return jsonify({"error": "Titolo e artista richiesti"}), 400
    return jsonify(find_potential_tracks(title, artist))

@app.route('/search_deezer_free')
def search_deezer_free():
    """Ricerca libera su Deezer con campo di testo personalizzato"""
    query = request.args.get('query')
    if not query: return jsonify({"error": "Query di ricerca richiesta"}), 400
    return jsonify(find_tracks_free_search(query))

@app.route('/search_album_multi_source')
def search_album_multi_source():
    """Ricerca album su Soulseek e Deezer per download nelle missing tracks"""
    album_name = request.args.get('album')
    source = request.args.get('source', 'soulseek')  # default soulseek
    
    if not album_name:
        return jsonify({"error": "Nome album richiesto"}), 400
    
    try:
        if source == 'soulseek':
            return search_album_soulseek(album_name)
        elif source == 'deezer':
            return search_album_deezer(album_name)
        else:
            return jsonify({"error": "Fonte non supportata. Usa 'soulseek' o 'deezer'"}), 400
            
    except Exception as e:
        log.error(f"Errore durante ricerca album '{album_name}' su {source}: {e}")
        return jsonify({"error": f"Errore durante la ricerca: {str(e)}"}), 500

def search_album_soulseek(album_name: str):
    """Cerca album su Soulseek"""
    from plex_playlist_sync.utils.soulseek import SoulseekClient
    
    client = SoulseekClient()
    if not client.enabled:
        return jsonify({"error": "Soulseek non è abilitato. Controlla USE_SOULSEEK nel .env"}), 400
    
    # Cerca l'album
    search_id = client.search(album_name)
    if not search_id:
        return jsonify({"results": [], "message": f"Nessun risultato trovato per '{album_name}' su Soulseek"})
    
    # Attendi i risultati con polling intelligente
    log.info(f"Waiting for Soulseek search results for '{album_name}' (intelligent polling enabled)...")
    responses = client.wait_for_search_completion(search_id, max_wait_time=120, check_interval=3)
    if not responses:
        return jsonify({"results": [], "message": f"Nessun risultato disponibile per '{album_name}' su Soulseek"})
    
    # Filtra e formatta i risultati per album/cartelle e singole tracce
    album_results = []
    seen_folders = set()
    single_tracks = []
    
    log.info(f"Processing {len(responses)} Soulseek responses for '{album_name}'")
    
    for response in responses[:15]:  # Prendi i primi 15 utenti per più opzioni
        username = response.get("username", "Unknown")
        files = response.get("files", [])
        
        log.debug(f"User '{username}' has {len(files)} files")
        
        # Raggruppa per cartelle (album) - supporta sia Windows (\) che Unix (/)
        folders = {}
        for file in files:
            filename = file.get("filename", "")
            
            # Filtra file video e non audio
            if any(ext in filename.lower() for ext in ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v']):
                log.debug(f"Skipping video file: {filename}")
                continue
                
            # Considera solo file audio e immagini (per cover)
            if not any(ext in filename.lower() for ext in ['.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.wma', '.jpg', '.jpeg', '.png', '.gif', '.bmp']):
                log.debug(f"Skipping non-audio file: {filename}")
                continue
            
            # Prova entrambi i separatori
            parts = filename.replace("\\", "/").split("/")
            
            if len(parts) > 1:
                folder = "/".join(parts[:-1])  # Tutto tranne il nome file
                if folder not in folders:
                    folders[folder] = []
                folders[folder].append(file)
            else:
                # File singolo senza cartella (solo se audio)
                if any(ext in filename.lower() for ext in ['.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.wma']):
                    single_tracks.append({
                        "username": username,
                        "filename": filename,
                        "file": file,
                        "queue_length": response.get("queueLength", 0),
                        "has_free_slot": response.get("hasFreeUploadSlot", False)
                    })
        
        # Aggiungi cartelle che sembrano album (almeno 1 file ora, era 3)
        for folder, folder_files in folders.items():
            if len(folder_files) >= 1:  # Almeno 1 file (più permissivo)
                folder_key = f"{username}|{folder}"
                if folder_key not in seen_folders:
                    seen_folders.add(folder_key)
                    album_title = folder.split("/")[-1] if "/" in folder else folder
                    # Calcola metadati aggiuntivi
                    total_size = sum(f.get("size", 0) for f in folder_files)
                    total_size_mb = total_size / (1024 * 1024) if total_size > 0 else 0
                    
                    # Rileva formati audio e qualità dettagliati
                    audio_formats = set()
                    quality_info = set()
                    bitrates = []
                    best_quality = "Unknown"
                    
                    for f in folder_files:
                        filename = f.get("filename", "").lower()
                        attrs = f.get("attributes", {})
                        
                        # Solo per file audio (esclude immagini)
                        if not any(ext in filename for ext in ['.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.wma']):
                            continue
                        
                        # Debug logging per capire la struttura
                        if attrs:
                            log.debug(f"File attributes for {filename}: {attrs}")
                            # Log specifico per campi di durata
                            duration_fields = {k: v for k, v in attrs.items() if any(d in k.lower() for d in ['duration', 'length', 'time'])}
                            if duration_fields:
                                log.info(f"Duration fields found in {filename}: {duration_fields}")
                            else:
                                log.debug(f"No duration fields found in {filename}. Available keys: {list(attrs.keys())}")
                        
                        # Determina formato e qualità
                        if any(ext in filename for ext in ['.flac', '.wav']):
                            audio_formats.add('Lossless')
                            if '.flac' in filename:
                                best_quality = "FLAC"
                            elif '.wav' in filename:
                                best_quality = "WAV"
                        elif any(ext in filename for ext in ['.mp3', '.m4a', '.aac']):
                            audio_formats.add('Compressed')
                        
                        # Estrai bitrate e sample rate - prova diversi nomi di campo
                        if isinstance(attrs, dict):
                            # Prova diversi nomi possibili per bitrate
                            bitrate = attrs.get("bitRate") or attrs.get("bitrate") or attrs.get("bit_rate") or attrs.get("Bitrate")
                            sample_rate = attrs.get("sampleRate") or attrs.get("sample_rate") or attrs.get("SampleRate")
                            bit_depth = attrs.get("bitDepth") or attrs.get("bit_depth") or attrs.get("BitDepth") or 16
                            duration = attrs.get("duration") or attrs.get("length") or attrs.get("Duration") or attrs.get("Length")
                            
                            # Log per debug
                            if bitrate or sample_rate or duration:
                                log.debug(f"Parsed attributes: bitrate={bitrate}, sample_rate={sample_rate}, duration={duration}")
                            
                            if bitrate:
                                bitrates.append(bitrate)
                                
                                # Determina qualità basata su bitrate
                                if bitrate >= 1411:  # CD quality o superiore
                                    quality_info.add(f"CD+ ({sample_rate/1000:.1f}kHz/{bit_depth}bit)" if sample_rate else "Lossless")
                                    if best_quality == "Unknown":
                                        best_quality = "FLAC" if '.flac' in filename else "CD+"
                                elif bitrate >= 320:
                                    quality_info.add(f"320kbps")
                                    if best_quality == "Unknown":
                                        best_quality = "320kbps"
                                elif bitrate >= 256:
                                    quality_info.add(f"256kbps")
                                    if best_quality == "Unknown":
                                        best_quality = "256kbps"
                                elif bitrate >= 192:
                                    quality_info.add(f"192kbps")
                                    if best_quality == "Unknown":
                                        best_quality = "192kbps"
                                elif bitrate >= 128:
                                    quality_info.add(f"128kbps")
                                    if best_quality == "Unknown":
                                        best_quality = "128kbps"
                                else:
                                    quality_info.add(f"{bitrate}kbps")
                                    if best_quality == "Unknown":
                                        best_quality = f"{bitrate}kbps"
                    
                    # Determina la migliore qualità per il badge
                    if best_quality == "Unknown" and bitrates:
                        max_bitrate = max(bitrates)
                        if max_bitrate >= 320:
                            best_quality = "320kbps"
                        elif max_bitrate >= 128:
                            best_quality = f"{max_bitrate}kbps"
                    
                    album_results.append({
                        "username": username,
                        "folder": folder,
                        "album_title": album_title,
                        "track_count": len(folder_files),
                        "files": folder_files,  # Tutti i file, non solo 5
                        "queue_length": response.get("queueLength", 0),
                        "has_free_slot": response.get("hasFreeUploadSlot", False),
                        "upload_speed": response.get("uploadSpeed", 0),
                        "total_size_mb": round(total_size_mb, 2),
                        "audio_formats": list(audio_formats),
                        "quality_info": list(quality_info),
                        "best_quality": best_quality,
                        "bitrates": bitrates,
                        "type": "album"
                    })
    
    # Aggiungi anche le migliori singole tracce se non abbiamo molti album
    if len(album_results) < 5:
        single_tracks.sort(key=lambda x: (not x["has_free_slot"], x["queue_length"]))
        for track in single_tracks[:10]:  # Massimo 10 singole tracce
            file_info = track["file"]
            file_size_mb = file_info.get("size", 0) / (1024 * 1024) if file_info.get("size") else 0
            filename = track["filename"].lower()
            
            # Rileva formato e qualità per singola traccia
            attrs = file_info.get("attributes", {})
            best_quality = "Unknown"
            
            if any(ext in filename for ext in ['.flac', '.wav']):
                audio_format = "Lossless"
                if '.flac' in filename:
                    best_quality = "FLAC"
                elif '.wav' in filename:
                    best_quality = "WAV"
            else:
                audio_format = "Compressed"
                if isinstance(attrs, dict) and attrs.get("bitRate"):
                    bitrate = attrs.get("bitRate")
                    if bitrate >= 320:
                        best_quality = "320kbps"
                    elif bitrate >= 256:
                        best_quality = "256kbps"
                    elif bitrate >= 192:
                        best_quality = "192kbps"
                    elif bitrate >= 128:
                        best_quality = "128kbps"
                    else:
                        best_quality = f"{bitrate}kbps"
            
            album_results.append({
                "username": track["username"],
                "folder": "",
                "album_title": f"Single: {track['filename'].split('/')[-1]}",
                "track_count": 1,
                "files": [track["file"]],
                "queue_length": track["queue_length"],
                "has_free_slot": track["has_free_slot"],
                "upload_speed": 0,  # Non disponibile per singole tracce
                "total_size_mb": round(file_size_mb, 2),
                "audio_formats": [audio_format],
                "quality_info": [],
                "best_quality": best_quality,
                "bitrates": [attrs.get("bitRate", 0)] if attrs.get("bitRate") else [],
                "type": "single"
            })
    
    log.info(f"Found {len(album_results)} results ({len([r for r in album_results if r.get('type') == 'album'])} albums, {len([r for r in album_results if r.get('type') == 'single'])} singles)")
    
    # Ordina per qualità (slot libero e coda corta)
    album_results.sort(key=lambda x: (not x["has_free_slot"], x["queue_length"]))
    
    return jsonify({
        "results": album_results[:15],  # Massimo 15 risultati
        "source": "soulseek",
        "search_id": search_id,
        "message": f"Trovati {len(album_results)} album per '{album_name}' su Soulseek"
    })

def search_album_deezer(album_name: str):
    """Cerca album su Deezer"""
    import requests
    import time
    
    try:
        # Cerca specificamente album su Deezer
        search_url = f'https://api.deezer.com/search/album?q={album_name}&limit=15'
        time.sleep(0.5)  # Rate limiting
        response = requests.get(search_url, timeout=10)
        
        if response.status_code == 403:
            return jsonify({"error": "Deezer API ha restituito 403 - accesso limitato"}), 403
            
        response.raise_for_status()
        deezer_data = response.json()
        
        results = []
        for album in deezer_data.get("data", []):
            album_info = {
                "album_id": album.get("id"),
                "title": album.get("title"),
                "artist": album.get("artist", {}).get("name"),
                "cover": album.get("cover_medium"),
                "track_count": album.get("nb_tracks", 0),
                "release_date": album.get("release_date"),
                "album_url": f"https://www.deezer.com/album/{album.get('id')}",
                "preview_tracks": []  # Potresti aggiungere preview se necessario
            }
            results.append(album_info)
        
        return jsonify({
            "results": results,
            "source": "deezer", 
            "message": f"Trovati {len(results)} album per '{album_name}' su Deezer"
        })
        
    except Exception as e:
        log.error(f"Errore ricerca Deezer per '{album_name}': {e}")
        return jsonify({"error": f"Errore Deezer: {str(e)}"}), 500

@app.route('/download_album_soulseek', methods=['POST'])
def download_album_soulseek():
    """Scarica un album completo da Soulseek"""
    data = request.json
    username = data.get('username')
    folder = data.get('folder') 
    files = data.get('files', [])
    
    if not username or not folder or not files:
        return jsonify({"success": False, "error": "Dati incompleti per il download"}), 400
    
    from plex_playlist_sync.utils.soulseek import SoulseekClient
    client = SoulseekClient()
    
    if not client.enabled:
        return jsonify({"success": False, "error": "Soulseek non abilitato"}), 400
    
    # Aggiungi tutti i file alla coda di download
    success_count = 0
    for file in files:
        # Handle both dict and string formats defensively
        if isinstance(file, dict):
            filename = file.get("filename")
            size = file.get("size", 0)
        elif isinstance(file, str):
            # If file is a string, try to parse it as JSON
            try:
                import json
                file_obj = json.loads(file)
                filename = file_obj.get("filename")
                size = file_obj.get("size", 0)
            except (json.JSONDecodeError, AttributeError):
                log.error(f"Failed to parse file data: {file}")
                continue
        else:
            log.error(f"Unexpected file data type: {type(file)} - {file}")
            continue
            
        if filename and client.queue_download(username, filename, size):
            success_count += 1
    
    if success_count > 0:
        return jsonify({
            "success": True, 
            "message": f"Aggiunti {success_count}/{len(files)} file alla coda Soulseek"
        })
    else:
        return jsonify({
            "success": False, 
            "error": "Nessun file aggiunto alla coda"
        })

@app.route('/process_soulseek_downloads', methods=['POST'])
def process_soulseek_downloads_route():
    """Process and organize Soulseek downloads into proper folder structure"""
    try:
        log.info("Starting Soulseek downloads post-processing...")
        from plex_playlist_sync.utils.soulseek_post_processor import process_soulseek_downloads as process_downloads
        processed_files = process_downloads()
        
        if processed_files:
            success_count = len([f for f in processed_files if f['status'] == 'processed'])
            skipped_count = len([f for f in processed_files if f['status'] == 'skipped'])
            
            message = f"Processing completed: {success_count} files organized"
            if skipped_count > 0:
                message += f", {skipped_count} files skipped (already exist)"
            
            log.info(f"Soulseek post-processing completed: {success_count} processed, {skipped_count} skipped")
            return jsonify({
                "success": True,
                "message": message,
                "processed_count": success_count,
                "skipped_count": skipped_count,
                "files": processed_files
            })
        else:
            return jsonify({
                "success": True,
                "message": "No files found to process in Soulseek downloads directory",
                "processed_count": 0,
                "skipped_count": 0,
                "files": []
            })
            
    except Exception as e:
        log.error(f"Error during Soulseek post-processing: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Post-processing failed: {str(e)}"
        }), 500

@app.route('/get_soulseek_processing_status')
def get_soulseek_processing_status():
    """Get status of Soulseek downloads and processing"""
    try:
        from pathlib import Path
        
        source_path = os.getenv("SOULSEEK_DOWNLOADS_PATH", "E:\\Docker image\\slskd\\downloads\\")
        target_path = os.getenv("SOULSEEK_ORGANIZED_PATH", "M:\\Organizzata\\")
        
        # Count files in source directory
        source_count = 0
        audio_extensions = {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac'}
        
        if os.path.exists(source_path):
            for root, dirs, files in os.walk(source_path):
                for file in files:
                    if Path(file).suffix.lower() in audio_extensions:
                        source_count += 1
        
        return jsonify({
            "success": True,
            "source_path": source_path,
            "target_path": target_path,
            "source_files_count": source_count,
            "source_path_exists": os.path.exists(source_path),
            "target_path_exists": os.path.exists(target_path)
        })
        
    except Exception as e:
        log.error(f"Error getting Soulseek processing status: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Status check failed: {str(e)}"
        }), 500

@app.route('/download_track', methods=['POST'])
def download_track():
    data = request.json
    track_id, album_url = data.get('track_id'), data.get('album_url')
    if track_id is None or not album_url: return jsonify({"success": False, "error": "Dati incompleti."}), 400
    
    # Aggiungi il download alla coda, non bloccare l'UI
    download_queue.put((album_url, track_id, 0))
    log.info(f"Traccia {track_id} con URL {album_url} aggiunta alla coda di download.")
    return jsonify({"success": True, "message": "Download aggiunto alla coda."})

@app.route('/get_notifications')
def get_notifications():
    """Endpoint per ottenere le notifiche per l'utente"""
    # Rimuovi notifiche più vecchie di 5 minuti
    current_time = time.time()
    global user_notifications
    user_notifications = [n for n in user_notifications if current_time - n['timestamp'] < 300]
    
    log.debug(f"Returning {len(user_notifications)} notifications to frontend") # Debug log
    return jsonify({"notifications": user_notifications})

@app.route('/clear_notifications', methods=['POST'])
def clear_notifications():
    """Endpoint per pulire le notifiche"""
    global user_notifications
    user_notifications.clear()
    return jsonify({"success": True})

@app.route('/test_notification', methods=['POST'])
def test_notification():
    """Endpoint per testare le notifiche"""
    global user_notifications
    test_notification = {
        "type": "info",
        "message": "Test notifica - sistema funzionante!",
        "timestamp": time.time(),
        "details": {
            "files_copied": 0,
            "files_found": 5,
            "already_existed": True
        }
    }
    user_notifications.append(test_notification)
    log.info(f"Added test notification: {test_notification}")
    return jsonify({"success": True, "message": "Test notification added"})


@app.route('/get_logs')
def get_logs():
    try:
        with open("plex_sync.log", "r", encoding="utf-8") as f:
            log_content = "".join(f.readlines()[-100:])
    except FileNotFoundError:
        log_content = "File di log non ancora creato."
    # Traduci lo stato e l'ultimo sync per la risposta API
    status = app_state["status"]
    # Se lo status contiene "Operazione (X) in corso", traduci con template
    if "Operazione (" in status and ") in corso" in status:
        # Estrai il tipo di operazione dalle parentesi
        import re
        match = re.search(r'Operazione \((.+)\) in corso', status)
        if match:
            trigger_type = match.group(1)
            from plex_playlist_sync.utils.i18n import get_i18n
            i18n = get_i18n()
            # Traduci il tipo di operazione
            if trigger_type == "Automatica":
                trigger_type_translated = i18n.get_translation('operation_types.automatic')
            else:
                trigger_type_translated = trigger_type
            status = i18n.get_translation('status_messages.operation_in_progress', trigger_type=trigger_type_translated)
    else:
        status = translate_status(status)
    
    translated_last_sync = translate_status(app_state["last_sync"])
    return jsonify(logs=log_content, status=status, last_sync=translated_last_sync, is_running=app_state["is_running"])

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
