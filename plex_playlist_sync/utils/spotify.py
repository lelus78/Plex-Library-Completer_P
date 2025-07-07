import logging
from typing import List
import os
import time

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials
from plexapi.server import PlexServer

# Web scraping imports (only regex needed for URL parsing)
import re

from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist
 

def get_spotify_credentials():
    """
    Crea e restituisce un oggetto Spotify autenticato usando le credenziali dell'ambiente.
    
    Returns:
        spotipy.Spotify: Oggetto Spotify autenticato o None se credenziali non configurate
    """
    try:
        client_id = os.getenv('SPOTIFY_CLIENT_ID')
        client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            logging.error("❌ Credenziali Spotify non configurate (SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET)")
            return None
        
        # Use a custom cache handler that doesn't try to write to disk
        from spotipy.cache_handler import MemoryCacheHandler
        memory_cache = MemoryCacheHandler()
        
        sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret,
                cache_handler=memory_cache  # Use memory cache instead
            )
        )
        
        logging.info("✅ Credenziali Spotify configurate correttamente")
        return sp
        
    except Exception as e:
        logging.error(f"❌ Errore durante configurazione credenziali Spotify: {e}")
        return None


def _get_sp_user_playlists(
    sp: spotipy.Spotify, user_id: str, userInputs: UserInputs, suffix: str = " - Spotify"
) -> List[Playlist]:
    """Get metadata for playlists in the given user_id."""
    playlists = []
    # Legge il limite di playlist dal file .env
    limit = int(os.getenv("TEST_MODE_PLAYLIST_LIMIT", 0))

    try:
        sp_playlists = sp.user_playlists(user_id)
        # Aggiunto 'enumerate' per poter contare le playlist
        for i, playlist in enumerate(sp_playlists["items"]):
            # Se il limite è impostato e lo abbiamo raggiunto, esce dal ciclo
            if limit > 0 and i >= limit:
                logging.warning(f"MODALITÀ TEST: Limite di {limit} playlist raggiunto per Spotify. Interrompo.")
                break

            playlists.append(
                Playlist(
                    id=playlist["id"],
                    name=playlist["name"] + suffix,
                    description=playlist.get("description", ""),
                    poster=playlist.get("images", [{}])[0].get("url", ""),
                )
            )
    except Exception as e:
        logging.error(f"Spotify User ID Error: {e}")
    return playlists


def extract_spotify_playlist_id(url_or_id: str) -> str:
    """
    Estrae l'ID della playlist da un URL Spotify o restituisce l'ID se già fornito.
    
    Esempi:
    - https://open.spotify.com/playlist/57EG9lWmdn7HHofXuQVsow -> 57EG9lWmdn7HHofXuQVsow
    - spotify:playlist:57EG9lWmdn7HHofXuQVsow -> 57EG9lWmdn7HHofXuQVsow  
    - 57EG9lWmdn7HHofXuQVsow -> 57EG9lWmdn7HHofXuQVsow
    """
    url_or_id = url_or_id.strip()
    
    # Se è già un ID (22 caratteri alfanumerici)
    if len(url_or_id) == 22 and url_or_id.isalnum():
        return url_or_id
    
    # Estrai da URL open.spotify.com
    if "open.spotify.com/playlist/" in url_or_id:
        # https://open.spotify.com/playlist/57EG9lWmdn7HHofXuQVsow?si=...
        playlist_id = url_or_id.split("playlist/")[1].split("?")[0].split("&")[0]
        return playlist_id
    
    # Estrai da URI spotify:
    if url_or_id.startswith("spotify:playlist:"):
        return url_or_id.replace("spotify:playlist:", "")
    
    # Se non riesce a estrarre, restituisce l'input originale
    logging.warning(f"Impossibile estrarre ID playlist da: {url_or_id}")
    return url_or_id

def _get_sp_playlists_by_ids(
    sp: spotipy.Spotify, ids: List[str], suffix: str = " - Spotify"
) -> List[Playlist]:
    """Fetch specific playlists by ID."""
    playlists = []
    for pid in ids:
        pid = pid.strip()
        if not pid:
            continue
        try:
            pl = sp.playlist(pid)
            playlists.append(
                Playlist(
                    id=pl["id"],
                    name=pl["name"] + suffix,
                    description=pl.get("description", ""),
                    poster=pl.get("images", [{}])[0].get("url", ""),
                )
            )
        except Exception as e:
            logging.error(f"Error fetching playlist {pid}: {e}")
    return playlists

def get_spotify_public_playlist(sp: spotipy.Spotify, url_or_id: str) -> dict:
    """
    Recupera metadati di una playlist pubblica Spotify tramite URL o ID.
    
    Args:
        sp: Oggetto Spotify autenticato
        url_or_id: URL completo o ID della playlist
        
    Returns:
        Dict con metadati della playlist o None se errore
    """
    try:
        playlist_id = extract_spotify_playlist_id(url_or_id)
        playlist_data = sp.playlist(playlist_id)
        
        # Recupera prime 5 tracce per anteprima
        tracks_data = sp.playlist_items(playlist_id, limit=5, fields='items(track(name,artists(name)))')
        preview_tracks = []
        
        for item in tracks_data.get('items', []):
            track = item.get('track')
            if track and track.get('name'):
                artist_name = track.get('artists', [{}])[0].get('name', 'Unknown Artist')
                preview_tracks.append(f"{track['name']} - {artist_name}")
        
        return {
            'id': playlist_data['id'],
            'name': playlist_data['name'],
            'description': playlist_data.get('description', ''),
            'poster': playlist_data.get('images', [{}])[0].get('url', ''),
            'track_count': playlist_data.get('tracks', {}).get('total', 0),
            'playlist_type': 'public',
            'preview_tracks': preview_tracks,
            'owner': playlist_data.get('owner', {}).get('display_name', ''),
            'public': playlist_data.get('public', False),
            'collaborative': playlist_data.get('collaborative', False),
            'url': url_or_id if url_or_id.startswith('http') else f"https://open.spotify.com/playlist/{playlist_id}"
        }
        
    except Exception as e:
        logging.error(f"❌ Errore recuperando playlist pubblica Spotify {url_or_id}: {e}")
        return None


