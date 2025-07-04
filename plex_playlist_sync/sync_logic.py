import os
import time
import sys
import logging
import concurrent.futures
from typing import List, Dict
from datetime import datetime, timedelta

from plexapi.server import PlexServer
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyClientCredentials
import spotipy

logger = logging.getLogger(__name__)

def check_stop_flag():
    """Check if operation should be stopped (for Flask integration)"""
    try:
        # Import here to avoid circular imports
        from flask import current_app
        if hasattr(current_app, 'app_state'):
            return current_app.app_state.get("stop_requested", False)
    except:
        # If not in Flask context, check global variable (for background tasks)
        try:
            import sys
            if hasattr(sys.modules.get('app'), 'app_state'):
                return sys.modules['app'].app_state.get("stop_requested", False)
        except:
            pass
    return False

# Global variable to store app_state reference for background tasks
_app_state_ref = None

def set_app_state_ref(app_state):
    """Set global app_state reference for background tasks"""
    global _app_state_ref
    _app_state_ref = app_state

def check_stop_flag_direct():
    """Direct check for stop flag using global reference"""
    global _app_state_ref
    if _app_state_ref:
        return _app_state_ref.get("stop_requested", False)
    return check_stop_flag()  # Fallback to Flask context method

from .utils.cleanup import delete_old_playlists, delete_previous_week_playlist
from .utils.deezer import deezer_playlist_sync, deezer_playlist_sync_with_discovery
from .utils.helperClasses import UserInputs, Playlist as PlexPlaylist, Track as PlexTrack
from .utils.spotify import spotify_playlist_sync, spotify_playlist_sync_with_discovery
from .utils.downloader import download_single_track_with_streamrip, DeezerLinkFinder
from .utils.gemini_ai import configure_gemini, get_plex_favorites_by_id, generate_playlist_prompt, get_gemini_playlist_data
from .utils.weekly_ai_manager import manage_weekly_ai_playlist
from .utils.plex import update_or_create_plex_playlist, search_plex_track
from .utils.state_manager import load_playlist_state, save_playlist_state
from .utils.database import (
    initialize_db, clear_library_index, add_track_to_index, bulk_add_tracks_to_index, get_missing_tracks,
    check_track_in_index, check_track_in_index_smart, update_track_status, get_selected_playlist_ids
)

# Carica le variabili dal file .env montato via Docker
load_dotenv('/app/.env')

def build_library_index(app_state: Dict):
    """
    Performs a complete scan of the Plex library and populates the local index.
    PARALLEL version with robust controls and extended debugging.
    """
    import os  # Import needed for os.getenv
    logger.info("=== STARTING PARALLEL PLEX LIBRARY INDEXING ===")
    plex_url, plex_token = os.getenv("PLEX_URL"), os.getenv("PLEX_TOKEN")
    library_name = os.getenv("LIBRARY_NAME", "Musica")
    logger.debug(f"Using library name: {library_name}")

    if not (plex_url and plex_token):
        logger.error("❌ Plex URL or Token not configured. Cannot index.")
        app_state['status'] = "Error: Missing Plex URL or Token."
        return

    try:
        # FASE 1: Inizializzazione e controlli
        from .utils.database import initialize_db, clear_library_index, add_track_to_index, get_library_index_stats
        
        logger.info("🔧 Database initialization...")
        initialize_db()
        
        # Verifica stato database
        initial_stats = get_library_index_stats()
        logger.info(f"📊 Initial index state: {initial_stats['total_tracks_indexed']} tracks")
        
        # Connessione con timeout esteso
        app_state['status'] = "Connecting to Plex Server..."
        plex = PlexServer(plex_url, plex_token, timeout=120)
        
        try:
            music_library = plex.library.section(library_name)
            logger.info(f"✅ Connected to library '{library_name}'")
        except Exception as lib_error:
            logger.error(f"❌ Error accessing library '{library_name}': {lib_error}")
            app_state['status'] = f"Error: Library '{library_name}' not found"
            return
        
        # FASE 2: Stima totale tracce
        app_state['status'] = "Estimating library size..."
        try:
            # Prova a ottenere il totale con un metodo veloce
            total_estimate = len(music_library.search(libtype='track', limit=50000))
            logger.info(f"📊 Estimated tracks in library: ~{total_estimate}")
        except Exception:
            logger.warning("⚠️ Unable to estimate library size, proceeding anyway")
            total_estimate = 0
        
        # FASE 3: Svuotamento indice esistente
        app_state['status'] = "Clearing existing index..."
        logger.info("🗺️ Clearing existing index...")
        clear_library_index()
        
        # FASE 4: Scarica TUTTE le tracce una volta sola (evita ripetuti fetch)
        logger.info("📥 Downloading complete tracks from Plex (one time only)...")
        all_tracks = music_library.search(libtype='track')
        total_tracks = len(all_tracks)
        logger.info(f"✅ Downloaded {total_tracks} total tracks from Plex")
        
        # FASE 5: Indicizzazione a batch delle tracce già scaricate
        batch_size = 2500  # Batch ridotti per evitare timeout
        total_processed = 0
        total_indexed = 0
        
        logger.info(f"🚀 Starting batch indexing (size: {batch_size})")
        
        container_start = 0
        batch_num = 0
        
        while container_start < total_tracks:
            # Check if stop was requested
            if check_stop_flag_direct():
                logger.info("🛑 Stop requested during library indexing")
                app_state['status'] = "Indexing stopped by user request"
                return
            
            try:
                batch_num += 1
                batch_end = min(container_start + batch_size, total_tracks)
                app_state['status'] = f"Batch {batch_num}: {container_start}-{batch_end} | Indicizzate: {total_indexed}"
                
                # Slice delle tracce già scaricate (nessun fetch aggiuntivo)
                batch_tracks = all_tracks[container_start:batch_end]
                
                if not batch_tracks:
                    logger.info(f"🏁 End of indexing - empty batch")
                    break
                
                logger.info(f"🔄 Processing batch {batch_num}: {len(batch_tracks)} tracks (slice {container_start}:{batch_end})")
                
                # Processa il batch con inserimento BULK (PERFORMANCE OTTIMIZZATA)
                try:
                    batch_indexed = bulk_add_tracks_to_index(batch_tracks)
                    total_indexed += batch_indexed
                    total_processed += len(batch_tracks)
                    batch_errors = len(batch_tracks) - batch_indexed
                    
                    # Update status every batch
                    app_state['status'] = f"Batch {batch_num}: {len(batch_tracks)} processed | Tot indexed: {total_indexed}"
                    
                except Exception as batch_error:
                    logger.error(f"Error in batch {batch_num}: {batch_error}")
                    batch_errors = len(batch_tracks)
                    batch_indexed = 0
                
                logger.info(f"✅ Batch {batch_num} completed: {batch_indexed}/{len(batch_tracks)} indexed, {batch_errors} errors")
                
                # Progress update every 5 batches
                if batch_num % 5 == 0:
                    current_stats = get_library_index_stats()
                    logger.info(f"📊 General progress: {total_processed} processed, {current_stats['total_tracks_indexed']} in DB")
                
                container_start += batch_size
                
                # Se il batch è più piccolo del batch_size, abbiamo finito
                if len(batch_tracks) < batch_size:
                    logger.info(f"🏁 Last batch completed - size: {len(batch_tracks)}")
                    break
                    
            except Exception as batch_error:
                logger.error(f"❌ Error in batch {batch_num}: {batch_error}")
                container_start += batch_size
                continue

        # PHASE 5: Final verification
        final_stats = get_library_index_stats()
        final_status = f"INDEXING COMPLETED! {total_processed} processed, {final_stats['total_tracks_indexed']} successfully indexed in {batch_num} batches"
        app_state['status'] = final_status
        logger.info(f"=== {final_status} ===")
        
        # Debug database information
        from .utils.database import DB_PATH
        db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        logger.info(f"📋 Final database: {DB_PATH} ({db_size} bytes)")
        
    except Exception as e:
        logger.error(f"❌ Critical error during library indexing: {e}", exc_info=True)
        app_state['status'] = "Critical error during indexing."


