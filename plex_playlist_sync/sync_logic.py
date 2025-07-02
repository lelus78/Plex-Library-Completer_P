import os
import time
import sys
import logging
import concurrent.futures
from typing import List, Dict
from datetime import datetime, timedelta

from plexapi.server import PlexServer
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from .utils.cleanup import delete_old_playlists, delete_previous_week_playlist
from .utils.deezer import deezer_playlist_sync
from .utils.helperClasses import UserInputs, Playlist as PlexPlaylist, Track as PlexTrack
from .utils.spotify import spotify_playlist_sync
from .utils.downloader import download_single_track_with_streamrip, DeezerLinkFinder
from .utils.soulseek import queue_search, SoulseekClient, is_enabled as soulseek_enabled
from .utils.gemini_ai import configure_gemini, get_plex_favorites_by_id, generate_playlist_prompt, get_gemini_playlist_data
from .utils.weekly_ai_manager import manage_weekly_ai_playlist
from .utils.plex import update_or_create_plex_playlist, search_plex_track
from .utils.state_manager import load_playlist_state, save_playlist_state
from .utils.database import (
    initialize_db, clear_library_index, add_track_to_index, bulk_add_tracks_to_index, get_missing_tracks,
    check_track_in_index, check_track_in_index_smart, update_track_status
)

load_dotenv()

def build_library_index(app_state: Dict):
    """
    Performs a complete scan of the Plex library and populates the local index.
    PARALLEL version with robust controls and extended debugging.
    """
    import os  # Import needed for os.getenv
    logger.info("=== STARTING PARALLEL PLEX LIBRARY INDEXING ===")
    plex_url, plex_token = os.getenv("PLEX_URL"), os.getenv("PLEX_TOKEN")
    library_name = os.getenv("LIBRARY_NAME", "Musica")

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


def sync_playlists_for_user(plex: PlexServer, user_inputs: UserInputs):
    """Performs Spotify and Deezer synchronization for a single user."""
    if not (os.getenv("SKIP_SPOTIFY_SYNC", "0") == "1"):
        logger.info(f"--- Starting Spotify sync for user {user_inputs.plex_token[:4]}... ---")
        spotify_playlist_sync(plex, user_inputs)
    
    if not (os.getenv("SKIP_DEEZER_SYNC", "0") == "1"):
        logger.info(f"--- Starting Deezer sync for user {user_inputs.plex_token[:4]}... ---")
        deezer_playlist_sync(plex, user_inputs)

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
            try:
                logger.info(f"Scanning playlist: {playlist.title}")
                
                # Get playlist tracks
                playlist_tracks = playlist.items()
                missing_count = 0
                
                for track in playlist_tracks:
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


def run_downloader_only(source=None):
    """Reads missing tracks from DB and tries to download them."""

    logger.info("--- Starting automatic search and download for missing tracks from DB ---")
    missing_tracks_from_db = get_missing_tracks()
    
    if not missing_tracks_from_db:
        logger.info("No missing tracks in database to process.")
        return False

    logger.info(f"Found {len(missing_tracks_from_db)} missing tracks. Starting parallel link search...")
    tracks_with_links = []  # (track_id, link)
    unresolved_tracks = []  # tracks without Deezer link

    
    # Use ThreadPoolExecutor to parallelize network requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        # Create a future for each API call
        future_to_track = {executor.submit(DeezerLinkFinder.find_track_link, {'title': track[1], 'artist': track[2]}): track for track in missing_tracks_from_db}
        
        for future in concurrent.futures.as_completed(future_to_track):
            track = future_to_track[future]
            link = future.result()
            if link:
                track_id = track[0]
                tracks_with_links.append((track_id, link))
                logger.info(f"Link found for '{track[1]}' by '{track[2]}' via Deezer: {link}")
            else:
                unresolved_tracks.append(track)


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
            try:
                logger.info(f"Starting download from Deezer: {link} (for {len(track_ids)} tracks)")
                download_single_track_with_streamrip(link)
                for track_id in track_ids:
                    update_track_status(track_id, 'downloaded')
                    logger.info(f"Status updated to 'downloaded' for track ID {track_id}")

            except Exception as e:
                logger.error(f"Error during download of {link}: {e}")
                for track_id in track_ids:
                    logger.warning(f"Download failed for track ID {track_id}")

    # Process unresolved tracks with Soulseek if enabled
    slsk_client = SoulseekClient()
    for track in unresolved_tracks:
        track_id, title, artist = track[0], track[1], track[2]
        if slsk_client.search_and_download(artist, title):
            update_track_status(track_id, 'downloaded')
            logger.info(f"Track '{title}' by '{artist}' sourced from Soulseek")
        else:
            logger.warning(f"No source found for '{title}' by '{artist}'")

    return bool(tracks_with_links or unresolved_tracks)



