import logging
from typing import List
import os
import time

import spotipy
from spotipy.exceptions import SpotifyException
from plexapi.server import PlexServer

from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist


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
        
        # Playlist per categorie popolari con gestione intelligente dei fallimenti
        popular_categories = ['toplists', 'pop', 'rock', 'hip-hop', 'electronic', 'jazz', 'classical', 'indie', 'country', 'latin']
        
        for category in popular_categories:
            spotify_content['discovery_stats']['total_attempts'] += 1
            
            category_playlists = get_spotify_category_playlists(sp, category, country, limit=5)
            
            if category_playlists:
                spotify_content['categories'][category] = category_playlists
                spotify_content['discovery_stats']['successful_categories'].append(category)
            elif category in ['toplists', 'pop', 'rock']:  # Categorie prioritarie, prova fallback
                # Prova senza specificare il paese per categorie importanti
                try:
                    fallback_playlists = get_spotify_category_playlists(sp, category, country=None, limit=3)
                    if fallback_playlists:
                        spotify_content['categories'][category] = fallback_playlists
                        spotify_content['discovery_stats']['successful_categories'].append(f"{category}_global")
                        logging.info(f"✅ Fallback globale per categoria '{category}' riuscito")
                    else:
                        spotify_content['discovery_stats']['unavailable_categories'].append(category)
                except Exception:
                    spotify_content['discovery_stats']['failed_categories'].append(category)
            else:
                spotify_content['discovery_stats']['unavailable_categories'].append(category)
        
        # Calcola totale e statistiche
        total_user = len(spotify_content['user_playlists'])
        total_featured = len(spotify_content['featured'])
        total_categories = sum(len(playlists) for playlists in spotify_content['categories'].values())
        total_count = total_user + total_featured + total_categories
        
        success_rate = len(spotify_content['discovery_stats']['successful_categories']) / len(popular_categories) * 100
        
        logging.info(f"🎉 Discovery Spotify completato: {total_count} elementi trovati")
        logging.info(f"   👤 Playlist utente: {total_user}")
        logging.info(f"   ✨ Playlist curate: {total_featured}")
        logging.info(f"   🎯 Playlist categorie: {total_categories}")
        logging.info(f"   📊 Tasso successo categorie: {success_rate:.1f}% ({len(spotify_content['discovery_stats']['successful_categories'])}/{len(popular_categories)})")
        
        if spotify_content['discovery_stats']['unavailable_categories']:
            logging.info(f"   📍 Categorie non disponibili per {country}: {', '.join(spotify_content['discovery_stats']['unavailable_categories'])}")
        
        return spotify_content
        
    except Exception as e:
        logging.error(f"❌ Errore critico durante discovery contenuto Spotify: {e}")
        return spotify_content