def _determine_user_type_from_token(plex_token: str) -> str:
    """
    Determina se l'utente è 'main' o 'secondary' in base al token Plex.
    
    Args:
        plex_token: Token Plex dell'utente
        
    Returns:
        'main' o 'secondary'
    """
    main_token = os.getenv("PLEX_TOKEN", "")
    secondary_token = os.getenv("PLEX_TOKEN_USERS", "")
    
    if plex_token == main_token:
        return 'main'
    elif plex_token == secondary_token:
        return 'secondary'
    else:
        # Fallback: se non riconosce il token, assume sia main
        logger.warning(f"⚠️ Token non riconosciuto {plex_token[:4]}..., assuming 'main' user")
        return 'main'

def sync_playlists_for_user_selective(plex: PlexServer, user_inputs: UserInputs, sync_options: dict):
    """Performs selective synchronization for a single user based on sync_options."""
    enable_spotify = sync_options.get('enable_spotify', True)
    enable_deezer = sync_options.get('enable_deezer', True)
    auto_discovery = sync_options.get('auto_discovery', False)
    
    # Determina il tipo di utente in base al token
    user_type = _determine_user_type_from_token(user_inputs.plex_token)
    
    # Spotify Sync
    if enable_spotify and os.getenv("SKIP_SPOTIFY_SYNC", "0") != "1":
        if not user_inputs.spotify_user_id:
            logger.error("SPOTIFY_USER_ID not configured; skipping Spotify sync")
        else:
            try:
                # Suppress Spotipy cache warnings - token caching is not critical for sync
                import warnings
                import logging as python_logging
                
                # Disable spotipy.cache_handler logger specifically
                spotipy_cache_logger = python_logging.getLogger('spotipy.cache_handler')
                spotipy_cache_logger.setLevel(python_logging.ERROR)
                
                # Also filter warnings
                warnings.filterwarnings("ignore", message="Couldn't write token to cache")
                
                logger.info("🎵 Initializing Spotify API connection...")
                
                # Use a custom cache handler that doesn't try to write to disk
                from spotipy.cache_handler import MemoryCacheHandler
                memory_cache = MemoryCacheHandler()
                
                sp = spotipy.Spotify(
                    auth_manager=SpotifyClientCredentials(
                        client_id=user_inputs.spotipy_client_id,
                        client_secret=user_inputs.spotipy_client_secret,
                        cache_handler=memory_cache  # Use memory cache instead
                    )
                )
                logger.info(
                    f"--- Starting Spotify sync for user {user_inputs.plex_token[:4]}... ({user_type}) ---"
                )
                
                # NEW: Check database for selected playlists first
                from .utils.database import get_selected_playlist_ids
                selected_spotify_ids = get_selected_playlist_ids(user_type, 'spotify')
                
                if selected_spotify_ids:
                    logger.info(f"📋 Using {len(selected_spotify_ids)} selected Spotify playlists from database")
                    # Override UserInputs with database selection
                    user_inputs.spotify_playlist_ids = ','.join(selected_spotify_ids)
                    spotify_playlist_sync(sp, plex, user_inputs)
                elif auto_discovery:
                    logger.info("🔍 Auto-discovery enabled for Spotify - fetching all user playlists")
                    spotify_playlist_sync_with_discovery(sp, plex, user_inputs)
                elif user_inputs.spotify_playlist_ids:
                    logger.info("📝 Using environment variable playlist IDs (legacy mode)")
                    spotify_playlist_sync(sp, plex, user_inputs)
                else:
                    logger.info("⚠️ No Spotify playlists selected in database or environment variables")
                    
                logger.info("✅ Spotify sync completed successfully")
            except Exception as spotify_error:
                logger.error(f"❌ Spotify sync failed: {spotify_error}")
                logger.info("ℹ️ Continuing without Spotify sync - other features unaffected")
    elif not enable_spotify:
        logger.info("⏭️ Spotify sync disabled by user selection")
    
    # Deezer Sync  
    if enable_deezer and os.getenv("SKIP_DEEZER_SYNC", "0") != "1":
        logger.info(
            f"--- Starting Deezer sync for user {user_inputs.plex_token[:4]}... ({user_type}) ---"
        )
        
        # NEW: Check database for selected playlists first
        from .utils.database import get_selected_playlist_ids
        selected_deezer_ids = get_selected_playlist_ids(user_type, 'deezer')
        
        if selected_deezer_ids:
            logger.info(f"📋 Using {len(selected_deezer_ids)} selected Deezer playlists from database")
            # Override UserInputs with database selection
            user_inputs.deezer_playlist_ids = ','.join(selected_deezer_ids)
            deezer_playlist_sync(plex, user_inputs)
        elif auto_discovery:
            logger.info("🔍 Auto-discovery enabled for Deezer - fetching all user playlists")
            deezer_playlist_sync_with_discovery(plex, user_inputs)
        elif user_inputs.deezer_playlist_ids:
            logger.info("📝 Using environment variable playlist IDs (legacy mode)")
            deezer_playlist_sync(plex, user_inputs)
        else:
            logger.info("⚠️ No Deezer playlists selected in database or environment variables")
            
    elif not enable_deezer:
        logger.info("⏭️ Deezer sync disabled by user selection")


