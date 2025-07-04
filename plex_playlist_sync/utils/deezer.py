# utils/deezer.py
import logging
from typing import List
import requests
import os
from plexapi.server import PlexServer
from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist

DEEZER_API_URL = "https://api.deezer.com"

def _get_deezer_user_playlists(user_id: str, suffix: str = " - Deezer") -> List[Playlist]:
    """
    Fetch all public playlists for a given Deezer user ID.
    
    Args:
        user_id: Deezer user ID
        suffix: Suffix to append to playlist names
    
    Returns:
        List of Playlist objects
    """
    playlists = []
    url = f"{DEEZER_API_URL}/user/{user_id}/playlists"
    
    try:
        while url:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            if 'error' in data:
                logging.error(f"Deezer API error: {data['error']['message']}")
                break
            
            for playlist_data in data.get('data', []):
                # Skip if playlist is not public
                if not playlist_data.get('public', True):
                    continue
                    
                playlist = Playlist(
                    id=str(playlist_data['id']),
                    name=playlist_data['title'] + suffix,
                    description=playlist_data.get('description', ''),
                    poster=playlist_data.get('picture_big', '')
                )
                playlists.append(playlist)
            
            # Handle pagination
            url = data.get('next', None)
            
        logging.info(f"🔍 Discovered {len(playlists)} public Deezer playlists for user {user_id}")
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Deezer user playlists: {e}")
    except Exception as e:
        logging.error(f"Unexpected error during Deezer playlist discovery: {e}")
    
    return playlists


def _get_all_tracks_from_playlist(tracklist_url: str) -> List[Track]:
    """
    Recupera TUTTE le tracce da un URL di tracklist, gestendo la paginazione.
    """
    all_tracks = []
    url = tracklist_url

    while url:
        try:
            response = requests.get(url)
            response.raise_for_status()  # Lancia un errore per status HTTP non 200
            data = response.json()

            for track_data in data.get('data', []):
                track = Track(
                    title=track_data.get('title', ''),
                    artist=track_data.get('artist', {}).get('name', ''),
                    album=track_data.get('album', {}).get('title', ''),
                    url=track_data.get('link', '')
                )
                all_tracks.append(track)
            
            # Passa alla pagina successiva, se esiste
            url = data.get('next', None)
            if url:
                logging.debug(f"Paginazione Deezer: passo a {url}")

        except requests.exceptions.RequestException as e:
            logging.error(f"Errore durante la richiesta alla tracklist di Deezer {url}: {e}")
            break # Interrompe il ciclo in caso di errore di rete
        except Exception as e:
            logging.error(f"Errore imprevisto durante il parsing delle tracce da Deezer: {e}")
            break

    return all_tracks


