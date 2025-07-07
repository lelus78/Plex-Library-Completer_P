#!/usr/bin/env python3
"""
Script per aggiornamento incrementale del database Plex
Aggiunge solo gli artisti/tracce mancanti invece di ricreare tutto
"""
import os
import sys
import logging
from datetime import datetime
from typing import List, Dict, Set, Tuple
from plexapi.server import PlexServer
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Add the project root to Python path
sys.path.insert(0, '/app')

from plex_playlist_sync.utils.database import (
    DatabasePool, 
    _clean_string,
    bulk_add_tracks_to_index
)

def get_indexed_artists() -> Set[str]:
    """Ottiene tutti gli artisti già indicizzati nel database"""
    db_path = os.path.join('/app/state_data', 'sync_database.db')
    db_pool = DatabasePool(db_path)
    
    with db_pool.get_connection_context() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT artist_clean FROM plex_library_index')
        results = cursor.fetchall()
        
    indexed_artists = {row[0] for row in results}
    logger.info(f"📊 Artisti attualmente indicizzati: {len(indexed_artists)}")
    return indexed_artists

def get_indexed_tracks() -> Set[Tuple[str, str, str]]:
    """Ottiene tutte le tracce già indicizzate (artista, album, titolo)"""
    db_path = os.path.join('/app/state_data', 'sync_database.db')
    db_pool = DatabasePool(db_path)
    
    with db_pool.get_connection_context() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT artist_clean, album_clean, title_clean FROM plex_library_index')
        results = cursor.fetchall()
        
    indexed_tracks = {(row[0], row[1] or '', row[2]) for row in results}
    logger.info(f"📊 Tracce attualmente indicizzate: {len(indexed_tracks)}")
    return indexed_tracks

def find_missing_artists(plex_server: PlexServer, library_name: str = "Musica") -> List[str]:
    """Trova artisti presenti in Plex ma non nel database"""
    try:
        music_library = plex_server.library.section(library_name)
        logger.info(f"🔍 Connesso alla libreria: {library_name}")
        
        # Ottieni tutti gli artisti da Plex
        logger.info("📥 Caricamento artisti da Plex...")
        plex_artists = music_library.searchArtists()
        plex_artist_names = {_clean_string(artist.title) for artist in plex_artists}
        logger.info(f"📊 Artisti trovati in Plex: {len(plex_artist_names)}")
        
        # Ottieni artisti già indicizzati
        indexed_artists = get_indexed_artists()
        
        # Trova artisti mancanti
        missing_artists = plex_artist_names - indexed_artists
        logger.info(f"🔍 Artisti mancanti nel database: {len(missing_artists)}")
        
        return sorted(missing_artists)
        
    except Exception as e:
        logger.error(f"❌ Errore durante la ricerca artisti: {e}")
        return []