def _get_sp_tracks_from_playlist(
    sp: spotipy.Spotify, playlist: Playlist
) -> List[Track]:
    """Return list of tracks with metadata."""

    def extract_sp_track_metadata(track) -> Track:
        title = track["track"]["name"]
        artist = track["track"]["artists"][0]["name"]
        album = track["track"]["album"]["name"]
        url = track["track"]["external_urls"].get("spotify", "")
        return Track(title, artist, album, url)

    sp_playlist_tracks = sp.playlist_items(playlist.id)

    tracks = list(
        map(
            extract_sp_track_metadata,
            [i for i in sp_playlist_tracks["items"] if i.get("track")],
        )
    )

    while sp_playlist_tracks["next"]:
        sp_playlist_tracks = sp.next(sp_playlist_tracks)
        tracks.extend(
            list(
                map(
                    extract_sp_track_metadata,
                    [i for i in sp_playlist_tracks["items"] if i.get("track")],
                )
            )
        )
    return tracks


def spotify_playlist_sync_with_discovery(sp: spotipy.Spotify, plex: PlexServer, userInputs: UserInputs) -> None:
    """
    Discovers and syncs all user playlists from Spotify (auto-discovery mode).
    This function fetches all user playlists instead of using pre-configured IDs.
    """
    if not userInputs.spotify_user_id:
        logging.error("SPOTIFY_USER_ID not configured; cannot discover playlists")
        return

    logging.info(f"🔍 Auto-discovering Spotify playlists for user: {userInputs.spotify_user_id}")
    
    try:
        # Use existing function to get all user playlists
        discovered_playlists = _get_sp_user_playlists(sp, userInputs.spotify_user_id, userInputs)
        
        if not discovered_playlists:
            logging.warning("No Spotify playlists found for auto-discovery")
            return
        
        logging.info(f"📋 Discovered {len(discovered_playlists)} Spotify playlists:")
        for playlist in discovered_playlists:
            logging.info(f"   - {playlist.name} (ID: {playlist.id})")
        
        # Process each discovered playlist
        for playlist in discovered_playlists:
            try:
                logging.info(f"🎵 Processing discovered playlist: {playlist.name}")
                
                # Get tracks for this playlist  
                tracks = _get_sp_tracks_from_playlist(sp, playlist)
                if tracks:
                    update_or_create_plex_playlist(plex, playlist, tracks, userInputs)
                    logging.info(f"✅ Synced playlist '{playlist.name}' with {len(tracks)} tracks")
                else:
                    logging.warning(f"⚠️ No tracks found in playlist '{playlist.name}'")
                    
            except Exception as playlist_error:
                logging.error(f"❌ Error processing playlist '{playlist.name}': {playlist_error}")
                continue
        
        logging.info(f"🎉 Auto-discovery completed: processed {len(discovered_playlists)} Spotify playlists")
        
    except Exception as e:
        logging.error(f"❌ Error during Spotify auto-discovery: {e}")


def spotify_playlist_sync(
    sp: spotipy.Spotify, plex: PlexServer, userInputs: UserInputs
) -> None:
    """Create/Update plex playlists with playlists from spotify."""
    if not userInputs.spotify_user_id:
        logging.error("SPOTIFY_USER_ID not configured; skipping Spotify sync")
        return

    suffix = " - Spotify" if userInputs.append_service_suffix else ""
    if userInputs.spotify_playlist_ids:
        ids = userInputs.spotify_playlist_ids.split(",")
        playlists = _get_sp_playlists_by_ids(sp, ids, suffix)
    else:
        playlists = _get_sp_user_playlists(sp, userInputs.spotify_user_id, userInputs, suffix)
    if playlists:
        for playlist in playlists:
            # Check if stop was requested
            from ..sync_logic import check_stop_flag_direct
            if check_stop_flag_direct():
                logging.info("🛑 Stop requested during Spotify playlist sync")
                return
            
            tracks = _get_sp_tracks_from_playlist(sp, playlist)
            update_or_create_plex_playlist(plex, playlist, tracks, userInputs)
    else:
        logging.error("No spotify playlists found for given user")


# ================================
# DISCOVERY SPOTIFY CON METADATI ESTESI
# ================================

def get_spotify_playlist_with_metadata(sp: spotipy.Spotify, playlist_id: str) -> dict:
    """
    Recupera metadati completi per una singola playlist Spotify.
    Include prime 5 tracce per anteprima.
    
    Args:
        sp: Oggetto Spotify autenticato
        playlist_id: ID della playlist
        
    Returns:
        Dict con metadati completi della playlist
    """
    try:
        # Recupera metadati playlist
        playlist_data = sp.playlist(playlist_id)
        
        # Recupera prime 5 tracce per anteprima
        tracks_data = sp.playlist_items(playlist_id, limit=5, fields='items(track(name,artists(name)))')
        preview_tracks = []
        
        for item in tracks_data.get('items', []):
            track = item.get('track')
            if track and track.get('name'):
                artist_name = track.get('artists', [{}])[0].get('name', 'Unknown Artist')
                preview_tracks.append(f"{track['name']} - {artist_name}")
        
        return {
            'id': playlist_data['id'],
            'name': playlist_data['name'],
            'description': playlist_data.get('description', ''),
            'poster': playlist_data.get('images', [{}])[0].get('url', ''),
            'track_count': playlist_data.get('tracks', {}).get('total', 0),
            'playlist_type': 'user',
            'preview_tracks': preview_tracks,
            'owner': playlist_data.get('owner', {}).get('display_name', ''),
            'public': playlist_data.get('public', False),
            'collaborative': playlist_data.get('collaborative', False)
        }
        
    except Exception as e:
        logging.error(f"❌ Errore recuperando metadati Spotify playlist {playlist_id}: {e}")
        return None

def discover_spotify_user_playlists_enhanced(sp: spotipy.Spotify, user_id: str, suffix: str = " - Spotify") -> List[dict]:
    """
    Versione migliorata del discovery playlist utente Spotify con metadati completi.
    
    Args:
        sp: Oggetto Spotify autenticato
        user_id: ID utente Spotify
        suffix: Suffisso da aggiungere ai nomi playlist
        
    Returns:
        Lista di dict con metadati completi delle playlist
    """
    try:
        playlists = []
        limit = int(os.getenv("TEST_MODE_PLAYLIST_LIMIT", 0))
        
        sp_playlists = sp.user_playlists(user_id)
        
        for i, playlist in enumerate(sp_playlists["items"]):
            # Controllo limite test mode
            if limit > 0 and i >= limit:
                logging.warning(f"MODALITÀ TEST: Limite di {limit} playlist raggiunto per Spotify. Interrompo.")
                break
            
            # Recupera metadati completi
            enhanced_metadata = get_spotify_playlist_with_metadata(sp, playlist['id'])
            
            if enhanced_metadata:
                # Aggiorna nome con suffisso
                enhanced_metadata['name'] += suffix
                playlists.append(enhanced_metadata)
        
        logging.info(f"✅ Discovery migliorato: {len(playlists)} playlist Spotify con metadati completi")
        return playlists
        
    except Exception as e:
        logging.error(f"❌ Errore durante discovery migliorato Spotify: {e}")
        return []

