import os
import csv
import logging
import requests
import subprocess
from typing import List, Dict
import time
import unicodedata

def clean_url(url: str) -> str:
    """
    Rimuove caratteri invisibili come zero-width space dagli URL.
    Questo risolve problemi con URL corrotti che causano errori di download.
    """
    if not url:
        return ""
    
    # Rimuove caratteri di controllo Unicode (categoria Cf) come zero-width space
    cleaned = ''.join(char for char in url if unicodedata.category(char) != 'Cf')
    
    # Rimuove spazi extra all'inizio e alla fine
    cleaned = cleaned.strip()
    
    # Log solo se è stato effettivamente pulito qualcosa
    if cleaned != url:
        logging.info(f"URL pulito: '{url}' -> '{cleaned}'")
    
    return cleaned

class DeezerLinkFinder:
    @staticmethod
    def find_track_link(track_info: dict) -> str | None:
        """
        Cerca una singola traccia su Deezer e restituisce il link dell'album.
        Questa funzione è usata dal downloader automatico.
        """
        try:
            title = track_info.get("title", "").strip()
            artist = track_info.get("artist", "").strip()

            if not title or not artist:
                return None

            # Multiple search strategies for better results
            search_strategies = [
                # Strategy 1: Exact match with quotes
                f'track:"{title}" artist:"{artist}"',
                # Strategy 2: Clean titles (remove anime references)
                f'track:"{DeezerLinkFinder._clean_anime_title(title)}" artist:"{artist}"',
                # Strategy 3: Simple search without quotes
                f'{title} {artist}',
                # Strategy 4: Artist only search
                f'artist:"{artist}"'
            ]

            for strategy in search_strategies:
                try:
                    search_url = f'https://api.deezer.com/search?q={strategy}&limit=5'
                    # Add delay to avoid rate limiting
                    time.sleep(0.5)
                    response = requests.get(search_url, timeout=10)
                    
                    # Skip 403 errors and try next strategy
                    if response.status_code == 403:
                        continue
                        
                    response.raise_for_status()
                    deezer_data = response.json()

                    if deezer_data.get("data"):
                        # Validazione risultati per evitare match errati (MODALITÀ RESTRITTIVA per download automatico)
                        for track in deezer_data["data"]:
                            if _is_valid_match(title, artist, track, strict_mode=True):
                                album_id = track.get("album", {}).get("id")
                                if album_id:
                                    album_link = f'https://www.deezer.com/album/{album_id}'
                                    return album_link
                except:
                    continue
                    
            return None
        except Exception:
            return None
            
    @staticmethod
    def _clean_anime_title(title: str) -> str:
        """Clean anime-specific references from title for better search results"""
        import re
        
        # Remove common anime opening/ending references
        patterns = [
            r'\s*\([^)]*Opening[^)]*\)',
            r'\s*\([^)]*Ending[^)]*\)', 
            r'\s*\([^)]*Theme[^)]*\)',
            r'\s*\([^)]*OP[^)]*\)',
            r'\s*\([^)]*ED[^)]*\)',
            r'\s*\([^)]*OST[^)]*\)',
            r'\s*\([^)]*Soundtrack[^)]*\)'
        ]
        
        cleaned = title
        for pattern in patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        return cleaned.strip()