def deezer_playlist_sync_with_discovery(plex: PlexServer, userInputs: UserInputs) -> None:
    """
    Discovers and syncs all user playlists from Deezer (auto-discovery mode).
    This function fetches all user playlists instead of using pre-configured IDs.
    """
    if not userInputs.deezer_user_id:
        logging.error("DEEZER_USER_ID not configured; cannot discover playlists")
        return

    logging.info(f"🔍 Auto-discovering Deezer playlists for user: {userInputs.deezer_user_id}")
    suffix = " - Deezer" if userInputs.append_service_suffix else ""
    
    try:
        # Get all user playlists
        discovered_playlists = _get_deezer_user_playlists(userInputs.deezer_user_id, suffix)
        
        if not discovered_playlists:
            logging.warning("No Deezer playlists found for auto-discovery")
            return
        
        logging.info(f"📋 Discovered {len(discovered_playlists)} Deezer playlists:")
        for playlist in discovered_playlists:
            logging.info(f"   - {playlist.name} (ID: {playlist.id})")
        
        # Apply TEST_MODE_PLAYLIST_LIMIT if configured
        limit = int(os.getenv("TEST_MODE_PLAYLIST_LIMIT", 0))
        if limit > 0:
            discovered_playlists = discovered_playlists[:limit]
            logging.warning(f"MODALITÀ TEST: Limite di {limit} playlist applicato per Deezer auto-discovery")
        
        # Process each discovered playlist
        for i, playlist_obj in enumerate(discovered_playlists):
            try:
                # Check for stop flag
                from ..sync_logic import check_stop_flag_direct
                if check_stop_flag_direct():
                    logging.info("🛑 Stop requested during Deezer playlist discovery")
                    return
                
                logging.info(f"🎵 Processing discovered playlist: {playlist_obj.name}")
                
                # Get playlist details from API
                playlist_url = f"{DEEZER_API_URL}/playlist/{playlist_obj.id}"
                response = requests.get(playlist_url)
                response.raise_for_status()
                playlist_data = response.json()
                
                if 'error' in playlist_data:
                    logging.error(f"Error accessing playlist {playlist_obj.id}: {playlist_data['error']['message']}")
                    continue
                
                # Get tracks using existing function
                tracks = _get_all_tracks_from_playlist(playlist_data['tracklist'])
                
                if tracks:
                    update_or_create_plex_playlist(plex, playlist_obj, tracks, userInputs)
                    logging.info(f"✅ Synced playlist '{playlist_obj.name}' with {len(tracks)} tracks")
                else:
                    logging.warning(f"⚠️ No tracks found in playlist '{playlist_obj.name}'")
                    
            except Exception as playlist_error:
                logging.error(f"❌ Error processing playlist '{playlist_obj.name}': {playlist_error}")
                continue
        
        logging.info(f"🎉 Auto-discovery completed: processed {len(discovered_playlists)} Deezer playlists")
        
    except Exception as e:
        logging.error(f"❌ Error during Deezer auto-discovery: {e}")


def deezer_playlist_sync(plex: PlexServer, userInputs: UserInputs) -> None:
    """
    Crea/Aggiorna le playlist di Plex usando le playlist di Deezer,
    utilizzando richieste dirette all'API pubblica.
    """
    playlist_ids_str = userInputs.deezer_playlist_ids
    if not playlist_ids_str:
        logging.info("Nessun ID di playlist Deezer fornito, salto la sincronizzazione Deezer.")
        return

    playlist_ids = [pid.strip() for pid in playlist_ids_str.split(',') if pid.strip()]
    suffix = " - Deezer" if userInputs.append_service_suffix else ""
    limit = int(os.getenv("TEST_MODE_PLAYLIST_LIMIT", 0))

    for i, playlist_id in enumerate(playlist_ids):
        # Check if stop was requested
        from ..sync_logic import check_stop_flag_direct
        if check_stop_flag_direct():
            logging.info("🛑 Stop requested during Deezer playlist sync")
            return
            
        if limit > 0 and i >= limit:
            logging.warning(f"MODALITÀ TEST: Limite di {limit} playlist raggiunto per Deezer. Interrompo.")
            break

        logging.info(f"Sincronizzazione playlist Deezer con ID: {playlist_id}")
        playlist_url = f"{DEEZER_API_URL}/playlist/{playlist_id}"
        
        try:
            response = requests.get(playlist_url)
            response.raise_for_status()
            playlist_data = response.json()

            # Controlla se la playlist è valida (ad es. se non è privata)
            if 'error' in playlist_data:
                logging.error(f"Errore dall'API Deezer per la playlist ID {playlist_id}: {playlist_data['error']['message']}")
                continue

            playlist_obj = Playlist(
                id=playlist_data['id'],
                name=playlist_data['title'] + suffix,
                description=playlist_data.get('description', ''),
                poster=playlist_data.get('picture_big', '')
            )
            
            # Otteniamo le tracce usando la funzione che gestisce la paginazione
            tracks = _get_all_tracks_from_playlist(playlist_data['tracklist'])
            
            if tracks:
                logging.info(f"Trovate {len(tracks)} tracce per la playlist '{playlist_obj.name}'.")
                update_or_create_plex_playlist(plex, playlist_obj, tracks, userInputs)
            else:
                logging.warning(f"Nessuna traccia trovata per la playlist '{playlist_obj.name}'.")

        except requests.exceptions.RequestException as e:
            logging.error(f"Errore nel recuperare la playlist Deezer ID {playlist_id}: {e}")
        except Exception as e:
            logging.error(f"Errore imprevisto durante la sincronizzazione della playlist {playlist_id}: {e}")