def get_spotify_featured_playlists(sp: spotipy.Spotify, country: str = 'IT', limit: int = 20, max_retries: int = 2) -> List[dict]:
    """
    Recupera playlist in evidenza curate da Spotify con gestione errori migliorata.
    
    Args:
        sp: Oggetto Spotify autenticato
        country: Codice paese ISO
        limit: Numero massimo di playlist
        max_retries: Numero massimo di tentativi
        
    Returns:
        Lista di dict con playlist curate
    """
    for attempt in range(max_retries + 1):
        try:
            featured = sp.featured_playlists(country=country, limit=limit)
            curated_playlists = []
            
            for playlist in featured.get('playlists', {}).get('items', []):
                # Recupera metadati completi
                enhanced_metadata = get_spotify_playlist_with_metadata(sp, playlist['id'])
                
                if enhanced_metadata:
                    # Aggiorna tipo e nome per indicare che è curata
                    enhanced_metadata['name'] = f"✨ {enhanced_metadata['name']}"
                    enhanced_metadata['playlist_type'] = 'curated'
                    enhanced_metadata['description'] = f"Playlist in evidenza curata da Spotify. {enhanced_metadata.get('description', '')}"
                    curated_playlists.append(enhanced_metadata)
            
            logging.info(f"✅ Trovate {len(curated_playlists)} playlist Spotify curate")
            return curated_playlists
            
        except SpotifyException as e:
            if e.http_status == 404:
                logging.warning(f"⚠️ Playlist in evidenza non disponibili per la regione {country}")
                return []
            elif e.http_status == 429:  # Rate limit
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    logging.warning(f"⏳ Rate limit raggiunto, attendo {wait_time}s prima del tentativo {attempt + 2}/{max_retries + 1}")
                    time.sleep(wait_time)
                    continue
                else:
                    logging.error(f"❌ Rate limit persistente dopo {max_retries} tentativi")
                    return []
            else:
                if attempt < max_retries:
                    logging.warning(f"⚠️ Errore temporaneo (HTTP {e.http_status}), tentativo {attempt + 2}/{max_retries + 1}")
                    time.sleep(1)
                    continue
                else:
                    logging.error(f"❌ Errore Spotify persistente: HTTP {e.http_status} - {e}")
                    return []
        except Exception as e:
            if attempt < max_retries:
                logging.warning(f"⚠️ Errore generico, tentativo {attempt + 2}/{max_retries + 1}: {e}")
                time.sleep(1)
                continue
            else:
                logging.error(f"❌ Errore recuperando playlist curate Spotify: {e}")
                return []
    
    return []

def get_spotify_category_playlists(sp: spotipy.Spotify, category_id: str, country: str = 'IT', limit: int = 10, max_retries: int = 2) -> List[dict]:
    """
    Recupera playlist di una specifica categoria Spotify con gestione errori migliorata.
    
    Args:
        sp: Oggetto Spotify autenticato
        category_id: ID categoria (es. 'toplists', 'pop', 'rock')
        country: Codice paese ISO
        limit: Numero massimo di playlist
        max_retries: Numero massimo di tentativi
        
    Returns:
        Lista di dict con playlist della categoria
    """
    for attempt in range(max_retries + 1):
        try:
            category_playlists = sp.category_playlists(category_id, country=country, limit=limit)
            playlists = []
            
            for playlist in category_playlists.get('playlists', {}).get('items', []):
                # Recupera metadati completi
                enhanced_metadata = get_spotify_playlist_with_metadata(sp, playlist['id'])
                
                if enhanced_metadata:
                    # Aggiorna tipo e nome per indicare categoria
                    enhanced_metadata['name'] = f"🎯 {enhanced_metadata['name']}"
                    enhanced_metadata['playlist_type'] = 'category'
                    enhanced_metadata['genre'] = category_id
                    enhanced_metadata['description'] = f"Playlist categoria {category_id}. {enhanced_metadata.get('description', '')}"
                    playlists.append(enhanced_metadata)
            
            if playlists:
                logging.info(f"✅ Trovate {len(playlists)} playlist Spotify categoria '{category_id}'")
            else:
                logging.info(f"ℹ️ Nessuna playlist trovata per categoria '{category_id}' in {country}")
            return playlists
            
        except SpotifyException as e:
            if e.http_status == 404:
                logging.debug(f"📍 Categoria '{category_id}' non disponibile per la regione {country}")
                return []
            elif e.http_status == 429:  # Rate limit
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    logging.warning(f"⏳ Rate limit per categoria '{category_id}', attendo {wait_time}s")
                    time.sleep(wait_time)
                    continue
                else:
                    logging.error(f"❌ Rate limit persistente per categoria '{category_id}'")
                    return []
            else:
                if attempt < max_retries:
                    logging.warning(f"⚠️ Errore temporaneo categoria '{category_id}' (HTTP {e.http_status}), tentativo {attempt + 2}/{max_retries + 1}")
                    time.sleep(1)
                    continue
                else:
                    logging.error(f"❌ Errore persistente categoria '{category_id}': HTTP {e.http_status} - {e}")
                    return []
        except Exception as e:
            if attempt < max_retries:
                logging.warning(f"⚠️ Errore generico categoria '{category_id}', tentativo {attempt + 2}/{max_retries + 1}: {e}")
                time.sleep(1)
                continue
            else:
                logging.error(f"❌ Errore recuperando playlist categoria Spotify '{category_id}': {e}")
                return []
    
    return []