def rescan_and_update_missing():
    """Scans recently added tracks to Plex and updates the missing list."""
    logger.info("--- Starting post-download scan to clean missing tracks list ---")
    plex_url, plex_token = os.getenv("PLEX_URL"), os.getenv("PLEX_TOKEN")
    if not (plex_url and plex_token):
        logger.error("Main Plex URL or Token not configured.")
        return

    try:
        plex = PlexServer(plex_url, plex_token)
        music_library = plex.library.section(os.getenv("LIBRARY_NAME", "Musica"))
        
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
                plex = PlexServer(os.getenv("PLEX_URL"), token)
                logger.info(f"--- Starting cleanup of old playlists for user {token[:4]}... ---")
                delete_old_playlists(plex, os.getenv("LIBRARY_NAME"), int(os.getenv("WEEKS_LIMIT")), os.getenv("PRESERVE_TAG"))
            except Exception as e:
                logger.error(f"Error during Plex connection for cleanup (user {token[:4]}...): {e}")


def run_full_sync_cycle():
    """Performs a complete cycle of synchronization, AI, and then attempts download/rescan."""
    logger.info("Starting new complete synchronization cycle...")
    
    RUN_GEMINI_PLAYLIST_CREATION = os.getenv("RUN_GEMINI_PLAYLIST_CREATION", "0") == "1"
    AUTO_DELETE_AI_PLAYLIST = os.getenv("AUTO_DELETE_AI_PLAYLIST", "0") == "1"
    RUN_DOWNLOADER = os.getenv("RUN_DOWNLOADER", "1") == "1"
    
    sync_start_time = datetime.now()
    current_year, current_week, _ = sync_start_time.isocalendar()

    user_configs = [
        {"name": "main user", "token": os.getenv("PLEX_TOKEN"), "favorites_id": os.getenv("PLEX_FAVORITES_PLAYLIST_ID_MAIN")},
        {"name": "secondary user", "token": os.getenv("PLEX_TOKEN_USERS"), "favorites_id": os.getenv("PLEX_FAVORITES_PLAYLIST_ID_SECONDARY")}
    ]
    
    gemini_model = configure_gemini() if RUN_GEMINI_PLAYLIST_CREATION else None
    
    for user_config in user_configs:
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
            append_instead_of_sync=False, wait_seconds=0,
            spotipy_client_id=os.getenv("SPOTIFY_CLIENT_ID"), spotipy_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            spotify_user_id=os.getenv("SPOTIFY_USER_ID"), deezer_user_id=os.getenv("DEEZER_USER_ID"),
            deezer_playlist_ids=os.getenv("DEEZER_PLAYLIST_ID_SECONDARY") if name == "secondary user" else os.getenv("DEEZER_PLAYLIST_ID"),
            spotify_playlist_ids=os.getenv("SPOTIFY_PLAYLIST_IDS_SECONDARY") if name == "secondary user" else os.getenv("SPOTIFY_PLAYLIST_IDS"),
            spotify_categories=[], country=os.getenv("COUNTRY")
        )
        
        try:
            plex = PlexServer(user_inputs.plex_url, user_inputs.plex_token)
            sync_playlists_for_user(plex, user_inputs)
            
            if gemini_model and favorites_playlist_id:
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
    
    # CRITICAL CHECK: Do not run playlist scan if library index is empty!
    from .utils.database import get_library_index_stats
    index_stats = get_library_index_stats()
    
    if index_stats['total_tracks_indexed'] == 0:
        logger.error("❌ BLOCKING SCAN: Library index EMPTY! Cannot detect missing tracks without index.")
        logger.error("⚠️ Run 'Index Library' from homepage before continuing.")
        logger.error("🛑 Skipping playlist scan and download to avoid massive false positives.")
        return  # Exit cycle without doing anything
    
    logger.info(f"✅ Library index OK: {index_stats['total_tracks_indexed']} tracks indexed")
    
    # Force playlist scan to detect missing tracks if DB is empty
    current_missing_count = len(get_missing_tracks())
    if current_missing_count == 0:
        logger.info("No missing tracks in DB. Forcing playlist scan...")
        force_playlist_scan_and_missing_detection()
    else:
        logger.info(f"Found {current_missing_count} missing tracks in existing DB.")
    
    if RUN_DOWNLOADER:
        download_attempted = run_downloader_only(source="Deezer")
        if download_attempted:
            wait_time = int(os.getenv("PLEX_SCAN_WAIT_TIME", "300"))
            logger.info(f"Waiting {wait_time} seconds to give Plex time to index...")
            time.sleep(wait_time)
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
            plex_connections['main'] = PlexServer(plex_url, main_token)
        if secondary_token:
            plex_connections['secondary'] = PlexServer(plex_url, secondary_token)
        
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
                                
                                # Search track on Plex using correct user connection
                                plex_track = search_plex_track(user_plex, track_title, track_artist)
                                if plex_track:
                                    tracks_to_add.append(plex_track)
                                    logger.info(f"✅ Found track for addition: '{track_title}' by '{track_artist}'")
                            
                            # Add new tracks to playlist
                            if tracks_to_add:
                                current_tracks = existing_playlist.items()
                                all_tracks = list(current_tracks) + tracks_to_add
                                
                                # Update playlist with all tracks (old + new)
                                existing_playlist.clear()
                                existing_playlist.addItems(all_tracks)
                                
                                logger.info(f"🎉 Playlist '{playlist_title}' updated with {len(tracks_to_add)} new tracks")
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