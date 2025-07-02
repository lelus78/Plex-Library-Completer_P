import logging
import re
from typing import List

from plexapi.exceptions import NotFound
from plexapi.server import PlexServer
from plexapi.audio import Track as PlexTrack
from thefuzz import fuzz

from .helperClasses import Playlist, Track, UserInputs
from .database import add_missing_track, check_track_in_index

def _clean_string_for_search(text: str) -> str:
    """Funzione di pulizia standard per la ricerca, rimuove caratteri speciali e parentesi."""
    if not text:
        return ""
    text = re.sub(r"\(.*?\)|\[.*?\]", "", text).strip()
    text = re.sub(r"(feat\.|ft\.).*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r'[^\w\s\-\']', '', text).strip()
    return text

def search_plex_track(plex: PlexServer, track: Track, limit: int = 10) -> "plexapi.audio.Track | None":
    """
    Cerca una singola traccia su Plex. È la funzione principale di ricerca.
    """
    # Controllo preliminare: verifica se l'indice locale è popolato
    from .database import check_track_in_index
    if check_track_in_index(track.title, track.artist):
        logging.debug(f"Traccia '{track.title}' - '{track.artist}' trovata nell'indice locale, ora cerco oggetto Plex...")
    
    cleaned_title = _clean_string_for_search(track.title)
    cleaned_artist = _clean_string_for_search(track.artist)
    
    # Debug per capire cosa stiamo cercando
    logging.debug(f"Ricerca Plex: Originale='{track.title}' - '{track.artist}' | Pulito='{cleaned_title}' - '{cleaned_artist}'")

    queries = []
    if cleaned_artist.lower() == "various artists":
        queries.append(cleaned_title)
    else:
        queries.append(f"{cleaned_title} {cleaned_artist}")
        queries.append(cleaned_title)

    best_match = None
    best_score = 0
    best_query = None

    for query in queries:
        if not query.strip():
            continue
        try:
            search_results = plex.search(query, mediatype='track', limit=limit)
            logging.debug(f"Ricerca Plex per query '{query}': {len(search_results)} risultati")
            for result in search_results:
                if not isinstance(result, PlexTrack):
                    continue
                result_artist_clean = _clean_string_for_search(result.grandparentTitle)
                result_title_clean = _clean_string_for_search(result.title)
                
                title_score = fuzz.token_set_ratio(cleaned_title, result_title_clean)
                artist_score = fuzz.token_set_ratio(cleaned_artist, result_artist_clean)
                overall_score = (title_score + artist_score) / 2

                if overall_score > best_score:
                    best_score = overall_score
                    best_match = result
                    best_query = query
        except Exception as e:
            logging.error(f"Errore durante la ricerca Plex con query '{query}': {e}")
            continue

    threshold = 75
    if best_score >= threshold and isinstance(best_match, PlexTrack):
        logging.info(f"Track found via API: {best_match.title} - {best_match.grandparentTitle} (score: {best_score}, query: '{best_query}')")
        return best_match
    else:
        if best_score == 0:
            logging.warning(f"❌ Nessun risultato da ricerca Plex per: {track.title} - {track.artist} - Possibile problema indice libreria!")
        else:
            logging.info(f"Track not found via API for: {track.title} - {track.artist} (best score: {best_score})")
        return None

def _get_available_plex_tracks(plex: PlexServer, tracks: List[Track]) -> tuple[list, list]:
    """Trova le tracce Plex corrispondenti."""
    plex_tracks, potentially_missing = [], []
    for track in tracks:
        if check_track_in_index(track.title, track.artist):
            logging.info(f"De-duplicazione: Traccia '{track.title}' trovata nell'indice. Cerco l'oggetto Plex...")
        
        plex_track_obj = search_plex_track(plex, track)
        if plex_track_obj:
            plex_tracks.append(plex_track_obj)
        else:
            potentially_missing.append(track)
    return plex_tracks, potentially_missing

def _update_plex_playlist(plex: PlexServer, available_tracks: List, playlist: Playlist, append_mode: bool = False) -> "Optional[plexapi.playlist.Playlist]":
    """
    MODIFICATA: Cerca una playlist esistente per nome. Se la trova E NON ha tag NO_DELETE, la aggiorna.
    Se non la trova, ne crea una nuova. Playlist con tag NO_DELETE vengono solo lette.
    append_mode: Se True, aggiunge tracce alla playlist esistente invece di sostituirle
    """
    import os
    preserve_tag = os.getenv("PRESERVE_TAG", "NO_DELETE")
    
    logging.info(f"Gestione playlist: {playlist.name}")
    try:
        # Cerca la playlist per nome
        plex_playlist = plex.playlist(playlist.name)
        
        # CONTROLLO CRITICO: Verifica se la playlist ha il tag di protezione
        if preserve_tag.lower() in plex_playlist.title.lower():
            logging.warning(f"❌ Playlist '{playlist.name}' contiene tag '{preserve_tag}' - SOLO LETTURA, non verrà modificata!")
            return plex_playlist  # Restituisce la playlist esistente senza modificarla
        
        if append_mode:
            logging.info(f"Playlist '{playlist.name}' trovata. Modalità APPEND - aggiungendo {len(available_tracks)} nuove tracce...")
            
            # Controlla duplicati prima di aggiungere
            existing_tracks = plex_playlist.items()
            existing_track_keys = {track.ratingKey for track in existing_tracks}
            new_tracks = [track for track in available_tracks if track.ratingKey not in existing_track_keys]
            
            if new_tracks:
                plex_playlist.addItems(new_tracks)
                logging.info(f"Playlist '{playlist.name}' aggiornata con {len(new_tracks)} nuove tracce (evitati {len(available_tracks) - len(new_tracks)} duplicati).")
            else:
                logging.info(f"Nessuna nuova traccia da aggiungere alla playlist '{playlist.name}' (tutte già presenti).")
        else:
            logging.info(f"Playlist '{playlist.name}' trovata. Modalità SYNC - sostituendo con {len(available_tracks)} tracce...")
            # Rimuove tutte le tracce esistenti per fare un sync pulito
            plex_playlist.removeItems(plex_playlist.items())
            # Aggiunge le nuove tracce
            plex_playlist.addItems(available_tracks)
            logging.info(f"Playlist '{playlist.name}' sostituita con {len(available_tracks)} tracce.")
        return plex_playlist

    except NotFound:
        # Se non la trova, la crea
        logging.info(f"Playlist '{playlist.name}' non trovata. Creazione di una nuova playlist...")
        try:
            new_playlist = plex.createPlaylist(title=playlist.name, items=available_tracks)
            logging.info(f"Nuova playlist '{new_playlist.title}' creata con successo.")
            return new_playlist
        except Exception as e:
            logging.error(f"Errore critico durante la creazione della playlist '{playlist.name}': {e}")
            return None
    except Exception as e:
        logging.error(f"Errore imprevisto durante la gestione della playlist '{playlist.name}': {e}")
        return None

def update_or_create_plex_playlist(plex: PlexServer, playlist: Playlist, tracks: List[Track], userInputs: UserInputs, force_sync_mode: bool = False) -> "Optional[plexapi.playlist.Playlist]":
    """
    Crea o aggiorna una playlist Plex, salva le tracce mancanti e restituisce l'oggetto playlist creato.
    force_sync_mode: Se True, forza la modalità SYNC anche se userInputs.append_instead_of_sync è True
    """
    available_tracks, potentially_missing = _get_available_plex_tracks(plex, tracks)
    
    created_playlist = None
    if len(available_tracks) >= userInputs.plex_min_songs:
        # Determina la modalità: se force_sync_mode è True, usa SYNC (False), altrimenti usa la configurazione utente
        append_mode = userInputs.append_instead_of_sync and not force_sync_mode
        mode_desc = "SYNC" if not append_mode else "APPEND"
        logging.info(f"🔧 Playlist '{playlist.name}': usando modalità {mode_desc} (force_sync={force_sync_mode}, user_append={userInputs.append_instead_of_sync})")
        created_playlist = _update_plex_playlist(plex, available_tracks, playlist, append_mode)
    else:
        logging.warning(f"Creazione playlist '{playlist.name}' saltata: brani trovati insufficienti ({len(available_tracks)} < {userInputs.plex_min_songs}).")

    if created_playlist:
        playlist.id = created_playlist.ratingKey
        if playlist.description and userInputs.add_playlist_description:
            try:
                created_playlist.edit(summary=playlist.description)
            except Exception as e:
                logging.error(f"Failed to update description for playlist {playlist.name}: {e}")
        if playlist.poster and userInputs.add_playlist_poster:
            try:
                created_playlist.uploadPoster(url=playlist.poster)
            except Exception as e:
                logging.error(f"Failed to update poster for playlist {playlist.name}: {e}")
        logging.info(f"Updated playlist {playlist.name} with summary and poster.")

    if potentially_missing:
        truly_missing_tracks = []
        for track in potentially_missing:
            if not check_track_in_index(track.title, track.artist):
                truly_missing_tracks.append(track)
            else:
                logging.info(f"De-duplicazione finale: Traccia '{track.title}' non trovata da API ma PRESENTE nell'indice locale.")
        
        if truly_missing_tracks:
            logging.info(f"Trovate {len(truly_missing_tracks)} tracce veramente mancanti per '{playlist.name}'. Le aggiungo al database.")
            for track in truly_missing_tracks:
                track_data = {
                    'title': track.title,
                    'artist': track.artist,
                    'album': track.album,
                    'source_playlist_title': playlist.name,
                    'source_playlist_id': playlist.id 
                }
                add_missing_track(track_data)
    
    return created_playlist