def get_spotify_popular_playlists_curated(sp: spotipy.Spotify, country: str = 'IT', limit: int = 20, max_retries: int = 2) -> List[dict]:
    """
    Recupera playlist popolari usando ricerca intelligente per playlist pubbliche reali.
    
    Args:
        sp: Oggetto Spotify autenticato
        country: Codice paese per contenuto localizzato
        limit: Numero massimo di playlist da recuperare  
        max_retries: Numero massimo di tentativi per ogni ricerca
        
    Returns:
        Lista di dict con playlist popolari
    """
    popular_playlists = []
    
    # Strategia di ricerca per trovare playlist pubbliche popolari
    # Cerchiamo termini generici che probabilmente hanno playlist pubbliche con molti follower
    search_terms = [
        "top hits 2024",
        "best songs",
        "pop music", 
        "workout playlist",
        "chill music",
        "party songs",
        "rock classics",
        "hip hop",
        "dance music",
        "indie music",
        "country music",
        "electronic music",
        "jazz music",
        "classical music",
        "latin music",
        "viral songs",
        "trending music",
        "summer hits",
        "billboard",
        "chart toppers"
    ]
    
    # Prima aggiungiamo la playlist di esempio che sappiamo funziona
    try:
        example_playlist = sp.playlist('6LyxrNMgOeHUDKoMGa2Goe', fields='id,name,description,images,tracks.total,followers.total,owner.display_name,public,collaborative')
        if example_playlist:
            followers = example_playlist.get('followers', {}).get('total', 0)
            track_count = example_playlist.get('tracks', {}).get('total', 0)
            
            enhanced_metadata = {
                'id': example_playlist['id'],
                'name': f"✅ {example_playlist['name']}",
                'description': f"Playlist di esempio verificata ({followers:,} follower). {example_playlist.get('description', '')}",
                'poster': example_playlist.get('images', [{}])[0].get('url', '') if example_playlist.get('images') else '',
                'track_count': track_count,
                'playlist_type': 'popular',
                'preview_tracks': ["Playlist di esempio funzionante"],
                'owner': example_playlist.get('owner', {}).get('display_name', 'User'),
                'public': example_playlist.get('public', True),
                'collaborative': example_playlist.get('collaborative', False),
                'followers': followers
            }
            popular_playlists.append(enhanced_metadata)
            logging.info(f"✅ Aggiunta playlist di esempio: {example_playlist['name']} ({followers:,} follower)")
    except Exception as e:
        logging.warning(f"⚠️ Errore caricamento playlist di esempio: {e}")
    
    # Ora cerchiamo altre playlist pubbliche
    for search_term in search_terms:
        if len(popular_playlists) >= limit:
            break
            
        try:
            # Cerca playlist con questo termine
            search_results = sp.search(q=search_term, type='playlist', limit=10)
            
            for playlist in search_results.get('playlists', {}).get('items', []):
                if len(popular_playlists) >= limit:
                    break
                
                # Salta playlist di Spotify ufficiali (quelle che iniziano con 37i9dQZF1DX)
                if playlist['id'].startswith('37i9dQZF1DX'):
                    continue
                    
                # Salta se è già nella lista
                if any(p['id'] == playlist['id'] for p in popular_playlists):
                    continue
                
                # Verifica che sia pubblica e popolare
                if not playlist.get('public', True):
                    continue
                    
                followers = playlist.get('followers', {}).get('total', 0)
                track_count = playlist.get('tracks', {}).get('total', 0)
                
                # Filtro per playlist popolari: almeno 1000 follower e 10 tracce
                if followers >= 1000 and track_count >= 10:
                    try:
                        # Verifica che sia davvero accessibile
                        full_playlist = sp.playlist(playlist['id'], fields='id,name,description,images,tracks.total,followers.total,owner.display_name,public')
                        
                        if full_playlist and full_playlist.get('public', True):
                            # Recupera prime 3 tracce per anteprima
                            preview_tracks = []
                            try:
                                tracks_data = sp.playlist_items(playlist['id'], limit=3, fields='items(track(name,artists(name)))')
                                for item in tracks_data.get('items', []):
                                    track = item.get('track')
                                    if track and track.get('name'):
                                        artists = track.get('artists', [])
                                        if artists and len(artists) > 0:
                                            artist_name = artists[0].get('name', 'Unknown Artist')
                                        else:
                                            artist_name = 'Unknown Artist'
                                        preview_tracks.append(f"{track['name']} - {artist_name}")
                            except Exception:
                                preview_tracks = ["Anteprima non disponibile"]
                            
                            enhanced_metadata = {
                                'id': full_playlist['id'],
                                'name': f"🔥 {full_playlist['name']}",
                                'description': f"Playlist pubblica popolare ({followers:,} follower). {full_playlist.get('description', '')}",
                                'poster': full_playlist.get('images', [{}])[0].get('url', '') if full_playlist.get('images') else '',
                                'track_count': track_count,
                                'playlist_type': 'popular',
                                'preview_tracks': preview_tracks,
                                'owner': full_playlist.get('owner', {}).get('display_name', 'Unknown'),
                                'public': full_playlist.get('public', True),
                                'collaborative': full_playlist.get('collaborative', False),
                                'followers': followers
                            }
                            
                            popular_playlists.append(enhanced_metadata)
                            logging.info(f"✅ Trovata playlist pubblica popolare: {full_playlist['name']} ({followers:,} follower) by {enhanced_metadata['owner']}")
                            
                    except Exception as playlist_error:
                        logging.debug(f"⚠️ Playlist {playlist['name']} non accessibile: {playlist_error}")
                        continue
                        
        except Exception as e:
            logging.debug(f"⚠️ Errore ricerca '{search_term}': {e}")
            continue
    
    # Ordina per numero di follower (decrescente) 
    popular_playlists.sort(key=lambda x: x.get('followers', 0), reverse=True)
    
    if popular_playlists:
        total_followers = sum(p.get('followers', 0) for p in popular_playlists)
        logging.info(f"🔥 Trovate {len(popular_playlists)} playlist pubbliche popolari (totale {total_followers:,} follower)")
    else:
        logging.warning("⚠️ Nessuna playlist pubblica popolare trovata")
    
    return popular_playlists

