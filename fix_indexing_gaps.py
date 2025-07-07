#!/usr/bin/env python3
"""
Fix per gap di indicizzazione - Rileva e corregge tracce/album mancanti dal database
Questo script confronta Plex con il database locale e aggiunge le tracce mancanti
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
from plexapi.server import PlexServer

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_plex_connection():
    """Connessione a Plex"""
    plex_url = os.getenv('PLEX_URL')
    plex_token = os.getenv('PLEX_TOKEN')
    
    if not plex_url or not plex_token:
        raise ValueError("PLEX_URL e PLEX_TOKEN devono essere configurati")
        
    return PlexServer(plex_url, plex_token)

def get_database_connection():
    """Connessione al database"""
    db_path = '/app/state_data/sync_database.db'
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database non trovato: {db_path}")
        
    return sqlite3.connect(db_path)

def get_artists_from_plex(plex_server):
    """Ottiene tutti gli artisti da Plex"""
    logger.info("🔍 Recuperando artisti da Plex...")
    music_section = plex_server.library.section('Musica')
    artists = music_section.search(libtype='artist')
    logger.info(f"📊 Trovati {len(artists)} artisti in Plex")
    return artists

def get_artists_from_database(db_conn):
    """Ottiene artisti unici dal database"""
    cursor = db_conn.cursor()
    cursor.execute('SELECT DISTINCT artist_clean FROM plex_library_index')
    db_artists = {row[0] for row in cursor.fetchall()}
    logger.info(f"📊 Trovati {len(db_artists)} artisti nel database")
    return db_artists

def find_missing_artists(plex_artists, db_artists):
    """Trova artisti presenti in Plex ma mancanti dal database"""
    missing_artists = []
    
    for artist in plex_artists:
        artist_clean = artist.title.lower().strip()
        if artist_clean not in db_artists:
            missing_artists.append(artist)
    
    logger.info(f"❌ Trovati {len(missing_artists)} artisti mancanti dal database")
    return missing_artists

def add_artist_tracks_to_database(artist, db_conn):
    """Aggiunge tutte le tracce di un artista al database"""
    cursor = db_conn.cursor()
    added_count = 0
    
    logger.info(f"🎵 Processando artista: {artist.title}")
    
    try:
        albums = artist.albums()
        for album in albums:
            logger.info(f"  📀 Album: {album.title} ({album.year})")
            
            tracks = album.tracks()
            for track in tracks:
                title_clean = track.title.lower().strip()
                artist_clean = track.grandparentTitle.lower().strip()
                album_clean = track.parentTitle.lower().strip()
                
                # Verifica se esiste già
                cursor.execute('''
                    SELECT COUNT(*) FROM plex_library_index 
                    WHERE title_clean = ? AND artist_clean = ? AND album_clean = ?
                ''', (title_clean, artist_clean, album_clean))
                
                if cursor.fetchone()[0] == 0:
                    logger.info(f"    ➕ Aggiungendo: {track.title}")
                    cursor.execute('''
                        INSERT INTO plex_library_index 
                        (title_clean, artist_clean, album_clean, year)
                        VALUES (?, ?, ?, ?)
                    ''', (title_clean, artist_clean, album_clean, album.year))
                    added_count += 1
                else:
                    logger.debug(f"    ✅ Già presente: {track.title}")
                    
    except Exception as e:
        logger.error(f"❌ Errore processando artista {artist.title}: {e}")
    
    return added_count

def find_incomplete_artists(plex_server, db_conn):
    """Trova artisti che potrebbero avere tracce mancanti"""
    logger.info("🔍 Cercando artisti con possibili tracce mancanti...")
    
    music_section = plex_server.library.section('Musica')
    cursor = db_conn.cursor()
    
    # Ottiene artisti dal database con conteggio tracce
    cursor.execute('''
        SELECT artist_clean, COUNT(*) as track_count 
        FROM plex_library_index 
        GROUP BY artist_clean
    ''')
    
    db_artist_counts = {row[0]: row[1] for row in cursor.fetchall()}
    incomplete_artists = []
    
    # Confronta con Plex
    for artist_name, db_count in db_artist_counts.items():
        try:
            # Cerca l'artista in Plex
            plex_artists = music_section.search(**{'artist.title': artist_name})
            if plex_artists:
                plex_artist = plex_artists[0]
                
                # Conta le tracce reali in Plex
                total_tracks = 0
                for album in plex_artist.albums():
                    total_tracks += len(album.tracks())
                
                if total_tracks > db_count:
                    incomplete_artists.append((plex_artist, db_count, total_tracks))
                    logger.info(f"⚠️ {artist_name}: DB={db_count} tracce, Plex={total_tracks} tracce")
                    
        except Exception as e:
            logger.debug(f"Errore verificando {artist_name}: {e}")
    
    logger.info(f"❌ Trovati {len(incomplete_artists)} artisti con tracce mancanti")
    return incomplete_artists

def main():
    """Funzione principale"""
    logger.info("🚀 Avvio fix gap indicizzazione")
    
    try:
        # Connessioni
        plex = get_plex_connection()
        db_conn = get_database_connection()
        
        total_added = 0
        
        # Step 1: Trova artisti completamente mancanti
        logger.info("\n=== STEP 1: ARTISTI MANCANTI ===")
        plex_artists = get_artists_from_plex(plex)
        db_artists = get_artists_from_database(db_conn)
        missing_artists = find_missing_artists(plex_artists, db_artists)
        
        for artist in missing_artists:
            added = add_artist_tracks_to_database(artist, db_conn)
            total_added += added
        
        # Step 2: Trova artisti con tracce incomplete 
        logger.info("\n=== STEP 2: ARTISTI INCOMPLETI ===")
        incomplete_artists = find_incomplete_artists(plex, db_conn)
        
        for plex_artist, db_count, plex_count in incomplete_artists:
            logger.info(f"🔧 Completando {plex_artist.title}...")
            added = add_artist_tracks_to_database(plex_artist, db_conn)
            total_added += added
        
        # Commit delle modifiche
        db_conn.commit()
        logger.info(f"\n✅ Fix completato! Aggiunte {total_added} tracce al database")
        
        # Statistiche finali
        cursor = db_conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM plex_library_index')
        final_count = cursor.fetchone()[0]
        logger.info(f"📊 Totale tracce nel database: {final_count}")
        
    except Exception as e:
        logger.error(f"❌ Errore durante il fix: {e}")
        sys.exit(1)
    finally:
        if 'db_conn' in locals():
            db_conn.close()

if __name__ == "__main__":
    main()