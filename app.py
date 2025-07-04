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

# Carica le variabili dal file .env montato via Docker
load_dotenv('/app/.env')

# --- Configurazione del Logging Centralizzato ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s -[%(levelname)s] - %(message)s",
    handlers=[
        logging.FileHandler("/app/logs/plex_sync.log", mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log.setLevel(logging.DEBUG)
# --- Fine Configurazione ---

# Import dei nostri moduli
from plex_playlist_sync.sync_logic import run_full_sync_cycle, run_selective_sync_cycle, run_cleanup_only, build_library_index, rescan_and_update_missing, force_playlist_scan_and_missing_detection
from plex_playlist_sync.stats_generator import (
    get_plex_tracks_as_df, generate_genre_pie_chart, generate_decade_bar_chart,
    generate_top_artists_chart, generate_duration_distribution, generate_year_trend_chart,
    get_library_statistics
)
from plex_playlist_sync.utils.gemini_ai import list_ai_playlists, generate_on_demand_playlist, test_ai_services, get_gemini_status
from plex_playlist_sync.utils.helperClasses import UserInputs
from plex_playlist_sync.utils.database import (
    initialize_db, get_missing_tracks, update_track_status, get_missing_track_by_id, 
    add_managed_ai_playlist, get_managed_ai_playlists_for_user, delete_managed_ai_playlist, get_managed_playlist_details,
    delete_all_missing_tracks, delete_missing_track, check_track_in_index, comprehensive_track_verification, get_library_index_stats,
    clean_tv_content_from_missing_tracks, clean_resolved_missing_tracks, add_missing_track_if_not_exists
)
from plex_playlist_sync.utils.downloader import DeezerLinkFinder, download_single_track_with_streamrip, find_potential_tracks, find_tracks_free_search
from plex_playlist_sync.utils.i18n import init_i18n_for_app, translate_status

initialize_db()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "una-chiave-segreta-casuale-e-robusta")

# Initialize i18n service
init_i18n_for_app(app)

app_state = { "status": "In attesa", "last_sync": "Mai eseguito", "is_running": False, "stop_requested": False }

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
            log.info(f"Starting download for {link} (Track ID: {track_id})")
            download_single_track_with_streamrip(link) # Ora accetta un singolo link
            update_track_status(track_id, 'downloaded')
            log.info(f"Download completed for {link} (Track ID: {track_id})")
        except Exception as e:
            log.error(f"Error downloading {link} (Track ID: {track_id}): {e}", exc_info=True)
        finally:
            download_queue.task_done()

def background_scheduler():
    wait_seconds = int(os.getenv("SECONDS_TO_WAIT", 86400))
    auto_sync_enabled = os.getenv("AUTO_SYNC_ENABLED", "0") == "1"
    
    if not auto_sync_enabled:
        log.info("Scheduler: Auto-sync disabled. Waiting for manual requests only.")
        # Keep scheduler alive but don't do automatic syncs
        while True:
            time.sleep(3600)  # Check every hour for setting changes
            auto_sync_enabled = os.getenv("AUTO_SYNC_ENABLED", "0") == "1"
            if auto_sync_enabled:
                log.info("Scheduler: Auto-sync re-enabled. Starting automatic cycles.")
                break
    
    # Original automatic scheduling logic (only if AUTO_SYNC_ENABLED=1)
    time.sleep(10)
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
    app_state["stop_requested"] = False  # Reset stop flag
    task_args = (app_state,) + args if target_function in [build_library_index, run_full_sync_cycle] else args
    app_state["status"] = f"Operazione ({trigger_type}) in corso..."
    try:
        target_function(*task_args)
        if target_function == run_full_sync_cycle:
            app_state["last_sync"] = time.strftime("%d/%m/%Y %H:%M:%S")
        
        # Check if operation was stopped
        if app_state["stop_requested"]:
            app_state["status"] = "⏹️ Operation stopped by user"
            log.info(f"🛑 Operation '{trigger_type}' stopped by user request")
        else:
            app_state["status"] = "In attesa"
            
            # Re-enable auto-sync after manual sync if configured
            if trigger_type != "Automatica" and os.getenv("AUTO_SYNC_AFTER_MANUAL", "0") == "1":
                os.environ["AUTO_SYNC_ENABLED"] = "1"
                log.info("✅ Auto-sync re-enabled after manual synchronization")
            
    except Exception as e:
        if app_state["stop_requested"]:
            log.info(f"🛑 Operation '{trigger_type}' interrupted by stop request")
            app_state["status"] = "⏹️ Operation stopped"
        else:
            log.error(f"Critical error during '{trigger_type}' cycle: {e}", exc_info=True)
            app_state["status"] = "Error! Check logs."
    finally:
        app_state["is_running"] = False
        app_state["stop_requested"] = False

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
        
        # Debug: log first few tracks
        if all_missing_tracks:
            log.info(f"🔍 DEBUG: Prime 3 tracce missing nel database:")
            for i, track in enumerate(all_missing_tracks[:3]):
                log.info(f"   {i+1}. ID={track[0]}, Title='{track[1]}', Artist='{track[2]}', Playlist='{track[4] if len(track)>4 else 'N/A'}'")
        else:
            log.info("🔍 DEBUG: Nessuna traccia missing trovata nel database")
            
        return render_template('missing_tracks.html', tracks=all_missing_tracks)
    except Exception as e:
        log.error(f"Error in missing_tracks page: {e}", exc_info=True)
        flash(f"Errore nel recupero delle tracce mancanti: {str(e)}", "error")
        return render_template('missing_tracks.html', tracks=[])

@app.route('/playlist_management')
def playlist_management():
    """Pagina per la gestione interattiva delle playlist"""
    try:
        from plex_playlist_sync.utils.database import get_user_playlist_selections
        
        # Recupera playlist per entrambi gli utenti
        main_spotify = get_user_playlist_selections('main', 'spotify')
        main_deezer = get_user_playlist_selections('main', 'deezer')
        secondary_deezer = get_user_playlist_selections('secondary', 'deezer')
        
        playlist_data = {
            'main': {
                'spotify': main_spotify,
                'deezer': main_deezer
            },
            'secondary': {
                'deezer': secondary_deezer
            }
        }
        
        # Traduzioni per JavaScript
        from plex_playlist_sync.utils.i18n import i18n
        js_translations = {
            'discovery_completed': i18n.get_translation('playlist_management.discovery_completed'),
            'migration_completed': i18n.get_translation('playlist_management.migration_completed'),
            'error_during_discovery': i18n.get_translation('playlist_management.error_during_discovery'),
            'error_during_migration': i18n.get_translation('playlist_management.error_during_migration'),
            'error_during_discovery_generic': i18n.get_translation('playlist_management.error_during_discovery_generic'),
            'error_during_migration_generic': i18n.get_translation('playlist_management.error_during_migration_generic'),
            'discovering_playlists': i18n.get_translation('playlist_management.discovering_playlists'),
            'migrating_playlists': i18n.get_translation('playlist_management.migrating_playlists'),
            'updated_playlists': i18n.get_translation('playlist_management.updated_playlists'),
            'error_updating_playlists': i18n.get_translation('playlist_management.error_updating_playlists'),
            'confirm_discover_all': i18n.get_translation('playlist_management.confirm_discover_all'),
            'confirm_migrate': i18n.get_translation('playlist_management.confirm_migrate'),
            'tracks': i18n.get_translation('playlist_management.tracks'),
            'playlists': i18n.get_translation('playlist_management.playlists'),
            'stats_with_radio_genres': i18n.get_translation('playlist_management.stats_with_radio_genres'),
            'stats_radio_genres_only': i18n.get_translation('playlist_management.stats_radio_genres_only'),
            'continuous_stream': i18n.get_translation('playlist_management.continuous_stream'),
            'category': i18n.get_translation('playlist_management.category')
        }
        
        return render_template('playlist_management.html', 
                             playlists=playlist_data,
                             aliases=get_user_aliases(),
                             js_translations=js_translations)
    except Exception as e:
        log.error(f"Error in playlist management page: {e}", exc_info=True)
        flash(f"Errore nel caricamento gestione playlist: {str(e)}", "error")
        return redirect(url_for('index'))

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
            plex = PlexServer(plex_url, user_token, timeout=120)
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
                plex = PlexServer(plex_url, user_token, timeout=120)
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
        
        # Ottieni il numero di tracce richiesto dall'utente
        requested_tracks = request.form.get('track_count', '').strip()
        track_count = None
        if requested_tracks and requested_tracks.isdigit():
            track_count = int(requested_tracks)
            if track_count < 10 or track_count > 100:
                flash("Numero di tracce deve essere tra 10 e 100. Usando valore predefinito.", "warning")
                track_count = None
        
        def generate_and_download_task(plex, user_inputs, fav_id, prompt, user_key):
            log.info("PHASE 1: On-demand AI playlist generation...")
            generate_on_demand_playlist(plex, user_inputs, fav_id, prompt, user_key, include_charts_data=include_charts, requested_track_count=track_count)
            log.info("PHASE 2: Starting automatic download...")
            download_attempted = run_downloader_only()
            
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
        
        return start_background_task(generate_and_download_task, "Generazione playlist e download automatico avviati!", PlexServer(plex_url, user_token, timeout=120), temp_user_inputs, favorites_id, custom_prompt, selected_user_key)

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
        plex = PlexServer(plex_url, user_token, timeout=120)
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

@app.route('/test_ai_services')
def test_ai_services_route():
    """Endpoint per testare la disponibilità dei servizi AI."""
    try:
        results = test_ai_services()
        return jsonify({
            "success": True,
            "results": results,
            "message": "Test completato"
        })
    except Exception as e:
        log.error(f"Errore durante il test dei servizi AI: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "Errore durante il test"
        }), 500