def enrich_playlist_with_spotify_api(sp: spotipy.Spotify, playlist_id: str, fallback_data: dict) -> dict:
    """
    Arricchisce i metadati di una playlist usando l'API Spotify ufficiale.
    
    Args:
        sp: Oggetto spotipy autenticato
        playlist_id: ID della playlist Spotify
        fallback_data: Dati di fallback da SpotifyScraper
        
    Returns:
        Dict con metadati arricchiti
    """
    try:
        # Prova a ottenere i dettagli dalla playlist via API
        playlist_info = sp.playlist(playlist_id, fields="id,name,description,images,tracks.total,owner.display_name,followers.total,public,collaborative")
        
        enhanced_data = {
            "name": playlist_info.get("name", ""),
            "description": playlist_info.get("description", ""),
            "poster": playlist_info.get("images", [{}])[0].get("url", "") if playlist_info.get("images") else "",
            "track_count": playlist_info.get("tracks", {}).get("total", 0),
            "owner": playlist_info.get("owner", {}).get("display_name", "Spotify"),
            "followers": playlist_info.get("followers", {}).get("total", 0),
            "public": playlist_info.get("public", True),
            "collaborative": playlist_info.get("collaborative", False)
        }
        
        # Prova a ottenere alcune tracce per anteprima
        try:
            tracks = sp.playlist_tracks(playlist_id, limit=5, fields="items(track(name,artists(name)))")
            preview_tracks = []
            for item in tracks.get("items", [])[:3]:
                track = item.get("track", {})
                if track and track.get("name"):
                    artists = ", ".join([artist.get("name", "") for artist in track.get("artists", [])])
                    preview_tracks.append(f"{track['name']} - {artists}")
            enhanced_data["preview_tracks"] = preview_tracks
        except Exception:
            enhanced_data["preview_tracks"] = []
            
        logging.debug(f"✅ Arricchiti metadati per playlist {playlist_id}: {enhanced_data['name']}")
        return enhanced_data
        
    except Exception as api_error:
        logging.debug(f"⚠️ Impossibile arricchire playlist {playlist_id} via API: {api_error}")
        # Restituisce dati vuoti per usare il fallback
        return {}

def get_all_playlist_tracks_with_fallback(client, sp, url: str, playlist_id: str, max_tracks: int = 500) -> list:
    """
    Estrae tutte le tracce di una playlist usando SpotifyScraper + fallback API Spotify per paginazione completa.
    
    Args:
        client: Client SpotifyScraper
        sp: Client Spotify API per fallback
        url: URL della playlist Spotify
        playlist_id: ID della playlist
        max_tracks: Numero massimo di tracce da estrarre (default 500)
        
    Returns:
        Lista di tutte le tracce della playlist
    """
    all_tracks = []
    
    try:
        # Prima prova con SpotifyScraper per ottenere fino a 100 tracce
        playlist_info = client.get_playlist_info(url)
        
        if playlist_info and 'tracks' in playlist_info:
            scraper_tracks = playlist_info.get('tracks', [])
            if scraper_tracks:
                all_tracks.extend(scraper_tracks)
                logging.debug(f"📊 SpotifyScraper: ottenute {len(scraper_tracks)} tracce")
        
        # Se abbiamo meno di 100 tracce, probabilmente sono tutte
        if len(all_tracks) < 100:
            return all_tracks
            
        # Se abbiamo esattamente 100 tracce, potrebbe esserci di più
        # Usa l'API Spotify per ottenere il conteggio totale e le tracce rimanenti
        try:
            # Ottieni informazioni complete dalla playlist via API ufficiale
            api_playlist = sp.playlist(playlist_id, fields='tracks.total,tracks.items.track(name,artists.name)')
            total_tracks = api_playlist.get('tracks', {}).get('total', 0)
            
            if total_tracks > 100:
                logging.info(f"🔄 Playlist ha {total_tracks} tracce, paginazione necessaria via API Spotify")
                
                # Usa API Spotify per ottenere le tracce rimanenti
                offset = 100
                while len(all_tracks) < min(total_tracks, max_tracks):
                    try:
                        tracks_page = sp.playlist_tracks(playlist_id, offset=offset, limit=100)
                        
                        if not tracks_page.get('items'):
                            break
                            
                        # Converti formato API Spotify al formato SpotifyScraper
                        for item in tracks_page.get('items', []):
                            track = item.get('track', {})
                            if track:
                                converted_track = {
                                    'name': track.get('name', ''),
                                    'artists': [{'name': artist.get('name', '')} for artist in track.get('artists', [])]
                                }
                                all_tracks.append(converted_track)
                        
                        offset += 100
                        
                        # Rispetta rate limits
                        time.sleep(0.1)
                        
                    except Exception as api_error:
                        logging.warning(f"⚠️ Errore paginazione API Spotify: {api_error}")
                        break
                        
        except Exception as api_error:
            logging.debug(f"⚠️ Fallback API Spotify non disponibile: {api_error}")
            
        logging.info(f"📊 Paginazione completata: {len(all_tracks)} tracce totali estratte")
        return all_tracks[:max_tracks]  # Limita al massimo richiesto
        
    except Exception as e:
        logging.warning(f"⚠️ Errore durante paginazione playlist {url}: {e}")
        return all_tracks

