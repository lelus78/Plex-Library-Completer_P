# utils/deezer.py
import logging
from typing import List
import requests
import os
from plexapi.server import PlexServer
from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist

DEEZER_API_URL = "https://api.deezer.com"

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