@app.route('/api/reset_downloaded_tracks', methods=['POST'])
def reset_downloaded_tracks_route():
    """Endpoint per resettare le tracce downloaded a missing."""
    try:
        if app_state["is_running"]:
            return jsonify({"success": False, "error": "Operation in progress"}), 409
        
        from plex_playlist_sync.utils.database import reset_downloaded_tracks_to_missing
        reset_count = reset_downloaded_tracks_to_missing()
        
        return jsonify({
            "success": True,
            "message": f"Reset di {reset_count} tracce da 'downloaded' a 'missing'",
            "reset_count": reset_count
        })
        
    except Exception as e:
        log.error(f"Errore durante il reset delle tracce downloaded: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/verify_downloaded_tracks', methods=['POST'])
def verify_downloaded_tracks_route():
    """Endpoint per verificare se le tracce downloaded sono realmente in Plex."""
    try:
        if app_state["is_running"]:
            return jsonify({"success": False, "error": "Operation in progress"}), 409
        
        from plex_playlist_sync.utils.database import verify_downloaded_tracks_in_plex
        confirmed_count, reset_count = verify_downloaded_tracks_in_plex()
        
        return jsonify({
            "success": True,
            "message": f"Verifica completata: {confirmed_count} confermate, {reset_count} resettate",
            "confirmed_count": confirmed_count,
            "reset_count": reset_count
        })
        
    except Exception as e:
        log.error(f"Errore durante la verifica delle tracce downloaded: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/get_notifications')
def get_notifications_route():
    """Endpoint per ottenere le notifiche dell'applicazione."""
    try:
        # Per ora restituiamo una lista vuota di notifiche
        # In futuro si possono aggiungere notifiche reali
        notifications = []
        return jsonify({
            "success": True,
            "notifications": notifications,
            "count": len(notifications)
        })
    except Exception as e:
        log.error(f"Errore durante il recupero delle notifiche: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/playlist_tracks/<int:playlist_id>')
def get_playlist_tracks(playlist_id):
    """API endpoint per ottenere le tracce di una playlist AI con status di presenza in Plex."""
    try:
        from plex_playlist_sync.utils.database import get_managed_ai_playlist_by_id, add_missing_track_if_not_exists
        import json
        
        # Ottieni la playlist dal database
        playlist_data = get_managed_ai_playlist_by_id(playlist_id)
        if not playlist_data:
            return jsonify({"success": False, "error": "Playlist not found"}), 404
        
        # Parse tracklist JSON
        try:
            tracklist = json.loads(playlist_data.get('tracklist_json', '[]'))
        except (json.JSONDecodeError, TypeError):
            return jsonify({"success": False, "error": "Invalid tracklist data"}), 400
        
        # No Plex connection needed - using fast database lookup
        
        # Check each track using database lookup with verification option
        tracks_with_status = []
        verify_with_plex = request.args.get('verify', 'false').lower() == 'true'
        
        # Setup Plex connection only if verification is requested
        plex = None
        if verify_with_plex:
            user_type = request.args.get('user', 'main')
            plex_token = os.getenv('PLEX_TOKEN') if user_type == 'main' else os.getenv('PLEX_TOKEN_USERS')
            plex_url = os.getenv('PLEX_URL')
            
            if plex_token and plex_url:
                from plexapi.server import PlexServer
                plex = PlexServer(plex_url, plex_token, timeout=120)
        
        for track_data in tracklist:
            track_title = track_data.get('title', '')
            track_artist = track_data.get('artist', '')
            track_album = track_data.get('album', '')
            
            # FIRST: Check if this track was already downloaded
            from plex_playlist_sync.utils.database import find_missing_track_in_db, update_track_status
            existing_missing_tracks = find_missing_track_in_db(track_title, track_artist)
            downloaded_track = None
            for track in existing_missing_tracks:
                if len(track) > 6 and track[6] == 'downloaded':
                    downloaded_track = track
                    break
            
            if downloaded_track:
                # Track was marked as downloaded, but let's verify it actually exists in Plex
                if verify_with_plex and plex:
                    # Perform actual verification for downloaded tracks too
                    from plex_playlist_sync.utils.plex import search_plex_track
                    from plex_playlist_sync.utils.helperClasses import Track as PlexTrack
                    
                    track_obj = PlexTrack(title=track_title, artist=track_artist, album=track_album, url='')
                    found_plex_track = search_plex_track(plex, track_obj)
                    
                    if found_plex_track:
                        # Great! The downloaded track is actually in Plex
                        log.info(f"✅ Traccia scaricata confermata in Plex: '{track_title}' - '{track_artist}'")
                        tracks_with_status.append({
                            'title': track_title,
                            'artist': track_artist,
                            'album': track_album,
                            'year': track_data.get('year', ''),
                            'found_in_plex': True,
                            'plex_rating_key': found_plex_track.ratingKey,
                            'download_status': 'downloaded_confirmed'
                        })
                        continue
                    else:
                        # Downloaded track is NOT in Plex - reset to missing!
                        log.warning(f"🔄 Traccia marcata come scaricata ma non trovata in Plex, resettando a missing: '{track_title}' - '{track_artist}'")
                        update_track_status(downloaded_track[0], 'missing')
                        # Continue with normal verification below
                else:
                    # Not doing full verification, assume downloaded tracks are OK
                    log.info(f"⬇️ Traccia già scaricata (non verificata): '{track_title}' - '{track_artist}'")
                    tracks_with_status.append({
                        'title': track_title,
                        'artist': track_artist,
                        'album': track_album,
                        'year': track_data.get('year', ''),
                        'found_in_plex': True,  # Assume found since it was downloaded
                        'plex_rating_key': None,
                        'download_status': 'downloaded_assumed'
                    })
                    continue
            
            if verify_with_plex and plex:
                # More accurate but slower: actual Plex search
                from plex_playlist_sync.utils.plex import search_plex_track
                from plex_playlist_sync.utils.helperClasses import Track as PlexTrack
                
                track_obj = PlexTrack(title=track_title, artist=track_artist, album=track_album, url='')
                found_plex_track = search_plex_track(plex, track_obj)
                found_in_plex = bool(found_plex_track)
                plex_rating_key = found_plex_track.ratingKey if found_plex_track else None
                
                # Se la traccia non è trovata con Plex API, aggiungila alle missing tracks
                if not found_in_plex:
                    log.info(f"🔍 Traccia non trovata con Plex API, aggiungendo alle missing: '{track_title}' - '{track_artist}'")
                    add_missing_track_if_not_exists(
                        title=track_title,
                        artist=track_artist, 
                        album=track_album,
                        source_playlist=playlist_data.get('title', f'AI Playlist {playlist_id}'),
                        source_type='ai_playlist'
                    )
            else:
                # Fast balanced approach: conservative fuzzy matching
                from plex_playlist_sync.utils.database import check_track_in_index_balanced
                found_in_plex = check_track_in_index_balanced(track_title, track_artist)
                plex_rating_key = None
                
                # Se non trovata con fast mode, prova ricerca Plex automatica
                if not found_in_plex:
                    user_type = request.args.get('user', 'main')
                    plex_token = os.getenv('PLEX_TOKEN') if user_type == 'main' else os.getenv('PLEX_TOKEN_USERS')
                    plex_url = os.getenv('PLEX_URL')
                    
                    if plex_token and plex_url:
                        try:
                            from plexapi.server import PlexServer
                            from plex_playlist_sync.utils.plex import search_plex_track
                            from plex_playlist_sync.utils.helperClasses import Track as PlexTrack
                            
                            plex_auto = PlexServer(plex_url, plex_token, timeout=120)
                            track_obj = PlexTrack(title=track_title, artist=track_artist, album=track_album, url='')
                            found_plex_track = search_plex_track(plex_auto, track_obj)
                            
                            if found_plex_track:
                                found_in_plex = True
                                plex_rating_key = found_plex_track.ratingKey
                                logging.info(f"🔍 Auto-search trovata: '{track_title}' - '{track_artist}'")
                            else:
                                # Se non trovata neanche con Plex search, aggiungila alle missing
                                log.info(f"🔍 Traccia non trovata con auto-search, aggiungendo alle missing: '{track_title}' - '{track_artist}'")
                                add_missing_track_if_not_exists(
                                    title=track_title,
                                    artist=track_artist, 
                                    album=track_album,
                                    source_playlist=playlist_data.get('title', f'AI Playlist {playlist_id}'),
                                    source_type='ai_playlist'
                                )
                        except Exception as e:
                            logging.warning(f"Errore durante auto-search Plex: {e}")
                            # Fallback: aggiungi comunque alle missing tracks
                            add_missing_track_if_not_exists(
                                title=track_title,
                                artist=track_artist, 
                                album=track_album,
                                source_playlist=playlist_data.get('title', f'AI Playlist {playlist_id}'),
                                source_type='ai_playlist'
                            )
            
            tracks_with_status.append({
                'title': track_title,
                'artist': track_artist,
                'album': track_album,
                'year': track_data.get('year', ''),
                'found_in_plex': found_in_plex,
                'plex_rating_key': plex_rating_key,
                'download_status': 'not_downloaded'
            })
        
        return jsonify({
            "success": True,
            "playlist_title": playlist_data.get('title', ''),
            "total_tracks": len(tracks_with_status),
            "tracks_found": sum(1 for t in tracks_with_status if t['found_in_plex']),
            "tracks_missing": sum(1 for t in tracks_with_status if not t['found_in_plex']),
            "verification_mode": "plex_api" if verify_with_plex else "database_index",
            "tracks": tracks_with_status
        })
        
    except Exception as e:
        log.error(f"Error getting playlist tracks: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/stop_operation', methods=['POST'])
def stop_operation():
    """Endpoint per fermare l'operazione corrente in modo sicuro."""
    try:
        if not app_state["is_running"]:
            return jsonify({
                "success": False, 
                "error": "No operation is currently running"
            }), 400
        
        # Set stop flag
        app_state["stop_requested"] = True
        app_state["status"] = "⏹️ Stopping operation... Please wait"
        
        log.info("🛑 Stop operation requested by user")
        
        return jsonify({
            "success": True,
            "message": "Stop request sent. Operation will terminate safely."
        })
        
    except Exception as e:
        log.error(f"Error stopping operation: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/gemini_status')
def gemini_status_route():
    """Endpoint per ottenere lo stato dettagliato di Gemini."""
    try:
        status = get_gemini_status()
        return jsonify({
            "success": True,
            "status": status
        })
    except Exception as e:
        log.error(f"Errore durante il recupero dello stato Gemini: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/simulate_gemini_error/<error_type>')
def simulate_gemini_error_route(error_type):
    """Endpoint per simulare errori Gemini per testing."""
    try:
        from plex_playlist_sync.utils.gemini_ai import gemini_state
        
        if error_type == "daily_limit":
            gemini_state.record_failure("429 You exceeded your current quota, please check your plan and billing details. quota_value: 50", True)
        elif error_type == "rate_limit":
            gemini_state.record_failure("429 Rate limit exceeded", True)
        elif error_type == "reset":
            gemini_state.is_blocked = False
            gemini_state.blocked_until = None
            gemini_state.last_error = None
            
        return jsonify({
            "success": True,
            "message": f"Simulated {error_type} error",
            "status": get_gemini_status()
        })
    except Exception as e:
        log.error(f"Errore durante la simulazione: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

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

@app.route('/sync_selective', methods=['POST'])
def sync_selective():
    """Endpoint for selective synchronization based on user preferences."""
    try:
        # Check if operation is already running
        if app_state["is_running"]:
            return jsonify({"success": False, "error": "Another operation is already in progress"}), 409
        
        # Get form data
        enable_spotify = request.form.get('enable_spotify') == 'on'
        enable_deezer = request.form.get('enable_deezer') == 'on'
        enable_ai = request.form.get('enable_ai') == 'on'
        auto_discovery = request.form.get('auto_discovery') == 'on'
        
        # Ensure at least one service is enabled
        if not any([enable_spotify, enable_deezer, enable_ai]):
            return jsonify({"success": False, "error": "At least one service must be selected"}), 400
        
        # Build descriptive message
        services = []
        if enable_spotify: services.append("Spotify")
        if enable_deezer: services.append("Deezer")
        if enable_ai: services.append("AI")
        
        discovery_note = " (with auto-discovery)" if auto_discovery else ""
        message = f"Sincronizzazione selettiva avviata: {', '.join(services)}{discovery_note}"
        
        log.info(f"Starting selective sync - Spotify: {enable_spotify}, Deezer: {enable_deezer}, AI: {enable_ai}, Auto-discovery: {auto_discovery}")
        
        # Create wrapper function with parameters
        def selective_sync_wrapper():
            return run_selective_sync_cycle(
                app_state=app_state,
                enable_spotify=enable_spotify,
                enable_deezer=enable_deezer,
                enable_ai=enable_ai,
                auto_discovery=auto_discovery
            )
        
        # Start the background task manually (avoiding redirect from start_background_task)
        if app_state["is_running"]:
            return jsonify({"success": False, "error": "Un'operazione è già in corso. Attendi il completamento."}), 409
        
        # Start the task
        task_thread = threading.Thread(target=run_task_in_background, args=("Selettiva", selective_sync_wrapper))
        task_thread.start()
        
        return jsonify({"success": True, "message": message})
        
    except Exception as e:
        log.error(f"Error in selective sync endpoint: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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
        plex = PlexServer(plex_url, user_token, timeout=120)
        # Import timeout wrapper for safe search
        from plex_playlist_sync.utils.plex import _search_with_timeout
        results = _search_with_timeout(plex, query, limit=15, timeout_seconds=45)
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
        plex = PlexServer(plex_url, user_token, timeout=120)
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
        with open("/app/logs/plex_sync.log", "r", encoding="utf-8") as f:
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
        
        # Get real library stats from index
        from plex_playlist_sync.utils.database import get_library_index_stats
        index_stats = get_library_index_stats()
        
        library_stats = {
            'total_tracks': index_stats['total_tracks_indexed'],
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

@app.route('/api/service_config')
def api_service_config():
    """API endpoint per verificare la configurazione dei servizi"""
    try:
        config = {
            'spotify': {
                'configured': bool(os.getenv('SPOTIFY_CLIENT_ID') and 
                                os.getenv('SPOTIFY_CLIENT_SECRET') and 
                                os.getenv('SPOTIFY_USER_ID')),
                'auto_discovery_available': bool(os.getenv('SPOTIFY_USER_ID'))
            },
            'deezer': {
                'configured': bool(os.getenv('DEEZER_USER_ID')),
                'auto_discovery_available': bool(os.getenv('DEEZER_USER_ID'))
            },
            'ai': {
                'configured': bool(os.getenv('RUN_GEMINI_PLAYLIST_CREATION') == '1'),
                'auto_discovery_available': True  # AI sempre disponibile se configurato
            }
        }
        return jsonify({'success': True, 'config': config})
    except Exception as e:
        log.error(f"Errore nella verifica configurazione: {e}", exc_info=True)
        return jsonify({'error': 'Errore nella verifica configurazione'}), 500


# ================================
# PLAYLIST MANAGEMENT API ENDPOINTS
# ================================

@app.route('/api/discover_playlists/<user_type>/<service>', methods=['POST'])
def api_discover_playlists(user_type, service):
    """
    Endpoint per scoprire playlist disponibili (user e curate).
    
    Args:
        user_type: 'main' o 'secondary'
        service: 'spotify' o 'deezer'
    """
    try:
        if app_state["is_running"]:
            return jsonify({"success": False, "error": "Operation in progress"}), 409
        
        # Importa le funzioni discovery
        from plex_playlist_sync.utils.database import save_discovered_playlists
        from plex_playlist_sync.utils.helperClasses import UserInputs
        
        # Configurazione utente
        user_inputs = UserInputs(
            plex_url=os.getenv("PLEX_URL"),
            plex_token=os.getenv("PLEX_TOKEN"),
            plex_token_others=os.getenv("PLEX_TOKEN_USERS", ""),
            plex_min_songs=int(os.getenv("PLEX_MIN_SONGS", 1)),
            write_missing_as_csv=bool(int(os.getenv("WRITE_MISSING_AS_CSV", 0))),
            append_service_suffix=bool(int(os.getenv("APPEND_SERVICE_SUFFIX", 1))),
            add_playlist_poster=bool(int(os.getenv("ADD_PLAYLIST_POSTER", 1))),
            add_playlist_description=bool(int(os.getenv("ADD_PLAYLIST_DESCRIPTION", 1))),
            append_instead_of_sync=bool(int(os.getenv("APPEND_INSTEAD_OF_SYNC", 0))),
            wait_seconds=int(os.getenv("SECONDS_TO_WAIT", 86400)),
            spotipy_client_id=os.getenv("SPOTIFY_CLIENT_ID", ""),
            spotipy_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET", ""),
            spotify_user_id=os.getenv("SPOTIFY_USER_ID", ""),
            spotify_playlist_ids=os.getenv("SPOTIFY_PLAYLIST_IDS", ""),
            spotify_categories=os.getenv("SPOTIFY_CATEGORIES", ""),
            country=os.getenv("COUNTRY", "IT"),
            deezer_user_id=os.getenv("DEEZER_USER_ID", ""),
            deezer_playlist_ids=os.getenv("DEEZER_PLAYLIST_ID", "")
        )
        
        discovered_content = {}
        
        if service == 'spotify':
            if not (user_inputs.spotipy_client_id and user_inputs.spotipy_client_secret):
                return jsonify({"success": False, "error": "Spotify credentials not configured"}), 400
            
            from plex_playlist_sync.utils.spotify import discover_all_spotify_content
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            
            # Crea client Spotify
            sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                client_id=user_inputs.spotipy_client_id,
                client_secret=user_inputs.spotipy_client_secret
            ))
            
            # Scopri contenuto Spotify
            spotify_content = discover_all_spotify_content(sp, user_inputs.spotify_user_id, user_inputs.country)
            
            # Salva playlist utente
            if spotify_content['user_playlists']:
                save_discovered_playlists(user_type, service, spotify_content['user_playlists'], 'user')
            
            # Salva playlist curate
            if spotify_content['featured']:
                save_discovered_playlists(user_type, service, spotify_content['featured'], 'curated')
            
            # Salva playlist per categoria
            for category, playlists in spotify_content['categories'].items():
                if playlists:
                    save_discovered_playlists(user_type, service, playlists, 'category')
            
            discovered_content = spotify_content
            
        elif service == 'deezer':
            if not user_inputs.deezer_user_id:
                return jsonify({"success": False, "error": "Deezer user ID not configured"}), 400
            
            from plex_playlist_sync.utils.deezer import _get_deezer_user_playlists, discover_all_deezer_curated_content
            
            # Scopri playlist utente Deezer
            user_playlists = _get_deezer_user_playlists(user_inputs.deezer_user_id)
            
            # Converti in formato dict per compatibilità
            user_playlists_dict = []
            for playlist in user_playlists:
                user_playlists_dict.append({
                    'id': playlist.id,
                    'name': playlist.name,
                    'description': playlist.description,
                    'poster': playlist.poster,
                    'track_count': 0,  # Da calcolare se necessario
                    'playlist_type': 'user'
                })
            
            # Scopri contenuto curato Deezer
            curated_content = discover_all_deezer_curated_content(user_inputs.country)
            
            # Salva playlist utente
            if user_playlists_dict:
                save_discovered_playlists(user_type, service, user_playlists_dict, 'user')
            
            # Salva contenuto curato
            for content_type, playlists in curated_content.items():
                if playlists:
                    save_discovered_playlists(user_type, service, playlists, content_type)
            
            discovered_content = {
                'user_playlists': user_playlists_dict,
                'curated_content': curated_content
            }
        
        else:
            return jsonify({"success": False, "error": f"Service '{service}' not supported"}), 400
        
        # Calcola totali
        total_discovered = 0
        if service == 'spotify':
            total_discovered = (len(discovered_content.get('user_playlists', [])) + 
                              len(discovered_content.get('featured', [])) + 
                              sum(len(cat) for cat in discovered_content.get('categories', {}).values()))
        elif service == 'deezer':
            total_discovered = (len(discovered_content.get('user_playlists', [])) + 
                              sum(len(cat) for cat in discovered_content.get('curated_content', {}).values()))
        
        return jsonify({
            "success": True,
            "message": f"Discovery completato per {service}",
            "total_discovered": total_discovered,
            "content": discovered_content
        })
        
    except Exception as e:
        log.error(f"Errore durante discovery playlist {service}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/user_playlists/<user_type>/<service>')
def api_get_user_playlists(user_type, service):
    """
    Recupera le playlist disponibili per un utente e servizio.
    
    Args:
        user_type: 'main' o 'secondary'
        service: 'spotify' o 'deezer'
    """
    try:
        from plex_playlist_sync.utils.database import get_user_playlist_selections
        
        # Parametri query opzionali
        selected_only = request.args.get('selected_only', 'false').lower() == 'true'
        playlist_type = request.args.get('type')  # user, curated, chart, radio
        
        playlists = get_user_playlist_selections(user_type, service, selected_only)
        
        # Filtra per tipo se specificato
        if playlist_type:
            playlists = [p for p in playlists if p.get('playlist_type') == playlist_type]
        
        return jsonify({
            "success": True,
            "playlists": playlists,
            "total": len(playlists),
            "user_type": user_type,
            "service": service
        })
        
    except Exception as e:
        log.error(f"Errore recuperando playlist {user_type}/{service}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/playlist_selection', methods=['POST'])
def api_update_playlist_selection():
    """
    Aggiorna la selezione di una playlist.
    
    Expected JSON body:
    {
        "user_type": "main",
        "service": "spotify", 
        "playlist_id": "xyz",
        "selected": true
    }
    """
    try:
        data = request.get_json()
        
        user_type = data.get('user_type')
        service = data.get('service')
        playlist_id = data.get('playlist_id')
        selected = data.get('selected', True)
        
        if not all([user_type, service, playlist_id]):
            return jsonify({"success": False, "error": "Missing required fields"}), 400
        
        from plex_playlist_sync.utils.database import toggle_playlist_selection
        
        success = toggle_playlist_selection(user_type, service, playlist_id, selected)
        
        if success:
            action = "selezionata" if selected else "deselezionata"
            return jsonify({
                "success": True,
                "message": f"Playlist {action} con successo"
            })
        else:
            return jsonify({"success": False, "error": "Playlist not found"}), 404
            
    except Exception as e:
        log.error(f"Errore aggiornando selezione playlist: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/playlist_selection/bulk', methods=['POST'])
def api_bulk_update_playlist_selection():
    """
    Aggiorna la selezione di più playlist in una sola richiesta.
    
    Expected JSON body:
    {
        "user_type": "main",
        "service": "spotify",
        "selections": [
            {"playlist_id": "xyz", "selected": true},
            {"playlist_id": "abc", "selected": false}
        ]
    }
    """
    try:
        data = request.get_json()
        
        user_type = data.get('user_type')
        service = data.get('service')
        selections = data.get('selections', [])
        
        if not all([user_type, service]) or not selections:
            return jsonify({"success": False, "error": "Missing required fields"}), 400
        
        from plex_playlist_sync.utils.database import toggle_playlist_selection
        
        success_count = 0
        failed_count = 0
        
        for selection in selections:
            playlist_id = selection.get('playlist_id')
            selected = selection.get('selected', True)
            
            if playlist_id:
                if toggle_playlist_selection(user_type, service, playlist_id, selected):
                    success_count += 1
                else:
                    failed_count += 1
        
        return jsonify({
            "success": True,
            "message": f"Aggiornate {success_count} playlist, {failed_count} errori",
            "success_count": success_count,
            "failed_count": failed_count
        })
        
    except Exception as e:
        log.error(f"Errore aggiornamento bulk playlist: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/migrate_env_playlists', methods=['POST'])
def api_migrate_env_playlists():
    """
    Migra le playlist dalle environment variables al database.
    Operazione one-time per transizione al nuovo sistema.
    """
    try:
        if app_state["is_running"]:
            return jsonify({"success": False, "error": "Operation in progress"}), 409
        
        from plex_playlist_sync.utils.database import migrate_env_playlists_to_database
        
        migrated_count = migrate_env_playlists_to_database()
        
        return jsonify({
            "success": True,
            "message": f"Migrazione completata: {migrated_count} playlist migrate",
            "migrated_count": migrated_count
        })
        
    except Exception as e:
        log.error(f"Errore durante migrazione playlist: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    log.info("Avvio dell'applicazione Flask...")
    scheduler_thread = threading.Thread(target=background_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Avvia il worker per i download in un thread separato
    download_worker_thread = threading.Thread(target=download_worker, daemon=True)
    download_worker_thread.start()

    app.run(host='0.0.0.0', port=5000)