def get_spotify_popular_playlists_webscraping(sp: object, country: str = 'IT', limit: int = 100) -> list:
    """
    Ottieni playlist popolari da Spotify usando SpotifyScraper con API corretta.

    Args:
        sp: Oggetto spotipy.Spotify autenticato (per fallback e arricchimento metadati)
        country: Codice paese per filtro localizzazione
        limit: Quante playlist restituire

    Returns:
        Lista di dict normalizzati, formato:
        {'id', 'name', 'description', 'poster', 'track_count', 'playlist_type', 'preview_tracks', 'owner', ...}
    """
    playlists = []
    
    try:
        from spotify_scraper import SpotifyClient
        
        # Inizializza il client SpotifyScraper con configurazione per container
        client = SpotifyClient(
            browser_type="requests",  # Usa requests invece di selenium per evitare problemi di display
            log_level="WARNING"  # Riduci verbosità
        )
        
        # Lista estesa di playlist popolari Spotify ufficiali e di grandi creator
        popular_playlist_urls = [
            # Playlist ufficiali Spotify più popolari
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",  # Today's Top Hits
            "https://open.spotify.com/playlist/37i9dQZF1DX0XUsuxWHRQd",  # RapCaviar  
            "https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n",  # mint
            "https://open.spotify.com/playlist/37i9dQZF1DX1lVhptIYRda",  # Hot Country
            "https://open.spotify.com/playlist/37i9dQZF1DX4JAvHpjipBk",  # New Music Friday
            "https://open.spotify.com/playlist/37i9dQZF1DX10zKzsJ2jva",  # Viva Latino
            "https://open.spotify.com/playlist/37i9dQZF1DX0XUfTFmNBRM",  # Beast Mode
            "https://open.spotify.com/playlist/37i9dQZF1DWZeKCadgRdKQ",  # Deep House Relax
            "https://open.spotify.com/playlist/37i9dQZF1DX4sWSpwq3LiO",  # Peaceful Piano
            "https://open.spotify.com/playlist/37i9dQZF1DX7K31D69s4M1",  # Coffee Shop Pop
            "https://open.spotify.com/playlist/37i9dQZF1DXbTxeAdrVG2l",  # Good Vibes
            "https://open.spotify.com/playlist/37i9dQZF1DX6GGaYjjNLTU",  # Feel Good Pop
            "https://open.spotify.com/playlist/37i9dQZF1DXdPec7aLTmlC",  # Happy Hits!
            "https://open.spotify.com/playlist/37i9dQZF1DX3rxVfibe1L0",  # Mood Booster
            "https://open.spotify.com/playlist/37i9dQZF1DWWEJlAGA9gs0",  # Classical Essentials
            "https://open.spotify.com/playlist/37i9dQZF1DX0Tkc6CjGZVG",  # Power Workout
            "https://open.spotify.com/playlist/37i9dQZF1DX32NsLKyzScr",  # Workout
            "https://open.spotify.com/playlist/37i9dQZF1DX2Nc3B70tvx0",  # Cardio
            "https://open.spotify.com/playlist/37i9dQZF1DX76Wlfdnj7AP",  # Beast Mode
            "https://open.spotify.com/playlist/37i9dQZF1DXcF6B6QPhFDv",  # Rock Classics
            "https://open.spotify.com/playlist/37i9dQZF1DX1spT6G94GFC",  # State of Jazz
            "https://open.spotify.com/playlist/37i9dQZF1DX4WYpdgoIcn6",  # Chill Hits
            "https://open.spotify.com/playlist/37i9dQZF1DWWQRwui0ExPn",  # Lo-Fi Beats
            "https://open.spotify.com/playlist/37i9dQZF1DX8Uebhn9wzrS",  # Chill Pop
            "https://open.spotify.com/playlist/37i9dQZF1DWUa8ZRTfalHk",  # Pop Rising
            "https://open.spotify.com/playlist/37i9dQZF1DXcZDD7cfEKhW",  # Pop Right Now
            "https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd",  # All Out 2010s
            "https://open.spotify.com/playlist/37i9dQZF1DX5Ejj0EkURtP",  # All Out 2000s
            "https://open.spotify.com/playlist/37i9dQZF1DX4UtSsGT1Sbe",  # All Out 90s
            "https://open.spotify.com/playlist/37i9dQZF1DX2FM32OyeuPf",  # All Out 80s
            # Playlist internazionali popolari
            "https://open.spotify.com/playlist/37i9dQZF1DX8FwnYE6PRvL",  # Songs to Sing in the Car
            "https://open.spotify.com/playlist/37i9dQZF1DX9GRpeH4CL0S",  # Songs to Sing in the Shower
            "https://open.spotify.com/playlist/37i9dQZF1DXaKIA8E7WcJj",  # Songs to Sing Along
            "https://open.spotify.com/playlist/37i9dQZF1DX0h0QnLkMBl4",  # Party Hits
            "https://open.spotify.com/playlist/37i9dQZF1DWYxwmBaMqxsl",  # Party Mix
            "https://open.spotify.com/playlist/37i9dQZF1DX8LmqAaPNsKl",  # Dance Hits
            "https://open.spotify.com/playlist/37i9dQZF1DWTqMVGhcgJgO",  # Dance Party
            "https://open.spotify.com/playlist/37i9dQZF1DXaXB8fQg7xif",  # Dance Pop
            "https://open.spotify.com/playlist/37i9dQZF1DX1HUbZS4LEyL",  # Indie Pop
            "https://open.spotify.com/playlist/37i9dQZF1DWWEcRhUVtL8n",  # Indie Rock
            "https://open.spotify.com/playlist/37i9dQZF1DXdbXVBClp5S5",  # Alternative Rock
            # Aggiungere playlist per diversi generi
            "https://open.spotify.com/playlist/37i9dQZF1DWXRqgorJj26U",  # Rock Hard
            "https://open.spotify.com/playlist/37i9dQZF1DX1rVvRgjX59F",  # Metal
            "https://open.spotify.com/playlist/37i9dQZF1DX5J7FIl4q56G",  # Punk
            "https://open.spotify.com/playlist/37i9dQZF1DX6uz91Ugaqed",  # Funk
            "https://open.spotify.com/playlist/37i9dQZF1DWVaHyi2LWMBo",  # Soul & R&B
            "https://open.spotify.com/playlist/37i9dQZF1DX2Nc3B70tvx0",  # Hip-Hop Central
            "https://open.spotify.com/playlist/37i9dQZF1DX48TTZL62Yht",  # Blues
            "https://open.spotify.com/playlist/37i9dQZF1DX6taq0OKJDHt",  # Country
            "https://open.spotify.com/playlist/37i9dQZF1DWXQFBkOKhEms"   # Reggae
        ]
        
        found_ids = set()
        successful_extractions = 0
        
        for url in popular_playlist_urls:
            if len(playlists) >= limit:
                break
                
            try:
                # Estrai l'ID dalla URL
                playlist_id = extract_spotify_playlist_id(url)
                if not playlist_id or playlist_id in found_ids:
                    continue
                    
                # Prova il metodo get_playlist_info della documentazione
                playlist_info = client.get_playlist_info(url)
                
                # Debug: stampa i dati ricevuti
                logging.debug(f"🔍 SpotifyScraper raw response for {url}: {playlist_info}")
                
                # Se get_playlist_info non funziona, continua alla prossima URL
                if not playlist_info:
                    logging.debug(f"⚠️ get_playlist_info non ha restituito dati per {url}")
                    continue
                
                # Processa la risposta se abbiamo dati
                if playlist_info:
                    found_ids.add(playlist_id)
                    
                    # Estrai i dati dalla risposta SpotifyScraper
                    # Basandoci sui test, SpotifyScraper restituisce: name, tracks, owner, images, etc.
                    playlist_name = playlist_info.get('name', f'Playlist {playlist_id}')
                    
                    # Usa paginazione per ottenere tutte le tracce disponibili (SpotifyScraper + API fallback)
                    playlist_tracks = get_all_playlist_tracks_with_fallback(client, sp, url, playlist_id, max_tracks=500)
                    
                    # Owner può essere un dict o stringa
                    owner_info = playlist_info.get('owner', {})
                    if isinstance(owner_info, dict):
                        playlist_owner = owner_info.get('name', 'Spotify')
                    else:
                        playlist_owner = str(owner_info) if owner_info else 'Spotify'
                    
                    # Conta le tracce e crea anteprima
                    track_count = len(playlist_tracks) if isinstance(playlist_tracks, list) else 0
                    preview_tracks = []
                    
                    # Crea anteprima tracce dalla risposta SpotifyScraper
                    if isinstance(playlist_tracks, list) and len(playlist_tracks) > 0:
                        for track in playlist_tracks[:3]:
                            if isinstance(track, dict):
                                track_name = track.get('name', '')
                                # Artists può essere una lista
                                artists = track.get('artists', [])
                                if isinstance(artists, list) and len(artists) > 0:
                                    artist_name = artists[0].get('name', 'Unknown Artist')
                                else:
                                    artist_name = 'Unknown Artist'
                                
                                if track_name and artist_name:
                                    preview_tracks.append(f"{track_name} - {artist_name}")
                                elif track_name:
                                    preview_tracks.append(track_name)
                    
                    # Estrai URL immagine se disponibile
                    playlist_image = ""
                    images = playlist_info.get('images', [])
                    if isinstance(images, list) and len(images) > 0:
                        # Prendi l'immagine di dimensione media se disponibile
                        if len(images) >= 2:
                            playlist_image = images[1].get('url', '')
                        else:
                            playlist_image = images[0].get('url', '')
                    
                    # Categorizzazione intelligente basata sul nome della playlist
                    playlist_category = "popular"  # Default
                    name_lower = playlist_name.lower()
                    description_lower = playlist_info.get('description', '').lower()
                    
                    # Logica di categorizzazione avanzata
                    if any(word in name_lower for word in ['chart', 'top', 'hits', 'viral', 'trending', 'billboard']):
                        playlist_category = "charts"
                    elif any(word in name_lower for word in ['new music', 'fresh', 'discover', 'emerging', 'friday']):
                        playlist_category = "editorial"  # New Music Friday è editoriale
                    elif any(word in name_lower for word in ['workout', 'chill', 'focus', 'party', 'mood', 'peaceful', 'relax', 'energy', 'motivation', 'study', 'sleep', 'meditation']):
                        playlist_category = "thematic"
                    elif any(word in name_lower for word in ['rock', 'jazz', 'pop', 'country', 'hip hop', 'electronic', 'classical', 'blues', 'reggae', 'metal', 'punk', 'folk', 'soul', 'funk']):
                        playlist_category = "genre"
                    elif any(word in name_lower for word in ['rapcaviar', 'mint', 'pollen', 'lorem', 'beast mode', 'state of']):
                        playlist_category = "editorial"
                    elif any(word in name_lower for word in ['80s', '90s', '2000s', '2010s', '2020s', 'decade', 'year', 'all out']):
                        playlist_category = "genre"
                    elif any(word in name_lower for word in ['sing in', 'sing along', 'car', 'shower', 'dance', 'party']):
                        playlist_category = "thematic"
                    # Se contiene il nome dell'owner o è ufficiale Spotify
                    elif 'spotify' in playlist_owner.lower():
                        playlist_category = "editorial"
                    
                    # Arricchisci con dati Spotify API se possibile
                    enhanced_playlist = enrich_playlist_with_spotify_api(sp, playlist_id, playlist_info)
                    
                    # Normalizza i dati con fallback agli originali
                    normalized_playlist = {
                        "id": playlist_id,
                        "name": "🌐 " + (enhanced_playlist.get("name") or playlist_name),
                        "description": enhanced_playlist.get("description") or playlist_info.get("description", "") or f"Playlist SpotifyScraper con {track_count} tracce - categoria: {playlist_category}",
                        "poster": enhanced_playlist.get("poster") or playlist_image,
                        "track_count": enhanced_playlist.get("track_count") or track_count,
                        "playlist_type": playlist_category,  # Usa la categoria rilevata invece di "popular"
                        "preview_tracks": enhanced_playlist.get("preview_tracks") or preview_tracks,
                        "owner": enhanced_playlist.get("owner") or playlist_owner,
                        "public": True,
                        "collaborative": False,
                        "followers": enhanced_playlist.get("followers", 0)
                    }
                    
                    playlists.append(normalized_playlist)
                    successful_extractions += 1
                    logging.info(f"✅ SpotifyScraper: estratta playlist '{playlist_name}' - {track_count} tracce - categoria: {playlist_category}")
                        
            except Exception as url_error:
                logging.debug(f"⚠️ Errore SpotifyScraper per URL {url}: {url_error}")
                continue
        
        # SpotifyScraper non supporta la ricerca, ma abbiamo una lista curata di URL
        # Se non abbiamo trovato abbastanza playlist, log del risultato
        if len(playlists) < limit:
            logging.info(f"🔍 SpotifyScraper: trovate {len(playlists)}/{limit} playlist richieste. Lista curata completata.")
        
        # Chiudi il client
        try:
            client.close()
        except:
            pass  # Ignora errori di chiusura
        
        if playlists:
            logging.info(f"🎉 SpotifyScraper: trovate {len(playlists)} playlist popolari ({successful_extractions} estrazioni dirette)")
            
            # Aggiorna macro-categorie per playlist esistenti se necessario
            if len(playlists) > 0:
                try:
                    from .database import update_existing_playlists_macro_categories
                    updated_categories = update_existing_playlists_macro_categories()
                    if updated_categories > 0:
                        logging.info(f"🏷️ Aggiornate {updated_categories} macro-categorie per playlist esistenti")
                except Exception as cat_error:
                    logging.warning(f"⚠️ Errore aggiornamento macro-categorie: {cat_error}")
        else:
            logging.warning("⚠️ SpotifyScraper: nessuna playlist trovata")
            
        return playlists[:limit]
        
    except ImportError:
        logging.debug("SpotifyScraper non disponibile - installare con: pip install spotifyscraper[selenium]")
        return []
    except Exception as e:
        logging.warning(f"⚠️ Errore SpotifyScraper: {e}")
        return []