def sync_playlists_for_user(plex: PlexServer, user_inputs: UserInputs):
    """Performs Spotify and Deezer synchronization for a single user (backward compatibility)."""
    sync_options = {
        'enable_spotify': True,
        'enable_deezer': True,
        'enable_ai': True,
        'auto_discovery': False
    }
    return sync_playlists_for_user_selective(plex, user_inputs, sync_options)

def force_playlist_scan_and_missing_detection():
    """
    Forces a scan of existing playlists on Plex to detect missing tracks.
    WARNING: Requires the library index to be populated to work correctly.
    """
    # Controllo preventivo indice libreria
    from .utils.database import get_library_index_stats
    index_stats = get_library_index_stats()
    
    if index_stats['total_tracks_indexed'] == 0:
        logger.error("❌ BLOCKING FORCED SCAN: Library index EMPTY!")
        logger.error("⚠️ Scan would only produce false positives. Index the library first.")
        return
    
    logger.info(f"--- Starting forced playlist scan (index: {index_stats['total_tracks_indexed']} tracks) ---")
    
    plex_url = os.getenv("PLEX_URL")
    plex_token = os.getenv("PLEX_TOKEN")
    
    if not (plex_url and plex_token):
        logger.error("Plex credentials not configured")
        return
    
    try:
        plex = PlexServer(plex_url, plex_token, timeout=60)
        
        # Ottieni tutte le playlist dell'utente e filtra quelle musicali
        all_playlists = plex.playlists()
        logger.info(f"Found {len(all_playlists)} total playlists")
        
        # Filter playlists that should not be scanned
        tv_keywords = ['simpsons', 'simpson', 'family guy', 'american dad', 'king of the hill', 
                      'episode', 'tv', 'show', 'serie', 'film', 'movie', 'cinema']
        
        music_playlists = []
        for playlist in all_playlists:
            playlist_name_lower = playlist.title.lower()
            
            # Skip TV/Movie playlists
            is_tv_playlist = any(keyword in playlist_name_lower for keyword in tv_keywords)
            
            # Skip NO_DELETE playlists (created by Plex, cannot have missing tracks)
            is_no_delete = 'no_delete' in playlist_name_lower
            
            if is_tv_playlist:
                logger.info(f"🎭 Skipped TV/Movie playlist: '{playlist.title}'")
            elif is_no_delete:
                logger.info(f"🚫 Skipped NO_DELETE playlist: '{playlist.title}' (created by Plex)")
            else:
                music_playlists.append(playlist)
        
        logger.info(f"🎵 Scanning {len(music_playlists)} music playlists (skipped {len(all_playlists) - len(music_playlists)} TV/Movie)")
        
        total_missing_found = 0
        
        for playlist in music_playlists:
            # Check if stop was requested
            if check_stop_flag_direct():
                logger.info("🛑 Stop requested during playlist scan")
                return
            
            try:
                logger.info(f"Scanning playlist: {playlist.title}")
                
                # Get playlist tracks
                playlist_tracks = playlist.items()
                missing_count = 0
                
                for track in playlist_tracks:
                    # Check if stop was requested (every 10 tracks for performance)
                    if missing_count % 10 == 0 and check_stop_flag_direct():
                        logger.info("🛑 Stop requested during track scanning")
                        return
                    
                    try:
                        # Use new smart matching system
                        if not check_track_in_index_smart(track.title, track.grandparentTitle):
                            # Potentially missing track, add to DB
                            track_data = {
                                'title': track.title,
                                'artist': track.grandparentTitle,
                                'album': track.parentTitle if hasattr(track, 'parentTitle') else '',
                                'source_playlist_title': playlist.title,
                                'source_playlist_id': playlist.ratingKey
                            }
                            
                            from .utils.database import add_missing_track
                            add_missing_track(track_data)
                            missing_count += 1
                            total_missing_found += 1
                            
                    except Exception as track_error:
                        logger.warning(f"Error processing track {track.title}: {track_error}")
                        continue
                
                if missing_count > 0:
                    logger.info(f"Playlist '{playlist.title}': {missing_count} missing tracks detected")
                    
            except Exception as playlist_error:
                logger.warning(f"Error processing playlist {playlist.title}: {playlist_error}")
                continue
        
        logger.info(f"--- Scan completed: {total_missing_found} total missing tracks detected ---")
        
    except Exception as e:
        logger.error(f"Error during forced playlist scan: {e}", exc_info=True)


