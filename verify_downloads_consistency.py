#!/usr/bin/env python3
"""
Script per verificare la coerenza tra il database e i file scaricati
Identifica tracce marcate come 'downloaded' ma con file mancanti
"""
import os
import sys
import logging
import glob
from pathlib import Path
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv('/app/.env')

# Add the project root to Python path
sys.path.insert(0, '/app')

from plex_playlist_sync.utils.database import DatabasePool

def normalize_filename(text):
    """Normalizza una stringa per il confronto di file"""
    # Rimuovi caratteri speciali che possono causare problemi nei nomi file
    import re
    return re.sub(r'[^\w\s-]', '', text.lower().strip())

def check_file_exists(title, artist, download_path="/downloads"):
    """Controlla se esiste un file per la traccia specificata"""
    try:
        download_path_obj = Path(download_path)
        if not download_path_obj.exists():
            return False
        
        # Normalizza i nomi per la ricerca
        norm_title = normalize_filename(title)
        norm_artist = normalize_filename(artist)
        
        # Cerca ricorsivamente nella directory
        for file_path in download_path_obj.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in ['.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aac']:
                norm_filename = normalize_filename(file_path.name)
                norm_parent = normalize_filename(file_path.parent.name)
                
                # Controlla se il file contiene sia titolo che artista
                if (norm_title in norm_filename or norm_title in norm_parent) and \
                   (norm_artist in norm_filename or norm_artist in norm_parent):
                    return str(file_path)
        
        return False
    except Exception as e:
        logger.debug(f"Errore durante la ricerca del file: {e}")
        return False

def verify_downloads_consistency():
    """Verifica la coerenza tra database e file scaricati"""
    logger.info("🔍 Verifica coerenza download...")
    
    db_path = os.path.join('/app/state_data', 'sync_database.db')
    db_pool = DatabasePool(db_path)
    
    with db_pool.get_connection_context() as conn:
        cursor = conn.cursor()
        
        # Trova tutte le tracce marcate come downloaded
        cursor.execute("""
            SELECT id, title, artist, album, status, direct_download_id 
            FROM missing_tracks 
            WHERE status = 'downloaded'
        """)
        downloaded_tracks = cursor.fetchall()
        
        logger.info(f"📊 Trovate {len(downloaded_tracks)} tracce marcate come downloaded")
        
        inconsistent_tracks = []
        verified_tracks = []
        
        for track in downloaded_tracks:
            track_id, title, artist, album, status, download_id = track
            
            # Controlla se il file esiste
            file_exists = check_file_exists(title, artist)
            
            if file_exists:
                verified_tracks.append((track_id, title, artist, file_exists))
                logger.debug(f"✅ Verificato: {title} - {artist}")
            else:
                inconsistent_tracks.append((track_id, title, artist, album))
                logger.warning(f"❌ File mancante: {title} - {artist} (ID: {track_id})")
        
        logger.info(f"✅ Tracce verificate: {len(verified_tracks)}")
        logger.info(f"❌ Tracce inconsistenti: {len(inconsistent_tracks)}")
        
        if inconsistent_tracks:
            logger.info("\n🔧 Correzione tracce inconsistenti...")
            
            for track_id, title, artist, album in inconsistent_tracks:
                cursor.execute("""
                    UPDATE missing_tracks 
                    SET status = 'missing', 
                        added_date = datetime('now'),
                        direct_download_id = NULL
                    WHERE id = ?
                """, (track_id,))
                
                logger.info(f"🔄 Resettato: {title} - {artist} (ID: {track_id})")
            
            logger.info(f"✅ Corrette {len(inconsistent_tracks)} tracce inconsistenti")
        else:
            logger.info("✅ Tutti i download sono consistenti!")
        
        return len(inconsistent_tracks), len(verified_tracks)

def main():
    """Funzione principale"""
    logger.info("🚀 Avvio verifica coerenza download")
    
    try:
        inconsistent, verified = verify_downloads_consistency()
        
        logger.info(f"\n📊 RIEPILOGO:")
        logger.info(f"✅ Tracce verificate: {verified}")
        logger.info(f"🔄 Tracce corrette: {inconsistent}")
        
        if inconsistent > 0:
            logger.info(f"💡 {inconsistent} tracce sono ora disponibili per il ri-download")
        
    except Exception as e:
        logger.error(f"❌ Errore durante la verifica: {e}")
        raise

if __name__ == "__main__":
    main()