def get_spotify_popular_playlists(sp: spotipy.Spotify, country: str = 'IT', limit: int = 50, max_retries: int = 2) -> List[dict]:
    """
    Funzione principale che prova prima la ricerca curata, poi il web scraping se abilitato.
    """
    # Controlla se il web scraping è abilitato via variabile d'ambiente
    enable_webscraping = os.getenv('SPOTIFY_ENABLE_WEBSCRAPING', 'false').lower() == 'true'
    
    # Prova sempre prima il metodo curato che è più affidabile
    curated_results = get_spotify_popular_playlists_curated(sp, country, limit, max_retries)
    
    if enable_webscraping and len(curated_results) < limit:
        logging.info("🌐 Web scraping abilitato, tentativo di trovare playlist popolari aggiuntive...")
        try:
            webscraping_results = get_spotify_popular_playlists_webscraping(sp, country, limit - len(curated_results))
            if webscraping_results:
                # Combina i risultati, evitando duplicati
                existing_ids = {p['id'] for p in curated_results}
                for playlist in webscraping_results:
                    if playlist['id'] not in existing_ids:
                        curated_results.append(playlist)
                logging.info(f"✅ Web scraping: aggiunte {len(webscraping_results)} playlist aggiuntive")
        except Exception as e:
            logging.warning(f"⚠️ Web scraping fallito, continuando con risultati curati: {e}")
    
    return curated_results