def _is_valid_match(original_title: str, original_artist: str, deezer_track: dict, strict_mode: bool = True) -> bool:
    """Valida se un risultato di Deezer è effettivamente pertinente alla ricerca originale
    
    Args:
        strict_mode: True per download automatico (restrittivo), False per ricerca manuale (permissivo)
    """
    import difflib
    
    deezer_title = deezer_track.get("title", "").lower().strip()
    deezer_artist = deezer_track.get("artist", {}).get("name", "").lower().strip()
    
    original_title_clean = original_title.lower().strip()
    original_artist_clean = original_artist.lower().strip()
    
    # Gestione caso speciale "Various Artists"
    is_various_artists = any(va in original_artist_clean for va in ["various artists", "various", "compilation", "soundtrack"])
    
    # Pulizia per confronto
    def clean_for_comparison(text):
        import re
        # Rimuovi caratteri speciali, parentesi, e spazi extra
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    
    title_clean = clean_for_comparison(original_title_clean)
    artist_clean = clean_for_comparison(original_artist_clean)
    deezer_title_clean = clean_for_comparison(deezer_title)
    deezer_artist_clean = clean_for_comparison(deezer_artist)
    
    # Calcola similarità
    title_similarity = difflib.SequenceMatcher(None, title_clean, deezer_title_clean).ratio()
    artist_similarity = difflib.SequenceMatcher(None, artist_clean, deezer_artist_clean).ratio()
    
    # Criteri di validazione differenziati
    if strict_mode:
        # SISTEMA AUTOMATICO: Molto restrittivo per evitare download sbagliati
        if is_various_artists:
            # Per "Various Artists", richiedi titolo molto simile
            is_valid = title_similarity >= 0.85
        else:
            # Criteri restrittivi: entrambi devono essere buoni
            is_valid = (
                title_similarity >= 0.8 and artist_similarity >= 0.8
            )
    else:
        # SISTEMA MANUALE: Più permissivo per dare opzioni all'utente
        if is_various_artists:
            # Per "Various Artists", focus sul titolo ma meno restrittivo
            is_valid = title_similarity >= 0.6
        else:
            # Criteri permissivi: una delle due può essere più bassa
            is_valid = (
                (title_similarity >= 0.5 and artist_similarity >= 0.6) or
                title_similarity >= 0.7 or 
                artist_similarity >= 0.7
            )
    
    # Log per debug
    mode_str = "[AUTOMATICO]" if strict_mode else "[MANUALE]"
    if not is_valid:
        logging.debug(f"❌ {mode_str} Risultato Deezer scartato: '{deezer_title}' by '{deezer_artist}' "
                     f"(title: {title_similarity:.2f}, artist: {artist_similarity:.2f}) "
                     f"{'[Various Artists mode]' if is_various_artists else ''}")
    else:
        logging.debug(f"✅ {mode_str} Risultato Deezer accettato: '{deezer_title}' by '{deezer_artist}' "
                     f"(title: {title_similarity:.2f}, artist: {artist_similarity:.2f}) "
                     f"{'[Various Artists mode]' if is_various_artists else ''}")
    
    return is_valid

def find_potential_tracks(title: str, artist: str) -> List[Dict]:
    """
    Cerca su Deezer e restituisce una lista di potenziali tracce per la ricerca manuale.
    """
    # Multiple search strategies for better results
    search_strategies = [
        # Strategy 1: Exact match with quotes
        f'track:"{title}" artist:"{artist}"',
        # Strategy 2: Clean titles (remove anime references)
        f'track:"{_clean_anime_title(title)}" artist:"{artist}"',
        # Strategy 3: Simple search without quotes
        f'{title} {artist}',
        # Strategy 4: Just the cleaned title
        f'{_clean_anime_title(title)}',
        # Strategy 5: Artist only search
        f'artist:"{artist}"'
    ]

    for i, strategy in enumerate(search_strategies):
        try:
            search_url = f'https://api.deezer.com/search?q={strategy}&limit=10'
            # Add delay to avoid rate limiting
            time.sleep(0.5)
            response = requests.get(search_url, timeout=10)
            
            # Skip 403 errors and try next strategy
            if response.status_code == 403:
                if i == 0:  # Only log on first attempt
                    logging.warning(f"Deezer API returned 403 for '{title} - {artist}', trying alternative search strategies...")
                continue
                
            response.raise_for_status()
            deezer_data = response.json()
            
            if deezer_data.get("data"):
                # Filtra risultati per validità (MODALITÀ PERMISSIVA per ricerca manuale)
                valid_results = []
                for track in deezer_data.get("data", []):
                    if _is_valid_match(title, artist, track, strict_mode=False):
                        valid_results.append(track)
                
                total_results = len(deezer_data.get("data", []))
                valid_count = len(valid_results)
                
                if valid_results:
                    logging.info(f"Ricerca manuale per '{title} - {artist}' ha restituito {valid_count}/{total_results} risultati validi (strategia {i+1}).")
                    return valid_results
                else:
                    logging.debug(f"Tutti i {total_results} risultati della strategia {i+1} sono stati scartati per bassa similarità.")
                
        except Exception as e:
            if i == 0:  # Only log detailed error on first attempt
                logging.error(f"Errore durante la ricerca manuale su Deezer per '{title} - {artist}': {e}")
            continue
    
    logging.info(f"Nessun risultato trovato per '{title} - {artist}' dopo tutte le strategie di ricerca.")
    return []

