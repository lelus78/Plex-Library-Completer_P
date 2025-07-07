#!/usr/bin/env python3
"""
Fix SICURO per gap di indicizzazione - Versione con commit batch
Evita lock del database usando transazioni piccole e frequenti commit
"""

import os
import sys
import sqlite3
import logging
import time
from datetime import datetime, timedelta
from plexapi.server import PlexServer

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # Commit ogni 50 tracce per evitare lock lunghi
SLEEP_BETWEEN_BATCHES = 1  # Pausa 1 secondo tra batch per dare respiro al database

def get_plex_connection():
    """Connessione a Plex"""
    plex_url = os.getenv('PLEX_URL')
    plex_token = os.getenv('PLEX_TOKEN')
    
    if not plex_url or not plex_token:
        raise ValueError("PLEX_URL e PLEX_TOKEN devono essere configurati")
        
    return PlexServer(plex_url, plex_token)

def get_database_connection():
    """Connessione al database con timeout breve"""
    db_path = '/app/state_data/sync_database.db'
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database non trovato: {db_path}")
        
    conn = sqlite3.connect(db_path, timeout=10.0)  # Timeout 10 secondi
    return conn

def safe_add_tracks_batch(tracks_data, db_conn):
    """Aggiunge tracce in batch sicuro con commit frequenti"""
    cursor = db_conn.cursor()
    added_count = 0
    
    for i, (title_clean, artist_clean, album_clean, year) in enumerate(tracks_data):
        try:
            # Verifica se esiste già
            cursor.execute('''
                SELECT COUNT(*) FROM plex_library_index 
                WHERE title_clean = ? AND artist_clean = ? AND album_clean = ?
            ''', (title_clean, artist_clean, album_clean))
            
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO plex_library_index 
                    (title_clean, artist_clean, album_clean, year)
                    VALUES (?, ?, ?, ?)
                ''', (title_clean, artist_clean, album_clean, year))
                added_count += 1
                logger.debug(f"    ➕ Aggiunta: {title_clean} - {artist_clean}")
            
            # Commit ogni BATCH_SIZE tracce
            if (i + 1) % BATCH_SIZE == 0:
                db_conn.commit()
                logger.debug(f"    💾 Commit batch: {i + 1} tracce processate")
                time.sleep(SLEEP_BETWEEN_BATCHES)  # Pausa per non bloccare
                
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                logger.warning(f"⚠️ Database locked, aspetto 5 secondi...")
                time.sleep(5)
                continue
            else:
                raise e
    
    # Commit finale
    db_conn.commit()
    return added_count

def process_artist_safe(artist, db_conn):
    """Processa un artista in modo sicuro con batch"""
    logger.info(f"🎵 Processando artista: {artist.title}")
    
    # Raccoglie tutti i dati prima di scrivere
    tracks_data = []
    
    try:
        albums = artist.albums()
        for album in albums:
            logger.info(f"  📀 Album: {album.title} ({album.year})")
            
            tracks = album.tracks()
            for track in tracks:
                title_clean = track.title.lower().strip()
                artist_clean = track.grandparentTitle.lower().strip()
                album_clean = track.parentTitle.lower().strip()
                
                tracks_data.append((title_clean, artist_clean, album_clean, album.year))
        
        # Aggiunge le tracce in batch sicuro
        added_count = safe_add_tracks_batch(tracks_data, db_conn)
        logger.info(f"✅ Artista completato: {added_count} tracce aggiunte")
        return added_count
        
    except Exception as e:
        logger.error(f"❌ Errore processando artista {artist.title}: {e}")
        return 0

def find_missing_artists_limited(plex_server, db_conn, limit=10):
    """Trova un numero limitato di artisti mancanti per evitare operazioni troppo lunghe"""
    logger.info(f"🔍 Cercando max {limit} artisti mancanti...")
    
    music_section = plex_server.library.section('Musica')
    
    # Ottiene artisti dal database
    cursor = db_conn.cursor()
    cursor.execute('SELECT DISTINCT artist_clean FROM plex_library_index')
    db_artists = {row[0] for row in cursor.fetchall()}
    
    # Trova artisti mancanti (limitati)
    artists = music_section.search(libtype='artist')
    missing_artists = []
    
    for artist in artists:
        if len(missing_artists) >= limit:
            break
            
        artist_clean = artist.title.lower().strip()
        if artist_clean not in db_artists:
            missing_artists.append(artist)
    
    logger.info(f"❌ Trovati {len(missing_artists)} artisti mancanti (su {len(artists)} totali)")
    return missing_artists

def main():
    """Funzione principale - versione sicura"""
    logger.info("🚀 Avvio fix SICURO gap indicizzazione")
    
    try:
        # Connessioni
        plex = get_plex_connection()
        db_conn = get_database_connection()
        
        total_added = 0
        
        # Processa solo un numero limitato di artisti per volta
        logger.info("\n=== STEP 1: ARTISTI MANCANTI (BATCH LIMITATO) ===")
        missing_artists = find_missing_artists_limited(plex, db_conn, limit=5)
        
        for i, artist in enumerate(missing_artists):
            logger.info(f"🔧 Processando artista {i+1}/{len(missing_artists)}")
            added = process_artist_safe(artist, db_conn)
            total_added += added
            
            # Pausa tra artisti
            time.sleep(2)
        
        logger.info(f"\n✅ Fix batch completato! Aggiunte {total_added} tracce al database")
        
        # Statistiche finali
        cursor = db_conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM plex_library_index')
        final_count = cursor.fetchone()[0]
        logger.info(f"📊 Totale tracce nel database: {final_count}")
        
        logger.info("💡 Per completare l'indicizzazione, riesegui questo script più volte")
        
    except Exception as e:
        logger.error(f"❌ Errore durante il fix: {e}")
        sys.exit(1)
    finally:
        if 'db_conn' in locals():
            db_conn.close()

if __name__ == "__main__":
    main()