def discover_all_spotify_content(sp: spotipy.Spotify, user_id: str, country: str = 'IT') -> dict:
    """
    Funzione unificata per scoprire tutto il contenuto Spotify disponibile con gestione errori robusta.

    Args:
        sp: Oggetto Spotify autenticato
        user_id: ID utente per playlist personali
        country: Codice paese per contenuto localizzato

    Returns:
        Dict con tutte le categorie di contenuto Spotify
    """
    logging.info(f"🔍 Inizio discovery contenuto Spotify completo per utente {user_id}")

    spotify_content = {
        'user_playlists': [],
        'featured': [],
        'popular': [],
        'categories': {},
        'discovery_stats': {
            'total_attempts': 0,
            'successful_categories': [],
            'failed_categories': [],
            'unavailable_categories': []
        }
    }

    try:
        # Playlist utente con metadati completi
        if user_id:
            try:
                spotify_content['user_playlists'] = discover_spotify_user_playlists_enhanced(sp, user_id)
            except Exception as user_error:
                logging.error(f"❌ Errore durante discovery playlist utente: {user_error}")

        # Playlist in evidenza con fallback
        try:
            spotify_content['featured'] = get_spotify_featured_playlists(sp, country)
        except Exception as featured_error:
            logging.warning(f"⚠️ Playlist in evidenza non disponibili: {featured_error}")
            # Fallback: prova senza specificare il paese
            try:
                spotify_content['featured'] = get_spotify_featured_playlists(sp, country=None)
                logging.info("✅ Fallback: playlist in evidenza globali recuperate")
            except Exception:
                logging.info("ℹ️ Playlist in evidenza non disponibili in questa regione")

        # Playlist popolari con fallback
        try:
            spotify_content['popular'] = get_spotify_popular_playlists(sp, country, limit=50)
        except Exception as popular_error:
            logging.warning(f"⚠️ Playlist popolari non disponibili: {popular_error}")
            # Fallback: prova senza specificare il paese
            try:
                spotify_content['popular'] = get_spotify_popular_playlists(sp, country=None, limit=30)
                logging.info("✅ Fallback: playlist popolari globali recuperate")
            except Exception:
                logging.info("ℹ️ Playlist popolari non disponibili in questa regione")

        # ======= NUOVA PARTE: Categorie dinamiche con fallback =======
        def get_spotify_categories(sp, country='IT'):
            try:
                categories_resp = sp.categories(country=country, limit=50)
                return categories_resp['categories']['items']
            except Exception as e:
                logging.warning(f"Errore nel recupero categorie per {country}: {e}")
                if country != 'US':
                    return get_spotify_categories(sp, country='US')
                else:
                    return []

        categories = get_spotify_categories(sp, country)

        for cat in categories:
            cat_id = cat['id']
            cat_name = cat['name']
            spotify_content['discovery_stats']['total_attempts'] += 1

            playlists = []
            try:
                playlists = get_spotify_category_playlists(sp, cat_id, country, limit=5)
                if not playlists:
                    # Fallback su US se nessuna playlist trovata
                    try:
                        playlists = get_spotify_category_playlists(sp, cat_id, 'US', limit=5)
                    except Exception as e2:
                        logging.warning(f"Errore anche in US per {cat_name}: {e2}")
                        playlists = []
            except Exception as e:
                logging.warning(f"Errore per {cat_name} in {country}: {e}")
                # In caso di errore generale, prova subito US
                try:
                    playlists = get_spotify_category_playlists(sp, cat_id, 'US', limit=5)
                except Exception as e2:
                    logging.warning(f"Errore anche in US per {cat_name}: {e2}")
                    playlists = []

            if playlists:
                spotify_content['categories'][cat_id] = playlists
                spotify_content['discovery_stats']['successful_categories'].append(cat_name)
            else:
                spotify_content['discovery_stats']['unavailable_categories'].append(cat_name)

        # Calcola totale e statistiche
        total_user = len(spotify_content['user_playlists'])
        total_featured = len(spotify_content['featured'])
        total_popular = len(spotify_content['popular'])
        total_categories = sum(len(playlists) for playlists in spotify_content['categories'].values())
        total_count = total_user + total_featured + total_popular + total_categories

        total_cats = len(categories) if categories else 1  # Evita divisione per zero
        success_rate = len(spotify_content['discovery_stats']['successful_categories']) / total_cats * 100

        logging.info(f"🎉 Discovery Spotify completato: {total_count} elementi trovati")
        logging.info(f"   👤 Playlist utente: {total_user}")
        logging.info(f"   ✨ Playlist curate: {total_featured}")
        logging.info(f"   🔥 Playlist popolari: {total_popular}")
        logging.info(f"   🎯 Playlist categorie: {total_categories}")
        logging.info(f"   📊 Tasso successo categorie: {success_rate:.1f}% ({len(spotify_content['discovery_stats']['successful_categories'])}/{total_cats})")

        if spotify_content['discovery_stats']['unavailable_categories']:
            logging.info(f"   📍 Categorie non disponibili per {country}: {', '.join(spotify_content['discovery_stats']['unavailable_categories'])}")

        return spotify_content

    except Exception as e:
        logging.error(f"❌ Errore critico durante discovery contenuto Spotify: {e}")
        return spotify_content



