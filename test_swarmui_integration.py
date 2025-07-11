#!/usr/bin/env python3
"""
Test script per verificare l'integrazione SwarmUI
"""

import sys
import os
import tempfile
import logging

# Aggiungi il percorso del modulo al path Python
sys.path.insert(0, '/app')

from plex_playlist_sync.utils.playlist_cover_generator import (
    SwarmUIClient,
    generate_ai_cover_swarmui,
    detect_gpu_capabilities,
    test_cover_generation
)

# Configura logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_swarmui_client():
    """Test del client SwarmUI base"""
    logger.info("🧪 Test client SwarmUI...")
    
    try:
        # Test connessione
        client = SwarmUIClient()
        
        logger.info(f"SwarmUI URL: {client.base_url}")
        
        # Test disponibilità
        available = client.is_available()
        logger.info(f"SwarmUI disponibile: {available}")
        
        if not available:
            logger.warning("⚠️ SwarmUI non disponibile - test limitato")
            return False
        
        # Test sessione
        session_ok = client.get_session()
        logger.info(f"Sessione ottenuta: {session_ok}")
        
        if session_ok:
            logger.info(f"Session ID: {client.session_id}")
            logger.info(f"User ID: {client.user_id}")
        
        return session_ok
        
    except Exception as e:
        logger.error(f"❌ Errore test client SwarmUI: {e}")
        return False

def test_swarmui_image_generation():
    """Test generazione immagine SwarmUI"""
    logger.info("🧪 Test generazione immagine SwarmUI...")
    
    try:
        client = SwarmUIClient()
        
        if not client.is_available():
            logger.warning("⚠️ SwarmUI non disponibile")
            return False
        
        # Test prompt semplice
        prompt = "album cover, electronic music, neon colors, clean design"
        negative_prompt = "blurry, low quality, text artifacts"
        
        logger.info(f"Prompt: {prompt}")
        
        # Genera immagine con preset Flux Schnell
        image_data = client.generate_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            preset="flux shnell",
            width=1024,
            height=1024
        )
        
        if image_data:
            # Salva immagine di test
            test_path = os.path.join(tempfile.gettempdir(), "test_swarmui_image.png")
            with open(test_path, 'wb') as f:
                f.write(image_data)
            
            logger.info(f"✅ Immagine SwarmUI generata: {test_path}")
            logger.info(f"Dimensione: {len(image_data)} bytes")
            
            # Cleanup
            try:
                os.remove(test_path)
                logger.debug("🧹 File test rimosso")
            except:
                pass
            
            return True
        else:
            logger.error("❌ Generazione immagine SwarmUI fallita")
            return False
        
    except Exception as e:
        logger.error(f"❌ Errore test generazione SwarmUI: {e}")
        return False

def test_swarmui_cover_generation():
    """Test generazione copertina completa SwarmUI"""
    logger.info("🧪 Test generazione copertina completa SwarmUI...")
    
    try:
        cover_path = generate_ai_cover_swarmui(
            playlist_name="Test Electronic Mix",
            description="High-energy electronic music for workouts",
            genres=["electronic", "edm", "synthwave"]
        )
        
        if cover_path and os.path.exists(cover_path):
            logger.info(f"✅ Copertina SwarmUI generata: {cover_path}")
            
            # Verifica dimensioni file
            file_size = os.path.getsize(cover_path)
            logger.info(f"Dimensione file: {file_size} bytes")
            
            # Cleanup
            try:
                os.remove(cover_path)
                logger.debug("🧹 File test rimosso")
            except:
                pass
            
            return True
        else:
            logger.error("❌ Generazione copertina SwarmUI fallita")
            return False
        
    except Exception as e:
        logger.error(f"❌ Errore test copertina SwarmUI: {e}")
        return False

def test_capability_detection():
    """Test rilevamento capacità"""
    logger.info("🧪 Test rilevamento capacità...")
    
    try:
        capability = detect_gpu_capabilities()
        logger.info(f"Capacità rilevata: {capability}")
        
        if capability == "swarmui":
            logger.info("✅ SwarmUI rilevato come disponibile")
            return True
        elif capability == "comfyui":
            logger.info("⚠️ ComfyUI rilevato, SwarmUI non disponibile")
            return False
        else:
            logger.info("⚠️ Nessun AI rilevato")
            return False
        
    except Exception as e:
        logger.error(f"❌ Errore test rilevamento: {e}")
        return False

def main():
    """Esegue tutti i test SwarmUI"""
    logger.info("🚀 Inizio test integrazione SwarmUI")
    
    results = {
        "client": test_swarmui_client(),
        "image_generation": test_swarmui_image_generation(),
        "cover_generation": test_swarmui_cover_generation(),
        "capability_detection": test_capability_detection(),
    }
    
    # Test generale
    logger.info("🧪 Test copertine generali...")
    results["general_test"] = test_cover_generation()
    
    # Riassunto risultati
    logger.info("\n" + "="*50)
    logger.info("📊 RIASSUNTO TEST SWARMUI")
    logger.info("="*50)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{test_name}: {status}")
    
    all_passed = all(results.values())
    
    if all_passed:
        logger.info("🎉 Tutti i test SwarmUI superati!")
    else:
        logger.warning("⚠️ Alcuni test SwarmUI falliti")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)