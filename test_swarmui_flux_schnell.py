#!/usr/bin/env python3
"""
Test specifico per SwarmUI con preset Flux Schnell
"""

import sys
import os
import tempfile
import logging
import time

# Aggiungi il percorso del modulo al path Python
sys.path.insert(0, '/app')

from plex_playlist_sync.utils.playlist_cover_generator import (
    SwarmUIClient,
    generate_ai_cover_swarmui,
    optimize_prompt_for_flux,
    get_mood_prompt_from_genres
)

# Configura logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_flux_schnell_basic():
    """Test base con Flux Schnell"""
    logger.info("🧪 Test Flux Schnell - Base")
    
    try:
        client = SwarmUIClient()
        
        if not client.is_available():
            logger.error("❌ SwarmUI non disponibile")
            return False
        
        start_time = time.time()
        
        # Prompt ottimizzato per Flux Schnell
        prompt = "album cover, electronic music, vibrant neon colors, geometric patterns, clean design, modern aesthetic"
        negative_prompt = "blurry, low quality, text artifacts, watermark"
        
        logger.info(f"📝 Prompt: {prompt}")
        logger.info(f"🎛️ Model: flux1-schnell.safetensors")
        
        # Genera con parametri Flux Schnell
        image_data = client.generate_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            model="flux1-schnell.safetensors",
            width=1024,
            height=1024,
            steps=4,
            cfgscale=1.0
        )
        
        elapsed_time = time.time() - start_time
        logger.info(f"⏱️ Tempo generazione: {elapsed_time:.2f} secondi")
        
        if image_data:
            test_path = os.path.join(tempfile.gettempdir(), "test_flux_schnell_basic.png")
            with open(test_path, 'wb') as f:
                f.write(image_data)
            
            logger.info(f"✅ Immagine generata: {test_path}")
            logger.info(f"📊 Dimensione: {len(image_data)} bytes")
            
            return True
        else:
            logger.error("❌ Generazione fallita")
            return False
            
    except Exception as e:
        logger.error(f"❌ Errore: {e}")
        return False

def test_flux_schnell_genres():
    """Test con diversi generi musicali"""
    logger.info("🧪 Test Flux Schnell - Generi multipli")
    
    test_cases = [
        {
            "name": "Reggae Paradise",
            "description": "Tropical reggae vibes with island sunset",
            "genres": ["reggae", "tropical"],
            "expected_keywords": ["tropical", "sunset", "ocean"]
        },
        {
            "name": "Electronic Pulse",
            "description": "High-energy electronic dance music",
            "genres": ["electronic", "edm", "house"],
            "expected_keywords": ["neon", "electric", "geometric"]
        },
        {
            "name": "Jazz Lounge",
            "description": "Smooth jazz for late night relaxation",
            "genres": ["jazz", "smooth"],
            "expected_keywords": ["elegant", "vintage", "sophisticated"]
        },
        {
            "name": "Metal Storm",
            "description": "Aggressive metal with dark themes",
            "genres": ["metal", "black metal"],
            "expected_keywords": ["dark", "industrial", "aggressive"]
        }
    ]
    
    results = []
    
    for i, test_case in enumerate(test_cases):
        logger.info(f"🎵 Test {i+1}/4: {test_case['name']}")
        
        try:
            start_time = time.time()
            
            # Genera copertina completa
            cover_path = generate_ai_cover_swarmui(
                playlist_name=test_case["name"],
                description=test_case["description"],
                genres=test_case["genres"]
            )
            
            elapsed_time = time.time() - start_time
            
            if cover_path and os.path.exists(cover_path):
                file_size = os.path.getsize(cover_path)
                logger.info(f"✅ {test_case['name']}: {cover_path}")
                logger.info(f"⏱️ Tempo: {elapsed_time:.2f}s, Dimensione: {file_size} bytes")
                
                # Cleanup
                try:
                    os.remove(cover_path)
                except:
                    pass
                
                results.append(True)
            else:
                logger.error(f"❌ {test_case['name']}: Generazione fallita")
                results.append(False)
                
        except Exception as e:
            logger.error(f"❌ {test_case['name']}: {e}")
            results.append(False)
        
        # Pausa tra test per evitare overload
        time.sleep(2)
    
    success_rate = sum(results) / len(results) * 100
    logger.info(f"📊 Tasso successo: {success_rate:.1f}% ({sum(results)}/{len(results)})")
    
    return success_rate >= 75  # 75% soglia successo