def run_downloader_only():
    """Reads missing tracks from DB, searches for links in parallel and starts download."""
    logger.info("--- Starting automatic search and download for missing tracks from DB ---")
    
    # Check if stop was requested before starting
    if check_stop_flag_direct():
        logger.info("🛑 Stop requested before download start")
        return False
    
    missing_tracks_from_db = get_missing_tracks()
    
    if not missing_tracks_from_db:
        logger.info("No missing tracks in database to process.")
        return False

    logger.info(f"Found {len(missing_tracks_from_db)} missing tracks. Starting parallel link search...")
    tracks_with_links = []  # Lista di (track_id, link) per mantenere associazione
    
    # Use ThreadPoolExecutor to parallelize network requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        # Create a future for each API call
        future_to_track = {executor.submit(DeezerLinkFinder.find_track_link, {'title': track[1], 'artist': track[2]}): track for track in missing_tracks_from_db}
        
        for future in concurrent.futures.as_completed(future_to_track):
            # Check if stop was requested
            if check_stop_flag_direct():
                logger.info("🛑 Stop requested during link search")
                return False
            
            track = future_to_track[future]
            link = future.result()
            if link:
                track_id = track[0]  # ID della traccia dal database
                tracks_with_links.append((track_id, link))
                logger.info(f"Link found for '{track[1]}' by '{track[2]}': {link}")

    if tracks_with_links:
        logger.info(f"Found {len(tracks_with_links)} links to download.")
        
        # Group by unique links to avoid duplicate downloads
        unique_links = {}
        for track_id, link in tracks_with_links:
            if link not in unique_links:
                unique_links[link] = []
            unique_links[link].append(track_id)
        
        # Download each unique link and update all associated tracks
        for link, track_ids in unique_links.items():
            # Check if stop was requested
            if check_stop_flag_direct():
                logger.info("🛑 Stop requested during download")
                return False
            
            try:
                logger.info(f"Starting download: {link} (for {len(track_ids)} tracks)")
                download_single_track_with_streamrip(link)
                
                # Update status of all tracks associated with this link
                for track_id in track_ids:
                    update_track_status(track_id, 'downloaded')
                    logger.info(f"Status updated to 'downloaded' for track ID {track_id}")
                    
            except Exception as e:
                logger.error(f"Error during download of {link}: {e}")
                # Mark tracks as error instead of downloaded
                for track_id in track_ids:
                    logger.warning(f"Download failed for track ID {track_id}")
        
        return True
    else:
        logger.info("No download links found for missing tracks.")
        return False


def rescan_and_update_missing():
    """Scans recently added tracks to Plex and updates the missing list."""
    logger.info("--- Starting post-download scan to clean missing tracks list ---")
    plex_url, plex_token = os.getenv("PLEX_URL"), os.getenv("PLEX_TOKEN")
    if not (plex_url and plex_token):
        logger.error("Main Plex URL or Token not configured.")
        return

    try:
        plex = PlexServer(plex_url, plex_token, timeout=120)
        library_name = os.getenv("LIBRARY_NAME", "Musica")
        logger.debug(f"Using library name: {library_name}")
        music_library = plex.library.section(library_name)
        
        logger.info("Searching for recently added tracks to Plex to update index...")
        recently_added = music_library.search(sort="addedAt:desc", limit=500)
        
        newly_indexed_count = 0
        thirty_minutes_ago = datetime.now() - timedelta(minutes=30)

        for track in recently_added:
            if track.addedAt >= thirty_minutes_ago:
                add_track_to_index(track)
                newly_indexed_count += 1
        
        if newly_indexed_count > 0:
            logger.info(f"Added {newly_indexed_count} new tracks to local index.")
        else:
            logger.info("No new tracks found to add to index.")

        tracks_to_verify = get_missing_tracks()
        logger.info(f"Verifying {len(tracks_to_verify)} tracks from missing list...")

        updated_tracks = []
        for track_info in tracks_to_verify:
            if check_track_in_index_smart(track_info[1], track_info[2]):
                logger.info(f"SUCCESS: Track '{track_info[1]}' is now present. Updating status.")
                update_track_status(track_info[0], 'downloaded')
                updated_tracks.append(track_info)
        
        # Auto-update AI playlists if there are new tracks available
        if updated_tracks:
            auto_update_ai_playlists(plex, updated_tracks)
        
        logger.info("--- Post-download scan completed ---")

    except Exception as e:
        logger.error(f"Critical error during post-download scan: {e}", exc_info=True)


