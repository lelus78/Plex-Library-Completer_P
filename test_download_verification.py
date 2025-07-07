#!/usr/bin/env python3
"""
Test per verificare la nuova logica di verifica dei download
"""
import os
import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add the project root to Python path
sys.path.insert(0, '/app')

from plex_playlist_sync.utils.downloader import download_single_track_with_streamrip

def test_download_verification():
    """Test della logica di verifica download"""
    
    # Test con URL invalido (dovrebbe restituire False)
    logger.info("🧪 Test 1: URL invalido")
    result = download_single_track_with_streamrip("")
    logger.info(f"Risultato URL vuoto: {result}")
    assert result == False, "URL vuoto dovrebbe restituire False"
    
    # Test con URL inesistente (dovrebbe restituire False)
    logger.info("🧪 Test 2: URL inesistente")
    result = download_single_track_with_streamrip("https://www.deezer.com/track/999999999999")
    logger.info(f"Risultato URL inesistente: {result}")
    assert result == False, "URL inesistente dovrebbe restituire False"
    
    logger.info("✅ Test di verifica completati")

if __name__ == "__main__":
    test_download_verification()