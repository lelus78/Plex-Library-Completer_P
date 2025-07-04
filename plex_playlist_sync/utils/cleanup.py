import logging
from datetime import datetime, timedelta
from plexapi.server import PlexServer
import os
from plexapi.exceptions import NotFound

def delete_old_playlists(plex: PlexServer, library_name: str, weeks_limit: int, preserve_tag: str = "NO_DELETE") -> None:
    """
    Deletes playlists from the specified library that are older than the indicated limit (in weeks),
    unless the playlist title contains the exclusion tag.
    Deletion only occurs if the FORCE_DELETE_OLD_PLAYLISTS environment variable is set to "1".
    """
    # --- MODIFICATION: Reads environment variable to force deletion ---
    force_delete = os.getenv("FORCE_DELETE_OLD_PLAYLISTS", "0") == "1"

    try:
        library = plex.library.section(library_name)
        logging.info(f"Library '{library_name}' found for cleanup.")
    except Exception as e:
        logging.error(f"Error: Library '{library_name}' not found during cleanup. Error: {e}")
        return

    playlists_to_delete = []
    today = datetime.now()
    cutoff_date = today - timedelta(weeks=weeks_limit)

    for playlist in library.playlists():
        # Skip playlist if title contains the preserve tag (case-insensitive)
        if preserve_tag.lower() in playlist.title.lower():
            logging.info(f"Playlist '{playlist.title}' marked NOT to delete.")
            continue

        if hasattr(playlist, 'addedAt'):
            creation_date = playlist.addedAt
            if creation_date < cutoff_date:
                playlists_to_delete.append(playlist)
                logging.info(f"Playlist to delete: '{playlist.title}' (Created on {creation_date.strftime('%Y-%m-%d')})")
        else:
            logging.warning(f"Playlist '{playlist.title}' missing 'addedAt' attribute, ignored during cleanup.")

    if playlists_to_delete:
        # --- MODIFICATION: Removed input(), confirmation is automatic if flag is active ---
        if force_delete:
            logging.warning(f"FORCE_DELETE_OLD_PLAYLISTS is active. Deleting {len(playlists_to_delete)} playlists in progress...")
            for pl in playlists_to_delete:
                try:
                    pl.delete()
                    logging.info(f"Playlist '{pl.title}' deleted.")
                except Exception as e:
                    logging.error(f"Unable to delete playlist '{pl.title}': {e}")
        else:
            logging.info(f"Found {len(playlists_to_delete)} playlists to delete, but automatic deletion is not active. Set FORCE_DELETE_OLD_PLAYLISTS=1 in .env file to proceed.")
    else:
        logging.info("No old playlists found to delete.")
        
        
# In utils/cleanup.py

def delete_previous_week_playlist(plex: PlexServer, base_playlist_name: str, current_week: int):
    """
    Searches for and deletes the previous week version of a specific playlist.
    Example: if current_week is 25, searches and deletes "Playlist Name - Week 24".
    """
    # Calculate previous week and year
    previous_week_date = datetime.now() - timedelta(weeks=1)
    previous_week_year, previous_week_num, _ = previous_week_date.isocalendar()

    # Build exact name of playlist to search and delete
    playlist_to_delete_name = f"{base_playlist_name} - Week {previous_week_num}"

    logging.info(f"Looking for previous week playlist to delete: '{playlist_to_delete_name}'")

    try:
        # Search playlist by exact title
        old_playlist = plex.playlist(playlist_to_delete_name)
        logging.warning(f"Found old AI playlist: '{old_playlist.title}'. Deletion in progress...")
        old_playlist.delete()
        logging.info(f"Old AI playlist '{old_playlist.title}' deleted successfully.")
    except NotFound:
        # It's normal not to find it, especially the first time the script runs
        logging.info(f"No previous week playlist ('{playlist_to_delete_name}') found. No action required.")
    except Exception as e:
        logging.error(f"Error during deletion of old AI playlist '{playlist_to_delete_name}': {e}")