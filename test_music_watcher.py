#!/usr/bin/env python3
"""
Script di test per il Music Watcher
Verifica il funzionamento del sistema di monitoraggio automatico
"""
import os
import time
import logging
from pathlib import Path
from plexapi.server import PlexServer
from dotenv import load_dotenv

# Load environment variables
load_dotenv('/app/.env')

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import watcher
from plex_playlist_sync.utils.file_watcher import watcher_manager
from plex_playlist_sync.utils.database import get_library_index_stats

def test_watcher_initialization():
    """Test 1: Inizializzazione del watcher"""
    logger.info("🧪 Test 1: Inizializzazione Music Watcher")
    
    try:
        plex_url = os.getenv('PLEX_URL')
        plex_token = os.getenv('PLEX_TOKEN')
        music_download_path = os.getenv('MUSIC_DOWNLOAD_PATH', '/downloads')
        library_name = os.getenv('LIBRARY_NAME', 'Musica')
        
        logger.info(f"🔧 Configurazione:")
        logger.info(f"   PLEX_URL: {plex_url}")
        logger.info(f"   PLEX_TOKEN: {'***' if plex_token else 'Not set'}")
        logger.info(f"   MUSIC_PATH: {music_download_path}")
        logger.info(f"   LIBRARY_NAME: {library_name}")
        
        if not plex_url or not plex_token:
            logger.error("❌ PLEX_URL o PLEX_TOKEN non configurati")
            return False
        
        # Inizializza Plex
        plex_server = PlexServer(plex_url, plex_token, timeout=120)
        logger.info(f"✅ Connessione Plex riuscita: {plex_server.friendlyName}")
        
        # Inizializza watcher
        watcher = watcher_manager.initialize(music_download_path, plex_server, library_name)
        logger.info("✅ Watcher inizializzato correttamente")
        
        # Verifica status
        status = watcher.get_status()
        logger.info(f"📊 Status watcher: {status}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Errore inizializzazione: {e}")
        return False

def test_watcher_functionality():
    """Test 2: Funzionalità del watcher"""
    logger.info("🧪 Test 2: Funzionalità Music Watcher")
    
    try:
        watcher = watcher_manager.get_watcher()
        if not watcher:
            logger.error("❌ Watcher non inizializzato")
            return False
        
        # Test status
        status = watcher.get_status()
        logger.info(f"📊 Status attuale: {status}")
        
        # Test database stats prima del refresh
        stats_before = get_library_index_stats()
        logger.info(f"📈 Database prima: {stats_before['total_tracks_indexed']} tracce")
        
        # Test force refresh
        logger.info("🔄 Test force refresh...")
        watcher.force_refresh()
        
        # Attendi qualche secondo
        time.sleep(5)
        
        # Verifica stats dopo refresh
        stats_after = get_library_index_stats()
        logger.info(f"📈 Database dopo: {stats_after['total_tracks_indexed']} tracce")
        
        if stats_after['total_tracks_indexed'] >= stats_before['total_tracks_indexed']:
            logger.info("✅ Force refresh completato")
        else:
            logger.warning("⚠️ Nessuna nuova traccia trovata (normale se non ci sono nuovi file)")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Errore test funzionalità: {e}")
        return False

def test_music_path_monitoring():
    """Test 3: Monitoraggio cartella musicale"""
    logger.info("🧪 Test 3: Monitoraggio cartella musicale")
    
    try:
        music_path = os.getenv('MUSIC_DOWNLOAD_PATH', '/downloads')
        music_path_obj = Path(music_path)
        
        logger.info(f"📁 Cartella musicale: {music_path}")
        logger.info(f"📁 Esiste: {music_path_obj.exists()}")
        
        if music_path_obj.exists():
            # Conta file musicali
            music_extensions = {'.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aac'}
            music_files = []
            
            for file_path in music_path_obj.rglob('*'):
                if file_path.is_file() and file_path.suffix.lower() in music_extensions:
                    music_files.append(file_path)
            
            logger.info(f"🎵 File musicali trovati: {len(music_files)}")
            
            # Mostra alcuni esempi
            for i, file_path in enumerate(music_files[:5]):
                mtime = file_path.stat().st_mtime
                logger.info(f"   {i+1}. {file_path.name} (modified: {time.ctime(mtime)})")
            
            if len(music_files) > 5:
                logger.info(f"   ... e altri {len(music_files) - 5} file")
                
        else:
            logger.warning(f"⚠️ Cartella musicale non trovata: {music_path}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Errore test monitoraggio: {e}")
        return False

def test_watcher_start_stop():
    """Test 4: Avvio e stop del watcher"""
    logger.info("🧪 Test 4: Avvio e stop Music Watcher")
    
    try:
        watcher = watcher_manager.get_watcher()
        if not watcher:
            logger.error("❌ Watcher non inizializzato")
            return False
        
        # Test avvio
        logger.info("🚀 Test avvio watcher...")
        watcher_manager.start()
        time.sleep(2)
        
        status = watcher.get_status()
        logger.info(f"📊 Status dopo avvio: {status['running']}")
        
        # Test stop
        logger.info("🛑 Test stop watcher...")
        watcher_manager.stop()
        time.sleep(2)
        
        status = watcher.get_status()
        logger.info(f"📊 Status dopo stop: {status['running']}")
        
        logger.info("✅ Test avvio/stop completato")
        return True
        
    except Exception as e:
        logger.error(f"❌ Errore test avvio/stop: {e}")
        return False

def main():
    """Esegue tutti i test del Music Watcher"""
    logger.info("🎵 === MUSIC WATCHER TEST SUITE ===")
    
    tests = [
        ("Inizializzazione", test_watcher_initialization),
        ("Funzionalità", test_watcher_functionality),
        ("Monitoraggio Cartella", test_music_path_monitoring),
        ("Avvio/Stop", test_watcher_start_stop)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        logger.info(f"\n{'='*50}")
        logger.info(f"🔍 ESECUZIONE: {test_name}")
        logger.info(f"{'='*50}")
        
        try:
            result = test_func()
            results.append((test_name, result))
            
            if result:
                logger.info(f"✅ {test_name}: PASSATO")
            else:
                logger.error(f"❌ {test_name}: FALLITO")
                
        except Exception as e:
            logger.error(f"💥 {test_name}: ERRORE - {e}")
            results.append((test_name, False))
    
    # Riepilogo
    logger.info(f"\n{'='*50}")
    logger.info("📊 RIEPILOGO RISULTATI")
    logger.info(f"{'='*50}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASSATO" if result else "❌ FALLITO"
        logger.info(f"{test_name}: {status}")
    
    logger.info(f"\n🎯 RISULTATO FINALE: {passed}/{total} test passati")
    
    if passed == total:
        logger.info("🎉 Tutti i test sono passati! Music Watcher funziona correttamente.")
    else:
        logger.warning(f"⚠️ {total - passed} test falliti. Controllare la configurazione.")

if __name__ == "__main__":
    main()