def test_flux_schnell_prompt_optimization():
    """Test ottimizzazione prompt per Flux Schnell"""
    logger.info("🧪 Test Flux Schnell - Ottimizzazione prompt")
    
    try:
        # Test diversi tipi di prompt
        test_prompts = [
            {
                "base": "Synthwave playlist cover with retro aesthetic",
                "genres": ["synthwave", "retro"],
                "description": "80s inspired electronic music"
            },
            {
                "base": "Hip-hop album cover with urban street style",
                "genres": ["hip-hop", "rap"],
                "description": "Underground hip-hop beats"
            },
            {
                "base": "Classical music elegant design",
                "genres": ["classical", "opera"],
                "description": "Timeless classical compositions"
            }
        ]
        
        client = SwarmUIClient()
        
        if not client.is_available():
            logger.error("❌ SwarmUI non disponibile")
            return False
        
        results = []
        
        for i, test_prompt in enumerate(test_prompts):
            logger.info(f"🎯 Test prompt {i+1}/3")
            
            # Genera mood prompt
            mood_prompt = get_mood_prompt_from_genres(
                test_prompt["genres"], 
                test_prompt["description"]
            )
            
            # Ottimizza per Flux
            optimized_prompt = optimize_prompt_for_flux(
                test_prompt["base"] + ". " + mood_prompt,
                test_prompt["genres"]
            )
            
            logger.info(f"📝 Prompt ottimizzato: {optimized_prompt}")
            
            # Genera immagine
            start_time = time.time()
            
            image_data = client.generate_image(
                prompt=optimized_prompt,
                negative_prompt="blurry, low quality, text artifacts",
                model="flux1-schnell.safetensors",
                width=1024,
                height=1024,
                steps=4,
                cfgscale=1.0
            )
            
            elapsed_time = time.time() - start_time
            
            if image_data:
                test_path = os.path.join(tempfile.gettempdir(), f"test_flux_prompt_{i+1}.png")
                with open(test_path, 'wb') as f:
                    f.write(image_data)
                
                logger.info(f"✅ Prompt {i+1}: {elapsed_time:.2f}s, {len(image_data)} bytes")
                results.append(True)
                
                # Cleanup
                try:
                    os.remove(test_path)
                except:
                    pass
            else:
                logger.error(f"❌ Prompt {i+1}: Generazione fallita")
                results.append(False)
            
            time.sleep(1)
        
        success_rate = sum(results) / len(results) * 100
        logger.info(f"📊 Prompt optimization success: {success_rate:.1f}%")
        
        return success_rate >= 66  # 66% soglia successo
        
    except Exception as e:
        logger.error(f"❌ Errore test prompt: {e}")
        return False

def test_flux_schnell_performance():
    """Test performance con Flux Schnell"""
    logger.info("🧪 Test Flux Schnell - Performance")
    
    try:
        client = SwarmUIClient()
        
        if not client.is_available():
            logger.error("❌ SwarmUI non disponibile")
            return False
        
        # Test 3 generazioni consecutive per misurare performance
        times = []
        sizes = []
        
        for i in range(3):
            logger.info(f"⚡ Performance test {i+1}/3")
            
            start_time = time.time()
            
            prompt = f"album cover test {i+1}, electronic music, vibrant colors, clean design"
            
            image_data = client.generate_image(
                prompt=prompt,
                negative_prompt="blurry, low quality",
                model="flux1-schnell.safetensors",
                width=1024,
                height=1024,
                steps=4,
                cfgscale=1.0
            )
            
            elapsed_time = time.time() - start_time
            times.append(elapsed_time)
            
            if image_data:
                sizes.append(len(image_data))
                logger.info(f"✅ Test {i+1}: {elapsed_time:.2f}s, {len(image_data)} bytes")
                
                # Salva temporaneamente per verifica
                test_path = os.path.join(tempfile.gettempdir(), f"perf_test_{i+1}.png")
                with open(test_path, 'wb') as f:
                    f.write(image_data)
                
                # Cleanup immediato
                try:
                    os.remove(test_path)
                except:
                    pass
            else:
                logger.error(f"❌ Test {i+1}: Fallito")
                return False
        
        # Statistiche performance
        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        avg_size = sum(sizes) / len(sizes)
        
        logger.info("📊 STATISTICHE PERFORMANCE:")
        logger.info(f"   Tempo medio: {avg_time:.2f}s")
        logger.info(f"   Tempo minimo: {min_time:.2f}s")
        logger.info(f"   Tempo massimo: {max_time:.2f}s")
        logger.info(f"   Dimensione media: {avg_size:.0f} bytes")
        
        # Considera successo se tempo medio < 30s (Flux Schnell dovrebbe essere veloce)
        return avg_time < 30.0
        
    except Exception as e:
        logger.error(f"❌ Errore test performance: {e}")
        return False

def main():
    """Esegue tutti i test Flux Schnell"""
    logger.info("🚀 Test completi SwarmUI con Flux Schnell")
    logger.info("=" * 60)
    
    tests = [
        ("Basic Generation", test_flux_schnell_basic),
        ("Multiple Genres", test_flux_schnell_genres),
        ("Prompt Optimization", test_flux_schnell_prompt_optimization),
        ("Performance", test_flux_schnell_performance)
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        logger.info(f"\n{'='*20} {test_name} {'='*20}")
        try:
            results[test_name] = test_func()
        except Exception as e:
            logger.error(f"❌ {test_name} crashed: {e}")
            results[test_name] = False
    
    # Riassunto finale
    logger.info("\n" + "="*60)
    logger.info("📊 RIASSUNTO TEST FLUX SCHNELL")
    logger.info("="*60)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{test_name:25} {status}")
    
    passed = sum(results.values())
    total = len(results)
    success_rate = passed / total * 100
    
    logger.info(f"\nRisultato finale: {passed}/{total} test superati ({success_rate:.1f}%)")
    
    if success_rate >= 75:
        logger.info("🎉 Flux Schnell funziona correttamente!")
        return True
    else:
        logger.warning("⚠️ Alcuni problemi con Flux Schnell")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)