def _clean_anime_title(title: str) -> str:
    """Clean anime-specific references from title for better search results"""
    import re
    
    # Remove common anime opening/ending references
    patterns = [
        r'\s*\([^)]*Opening[^)]*\)',
        r'\s*\([^)]*Ending[^)]*\)', 
        r'\s*\([^)]*Theme[^)]*\)',
        r'\s*\([^)]*OP[^)]*\)',
        r'\s*\([^)]*ED[^)]*\)',
        r'\s*\([^)]*OST[^)]*\)',
        r'\s*\([^)]*Soundtrack[^)]*\)'
    ]
    
    cleaned = title
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    return cleaned.strip()

def find_tracks_free_search(search_query: str) -> List[Dict]:
    """
    Ricerca completamente libera su Deezer con campo di testo personalizzato
    Per ricerca manuale senza filtri di validazione
    """
    try:
        # Ricerca diretta senza filtri
        search_url = f'https://api.deezer.com/search?q={search_query}&limit=20'
        time.sleep(0.5)  # Rate limiting
        response = requests.get(search_url, timeout=10)
        
        if response.status_code == 403:
            logging.warning(f"Deezer API returned 403 for free search: '{search_query}'")
            return []
            
        response.raise_for_status()
        deezer_data = response.json()
        
        results = deezer_data.get("data", [])
        logging.info(f"Ricerca libera per '{search_query}' ha restituito {len(results)} risultati (senza filtri).")
        return results
        
    except Exception as e:
        logging.error(f"Errore durante la ricerca libera su Deezer per '{search_query}': {e}")
        return []

def _create_streamrip_config(config_path: str, deezer_arl: str):
    """Crea un file di configurazione base per streamrip con ARL di Deezer"""
    config_content = f"""[downloads]
folder = "/music"
source_subdirectories = true

[downloads.artwork]
save_artwork = false
max_crop_size = 10000

[cli]
text_output = true
progress_bars = false

[conversion]
enabled = false

[database]
downloads_path = "/app/state_data/.local/share/streamrip/downloads.db"

[deezer]
arl = "{deezer_arl}"
quality = 2
track_url_validity = 300

[misc]
version = "2.0"
"""
    
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(config_content)
        logging.info(f"✅ File di configurazione streamrip creato: {config_path}")
    except Exception as e:
        logging.error(f"❌ Errore nella creazione del file di configurazione streamrip: {e}")