def run_cleanup_only():
    """Performs only cleanup of old playlists for all users."""
    if not (os.getenv("SKIP_CLEANUP", "0") == "1"):
        user_tokens = [os.getenv("PLEX_TOKEN"), os.getenv("PLEX_TOKEN_USERS")]
        for token in filter(None, user_tokens):
            try:
                plex = PlexServer(os.getenv("PLEX_URL"), token, timeout=120)
                logger.info(f"--- Starting cleanup of old playlists for user {token[:4]}... ---")
                library_name = os.getenv("LIBRARY_NAME", "Musica")
                logger.debug(f"Using library name: {library_name}")
                delete_old_playlists(plex, library_name, int(os.getenv("WEEKS_LIMIT")), os.getenv("PRESERVE_TAG"))
            except Exception as e:
                logger.error(f"Error during Plex connection for cleanup (user {token[:4]}...): {e}")


def run_selective_sync_cycle(app_state=None, enable_spotify=True, enable_deezer=True, enable_ai=True, auto_discovery=False):
    """Performs a selective cycle of synchronization based on user preferences."""
    logger.info(f"Starting selective synchronization cycle - Spotify: {enable_spotify}, Deezer: {enable_deezer}, AI: {enable_ai}, Auto-discovery: {auto_discovery}")
    
    # Set app_state reference for background tasks
    if app_state:
        set_app_state_ref(app_state)
    
    # Check if stop was requested before starting
    if check_stop_flag_direct():
        logger.info("🛑 Stop requested before sync cycle start")
        return
    
    # Override environment settings based on user selection
    RUN_GEMINI_PLAYLIST_CREATION = enable_ai and os.getenv("RUN_GEMINI_PLAYLIST_CREATION", "0") == "1"
    AUTO_DELETE_AI_PLAYLIST = os.getenv("AUTO_DELETE_AI_PLAYLIST", "0") == "1"
    RUN_DOWNLOADER = os.getenv("RUN_DOWNLOADER", "1") == "1"
    
    # Create temporary sync options for this run
    sync_options = {
        'enable_spotify': enable_spotify,
        'enable_deezer': enable_deezer,
        'enable_ai': enable_ai,
        'auto_discovery': auto_discovery
    }
    
    sync_start_time = datetime.now()
    current_year, current_week, _ = sync_start_time.isocalendar()

    user_configs = [
        {"name": "main user", "token": os.getenv("PLEX_TOKEN"), "favorites_id": os.getenv("PLEX_FAVORITES_PLAYLIST_ID_MAIN")},
        {"name": "secondary user", "token": os.getenv("PLEX_TOKEN_USERS"), "favorites_id": os.getenv("PLEX_FAVORITES_PLAYLIST_ID_SECONDARY")}
    ]
    
    gemini_model, gemini_model_name = configure_gemini() if RUN_GEMINI_PLAYLIST_CREATION else (None, "")
    
    for user_config in user_configs:
        # Check if stop was requested
        if check_stop_flag_direct():
            logger.info("🛑 Stop requested during sync cycle")
            return
            
        token = user_config["token"]
        name = user_config["name"]
        favorites_playlist_id = user_config["favorites_id"]

        if not token: continue

        logger.info(f"--- Processing user: {name} ---")
        user_inputs = UserInputs(
            plex_url=os.getenv("PLEX_URL"), plex_token=token,
            plex_token_others=os.getenv("PLEX_TOKEN_USERS"),
            plex_min_songs=int(os.getenv("PLEX_MIN_SONGS", 0)),
            write_missing_as_csv=False,
            append_service_suffix=os.getenv("APPEND_SERVICE_SUFFIX", "1") == "1",
            add_playlist_poster=os.getenv("ADD_PLAYLIST_POSTER", "1") == "1",
            add_playlist_description=os.getenv("ADD_PLAYLIST_DESCRIPTION", "1") == "1",
            append_instead_of_sync=True, wait_seconds=0,
            spotipy_client_id=os.getenv("SPOTIFY_CLIENT_ID"), spotipy_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            spotify_user_id=os.getenv("SPOTIFY_USER_ID"), deezer_user_id=os.getenv("DEEZER_USER_ID"),
            deezer_playlist_ids=os.getenv("DEEZER_PLAYLIST_ID_SECONDARY") if name == "secondary user" else os.getenv("DEEZER_PLAYLIST_ID"),
            spotify_playlist_ids=os.getenv("SPOTIFY_PLAYLIST_IDS_SECONDARY") if name == "secondary user" else os.getenv("SPOTIFY_PLAYLIST_IDS"),
            spotify_categories=[], country=os.getenv("COUNTRY")
        )
        
        try:
            plex = PlexServer(user_inputs.plex_url, user_inputs.plex_token, timeout=120)
            sync_playlists_for_user_selective(plex, user_inputs, sync_options)
            
            if gemini_model and favorites_playlist_id and enable_ai:
                logger.info(f"--- Gestione Playlist AI Settimanale per {name} ---")
                try:
                    # Usa il nuovo sistema settimanale con persistenza JSON
                    weekly_success = manage_weekly_ai_playlist(
                        plex, user_inputs, favorites_playlist_id, 
                        "main" if name == "main user" else "secondary"
                    )
                    if weekly_success:
                        logger.info(f"✅ Weekly AI playlist managed successfully for {name}")
                    else:
                        logger.warning(f"⚠️ Issues managing weekly AI playlist for {name}")
                except Exception as ai_error:
                    logger.error(f"❌ Error managing weekly AI playlist for {name}: {ai_error}")
                    continue

        except Exception as e:
            logger.error(f"Critical error during processing of {name}: {e}", exc_info=True)

    logger.info("Synchronization and AI cycle completed.")
    
    # REFRESH AI PLAYLISTS: Regenerate managed AI playlists with new content from library
    if enable_ai:
        try:
            refresh_managed_ai_playlists()
        except Exception as e:
            logger.error(f"Error refreshing managed AI playlists: {e}")
    
    # Continue with the rest of the sync cycle (library check, download, etc.)
    return _continue_sync_cycle_post_sync(RUN_DOWNLOADER)


