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
folder = "/downloads"
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
    logging.info(f"🔧 DEBUG: Inizio download_single_track_with_streamrip per: {link}")
    
    if not link:
        logging.info("Nessun link da scaricare fornito.")
        return

    # Pulisci l'URL da caratteri invisibili prima del download
    cleaned_link = clean_url(link)
    if not cleaned_link:
        logging.error("URL vuoto dopo la pulizia, download annullato.")
        return
    
    logging.info(f"🔧 DEBUG: URL pulito: {cleaned_link}")

    logging.info(f"🔧 DEBUG: Verifico directory temp...")
    
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
    logging.info(f"🔧 DEBUG: File temporaneo: {temp_links_file}")
    try:
        with open(temp_links_file, "w", encoding="utf-8") as f:
            f.write(f"{cleaned_link}\n")
        
        logging.info(f"Avvio del download con streamrip per il link: {cleaned_link}")
        logging.info(f"🔧 DEBUG: Configuro path streamrip...")
        
        # Configura il path per streamrip usando directory con permessi corretti
        config_dir = "/app/state_data/.config/streamrip"
        config_path = os.path.join(config_dir, "config.toml")
        logging.info(f"🔧 DEBUG: Config dir: {config_dir}")
        
        # Crea la directory di config se non esiste
        os.makedirs(config_dir, exist_ok=True)
        
        # Crea la directory Downloads se non esiste
        downloads_dir = os.getenv("MUSIC_DOWNLOAD_PATH", "/downloads")  # Usa /downloads mappato via docker-compose
        os.makedirs(downloads_dir, exist_ok=True)
        
        # Controlla se esiste già un config.toml (backward compatibility)
        # Prima controlla la configurazione Docker-mounted
        docker_config_path = "/root/.config/streamrip/config.toml"
        writable_config_path = "/app/state_data/config.toml"
        legacy_config_path = "/app/config.toml"
        
        if os.path.exists(docker_config_path):
            logging.info(f"✅ Utilizzando configurazione streamrip Docker-mounted: {docker_config_path}")
            config_path = docker_config_path
        elif os.path.exists(writable_config_path):
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
        
        # Debug: log del comando e della configurazione
        logging.info(f"🔧 DEBUG: Comando streamrip: {' '.join(command)}")
        logging.info(f"🔧 DEBUG: Config path utilizzato: {config_path}")
        try:
            with open(config_path, 'r') as f:
                config_content = f.read()
                if '/music' in config_content:
                    logging.error(f"🔧 DEBUG: TROVATO /music nel config file!")
                if '/downloads' in config_content:
                    logging.info(f"🔧 DEBUG: Config contiene /downloads (corretto)")
        except Exception as e:
            logging.error(f"🔧 DEBUG: Errore leggendo config: {e}")
        
        # Retry logic per errori intermittenti (es. permission denied)
        max_retries = 2
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                logging.info(f"🔧 DEBUG: Esecuzione comando streamrip (tentativo {retry_count + 1})...")
                # Aggiungiamo un timeout per evitare che il processo si blocchi all'infinito
                process = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8', timeout=1800, env=env)
                logging.info(f"Download di {cleaned_link} completato con successo.")
                if process.stdout:
                     logging.debug(f"Output di streamrip per {cleaned_link}:\n{process.stdout}")
                if process.stderr:
                     logging.warning(f"Output di warning da streamrip per {cleaned_link}:\n{process.stderr}")
                break  # Success, exit retry loop
                
            except subprocess.CalledProcessError as e:
                retry_count += 1
                
                # Check if it's a permission error that might be temporary
                is_permission_error = (e.stderr and "Permission denied" in e.stderr) or (e.stdout and "Permission denied" in e.stdout)
                
                if is_permission_error and retry_count <= max_retries:
                    logging.warning(f"⚠️ Permission error durante download di {cleaned_link} (tentativo {retry_count}/{max_retries + 1}). Retry in 5 secondi...")
                    time.sleep(5)  # Wait before retry
                    continue
                else:
                    # Final failure or non-permission error
                    logging.error(f"❌ Errore durante l'esecuzione di streamrip per {cleaned_link} (tentativo finale {retry_count}/{max_retries + 1}).")
                    if e.stdout: logging.error(f"Output Standard (stdout):\n{e.stdout}")
                    if e.stderr: logging.error(f"Output di Errore (stderr):\n{e.stderr}")
                    break

    except subprocess.CalledProcessError as e:
        logging.error(f"Errore durante l'esecuzione di streamrip per {cleaned_link}.")
        if e.stdout: logging.error(f"Output Standard (stdout):\n{e.stdout}")
        if e.stderr: logging.error(f"Output di Errore (stderr):\n{e.stderr}")
    except Exception as e:
        logging.error(f"🔧 DEBUG: Exception catturata nel download_single_track_with_streamrip")
        logging.error(f"🔧 DEBUG: Tipo eccezione: {type(e).__name__}")
        logging.error(f"🔧 DEBUG: Messaggio: {str(e)}")
        logging.error(f"Un errore imprevisto è occorso durante l'avvio di streamrip per {cleaned_link}: {e}")
        import traceback
        logging.error(f"🔧 DEBUG: Traceback completo:\n{traceback.format_exc()}")
    finally:
        if os.path.exists(temp_links_file):
            os.remove(temp_links_file)
            logging.info(f"File temporaneo di download rimosso: {temp_links_file}")