def download_single_track_with_streamrip(link: str):
    """
    Lancia streamrip per scaricare un singolo URL.
    """
    if not link:
        logging.info("Nessun link da scaricare fornito.")
        return

    # Pulisci l'URL da caratteri invisibili prima del download
    cleaned_link = clean_url(link)
    if not cleaned_link:
        logging.error("URL vuoto dopo la pulizia, download annullato.")
        return

    # Assicura che la directory temp esista
    temp_dir = "/app/state_data"
    if not os.path.exists(temp_dir):
        try:
            os.makedirs(temp_dir, exist_ok=True)
            logging.info(f"📁 Creata directory temporanea: {temp_dir}")
        except Exception as e:
            logging.error(f"❌ Impossibile creare directory {temp_dir}: {e}")
            # Fallback su directory corrente
            temp_dir = "."
    
    temp_links_file = f"{temp_dir}/temp_download_{int(time.time())}.txt"
    try:
        with open(temp_links_file, "w", encoding="utf-8") as f:
            f.write(f"{cleaned_link}\n")
        
        logging.info(f"Avvio del download con streamrip per il link: {cleaned_link}")
        
        # Configura il path per streamrip usando directory con permessi corretti
        config_dir = "/app/state_data/.config/streamrip"
        config_path = os.path.join(config_dir, "config.toml")
        
        # Crea la directory di config se non esiste
        os.makedirs(config_dir, exist_ok=True)
        
        # Crea la directory Downloads se non esiste
        downloads_dir = "/music"  # Usa /music che è mappato a M:\Organizzata
        os.makedirs(downloads_dir, exist_ok=True)
        
        # Controlla se esiste già un config.toml (backward compatibility)
        # Prima controlla la copia con i permessi corretti
        writable_config_path = "/app/state_data/config.toml"
        legacy_config_path = "/app/config.toml"
        
        if os.path.exists(writable_config_path):
            logging.info(f"✅ Utilizzando configurazione streamrip esistente: {writable_config_path}")
            config_path = writable_config_path
        elif os.path.exists(legacy_config_path):
            logging.info(f"✅ Utilizzando configurazione streamrip esistente: {legacy_config_path}")
            config_path = legacy_config_path
        elif os.path.exists(config_path):
            logging.info(f"✅ Utilizzando configurazione streamrip esistente: {config_path}")
        else:
            # Nessun config esistente, controlla se abbiamo l'ARL nella variabile d'ambiente
            deezer_arl = os.getenv("DEEZER_ARL", "").strip()
            
            if not deezer_arl:
                logging.warning(f"⚠️ Nessuna configurazione Deezer trovata - saltando download: {cleaned_link}")
                logging.info("💡 Opzioni per abilitare i download da Deezer:")
                logging.info("   1. Aggiungi DEEZER_ARL=your_arl_cookie nel file .env, oppure")
                logging.info("   2. Usa il file config.toml nella directory principale")
                logging.info("📖 Istruzioni ARL: https://github.com/nathom/streamrip/wiki/Finding-your-Deezer-ARL-Cookie")
                return
            
            # Crea file di configurazione streamrip con l'ARL dal .env
            _create_streamrip_config(config_path, deezer_arl)
        
        # Configura variabili d'ambiente per streamrip
        env = os.environ.copy()
        env['HOME'] = '/app/state_data'  # Usa directory con permessi corretti come HOME
        env['XDG_DATA_HOME'] = '/app/state_data/.local/share'  # Directory per database streamrip
        env['XDG_CACHE_HOME'] = '/app/state_data/.cache'  # Directory per cache streamrip
        
        # Debug delle directory
        logging.info(f"Environment variables for streamrip:")
        logging.info(f"  HOME: {env.get('HOME')}")
        logging.info(f"  XDG_DATA_HOME: {env.get('XDG_DATA_HOME')}")
        logging.info(f"  XDG_CACHE_HOME: {env.get('XDG_CACHE_HOME')}")
        
        # Controlla che le directory esistano
        data_dir = env.get('XDG_DATA_HOME', '/app/state_data/.local/share')
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
            logging.info(f"Created XDG_DATA_HOME directory: {data_dir}")
        
        streamrip_data_dir = os.path.join(data_dir, 'streamrip')
        if not os.path.exists(streamrip_data_dir):
            os.makedirs(streamrip_data_dir, exist_ok=True)
            logging.info(f"Created streamrip data directory: {streamrip_data_dir}")
        
        # Prova a creare manualmente il database di streamrip se non esiste
        db_path = os.path.join(streamrip_data_dir, 'downloads.db')
        if not os.path.exists(db_path):
            try:
                import sqlite3
                with sqlite3.connect(db_path) as conn:
                    # Crea una tabella vuota di base (streamrip la gestirà automaticamente)
                    conn.execute("CREATE TABLE IF NOT EXISTS downloads (id INTEGER PRIMARY KEY)")
                    conn.commit()
                logging.info(f"Created streamrip database: {db_path}")
            except Exception as db_error:
                logging.warning(f"Could not pre-create streamrip database: {db_error}")
        
        # Inizializza streamrip se necessario (crea database)
        try:
            init_command = ["rip", "--config-path", config_path, "config", "--help"]
            result = subprocess.run(init_command, capture_output=True, text=True, timeout=30, env=env)
            logging.debug("Streamrip initialization completed")
            if result.stderr:
                logging.debug(f"Streamrip init stderr: {result.stderr}")
        except Exception as init_error:
            logging.debug(f"Streamrip init warning (non-critical): {init_error}")
        
        command = ["rip", "--config-path", config_path, "file", temp_links_file]
        
        # Aggiungiamo un timeout per evitare che il processo si blocchi all'infinito
        process = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8', timeout=1800, env=env)
        logging.info(f"Download di {cleaned_link} completato con successo.")
        if process.stdout:
             logging.debug(f"Output di streamrip per {cleaned_link}:\n{process.stdout}")
        if process.stderr:
             logging.warning(f"Output di warning da streamrip per {cleaned_link}:\n{process.stderr}")

    except subprocess.CalledProcessError as e:
        logging.error(f"Errore durante l'esecuzione di streamrip per {cleaned_link}.")
        if e.stdout: logging.error(f"Output Standard (stdout):\n{e.stdout}")
        if e.stderr: logging.error(f"Output di Errore (stderr):\n{e.stderr}")
    except Exception as e:
        logging.error(f"Un errore imprevisto è occorso durante l'avvio di streamrip per {cleaned_link}: {e}")
    finally:
        if os.path.exists(temp_links_file):
            os.remove(temp_links_file)
            logging.info(f"File temporaneo di download rimosso: {temp_links_file}")