def run_full_sync_cycle(app_state=None):
    """Performs a complete cycle of synchronization, AI, and then attempts download/rescan."""
    logger.info("Starting new complete synchronization cycle...")
    
    # Delegate to selective sync with all services enabled
    return run_selective_sync_cycle(app_state, enable_spotify=True, enable_deezer=True, enable_ai=True, auto_discovery=False)


def _continue_sync_cycle_post_sync(RUN_DOWNLOADER):
    """Common logic for completing the sync cycle after playlist synchronization."""
    # CRITICAL CHECK: Do not run playlist scan if library index is empty!
    from .utils.database import get_library_index_stats, get_missing_tracks
    index_stats = get_library_index_stats()
    
    if index_stats['total_tracks_indexed'] == 0:
        logger.error("❌ BLOCKING SCAN: Library index EMPTY! Cannot detect missing tracks without index.")
        logger.error("⚠️ Run 'Index Library' from homepage before continuing.")
        logger.error("🛑 Skipping playlist scan and download to avoid massive false positives.")
        return  # Exit cycle without doing anything
    
    logger.info(f"✅ Library index OK: {index_stats['total_tracks_indexed']} tracks indexed")
    
    # Check if stop was requested before playlist scan
    if check_stop_flag_direct():
        logger.info("🛑 Stop requested before playlist scan")
        return
    
    # Force playlist scan to detect missing tracks if DB is empty
    current_missing_count = len(get_missing_tracks())
    if current_missing_count == 0:
        logger.info("No missing tracks in DB. Forcing playlist scan...")
        force_playlist_scan_and_missing_detection()
    else:
        logger.info(f"Found {current_missing_count} missing tracks in existing DB.")
    
    if RUN_DOWNLOADER:
        # Check if stop was requested before download
        if check_stop_flag_direct():
            logger.info("🛑 Stop requested before download phase")
            return
        
        download_attempted = run_downloader_only()
        if download_attempted:
            wait_time = int(os.getenv("PLEX_SCAN_WAIT_TIME", "300"))
            logger.info(f"Waiting {wait_time} seconds to give Plex time to index...")
            
            # Check stop flag during wait time (check every 10 seconds)
            for i in range(0, wait_time, 10):
                if check_stop_flag_direct():
                    logger.info("🛑 Stop requested during Plex scan wait")
                    return
                time.sleep(min(10, wait_time - i))
            
            rescan_and_update_missing()
    else:
        logger.warning("Automatic download skipped as per configuration.")
    
    logger.info("--- Complete cycle finished ---")


def auto_update_ai_playlists(plex, updated_tracks):
    """
    Automatically updates managed AI playlists when new tracks are available.
    
    Args:
        plex: PlexServer instance (main user)
        updated_tracks: List of tracks that just became available
    """
    logger.info("🔄 Auto-updating AI playlists with new tracks...")
    
    try:
        from .utils.database import get_managed_ai_playlists_for_user
        from .utils.plex import search_plex_track, update_or_create_plex_playlist
        
        # Prepare connections for both users
        plex_url = os.getenv("PLEX_URL")
        main_token = os.getenv("PLEX_TOKEN") 
        secondary_token = os.getenv("PLEX_TOKEN_USERS")
        
        # Create separate connections for each user
        plex_connections = {}
        if main_token:
            plex_connections['main'] = PlexServer(plex_url, main_token, timeout=120)
        if secondary_token:
            plex_connections['secondary'] = PlexServer(plex_url, secondary_token, timeout=120)
        
        # Get playlists for each user
        updated_count = 0
        for user_type in ['main', 'secondary']:
            if user_type not in plex_connections:
                continue
                
            user_plex = plex_connections[user_type]
            user_playlists = get_managed_ai_playlists_for_user(user_type)
            
            if not user_playlists:
                logger.info(f"No AI playlists for user {user_type}")
                continue
                
            logger.info(f"Found {len(user_playlists)} AI playlists for user {user_type}")
            for playlist_data in user_playlists:
                logger.info(f"  - {playlist_data['title']} (ID: {playlist_data.get('plex_rating_key', 'N/A')})")
            
            for playlist_data in user_playlists:
                playlist_title = playlist_data['title']  # title from managed_ai_playlists table
                source_playlist_titles = [track[4] for track in updated_tracks]  # source_playlist_title
                
                # Check if this AI playlist has tracks among those just downloaded
                if playlist_title in source_playlist_titles:
                    logger.info(f"🎵 Updating AI playlist '{playlist_title}' for user {user_type}")
                    
                    try:
                        # Find the playlist on Plex for the correct user
                        existing_playlist = None
                        for playlist in user_plex.playlists():
                            if playlist.title == playlist_title:
                                existing_playlist = playlist
                                break
                        
                        if existing_playlist:
                            # Get tracks that are now available for this playlist
                            new_tracks_for_playlist = [
                                track for track in updated_tracks 
                                if track[4] == playlist_title  # source_playlist_title
                            ]
                            
                            # Search and add the new tracks found
                            tracks_to_add = []
                            for track_info in new_tracks_for_playlist:
                                track_title, track_artist = track_info[1], track_info[2]
                                track_album = track_info[3] if len(track_info) > 3 else ""
                                
                                # Create Track object for search function
                                from .utils.helperClasses import Track
                                track_obj = Track(title=track_title, artist=track_artist, album=track_album, url="")
                                
                                # Search track on Plex using correct user connection
                                plex_track = search_plex_track(user_plex, track_obj)
                                if plex_track:
                                    tracks_to_add.append(plex_track)
                                    logger.info(f"✅ Found track for addition: '{track_title}' by '{track_artist}'")
                            
                            # Add new tracks to playlist
                            if tracks_to_add:
                                # Check for duplicates before adding
                                current_tracks = existing_playlist.items()
                                existing_track_keys = {track.ratingKey for track in current_tracks}
                                new_tracks_to_add = [track for track in tracks_to_add if track.ratingKey not in existing_track_keys]
                                
                                if new_tracks_to_add:
                                    # Simply append new tracks without clearing
                                    existing_playlist.addItems(new_tracks_to_add)
                                
                                    logger.info(f"🎉 Playlist '{playlist_title}' updated with {len(new_tracks_to_add)} new tracks (evitati {len(tracks_to_add) - len(new_tracks_to_add)} duplicati)")
                                else:
                                    logger.info(f"ℹ️ Playlist '{playlist_title}' - tutte le tracce sono già presenti")
                                updated_count += 1
                            else:
                                logger.info(f"⚠️ No new tracks found on Plex for '{playlist_title}'")
                        else:
                            logger.warning(f"❌ Playlist '{playlist_title}' not found on Plex for user {user_type}")
                            
                    except Exception as playlist_error:
                        logger.error(f"Error updating playlist '{playlist_title}' for user {user_type}: {playlist_error}")
                        continue
        
        if updated_count > 0:
            logger.info(f"✅ Auto-update completed: {updated_count} AI playlists updated")
        else:
            logger.info("ℹ️ No AI playlists needed updates")
            
    except Exception as e:
        logger.error(f"Error during AI playlists auto-update: {e}", exc_info=True)