def add_missing_tracks_for_artists(plex_server: PlexServer, artist_names: List[str], library_name: str = "Musica") -> int:
    """Aggiunge tutte le tracce degli artisti mancanti"""
    if not artist_names:
        logger.info("✅ Nessun artista mancante da aggiungere")
        return 0
    
    try:
        music_library = plex_server.library.section(library_name)
        indexed_tracks = get_indexed_tracks()
        new_tracks = []
        
        for artist_name in artist_names:
            logger.info(f"🔍 Processando artista: {artist_name}")
            
            # Cerca l'artista in Plex
            artists = music_library.searchArtists(title=artist_name)
            
            for artist in artists:
                if _clean_string(artist.title) == artist_name:
                    logger.info(f"✅ Trovato artista: {artist.title}")
                    
                    # Ottieni tutti gli album dell'artista
                    albums = artist.albums()
                    logger.info(f"📀 Album trovati: {len(albums)}")
                    
                    for album in albums:
                        # Ottieni tutte le tracce dell'album
                        tracks = album.tracks()
                        
                        for track in tracks:
                            # Crea chiave univoca per la traccia
                            track_key = (
                                _clean_string(track.grandparentTitle or track.artist().title),
                                _clean_string(track.parentTitle or track.album().title),
                                _clean_string(track.title)
                            )
                            
                            # Aggiungi solo se non già presente
                            if track_key not in indexed_tracks:
                                new_tracks.append({
                                    'title_clean': track_key[2],
                                    'artist_clean': track_key[0],
                                    'album_clean': track_key[1],
                                    'year': getattr(track, 'year', None) or getattr(track.album(), 'year', None),
                                    'added_at': datetime.now().isoformat()
                                })
                                indexed_tracks.add(track_key)  # Evita duplicati
                                
                                if len(new_tracks) % 100 == 0:
                                    logger.info(f"📊 Tracce da aggiungere: {len(new_tracks)}")
                    
                    break  # Artista trovato, esci dal loop
            else:
                logger.warning(f"⚠️ Artista non trovato in Plex: {artist_name}")
        
        # Inserisci tutte le nuove tracce usando SQL diretto
        if new_tracks:
            logger.info(f"💾 Inserimento {len(new_tracks)} nuove tracce nel database...")
            
            db_path = os.path.join('/app/state_data', 'sync_database.db')
            db_pool = DatabasePool(db_path)
            
            inserted_count = 0
            with db_pool.get_connection_context() as conn:
                cursor = conn.cursor()
                
                for track in new_tracks:
                    try:
                        cursor.execute('''
                            INSERT OR IGNORE INTO plex_library_index 
                            (title_clean, artist_clean, album_clean, year, added_at) 
                            VALUES (?, ?, ?, ?, ?)
                        ''', (
                            track['title_clean'],
                            track['artist_clean'], 
                            track['album_clean'],
                            track['year'],
                            track['added_at']
                        ))
                        if cursor.rowcount > 0:
                            inserted_count += 1
                    except Exception as e:
                        logger.error(f"❌ Errore inserimento traccia {track['title_clean']}: {e}")
            
            logger.info(f"✅ Tracce inserite con successo: {inserted_count}/{len(new_tracks)}")
            return inserted_count
        else:
            logger.info("✅ Nessuna nuova traccia da aggiungere")
            return 0
            
    except Exception as e:
        logger.error(f"❌ Errore durante l'aggiunta delle tracce: {e}")
        return 0

def fix_artist_names_in_missing_tracks():
    """Corregge i nomi degli artisti errati nel database missing_tracks"""
    logger.info("🔧 Correzione nomi artisti in missing_tracks...")
    
    db_path = os.path.join('/app/state_data', 'sync_database.db')
    db_pool = DatabasePool(db_path)
    
    corrections = [
        ("Soprano  Molly Grace", "Molly Grace"),  # Rimuovi "Soprano" e doppio spazio
        ("soprano molly grace", "Molly Grace"),   # Correggi case
        # Aggiungi altre correzioni qui se necessario
    ]
    
    with db_pool.get_connection_context() as conn:
        cursor = conn.cursor()
        total_corrections = 0
        
        for wrong_name, correct_name in corrections:
            cursor.execute(
                "UPDATE missing_tracks SET artist = ? WHERE artist = ?",
                (correct_name, wrong_name)
            )
            corrected = cursor.rowcount
            if corrected > 0:
                logger.info(f"✅ Corretto '{wrong_name}' → '{correct_name}' ({corrected} tracce)")
                total_corrections += corrected
        
        logger.info(f"🔧 Totale correzioni: {total_corrections}")
        return total_corrections

def main():
    """Funzione principale per l'aggiornamento incrementale"""
    logger.info("🚀 Avvio aggiornamento incrementale database")
    
    # Configurazione Plex
    plex_url = os.getenv('PLEX_URL', 'http://192.168.1.100:32400')
    plex_token = os.getenv('PLEX_TOKEN')
    library_name = os.getenv('LIBRARY_NAME', 'Musica')
    
    if not plex_token:
        logger.error("❌ PLEX_TOKEN non configurato")
        return
    
    try:
        # Connessione a Plex
        logger.info(f"🔌 Connessione a Plex: {plex_url}")
        plex = PlexServer(plex_url, plex_token)
        logger.info("✅ Connesso a Plex")
        
        # 1. Correggi nomi artisti errati
        fix_artist_names_in_missing_tracks()
        
        # 2. Trova artisti mancanti
        missing_artists = find_missing_artists(plex, library_name)
        
        if missing_artists:
            logger.info(f"🔍 Artisti mancanti trovati: {missing_artists}")
            
            # 3. Aggiungi tracce degli artisti mancanti
            added_tracks = add_missing_tracks_for_artists(plex, missing_artists, library_name)
            logger.info(f"✅ Aggiornamento completato: {added_tracks} tracce aggiunte")
        else:
            logger.info("✅ Tutti gli artisti sono già indicizzati")
            
    except Exception as e:
        logger.error(f"❌ Errore durante l'aggiornamento: {e}")
        raise

if __name__ == "__main__":
    main()