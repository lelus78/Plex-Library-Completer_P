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


# ================================
# DISCOVERY PLAYLIST CURATE DEEZER
# ================================

def get_deezer_charts(country: str = 'IT') -> List[dict]:
    """
    Recupera le chart ufficiali Deezer per un paese.
    
    Args:
        country: Codice paese ISO (IT, US, FR, etc.)
        
    Returns:
        Lista di dict con metadati chart playlists
    """
    try:
        url = f"{DEEZER_API_URL}/chart/0"
        if country:
            url += f"?country={country}"
            
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        charts = []
        
        # Chart tracce top
        if 'tracks' in data and 'data' in data['tracks']:
            charts.append({
                'id': f'chart_tracks_{country}',
                'name': f'🔥 Top Tracks {country}',
                'description': f'Le tracce più popolari su Deezer in {country}',
                'poster': 'https://e-cdns-images.dzcdn.net/images/cover/1000x1000-000000-80-0-0.jpg',
                'track_count': len(data['tracks']['data']),
                'playlist_type': 'chart',
                'genre': 'various',
                'preview_tracks': [
                    f"{track['title']} - {track['artist']['name']}" 
                    for track in data['tracks']['data'][:5]
                ]
            })
        
        # Chart album top
        if 'albums' in data and 'data' in data['albums']:
            charts.append({
                'id': f'chart_albums_{country}',
                'name': f'💿 Top Albums {country}',
                'description': f'Gli album più popolari su Deezer in {country}',
                'poster': 'https://e-cdns-images.dzcdn.net/images/cover/1000x1000-000000-80-0-0.jpg',
                'track_count': len(data['albums']['data']),
                'playlist_type': 'chart',
                'genre': 'various',
                'preview_tracks': [
                    f"{album['title']} - {album['artist']['name']}" 
                    for album in data['albums']['data'][:5]
                ]
            })
        
        # Chart playlist top
        if 'playlists' in data and 'data' in data['playlists']:
            for playlist in data['playlists']['data'][:10]:  # Limite a 10 playlist chart
                charts.append({
                    'id': str(playlist['id']),
                    'name': f"📊 {playlist['title']}",
                    'description': playlist.get('description', f"Playlist chart curata da Deezer"),
                    'poster': playlist.get('picture_big', ''),
                    'track_count': playlist.get('nb_tracks', 0),
                    'playlist_type': 'chart',
                    'genre': 'various'
                })
        
        logging.info(f"✅ Trovate {len(charts)} chart playlists per {country}")
        return charts
        
    except Exception as e:
        logging.error(f"❌ Errore recuperando chart Deezer per {country}: {e}")
        return []

def get_deezer_genres() -> List[dict]:
    """
    Recupera tutti i generi musicali disponibili su Deezer.
    
    Returns:
        Lista di dict con metadati generi
    """
    try:
        url = f"{DEEZER_API_URL}/genre"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        genres = []
        
        if 'data' in data:
            for genre in data['data']:
                genres.append({
                    'id': f"genre_{genre['id']}",
                    'name': f"🎵 {genre['name']}",
                    'description': f"Categoria musicale {genre['name']} - Accesso a migliaia di brani",
                    'poster': genre.get('picture_big', ''),
                    'track_count': 0,  # Da calcolare dinamicamente
                    'playlist_type': 'genre',
                    'genre': genre['name'].lower(),
                    'genre_id': genre['id'],
                    'special_info': 'Categoria musicale - Contenuto dinamico'
                })
        
        logging.info(f"✅ Trovati {len(genres)} generi musicali Deezer")
        return genres
        
    except Exception as e:
        logging.error(f"❌ Errore recuperando generi Deezer: {e}")
        return []