def refresh_managed_ai_playlists():
    """
    Refreshes managed AI playlists by rescanning existing tracks from the database.
    This avoids wasting Gemini API calls and only searches for missing tracks from already generated playlists.
    """
    logger.info("🔄 Rescanning managed AI playlists for missing tracks...")
    
    try:
        from .utils.database import get_managed_ai_playlists_for_user
        from .utils.plex import update_or_create_plex_playlist, search_plex_track
        from .utils.helperClasses import Playlist as PlexPlaylist, Track as PlexTrack, UserInputs
        import json
        
        # NO GEMINI CALLS - just rescan existing tracks from database
        
        # Prepare connections for both users
        plex_url = os.getenv("PLEX_URL")
        main_token = os.getenv("PLEX_TOKEN") 
        secondary_token = os.getenv("PLEX_TOKEN_USERS")
        
        plex_connections = {}
        user_inputs_map = {}
        
        # Create connections and UserInputs for each user
        if main_token:
            plex_connections['main'] = PlexServer(plex_url, main_token, timeout=120)
            user_inputs_map['main'] = UserInputs(
                plex_url=plex_url, plex_token=main_token,
                plex_token_others=secondary_token,
                plex_min_songs=int(os.getenv("PLEX_MIN_SONGS", 0)),
                write_missing_as_csv=False,
                append_service_suffix=os.getenv("APPEND_SERVICE_SUFFIX", "1") == "1",
                add_playlist_poster=os.getenv("ADD_PLAYLIST_POSTER", "1") == "1",
                add_playlist_description=os.getenv("ADD_PLAYLIST_DESCRIPTION", "1") == "1",
                append_instead_of_sync=True, wait_seconds=0,
                spotipy_client_id=os.getenv("SPOTIFY_CLIENT_ID"), 
                spotipy_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
                spotify_user_id=os.getenv("SPOTIFY_USER_ID"), 
                deezer_user_id=os.getenv("DEEZER_USER_ID"),
                deezer_playlist_ids=os.getenv("DEEZER_PLAYLIST_ID"),
                spotify_playlist_ids=os.getenv("SPOTIFY_PLAYLIST_IDS"),
                spotify_categories=[], country=os.getenv("COUNTRY")
            )
            
        if secondary_token:
            plex_connections['secondary'] = PlexServer(plex_url, secondary_token, timeout=120)
            user_inputs_map['secondary'] = UserInputs(
                plex_url=plex_url, plex_token=secondary_token,
                plex_token_others=main_token,
                plex_min_songs=int(os.getenv("PLEX_MIN_SONGS", 0)),
                write_missing_as_csv=False,
                append_service_suffix=os.getenv("APPEND_SERVICE_SUFFIX", "1") == "1",
                add_playlist_poster=os.getenv("ADD_PLAYLIST_POSTER", "1") == "1",
                add_playlist_description=os.getenv("ADD_PLAYLIST_DESCRIPTION", "1") == "1",
                append_instead_of_sync=True, wait_seconds=0,
                spotipy_client_id=os.getenv("SPOTIFY_CLIENT_ID"), 
                spotipy_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
                spotify_user_id=os.getenv("SPOTIFY_USER_ID"), 
                deezer_user_id=os.getenv("DEEZER_USER_ID"),
                deezer_playlist_ids=os.getenv("DEEZER_PLAYLIST_ID_SECONDARY"),
                spotify_playlist_ids=os.getenv("SPOTIFY_PLAYLIST_IDS_SECONDARY"),
                spotify_categories=[], country=os.getenv("COUNTRY")
            )
        
        rescanned_count = 0
        
        # Process each user's managed AI playlists
        for user_type in ['main', 'secondary']:
            if user_type not in plex_connections:
                continue
                
            user_plex = plex_connections[user_type]
            user_inputs = user_inputs_map[user_type]
            user_playlists = get_managed_ai_playlists_for_user(user_type)
            
            if not user_playlists:
                logger.info(f"No managed AI playlists found for user {user_type}")
                continue
                
            logger.info(f"Found {len(user_playlists)} managed AI playlists for user {user_type}")
            
            for playlist_data in user_playlists:
                playlist_title = playlist_data['title']
                playlist_description = playlist_data.get('description', '')
                
                # Skip weekly playlists (they have their own management system)
                if 'settimana' in playlist_title.lower() or 'week' in playlist_title.lower():
                    logger.info(f"⏭️ Skipping weekly playlist: '{playlist_title}'")
                    continue
                
                logger.info(f"🔍 Rescanning AI playlist: '{playlist_title}' for user {user_type}")
                
                try:
                    # Get the original tracklist from database (NO GEMINI CALLS!)
                    original_tracklist = []
                    if 'tracklist_json' in playlist_data:
                        try:
                            original_tracklist = json.loads(playlist_data['tracklist_json'])
                            logger.info(f"📋 Found {len(original_tracklist)} tracks in database for '{playlist_title}'")
                        except (json.JSONDecodeError, TypeError) as e:
                            logger.warning(f"⚠️ Could not parse tracklist for '{playlist_title}': {e}")
                            continue
                    
                    if not original_tracklist:
                        logger.warning(f"⚠️ No tracks found in database for '{playlist_title}', skipping rescan")
                        continue
                    
                    # Convert database tracks to PlexTrack objects for rescan
                    rescan_tracks = []
                    found_tracks = 0
                    missing_tracks = 0
                    
                    for track_data in original_tracklist:
                        track_title = track_data.get('title', '')
                        track_artist = track_data.get('artist', '')
                        
                        if not track_title or not track_artist:
                            continue
                            
                        # Search for track in Plex library
                        # Create Track object for search function
                        track_obj = PlexTrack(
                            title=track_title, 
                            artist=track_artist, 
                            album=track_data.get('album', ''),
                            url=''
                        )
                        found_plex_track = search_plex_track(user_plex, track_obj)
                        
                        if found_plex_track:
                            # Track found in library
                            rescan_tracks.append(PlexTrack(
                                title=track_title, 
                                artist=track_artist, 
                                album=found_plex_track.album().title if hasattr(found_plex_track, 'album') else '',
                                url=found_plex_track.getStreamURL() if hasattr(found_plex_track, 'getStreamURL') else ''
                            ))
                            found_tracks += 1
                        else:
                            # Track not found - add to missing for potential download
                            missing_tracks += 1
                            # Still add to playlist as placeholder
                            rescan_tracks.append(PlexTrack(
                                title=track_title, 
                                artist=track_artist, 
                                album=track_data.get('year', ''),
                                url=''
                            ))
                    
                    logger.info(f"📊 Rescan results for '{playlist_title}': {found_tracks} found, {missing_tracks} missing")
                    
                    if not rescan_tracks:
                        logger.warning(f"⚠️ No valid tracks to rescan for '{playlist_title}'")
                        continue
                    
                    # Create Plex playlist object using EXISTING data (NO AI CALLS!)
                    playlist_obj = PlexPlaylist(
                        id=None,
                        name=playlist_title,  # Keep original name
                        description=playlist_description,  # Keep original description
                        poster=None,
                    )
                    
                    # Update playlist on Plex with rescanned tracks
                    updated_plex_playlist = update_or_create_plex_playlist(user_plex, playlist_obj, rescan_tracks, user_inputs)
                    
                    if updated_plex_playlist:
                        logger.info(f"✅ Rescanned AI playlist '{playlist_title}' with {len(rescan_tracks)} tracks ({found_tracks} found, {missing_tracks} missing)")
                        rescanned_count += 1
                    else:
                        logger.warning(f"⚠️ Failed to update playlist '{playlist_title}' on Plex")
                        
                except Exception as playlist_error:
                    logger.error(f"❌ Error rescanning playlist '{playlist_title}': {playlist_error}")
                    continue
        
        if rescanned_count > 0:
            logger.info(f"✅ AI playlists rescan completed: {rescanned_count} playlists rescanned without using AI quota")
        else:
            logger.info("ℹ️ No AI playlists needed rescanning")
            
    except Exception as e:
        logger.error(f"❌ Error during AI playlists rescan: {e}", exc_info=True)