def add_direct_download_to_queue(url: str, title: str, artist: str, service: str, content_type: str) -> str:
    """
    Aggiunge un download diretto alla coda utilizzando il sistema di missing tracks.
    
    Args:
        url: URL del contenuto da scaricare
        title: Titolo del contenuto
        artist: Artista del contenuto
        service: Servizio di origine (spotify, deezer, etc.)
        content_type: Tipo di contenuto (track, album, artist)
        
    Returns:
        ID del download creato
    """
    try:
        from .database import get_db_connection
        import uuid
        
        # Genera un ID unico per il download
        download_id = str(uuid.uuid4())
        
        # Converte l'URL in un formato supportato da streamrip se necessario
        download_url = convert_url_for_streamrip(url, service)
        
        if not download_url:
            raise ValueError(f"URL non supportato o non convertibile: {url}")
            
        # Aggiungi alla tabella missing_tracks come un download diretto
        with get_db_connection() as con:
            cur = con.cursor()
            
            # Controlla se esiste già un record simile
            playlist_title = f"Direct {content_type.title()} Download"
            cur.execute("""
                SELECT id, status FROM missing_tracks 
                WHERE title = ? AND artist = ? AND source_playlist_title = ?
            """, (title, artist, playlist_title))
            
            existing = cur.fetchone()
            
            if existing:
                track_id = existing[0]
                existing_status = existing[1]
                
                if existing_status == 'downloaded':
                    logging.info(f"Download già completato per: {title} - {artist} (ID: {track_id})")
                    return download_id
                elif existing_status == 'pending':
                    logging.info(f"Download già in coda per: {title} - {artist} (ID: {track_id})")
                    # Aggiorna solo l'URL se necessario
                    cur.execute("""
                        UPDATE missing_tracks 
                        SET deezer_link = ?, direct_download_original_url = ?, direct_download_id = ?
                        WHERE id = ?
                    """, (download_url, url, download_id, track_id))
                else:
                    # Status è failed o missing, riprova
                    logging.info(f"Riprovando download per: {title} - {artist} (ID: {track_id})")
                    cur.execute("""
                        UPDATE missing_tracks 
                        SET deezer_link = ?, direct_download_original_url = ?, direct_download_id = ?, status = 'pending'
                        WHERE id = ?
                    """, (download_url, url, download_id, track_id))
            else:
                # Inserisci nuovo record
                cur.execute("""
                    INSERT INTO missing_tracks 
                    (title, artist, album, source_playlist_title, source_playlist, source_service, deezer_link, status, direct_download_id, direct_download_original_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    title,
                    artist,
                    f"Direct Download ({content_type})" if content_type != 'track' else 'Direct Download',
                    playlist_title,
                    playlist_title,
                    service,
                    download_url,
                    'pending',
                    download_id,
                    url
                ))
                
                track_id = cur.lastrowid
            
        logging.info(f"Download diretto aggiunto alla coda: {title} - {artist} (ID: {download_id}, Track ID: {track_id})")
        
        # Aggiungi immediatamente alla coda di download per processamento
        try:
            # Avvia il download immediatamente
            logging.info(f"Avvio download immediato per: {download_url}")
            download_single_track_with_streamrip(download_url)
            
            # Aggiorna lo status a downloaded
            from .database import update_track_status
            update_track_status(track_id, 'downloaded')
            logging.info(f"Download completato per: {title} - {artist}")
            
        except Exception as download_error:
            logging.error(f"Errore durante download immediato: {download_error}")
            # Lo status rimane 'pending' per retry successivi
        
        return download_id
        
    except Exception as e:
        logging.error(f"Errore aggiungendo download diretto alla coda: {e}")
        raise

def convert_url_for_streamrip(url: str, service: str) -> str:
    """
    Converte un URL di streaming in un formato utilizzabile da streamrip.
    
    Args:
        url: URL originale
        service: Servizio di origine
        
    Returns:
        URL convertito o None se non supportato
    """
    try:
        # Deezer - le URL dirette sono già supportate
        if 'deezer.com' in url:
            return url
            
        # Spotify - streamrip non supporta Spotify direttamente
        # Dovremmo cercare l'equivalente su Deezer
        if 'spotify.com' in url:
            logging.info(f"URL Spotify rilevato: {url} - tentando conversione a Deezer")
            return search_equivalent_on_deezer(url)
            
        # YouTube - supportato da streamrip
        if 'youtube.com' in url or 'youtu.be' in url:
            return url
            
        # SoundCloud - supportato da streamrip  
        if 'soundcloud.com' in url:
            return url
            
        # Altri servizi non supportati
        logging.warning(f"Servizio non supportato per URL: {url}")
        return None
        
    except Exception as e:
        logging.error(f"Errore convertendo URL {url}: {e}")
        return None

def search_equivalent_on_deezer(spotify_url: str) -> str:
    """
    Cerca l'equivalente di un contenuto Spotify su Deezer.
    
    Args:
        spotify_url: URL Spotify da convertire
        
    Returns:
        URL Deezer equivalente o None se non trovato
    """
    try:
        from .spotify import get_spotify_credentials
        from .deezer import search_deezer_content
        import re
        
        # Estrai ID Spotify dall'URL
        spotify_id_match = re.search(r'/(track|album|artist)/([a-zA-Z0-9]+)', spotify_url)
        if not spotify_id_match:
            return None
            
        content_type = spotify_id_match.group(1)
        spotify_id = spotify_id_match.group(2)
        
        # Ottieni informazioni da Spotify
        sp = get_spotify_credentials()
        if not sp:
            return None
            
        spotify_data = None
        search_query = ""
        
        if content_type == 'track':
            spotify_data = sp.track(spotify_id)
            search_query = f"{spotify_data['name']} {spotify_data['artists'][0]['name']}"
        elif content_type == 'album':
            spotify_data = sp.album(spotify_id)
            search_query = f"{spotify_data['name']} {spotify_data['artists'][0]['name']}"
        elif content_type == 'artist':
            spotify_data = sp.artist(spotify_id)
            search_query = spotify_data['name']
            
        if not search_query:
            return None
            
        # Cerca su Deezer
        deezer_results = search_deezer_content(search_query, content_type)
        
        # Restituisci il primo risultato con alta rilevanza
        for result in deezer_results:
            if result.get('relevance', 0) > 50:  # Soglia di rilevanza
                logging.info(f"Trovato equivalente Deezer per {spotify_url}: {result['url']}")
                return result['url']
                
        logging.warning(f"Nessun equivalente Deezer trovato per {spotify_url}")
        return None
        
    except Exception as e:
        logging.error(f"Errore cercando equivalente Deezer per {spotify_url}: {e}")
        return None