def get_deezer_radio_stations() -> List[dict]:
    """
    Recupera le radio stations curate di Deezer.
    
    Returns:
        Lista di dict con metadati radio
    """
    try:
        url = f"{DEEZER_API_URL}/radio"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        radios = []
        
        if 'data' in data:
            for radio in data['data'][:20]:  # Limite a 20 radio
                radios.append({
                    'id': f"radio_{radio['id']}",
                    'name': f"📻 {radio['title']}",
                    'description': radio.get('description', f"Radio curata da Deezer - Stream musicale continuo"),
                    'poster': radio.get('picture_big', ''),
                    'track_count': 0,  # Le radio sono streams continui
                    'playlist_type': 'radio',
                    'genre': 'various',
                    'special_info': 'Stream continuo - Musica infinita'
                })
        
        logging.info(f"✅ Trovate {len(radios)} radio stations Deezer")
        return radios
        
    except Exception as e:
        logging.error(f"❌ Errore recuperando radio Deezer: {e}")
        return []

def get_deezer_editorial_playlists(limit: int = 50) -> List[dict]:
    """
    Recupera playlist editoriali/curate di Deezer.
    Usa la ricerca per trovare playlist popolari e curate.
    
    Args:
        limit: Numero massimo di playlist da recuperare
        
    Returns:
        Lista di dict con metadati playlist editoriali
    """
    try:
        # Categorie di playlist popolari da cercare
        search_terms = [
            'Top', 'Best', 'Hits', 'Mix', 'Trending', 'New', 'Hot',
            'Chill', 'Party', 'Workout', 'Study', 'Sleep', 'Focus'
        ]
        
        editorial_playlists = []
        
        for term in search_terms:
            if len(editorial_playlists) >= limit:
                break
                
            try:
                url = f"{DEEZER_API_URL}/search/playlist"
                params = {
                    'q': term,
                    'limit': 10  # 10 per termine di ricerca
                }
                
                response = requests.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                if 'data' in data:
                    for playlist in data['data']:
                        # Filtra per playlist con molte tracce (indicatore di qualità)
                        if playlist.get('nb_tracks', 0) > 20:
                            editorial_playlists.append({
                                'id': str(playlist['id']),
                                'name': f"✨ {playlist['title']}",
                                'description': playlist.get('description', f"Playlist curata con focus su {term.lower()}"),
                                'poster': playlist.get('picture_big', ''),
                                'track_count': playlist.get('nb_tracks', 0),
                                'playlist_type': 'curated',
                                'genre': term.lower()
                            })
                
            except Exception as term_error:
                logging.debug(f"Errore ricerca termine '{term}': {term_error}")
                continue
        
        # Rimuovi duplicati per ID
        seen_ids = set()
        unique_playlists = []
        for playlist in editorial_playlists:
            if playlist['id'] not in seen_ids:
                seen_ids.add(playlist['id'])
                unique_playlists.append(playlist)
        
        # Ordina per numero di tracce (più popolari prima)
        unique_playlists.sort(key=lambda x: x['track_count'], reverse=True)
        
        # Limita al numero richiesto
        result = unique_playlists[:limit]
        
        logging.info(f"✅ Trovate {len(result)} playlist editoriali Deezer")
        return result
        
    except Exception as e:
        logging.error(f"❌ Errore recuperando playlist editoriali Deezer: {e}")
        return []

def discover_all_deezer_curated_content(country: str = 'IT') -> dict:
    """
    Funzione unificata per scoprire tutto il contenuto curato Deezer.
    
    Args:
        country: Codice paese per le chart
        
    Returns:
        Dict con tutte le categorie di contenuto curato
    """
    logging.info(f"🔍 Inizio discovery contenuto curato Deezer per {country}")
    
    curated_content = {
        'charts': [],
        'genres': [],
        'radios': [],
        'editorial': []
    }
    
    try:
        # Recupera chart
        curated_content['charts'] = get_deezer_charts(country)
        
        # Recupera generi
        curated_content['genres'] = get_deezer_genres()
        
        # Recupera radio
        curated_content['radios'] = get_deezer_radio_stations()
        
        # Recupera playlist editoriali
        curated_content['editorial'] = get_deezer_editorial_playlists()
        
        total_count = sum(len(category) for category in curated_content.values())
        logging.info(f"🎉 Discovery completato: {total_count} elementi di contenuto curato trovati")
        
        return curated_content
        
    except Exception as e:
        logging.error(f"❌ Errore durante discovery contenuto curato Deezer: {e}")
        return curated_content