def regenerate_managed_ai_playlists():
    """
    Regenerates managed AI playlists with completely new content using AI services.
    This function WILL consume AI quota and should be used sparingly.
    Use refresh_managed_ai_playlists() for rescanning existing content without AI calls.
    """
    logger.info("🤖 Regenerating managed AI playlists with new AI-generated content...")
    logger.warning("⚠️ This operation will consume AI API quota!")
    
    try:
        from .utils.database import get_managed_ai_playlists_for_user
        from .utils.gemini_ai import configure_gemini, generate_playlist_prompt, get_gemini_playlist_data
        from .utils.plex import update_or_create_plex_playlist
        from .utils.helperClasses import Playlist as PlexPlaylist, Track as PlexTrack, UserInputs
        from .utils.i18n import i18n
        import json
        
        # Configure Gemini with cascading fallback support
        model, model_name = configure_gemini()
        if not model:
            logger.warning("⚠️ Nessun modello Gemini disponibile, cannot regenerate AI playlists")
            return
        
        logger.info(f"🤖 Usando modello Gemini: {model_name} per rigenerazione playlist")
        
        # This function would use the same structure as the old refresh_managed_ai_playlists
        # but would actually call AI services to generate new content
        # Implementation would be similar to the original function but clearly marked as AI-consuming
        
        logger.info("🚧 Function not fully implemented - use for complete playlist regeneration only")
        
    except Exception as e:
        logger.error(f"❌ Error during AI playlists regeneration: {e}", exc_info=True)