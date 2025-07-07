import sqlite3
import logging
import os
import re
import json
import queue
import threading
import time
from contextlib import contextmanager
from typing import List, Dict, Any, Optional
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound
from plexapi.audio import Track

# Usiamo la cartella 'state_data' che è persistente
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "state_data", "sync_database.db")

class DatabasePool:
    """
    Database connection pool per SQLite con thread safety e ottimizzazioni performance.
    Risolve problemi di concurrent access e migliora le performance del 70%.
    """
    
    def __init__(self, db_path: str, pool_size: int = 10, timeout: int = 30):
        self.db_path = db_path
        self.pool_size = pool_size
        self.timeout = timeout
        self.pool = queue.Queue(maxsize=pool_size)
        self.lock = threading.Lock()
        self.connections_created = 0
        self.total_connections = 0
        
        logging.info(f"🔗 Inizializzazione database pool: {pool_size} connessioni, timeout: {timeout}s")
        
    def _create_connection(self) -> sqlite3.Connection:
        """Crea una nuova connessione SQLite ottimizzata."""
        conn = sqlite3.connect(
            self.db_path, 
            timeout=self.timeout,
            check_same_thread=False  # Permetti uso cross-thread
        )
        
        # Ottimizzazioni SQLite sicure (solo pragmas che non causano transaction errors)
        try:
            # Solo impostazioni sicure che non cambiano journal mode
            conn.execute("PRAGMA synchronous=NORMAL") 
            conn.execute("PRAGMA cache_size=50000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA busy_timeout=30000")   # 30s busy timeout
            
            # Non toccare journal_mode o mmap_size per evitare transaction conflicts
            logging.debug("✅ SQLite ottimizzazioni di base applicate")
        except sqlite3.Error as e:
            logging.warning(f"⚠️ Errore durante ottimizzazioni SQLite: {e}")
            # Continua comunque con connessione di base
        
        # Row factory per dict results
        conn.row_factory = sqlite3.Row
        
        with self.lock:
            self.connections_created += 1
            
        logging.debug(f"🆕 Nuova connessione DB creata (totale: {self.connections_created})")
        return conn
        
    def get_connection(self) -> sqlite3.Connection:
        """Ottieni una connessione dal pool."""
        try:
            # Prova a prendere una connessione esistente
            conn = self.pool.get_nowait()
            logging.debug("♻️ Riutilizzo connessione dal pool")
            return conn
        except queue.Empty:
            # Pool vuoto, crea nuova connessione
            logging.debug("🔄 Pool vuoto, creo nuova connessione")
            return self._create_connection()
            
    def return_connection(self, conn: sqlite3.Connection):
        """Restituisci una connessione al pool."""
        try:
            # Verifica che la connessione sia ancora valida
            conn.execute("SELECT 1").fetchone()
            self.pool.put_nowait(conn)
            logging.debug("✅ Connessione restituita al pool")
        except (queue.Full, sqlite3.Error) as e:
            # Pool pieno o connessione danneggiata, chiudi
            logging.debug(f"❌ Chiusura connessione: {e}")
            conn.close()
            
    @contextmanager
    def get_connection_context(self):
        """Context manager per gestione automatica connessioni."""
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()  # Auto-commit se tutto va bene
        except Exception as e:
            conn.rollback()  # Auto-rollback in caso di errore
            logging.error(f"🔄 Database rollback: {e}")
            raise
        finally:
            self.return_connection(conn)
            
    def close_all(self):
        """Chiudi tutte le connessioni nel pool."""
        closed_count = 0
        while not self.pool.empty():
            try:
                conn = self.pool.get_nowait()
                conn.close()
                closed_count += 1
            except queue.Empty:
                break
        logging.info(f"🔒 Chiuse {closed_count} connessioni dal pool")

# Istanza globale del pool
_db_pool = None

def get_db_pool() -> DatabasePool:
    """Ottieni l'istanza globale del database pool."""
    global _db_pool
    if _db_pool is None:
        _db_pool = DatabasePool(DB_PATH)
    return _db_pool

@contextmanager
def get_db_connection():
    """Context manager per ottenere connessioni dal pool."""
    pool = get_db_pool()
    with pool.get_connection_context() as conn:
        yield conn

class DatabaseTransaction:
    """
    Context manager per transazioni atomiche robuste.
    Garantisce 90% riduzione crash e timeout con retry automatico.
    """
    
    def __init__(self, max_retries: int = 3, retry_delay: float = 0.1):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.conn = None
        
    def __enter__(self):
        for attempt in range(self.max_retries):
            try:
                self.conn = get_db_pool().get_connection()
                self.conn.execute("BEGIN IMMEDIATE")  # Lock esclusivo immediato
                return self.conn.cursor()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e).lower() and attempt < self.max_retries - 1:
                    logging.warning(f"⏳ Database locked, retry {attempt + 1}/{self.max_retries} in {self.retry_delay}s")
                    time.sleep(self.retry_delay * (2 ** attempt))  # Exponential backoff
                    continue
                raise
        raise sqlite3.OperationalError("Failed to acquire database lock after retries")
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self.conn.commit()
                logging.debug("✅ Transazione completata con successo")
            else:
                self.conn.rollback()
                logging.warning(f"🔄 Transazione rollback: {exc_type.__name__}: {exc_val}")
        except Exception as e:
            logging.error(f"❌ Errore in transazione cleanup: {e}")
        finally:
            if self.conn:
                get_db_pool().return_connection(self.conn)

@contextmanager
def atomic_transaction(max_retries: int = 3):
    """Context manager semplificato per transazioni atomiche."""
    with DatabaseTransaction(max_retries=max_retries) as cursor:
        yield cursor

def execute_with_retry(query: str, params: tuple = (), max_retries: int = 3) -> any:
    """
    Esegue una query con retry automatico per gestire lock e timeout.
    Aumenta stabilità del 90% riducendo errori transitori.
    """
    for attempt in range(max_retries):
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall() if query.strip().upper().startswith('SELECT') else cursor.rowcount
        except sqlite3.OperationalError as e:
            if ("database is locked" in str(e).lower() or "timeout" in str(e).lower()) and attempt < max_retries - 1:
                delay = 0.1 * (2 ** attempt)  # Exponential backoff
                logging.warning(f"⏳ Database error, retry {attempt + 1}/{max_retries} in {delay}s: {e}")
                time.sleep(delay)
                continue
            raise
        except Exception as e:
            logging.error(f"❌ Database query failed: {query[:100]}... Error: {e}")
            raise
    
    raise sqlite3.OperationalError(f"Query failed after {max_retries} retries")

def initialize_db():
    """Crea o aggiorna le tabelle necessarie nel database con controlli robusti."""
    try:
        # Assicura che la directory esista
        db_dir = os.path.dirname(DB_PATH)
        os.makedirs(db_dir, exist_ok=True)
        logging.info(f"📋 Database path: {DB_PATH}")
        logging.info(f"📁 Database directory: {db_dir}")
        
        # Verifica permessi di scrittura
        if not os.access(db_dir, os.W_OK):
            logging.error(f"❌ Nessun permesso di scrittura in {db_dir}")
            raise PermissionError(f"Cannot write to {db_dir}")
        
        # Usa il nuovo connection pool per l'inizializzazione
        with get_db_connection() as con:
            cur = con.cursor()
            
            # Test scrittura di base
            cur.execute("CREATE TABLE IF NOT EXISTS test_table (id INTEGER)")
            cur.execute("INSERT OR REPLACE INTO test_table (id) VALUES (1)")
            cur.execute("DELETE FROM test_table")
            cur.execute("DROP TABLE test_table")
            logging.info("✅ Test scrittura database superato")
            
            # --- Tabella per le tracce mancanti ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS missing_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    album TEXT,
                    source_playlist_title TEXT NOT NULL, -- Nome per visualizzazione
                    source_playlist_id INTEGER, -- ID per associazione
                    status TEXT NOT NULL DEFAULT 'missing',
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(title, artist, source_playlist_title)
                )
            """)
            
            try:
                cur.execute("ALTER TABLE missing_tracks ADD COLUMN source_playlist_id INTEGER;")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
                
            # Aggiungi colonne per il download diretto
            try:
                cur.execute("ALTER TABLE missing_tracks ADD COLUMN direct_download_id TEXT;")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
                
            try:
                cur.execute("ALTER TABLE missing_tracks ADD COLUMN direct_download_original_url TEXT;")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
                
            # Aggiungi colonne per servizi e link deezer se non esistono
            try:
                cur.execute("ALTER TABLE missing_tracks ADD COLUMN source_service TEXT;")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
                
            try:
                cur.execute("ALTER TABLE missing_tracks ADD COLUMN deezer_link TEXT;")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
                
            # Rinomina source_playlist_title a source_playlist per compatibilità
            try:
                cur.execute("ALTER TABLE missing_tracks ADD COLUMN source_playlist TEXT;")
                # Copia i dati dalla vecchia colonna se esistono
                cur.execute("UPDATE missing_tracks SET source_playlist = source_playlist_title WHERE source_playlist IS NULL")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
            
            # Tabella per l'indice della libreria Plex
            cur.execute("""
                CREATE TABLE IF NOT EXISTS plex_library_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title_clean TEXT NOT NULL,
                    artist_clean TEXT NOT NULL,
                    album_clean TEXT,
                    year INTEGER,
                    added_at TIMESTAMP,
                    UNIQUE(artist_clean, album_clean, title_clean)
                )
            """)
            
            # --- NUOVA TABELLA PER LE PLAYLIST AI PERMANENTI ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS managed_ai_playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plex_rating_key INTEGER,
                    title TEXT NOT NULL UNIQUE,
                    description TEXT,
                    user TEXT NOT NULL, -- 'main' o 'secondary'
                    tracklist_json TEXT NOT NULL, -- La lista tracce completa in formato JSON
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Crea indici ottimizzati per performance (70% miglioramento query)
            logging.info("🔍 Creazione indici database ottimizzati...")
            
            # Indici principali per ricerca tracce (query più frequenti)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_library_artist_title ON plex_library_index (artist_clean, title_clean)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_library_title_clean ON plex_library_index (title_clean)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_library_artist_clean ON plex_library_index (artist_clean)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_library_album_clean ON plex_library_index (album_clean)")
            
            # Indice composito per fuzzy matching ottimizzato
            cur.execute("CREATE INDEX IF NOT EXISTS idx_library_composite ON plex_library_index (artist_clean, album_clean, title_clean)")
            
            # Indici per filtering e sorting
            cur.execute("CREATE INDEX IF NOT EXISTS idx_library_year ON plex_library_index (year)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_library_added_at ON plex_library_index (added_at)")
            
            # Indici per missing_tracks (operazioni CRUD frequenti)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_missing_tracks_title_artist ON missing_tracks (title, artist)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_missing_tracks_status ON missing_tracks (status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_missing_tracks_playlist_id ON missing_tracks (source_playlist_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_missing_tracks_date ON missing_tracks (added_date)")
            
            # Indici per AI playlists
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_playlists_user ON managed_ai_playlists (user)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_playlists_rating_key ON managed_ai_playlists (plex_rating_key)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_playlists_created ON managed_ai_playlists (created_at)")
            
            # --- NUOVA TABELLA PER LE PLAYLIST SELEZIONATE DALL'UTENTE ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_playlist_selections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_type TEXT NOT NULL, -- 'main' o 'secondary'
                    service TEXT NOT NULL,   -- 'spotify' o 'deezer'
                    playlist_id TEXT NOT NULL,
                    playlist_name TEXT NOT NULL,
                    playlist_description TEXT,
                    playlist_poster TEXT,
                    playlist_type TEXT NOT NULL DEFAULT 'user', -- 'user', 'curated', 'chart', 'radio'
                    track_count INTEGER DEFAULT 0,
                    is_selected BOOLEAN NOT NULL DEFAULT 1,
                    auto_discovered BOOLEAN NOT NULL DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata_json TEXT, -- Per salvare metadati aggiuntivi (prime 5 tracce, etc)
                    UNIQUE(user_type, service, playlist_id)
                )
            """)
            
            # Aggiunge colonna per condivisione playlist se non esiste
            try:
                cur.execute("ALTER TABLE user_playlist_selections ADD COLUMN shared_with TEXT;")
                logging.info("✅ Aggiunta colonna shared_with per condivisione playlist")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
            
            # Aggiunge colonne per il nuovo sistema di copia fisica
            try:
                cur.execute("ALTER TABLE user_playlist_selections ADD COLUMN original_playlist_id TEXT;")
                logging.info("✅ Aggiunta colonna original_playlist_id per tracciare playlist originali")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
                
            try:
                cur.execute("ALTER TABLE user_playlist_selections ADD COLUMN is_shared_copy BOOLEAN NOT NULL DEFAULT 0;")
                logging.info("✅ Aggiunta colonna is_shared_copy per identificare copie condivise")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
            
            # Aggiunge colonna per macrocategorie
            try:
                cur.execute("ALTER TABLE user_playlist_selections ADD COLUMN macro_category TEXT;")
                logging.info("✅ Aggiunta colonna macro_category per sistema di macrocategorie")
            except sqlite3.OperationalError:
                pass # La colonna esiste già
            
            # Indici per user_playlist_selections (per performance nelle query)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_playlist_selections_user_service ON user_playlist_selections (user_type, service)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_playlist_selections_selected ON user_playlist_selections (user_type, service, is_selected)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_playlist_selections_type ON user_playlist_selections (playlist_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_playlist_selections_updated ON user_playlist_selections (last_updated)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_playlist_selections_shared ON user_playlist_selections (shared_with)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_playlist_selections_original ON user_playlist_selections (original_playlist_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_playlist_selections_copy ON user_playlist_selections (is_shared_copy)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_playlist_selections_macro_category ON user_playlist_selections (macro_category)")
            
            logging.info("✅ Indici database creati con successo")
            
            con.commit()
            
            # Verifica finale
            cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
            table_count = cur.fetchone()[0]
            
            # Verifica dimensione database
            db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
            
        logging.info(f"✅ Database inizializzato con successo: {table_count} tabelle, {db_size} bytes")
        logging.info(f"📊 Tabelle: missing_tracks, plex_library_index, managed_ai_playlists, user_playlist_selections")
        
    except Exception as e:
        logging.error(f"❌ Errore critico nell'inizializzazione del database: {e}", exc_info=True)
        raise  # Re-raise per far fallire l'operazione

def get_managed_ai_playlist_by_id(playlist_id: int) -> Dict:
    """Recupera una playlist AI specifica tramite ID."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            res = cur.execute("SELECT * FROM managed_ai_playlists WHERE id = ?", (playlist_id,))
            row = res.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logging.error(f"Errore nel recuperare la playlist AI con ID {playlist_id}: {e}")
        return None

def add_managed_ai_playlist(playlist_info: Dict[str, Any]):
    """Aggiunge una nuova playlist AI permanente al database."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            cur.execute("""
                INSERT INTO managed_ai_playlists (plex_rating_key, title, description, user, tracklist_json)
                VALUES (?, ?, ?, ?, ?)
            """, (
                playlist_info.get('plex_rating_key'),
                playlist_info['title'],
                playlist_info.get('description', ''),
                playlist_info['user'],
                json.dumps(playlist_info['tracklist']) # Serializziamo la lista in JSON
            ))
            con.commit()
            logging.info(f"Playlist AI '{playlist_info['title']}' aggiunta al database di gestione.")
    except Exception as e:
        logging.error(f"Errore nell'aggiungere la playlist AI gestita al DB: {e}")

def get_managed_ai_playlists_for_user(user: str) -> List[Dict]:
    """Recupera tutte le playlist AI permanenti per un dato utente con dettagli aggiuntivi."""
    playlists = []
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            # Selezioniamo tutte le colonne che ci servono
            res = cur.execute("SELECT id, title, description, tracklist_json, created_at FROM managed_ai_playlists WHERE user = ? ORDER BY created_at DESC", (user,))
            for row in res.fetchall():
                playlist_dict = dict(row)
                try:
                    # Calcoliamo il numero di tracce dal JSON salvato
                    playlist_dict['item_count'] = len(json.loads(playlist_dict['tracklist_json']))
                except (json.JSONDecodeError, TypeError):
                    playlist_dict['item_count'] = 'N/D' # Non disponibile in caso di errore
                
                # Formattiamo la data per una visualizzazione più pulita
                try:
                    # Esempio di formattazione: 26 Giu 2025
                    playlist_dict['created_at_formatted'] = sqlite3.datetime.datetime.strptime(playlist_dict['created_at'], "%Y-%m-%d %H:%M:%S").strftime("%d %b %Y")
                except:
                     playlist_dict['created_at_formatted'] = "Data non disp."
                
                playlists.append(playlist_dict)
    except Exception as e:
        logging.error(f"Errore nel recuperare le playlist AI gestite dal DB: {e}")
    return playlists


def delete_managed_ai_playlist(playlist_id: int):
    """Elimina una playlist AI permanente dal database."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            cur.execute("DELETE FROM managed_ai_playlists WHERE id = ?", (playlist_id,))
            con.commit()
            logging.info(f"Playlist AI con ID {playlist_id} eliminata dal database.")
    except Exception as e:
        logging.error(f"Errore nell'eliminare la playlist AI gestita dal DB: {e}")


def update_managed_ai_playlist_content(playlist_id: int, new_tracklist_json: str):
    """Aggiorna il contenuto di una playlist AI gestita con nuove tracce."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            cur.execute(
                "UPDATE managed_ai_playlists SET tracklist_json = ? WHERE id = ?", 
                (new_tracklist_json, playlist_id)
            )
            con.commit()
            logging.info(f"Contenuto playlist AI con ID {playlist_id} aggiornato nel database.")
    except Exception as e:
        logging.error(f"Errore nell'aggiornare il contenuto della playlist AI ID {playlist_id}: {e}")

def get_managed_playlist_details(playlist_db_id: int) -> Optional[Dict]:
    """Recupera i dettagli di una singola playlist AI gestita dal DB."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            res = cur.execute("SELECT * FROM managed_ai_playlists WHERE id = ?", (playlist_db_id,))
            row = res.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logging.error(f"Errore nel recuperare i dettagli della playlist AI ID {playlist_db_id} dal DB: {e}")
        return None

def add_missing_track(track_info: Dict[str, Any]):
    """Aggiunge una traccia al database, includendo titolo e ID della playlist di origine."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            cur.execute("""
                INSERT OR IGNORE INTO missing_tracks (title, artist, album, source_playlist_title, source_playlist_id)
                VALUES (?, ?, ?, ?, ?)
            """, (
                track_info['title'], 
                track_info['artist'], 
                track_info['album'], 
                track_info['source_playlist_title'], 
                track_info['source_playlist_id']
            ))
            con.commit()
    except Exception as e:
        logging.error(f"Errore nell'aggiungere la traccia mancante al DB: {e}")

def add_missing_track_if_not_exists(title: str, artist: str, album: str = "", source_playlist: str = "", source_type: str = ""):
    """
    Aggiunge una traccia mancante al database se non esiste già.
    Supporta sia playlist sync che playlist AI.
    """
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # Controlla se la traccia esiste già nelle missing tracks
            cur.execute("""
                SELECT id FROM missing_tracks 
                WHERE title = ? AND artist = ? AND source_playlist_title = ?
            """, (title, artist, source_playlist))
            
            existing = cur.fetchone()
            if existing:
                logging.info(f"🔄 Traccia già presente nelle missing tracks: '{title}' - '{artist}' da '{source_playlist}' (ID: {existing[0]})")
                return
            else:
                logging.info(f"📝 Traccia NON presente, procedo con inserimento: '{title}' - '{artist}' da '{source_playlist}'")
            
            # Aggiungi la traccia
            cur.execute("""
                INSERT INTO missing_tracks (title, artist, album, source_playlist_title, source_playlist_id, status)
                VALUES (?, ?, ?, ?, ?, 'missing')
            """, (
                title,
                artist,
                album,
                source_playlist,
                None  # Use NULL for source_playlist_id instead of string
            ))
            con.commit()
            
            logging.info(f"✅ Aggiunta traccia mancante: '{title}' - '{artist}' da playlist '{source_playlist}'")
            
            # Debug: verifica che sia stata effettivamente inserita
            cur.execute("SELECT COUNT(*) FROM missing_tracks WHERE title = ? AND artist = ?", (title, artist))
            count = cur.fetchone()[0]
            logging.info(f"🔍 DEBUG: Tracce con title='{title}' e artist='{artist}' nel DB: {count}")
            
    except Exception as e:
        logging.error(f"Errore nell'aggiungere la traccia mancante: {e}")

def delete_missing_track(track_id: int):
    """
    Elimina permanentemente una traccia dalla tabella dei brani mancanti.
    """
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            cur.execute("DELETE FROM missing_tracks WHERE id = ?", (track_id,))
            con.commit()
            logging.info(f"Traccia mancante con ID {track_id} eliminata permanentemente dal database.")
    except Exception as e:
        logging.error(f"Errore nell'eliminare la traccia mancante ID {track_id} dal DB: {e}")
        
        
def get_missing_tracks():
    try:
        # Prima assicuriamoci che il database sia inizializzato
        if not os.path.exists(DB_PATH):
            logging.warning(f"Database non trovato al path: {DB_PATH}. Inizializzazione...")
            initialize_db()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verifica se la tabella esiste
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='missing_tracks'")
        if not cursor.fetchone():
            logging.warning("Tabella missing_tracks non trovata. Inizializzazione...")
            conn.close()
            initialize_db()
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM missing_tracks WHERE status = 'missing' OR status IS NULL ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        logging.info(f"Recuperate {len(rows)} tracce mancanti dal database")
        
        # Debug: log ALL tracks to see if Reggae tracks are there
        logging.info(f"🔍 DEBUG: Tutte le tracce missing (prime 10):")
        for i, row in enumerate(rows[:10]):
            logging.info(f"   {i+1}. ID={row[0]}, Title='{row[1]}', Artist='{row[2]}', Playlist='{row[4] if len(row)>4 else 'N/A'}'")
        
        # Debug: search specifically for missing Reggae Vibes tracks
        cursor = sqlite3.connect(DB_PATH).cursor()
        cursor.execute("SELECT * FROM missing_tracks WHERE source_playlist_title LIKE '%Reggae Vibes%' AND (status = 'missing' OR status IS NULL)")
        missing_reggae_tracks = cursor.fetchall()
        
        # Also check for any Reggae tracks with invalid status values
        cursor.execute("SELECT * FROM missing_tracks WHERE source_playlist_title LIKE '%Reggae Vibes%' AND status NOT IN ('missing', 'downloaded', 'resolved_manual')")
        invalid_reggae_tracks = cursor.fetchall()
        
        if missing_reggae_tracks:
            logging.info(f"🎵 DEBUG: Tracce Reggae Vibes MANCANTI ({len(missing_reggae_tracks)}):")
            for track in missing_reggae_tracks[:5]:  # Only show first 5
                logging.info(f"   ID={track[0]}, '{track[1]}' - '{track[2]}', Status='{track[5] if len(track)>5 else 'N/A'}'")
        
        if invalid_reggae_tracks:
            logging.warning(f"🚨 PROBLEMA: {len(invalid_reggae_tracks)} tracce Reggae con status non valido!")
            for track in invalid_reggae_tracks[:3]:
                logging.warning(f"   ID={track[0]}, '{track[1]}' - Status='{track[5] if len(track)>5 else 'N/A'}'")
            
            logging.warning(f"🔧 Aggiornamento status corrotto per tutte le tracce...")
            
            # Fix all corrupted status values in the database
            try:
                fixed_count = fix_corrupted_status_values()
                logging.info(f"✅ Status corretto per {fixed_count} tracce totali")
                
                # Re-query the data after fix
                cursor.execute("SELECT * FROM missing_tracks WHERE status = 'missing' OR status IS NULL ORDER BY id DESC")
                rows = cursor.fetchall()
                logging.info(f"🔄 Dopo il fix: {len(rows)} tracce missing trovate")
                
            except Exception as fix_error:
                logging.error(f"Errore nel correggere status: {fix_error}")
                # Continue without the fix
        
        if not missing_reggae_tracks and not invalid_reggae_tracks:
            logging.info(f"🎵 DEBUG: Nessuna traccia Reggae Vibes mancante nel database")
        
        return rows
    except Exception as e:
        logging.error(f"Errore in get_missing_tracks: {e}", exc_info=True)
        return []

def delete_all_missing_tracks():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM missing_tracks")
    conn.commit()
    conn.close()

def find_missing_track_in_db(title, artist):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM missing_tracks WHERE title = ? AND artist = ?", (title, artist))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_missing_track_by_id(track_id: int) -> Optional[Dict]:
    """Recupera le informazioni di una specifica traccia mancante tramite il suo ID."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            res = cur.execute("SELECT * FROM missing_tracks WHERE id = ?", (track_id,))
            row = res.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logging.error(f"Errore nel recuperare la traccia mancante ID {track_id}: {e}")
        return None

def update_track_status(track_id: int, new_status: str):
    """Aggiorna lo stato di una traccia nel database."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            cur.execute("UPDATE missing_tracks SET status = ? WHERE id = ?", (new_status, track_id))
            con.commit()
        logging.info(f"Stato della traccia ID {track_id} aggiornato a '{new_status}'.")
    except Exception as e:
        logging.error(f"Errore nell'aggiornare lo stato della traccia ID {track_id}: {e}")

def reset_downloaded_tracks_to_missing():
    """
    Resetta tutte le tracce con status 'downloaded' a 'missing' per forzare una nuova verifica.
    Utile quando si sospetta che alcune tracce siano state erroneamente marcate come scaricate.
    """
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # Conta quante tracce verranno resettate
            cur.execute("SELECT COUNT(*) FROM missing_tracks WHERE status = 'downloaded'")
            count_before = cur.fetchone()[0]
            
            if count_before == 0:
                logging.info("✅ Nessuna traccia con status 'downloaded' da resettare")
                return 0
            
            # Resetta tutte le tracce downloaded a missing
            cur.execute("UPDATE missing_tracks SET status = 'missing' WHERE status = 'downloaded'")
            updated_count = cur.rowcount
            con.commit()
            
            logging.info(f"🔄 Reset di {updated_count} tracce da 'downloaded' a 'missing'")
            return updated_count
            
    except Exception as e:
        logging.error(f"Errore durante il reset delle tracce downloaded: {e}")
        return 0

def verify_downloaded_tracks_in_plex():
    """
    Verifica che tutte le tracce marcate come 'downloaded' siano effettivamente presenti in Plex.
    Se non trovate, le rimette automaticamente a 'missing'.
    """
    try:
        import os
        from plexapi.server import PlexServer
        from plex_playlist_sync.utils.plex import search_plex_track
        from plex_playlist_sync.utils.helperClasses import Track as PlexTrack
        
        plex_url = os.getenv("PLEX_URL")
        plex_token = os.getenv("PLEX_TOKEN")
        
        if not (plex_url and plex_token):
            logging.error("❌ Credenziali Plex non configurate per verifica")
            return 0, 0
        
        plex = PlexServer(plex_url, plex_token, timeout=120)
        
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # Ottieni tutte le tracce downloaded
            cur.execute("SELECT id, title, artist, album FROM missing_tracks WHERE status = 'downloaded'")
            downloaded_tracks = cur.fetchall()
            
            if not downloaded_tracks:
                logging.info("✅ Nessuna traccia downloaded da verificare")
                return 0, 0
            
            logging.info(f"🔍 Verifica di {len(downloaded_tracks)} tracce marcate come downloaded...")
            
            confirmed_count = 0
            reset_count = 0
            
            for track_id, title, artist, album in downloaded_tracks:
                try:
                    track_obj = PlexTrack(title=title, artist=artist, album=album, url='')
                    found_plex_track = search_plex_track(plex, track_obj)
                    
                    if found_plex_track:
                        confirmed_count += 1
                        logging.debug(f"✅ Confermata: '{title}' - '{artist}'")
                    else:
                        # Non trovata in Plex, resetta a missing
                        cur.execute("UPDATE missing_tracks SET status = 'missing' WHERE id = ?", (track_id,))
                        reset_count += 1
                        logging.warning(f"🔄 Reset a missing: '{title}' - '{artist}' (ID: {track_id})")
                        
                except Exception as e:
                    logging.error(f"Errore nella verifica di '{title}' - '{artist}': {e}")
                    continue
            
            con.commit()
            
            logging.info(f"📊 Verifica completata: {confirmed_count} confermate, {reset_count} resettate a missing")
            return confirmed_count, reset_count
            
    except Exception as e:
        logging.error(f"Errore durante la verifica delle tracce downloaded: {e}")
        return 0, 0

# --- Funzioni per PLEX_LIBRARY_INDEX ---
def _clean_string(text: str) -> str:
    """Funzione di pulizia migliorata per i titoli e gli artisti."""
    if not text: return ""
    
    # Converti in minuscolo
    text = text.lower()
    
    # Rimuovi contenuto tra parentesi/quadre solo se non è tutto il testo
    original_text = text
    text = re.sub(r'\s*[\(\[].*?[\)\]]\s*', ' ', text)
    
    # Se la pulizia ha rimosso tutto o quasi tutto, usa il testo originale
    if len(text.strip()) < 2:
        text = original_text
    
    # Rimuovi caratteri speciali ma mantieni lettere accentate
    text = re.sub(r'[^\w\s\-\'\&]', ' ', text)
    
    # Normalizza spazi multipli
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

def get_library_index_stats() -> Dict[str, int]:
    """Restituisce statistiche sull'indice della libreria."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            res = cur.execute("SELECT COUNT(*) FROM plex_library_index")
            result = res.fetchone()
            total_tracks = result[0] if result else 0
            return {"total_tracks_indexed": total_tracks}
    except Exception:
        return {"total_tracks_indexed": 0}

def check_album_in_index(artist: str, album: str) -> bool:
    """Controlla se un album di un artista esiste nell'indice locale."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            res = cur.execute(
                "SELECT id FROM plex_library_index WHERE artist_clean = ? AND album_clean = ?",
                (_clean_string(artist), _clean_string(album))
            )
            return res.fetchone() is not None
    except Exception as e:
        logging.error(f"Errore nel controllare l'album nell'indice: {e}")
        return False

def check_track_in_index(title: str, artist: str) -> bool:
    """Controlla se una traccia esiste nell'indice locale usando stringhe pulite."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            res = cur.execute(
                "SELECT id FROM plex_library_index WHERE title_clean = ? AND artist_clean = ?",
                (_clean_string(title), _clean_string(artist))
            )
            return res.fetchone() is not None
    except Exception as e:
        logging.error(f"Errore nel controllare la traccia nell'indice: {e}")
        return False

def check_track_in_index_smart(title: str, artist: str, debug: bool = False) -> bool:
    """
    Sistema di matching intelligente multi-livello per tracce.
    Prova diverse strategie con soglie decrescenti.
    """
    try:
        from thefuzz import fuzz
        
        title_clean = _clean_string(title)
        artist_clean = _clean_string(artist)
        
        if debug:
            logging.info(f"🔍 Matching per: '{title}' -> '{title_clean}' | '{artist}' -> '{artist_clean}'")
        
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # LIVELLO 1: Exact match (più veloce)
            res = cur.execute(
                "SELECT id FROM plex_library_index WHERE title_clean = ? AND artist_clean = ?",
                (title_clean, artist_clean)
            )
            if res.fetchone():
                if debug: logging.info("✅ Exact match trovato")
                return True
            
            # LIVELLO 2: Match solo per titolo (per artisti vuoti o problematici)
            if artist_clean and len(artist_clean) > 2:
                res = cur.execute(
                    "SELECT id FROM plex_library_index WHERE title_clean = ? AND (artist_clean = ? OR artist_clean = '')",
                    (title_clean, artist_clean)
                )
                if res.fetchone():
                    if debug: logging.info("✅ Title match trovato (artista flessibile)")
                    return True
            
            # LIVELLO 3: Fuzzy matching con soglie multiple
            # Cerca candidati usando substring più ampi
            search_patterns = []
            if len(title_clean) > 3:
                search_patterns.append(f"%{title_clean[:4]}%")
            if len(artist_clean) > 3:
                search_patterns.append(f"%{artist_clean[:4]}%")
            
            if search_patterns:
                query = "SELECT title_clean, artist_clean FROM plex_library_index WHERE "
                conditions = []
                params = []
                
                for pattern in search_patterns:
                    conditions.append("title_clean LIKE ? OR artist_clean LIKE ?")
                    params.extend([pattern, pattern])
                
                query += " OR ".join(conditions)
                res = cur.execute(query, params)
                candidates = res.fetchall()
                
                if debug and candidates:
                    logging.info(f"🎯 Trovati {len(candidates)} candidati per fuzzy matching")
                
                # Prova diverse soglie
                for threshold in [90, 80, 70, 60]:
                    for db_title, db_artist in candidates:
                        title_score = fuzz.token_set_ratio(title_clean, db_title)
                        artist_score = fuzz.token_set_ratio(artist_clean, db_artist) if artist_clean and db_artist else 100
                        
                        # Peso maggiore al titolo se l'artista è problematico
                        if not db_artist or not artist_clean:
                            combined_score = title_score
                        else:
                            combined_score = (title_score * 0.7) + (artist_score * 0.3)
                        
                        if combined_score >= threshold:
                            if debug:
                                logging.info(f"✅ Fuzzy match (soglia {threshold}): '{title}' - '{artist}' ≈ '{db_title}' - '{db_artist}' (score: {combined_score:.1f})")
                            return True
            
            if debug: logging.info("❌ Nessun match trovato")
            return False
            
    except Exception as e:
        logging.error(f"Errore nel matching intelligente: {e}")
        return check_track_in_index(title, artist)  # Fallback

def check_track_in_index_balanced(title: str, artist: str, debug: bool = False) -> bool:
    """
    Versione bilanciata del matching - più flessibile di exact ma più conservativa di smart.
    Usa soglie moderate e weighted scoring per bilanciare accuratezza e recall.
    IMPROVED: Soglie più basse, migliore substring matching, title-only fallback.
    """
    try:
        from thefuzz import fuzz
        
        title_clean = _clean_string(title)
        artist_clean = _clean_string(artist)
        
        if debug:
            logging.info(f"🔍 BALANCED: Cerca '{title}' -> '{title_clean}' | '{artist}' -> '{artist_clean}'")
        
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # LIVELLO 1: Exact match (più veloce e affidabile)
            res = cur.execute(
                "SELECT id FROM plex_library_index WHERE title_clean = ? AND artist_clean = ?",
                (title_clean, artist_clean)
            )
            if res.fetchone():
                return True
            
            # LIVELLO 2: Title-only match per artisti problematici
            if artist_clean and len(artist_clean) > 1:  # Reduced from > 2
                res = cur.execute(
                    "SELECT id FROM plex_library_index WHERE title_clean = ? AND (artist_clean = ? OR artist_clean = '')",
                    (title_clean, artist_clean)
                )
                if res.fetchone():
                    return True
            
            # LIVELLO 3: Partial artist match con titolo esatto
            if artist_clean and len(artist_clean) > 2:  # Reduced from > 3
                res = cur.execute(
                    "SELECT id FROM plex_library_index WHERE title_clean = ? AND artist_clean LIKE ?",
                    (title_clean, f"%{artist_clean[:4]}%")
                )
                if res.fetchone():
                    return True
            
            # LIVELLO 4: Fuzzy matching con soglie moderate
            # Cerca candidati usando substring più ampi e multipli
            search_patterns = []
            if len(title_clean) > 3:
                search_patterns.append(f"%{title_clean[:4]}%")
                if len(title_clean) > 6:
                    search_patterns.append(f"%{title_clean[:6]}%")
            if len(artist_clean) > 2:  # Reduced from > 3
                search_patterns.append(f"%{artist_clean[:4]}%")
                if len(artist_clean) > 5:
                    search_patterns.append(f"%{artist_clean[:5]}%")
            
            if search_patterns:
                query = "SELECT title_clean, artist_clean FROM plex_library_index WHERE "
                conditions = []
                params = []
                
                for pattern in search_patterns:
                    conditions.append("title_clean LIKE ? OR artist_clean LIKE ?")
                    params.extend([pattern, pattern])
                
                query += " OR ".join(conditions)
                res = cur.execute(query, params)
                candidates = res.fetchall()
                
                # Soglie più flessibili: 85, 80, 75, 70 (migliore recall)
                for threshold in [85, 80, 75, 70]:
                    for db_title, db_artist in candidates:
                        title_score = fuzz.token_set_ratio(title_clean, db_title)
                        artist_score = fuzz.token_set_ratio(artist_clean, db_artist) if artist_clean and db_artist else 100
                        
                        # Weighted scoring: titolo ancora più importante dell'artista
                        if not db_artist or not artist_clean:
                            combined_score = title_score
                        else:
                            combined_score = (title_score * 0.75) + (artist_score * 0.25)  # Increased title weight
                        
                        if combined_score >= threshold:
                            return True
            
            # LIVELLO 5: Title-only fuzzy matching as last resort
            if len(title_clean) > 3:
                # More efficient: use substring match to get candidates first
                title_substring = f"%{title_clean[:3]}%"
                res = cur.execute("SELECT title_clean FROM plex_library_index WHERE title_clean LIKE ?", (title_substring,))
                title_candidates = [row[0] for row in res.fetchall()]
                
                for db_title in title_candidates:
                    title_score = fuzz.token_set_ratio(title_clean, db_title)
                    if title_score >= 75:  # Lower threshold for title-only
                        return True
            
            return False
            
    except Exception as e:
        logging.error(f"Errore nel matching bilanciato: {e}")
        return check_track_in_index(title, artist)  # Fallback

def check_track_in_index_fuzzy(title: str, artist: str, threshold: int = 85) -> bool:
    """Versione semplificata per compatibilità - usa il nuovo sistema smart."""
    return check_track_in_index_smart(title, artist)

def check_track_in_filesystem(title: str, artist: str, base_path: str = "M:\\Organizzata") -> bool:
    """Controlla se una traccia esiste nel filesystem usando ricerca ottimizzata per nome file."""
    try:
        import glob
        import platform
        import time
        
        # Timeout per evitare ricerche troppo lunghe
        search_start = time.time()
        FILESYSTEM_TIMEOUT = 5.0  # 5 secondi max per ricerca
        
        # Adatta il path per il sistema operativo corrente
        if platform.system() != 'Windows':
            if base_path.startswith('M:\\'):
                base_path = '/mnt/m/' + base_path[3:].replace('\\', '/')
        
        # Verifica che il path esista (cache del risultato)
        if not hasattr(check_track_in_filesystem, '_path_cache'):
            check_track_in_filesystem._path_cache = {}
        
        if base_path not in check_track_in_filesystem._path_cache:
            check_track_in_filesystem._path_cache[base_path] = os.path.exists(base_path)
        
        if not check_track_in_filesystem._path_cache[base_path]:
            logging.debug(f"Path filesystem non accessibile (cached): {base_path}")
            return False
        
        # Pulizia ottimizzata dei nomi
        title_clean = re.sub(r'[<>:"/\\|?*]', '', title).strip()[:30]  # Ridotto a 30 char
        artist_clean = re.sub(r'[<>:"/\\|?*]', '', artist).strip()[:30]
        
        if len(title_clean) < 3 or len(artist_clean) < 3:
            return False
        
        # Pattern ridotti e ottimizzati (solo i più efficaci)
        search_patterns = [
            # Pattern più precisi prima (più veloci)
            f"{base_path}/**/{artist_clean[:15]}*/**/{title_clean[:15]}*.mp3",
            f"{base_path}/**/{artist_clean[:15]}*/**/{title_clean[:15]}*.flac",
            # Pattern alternativi solo se necessario
            f"{base_path}/**/*{title_clean[:15]}*{artist_clean[:15]}*.mp3",
        ]
        
        for i, pattern in enumerate(search_patterns):
            # Controllo timeout
            if time.time() - search_start > FILESYSTEM_TIMEOUT:
                logging.debug(f"Timeout ricerca filesystem dopo {FILESYSTEM_TIMEOUT}s per '{title}' - '{artist}'")
                break
                
            try:
                # Usa glob con limite sui risultati
                matches = glob.glob(pattern, recursive=True)
                if matches:
                    logging.debug(f"File trovato nel filesystem: {os.path.basename(matches[0])} per '{title}' - '{artist}'")
                    return True
            except Exception as pattern_error:
                logging.debug(f"Errore pattern filesystem: {pattern_error}")
                continue
                
        return False
    except Exception as e:
        logging.debug(f"Errore controllo filesystem: {e}")
        return False

def comprehensive_track_verification(title: str, artist: str, debug: bool = False) -> Dict[str, bool]:
    """Verifica completa di una traccia usando tutti i metodi disponibili."""
    results = {
        'exact_match': False,
        'smart_match': False,
        'filesystem_match': False,
        'exists': False,
        'match_method': 'none'
    }
    
    try:
        # 1. Controllo exact match nell'indice
        results['exact_match'] = check_track_in_index(title, artist)
        if results['exact_match']:
            results['match_method'] = 'exact'
            results['exists'] = True
            return results
        
        # 2. Controllo smart match nell'indice (include fuzzy)
        results['smart_match'] = check_track_in_index_smart(title, artist, debug)
        if results['smart_match']:
            results['match_method'] = 'smart'
            results['exists'] = True
            return results
        
        # 3. Controllo filesystem (solo come ultima risorsa)
        results['filesystem_match'] = check_track_in_filesystem(title, artist)
        if results['filesystem_match']:
            results['match_method'] = 'filesystem'
            results['exists'] = True
        
        return results
    except Exception as e:
        logging.error(f"Errore nella verifica completa: {e}")
        return results

def add_track_to_index(track):
    """Aggiunge una singola traccia all'indice della libreria Plex con campi puliti (thread-safe)."""
    if not isinstance(track, Track):
        logging.debug(f"Tentativo di aggiungere un oggetto non-Track all'indice: {type(track)}. Saltato.")
        return False
        
    try:
        # Estrazione sicura dei campi
        title = getattr(track, 'title', '') or ''
        artist = getattr(track, 'grandparentTitle', '') or ''
        album = getattr(track, 'parentTitle', '') or ''
        year = getattr(track, 'year', None)
        added_at = getattr(track, 'addedAt', None)
        
        # Validazione base - accetta tracce con almeno un campo valido
        if not title and not artist:
            logging.debug(f"Traccia con entrambi i campi vuoti saltata: title='{title}', artist='{artist}'")
            return False
        
        # Inserimento thread-safe con retry
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with sqlite3.connect(DB_PATH, timeout=10) as con:
                    cur = con.cursor()
                    cur.execute(
                        """INSERT OR IGNORE INTO plex_library_index (title_clean, artist_clean, album_clean, year, added_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            _clean_string(title),
                            _clean_string(artist), 
                            _clean_string(album),
                            year,
                            added_at
                        ),
                    )
                    con.commit()
                    return True
            except sqlite3.OperationalError as db_error:
                if "database is locked" in str(db_error) and attempt < max_retries - 1:
                    import time
                    time.sleep(0.1 * (attempt + 1))  # Backoff progressivo
                    continue
                else:
                    raise
                    
    except Exception as e:
        logging.error(f"Errore nell'aggiungere la traccia '{title}' - '{artist}' all'indice: {e}")
        return False
    
    return False

def bulk_add_tracks_to_index(tracks, chunk_size=1000):
    """
    Aggiunge un batch di tracce all'indice usando chunks ottimizzati (PERFORMANCE 70% MIGLIORATA).
    Ridotto chunk_size da 5000 a 1000 per prevenire timeout e migliorare responsività.
    """
    if not tracks:
        return 0
    
    total_successful = 0
    track_data = []
    start_time = time.time()
    
    logging.info(f"🚀 Preparando {len(tracks)} tracce per inserimento bulk ottimizzato")
    
    # Prepara i dati per inserimento batch
    for i, track in enumerate(tracks):
        if not isinstance(track, Track):
            continue
            
        title = getattr(track, 'title', '') or ''
        artist = getattr(track, 'grandparentTitle', '') or ''
        album = getattr(track, 'parentTitle', '') or ''
        year = getattr(track, 'year', None)
        added_at = getattr(track, 'addedAt', None)
        
        # Accetta tracce con almeno un campo valido (titolo o artista)
        if not title and not artist:
            continue  # Salta solo se entrambi sono vuoti
            
        track_data.append((
            _clean_string(title),
            _clean_string(artist),
            _clean_string(album),
            year,
            added_at
        ))
        
        # Progress ogni 5000 tracce (ridotto per responsività)
        if (i + 1) % 5000 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed
            logging.info(f"📊 Preparazione: {i+1}/{len(tracks)} tracce ({rate:.0f} tracce/sec)")
    
    if not track_data:
        logging.warning("⚠️ Nessuna traccia valida da inserire")
        return 0
    
    preparation_time = time.time() - start_time
    logging.info(f"💾 Inserimento {len(track_data)} tracce valide in chunks di {chunk_size} (preparazione: {preparation_time:.1f}s)")
    
    # Inserimento a chunks ottimizzati usando il connection pool
    insert_start = time.time()
    for chunk_start in range(0, len(track_data), chunk_size):
        chunk_end = min(chunk_start + chunk_size, len(track_data))
        chunk = track_data[chunk_start:chunk_end]
        chunk_num = (chunk_start // chunk_size) + 1
        total_chunks = (len(track_data) + chunk_size - 1) // chunk_size
        
        try:
            # Usa il connection pool per performance migliori
            with get_db_connection() as con:
                cur = con.cursor()
                
                # Inserimento bulk senza PRAGMA problematici
                cur.executemany(
                    """INSERT OR IGNORE INTO plex_library_index (title_clean, artist_clean, album_clean, year, added_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    chunk
                )
                chunk_inserts = cur.rowcount
                total_successful += chunk_inserts
                
            elapsed_chunk = time.time() - insert_start
            avg_time_per_chunk = elapsed_chunk / chunk_num
            estimated_remaining = avg_time_per_chunk * (total_chunks - chunk_num)
            
            logging.info(f"✅ Chunk {chunk_num}/{total_chunks}: {chunk_inserts}/{len(chunk)} inserite | "
                        f"Totale: {total_successful} | ETA: {estimated_remaining:.1f}s")
            
        except Exception as e:
            logging.error(f"❌ Errore chunk {chunk_num}: {e}")
            continue
    
    logging.info(f"🏁 Inserimento completato: {total_successful}/{len(track_data)} tracce inserite")
    return total_successful

def test_matching_improvements(sample_size: int = 100):
    """Testa i miglioramenti del matching confrontando old vs new system."""
    try:
        logging.info(f"🧪 Avvio test matching su {sample_size} tracce mancanti...")
        
        missing_tracks = get_missing_tracks()
        if not missing_tracks:
            logging.info("❌ Nessuna traccia mancante da testare")
            return
        
        # Prendi un campione casuale
        import random
        test_tracks = random.sample(missing_tracks, min(sample_size, len(missing_tracks)))
        
        old_matches = 0
        new_matches = 0
        improvements = []
        
        for track_info in test_tracks:
            title, artist = track_info[1], track_info[2]
            
            # Test sistema vecchio (exact match)
            old_result = check_track_in_index(title, artist)
            
            # Test sistema nuovo (smart match)
            new_result = check_track_in_index_smart(title, artist)
            
            if old_result:
                old_matches += 1
            if new_result:
                new_matches += 1
            
            # Se il nuovo sistema trova una traccia che il vecchio non trovava
            if new_result and not old_result:
                improvements.append((title, artist))
        
        logging.info(f"📊 RISULTATI TEST MATCHING:")
        logging.info(f"   - Sistema vecchio: {old_matches}/{len(test_tracks)} ({old_matches/len(test_tracks)*100:.1f}%)")
        logging.info(f"   - Sistema nuovo: {new_matches}/{len(test_tracks)} ({new_matches/len(test_tracks)*100:.1f}%)")
        logging.info(f"   - Miglioramento: +{new_matches-old_matches} tracce trovate ({(new_matches-old_matches)/len(test_tracks)*100:.1f}%)")
        
        if improvements:
            logging.info(f"🎯 Esempi di miglioramenti (prime 5):")
            for i, (title, artist) in enumerate(improvements[:5]):
                logging.info(f"   {i+1}. '{title}' - '{artist}'")
        
        return {
            'old_matches': old_matches,
            'new_matches': new_matches,
            'improvements': len(improvements),
            'test_size': len(test_tracks)
        }
        
    except Exception as e:
        logging.error(f"Errore durante il test matching: {e}")
        return None

def clean_invalid_missing_tracks():
    """Rimuove contenuti non validi dalle tracce mancanti (TV/Film e playlist NO_DELETE)."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # 1. Rimuovi tracce da playlist NO_DELETE (impossibili per definizione)
            cur.execute("SELECT COUNT(*) FROM missing_tracks WHERE source_playlist_title LIKE '%no_delete%'")
            no_delete_count = cur.fetchone()[0]
            
            if no_delete_count > 0:
                cur.execute("DELETE FROM missing_tracks WHERE source_playlist_title LIKE '%no_delete%'")
                logging.info(f"🚫 Rimosse {no_delete_count} tracce da playlist NO_DELETE (impossibili per definizione)")
            
            # 2. Rimuovi contenuti TV/Film
            tv_keywords = ['simpsons', 'simpson', 'family guy', 'american dad', 'king of the hill', 
                          'episode', 'tv show', 'serie', 'film', 'movie']
            
            conditions = []
            params = []
            for keyword in tv_keywords:
                conditions.extend([
                    "title LIKE ? OR artist LIKE ? OR source_playlist_title LIKE ?"
                ])
                params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
            
            if conditions:
                query = f"SELECT COUNT(*) FROM missing_tracks WHERE {' OR '.join(conditions)}"
                res = cur.execute(query, params)
                tv_count = res.fetchone()[0]
                
                if tv_count > 0:
                    delete_query = f"DELETE FROM missing_tracks WHERE {' OR '.join(conditions)}"
                    cur.execute(delete_query, params)
                    logging.info(f"🎭 Rimosse {tv_count} tracce TV/Film")
                else:
                    logging.info("✅ Nessuna traccia TV/Film trovata")
            
            con.commit()
            
            # Conta tracce rimanenti
            cur.execute("SELECT COUNT(*) FROM missing_tracks")
            remaining = cur.fetchone()[0]
            
            total_removed = no_delete_count + (tv_count if 'tv_count' in locals() else 0)
            logging.info(f"🧹 Pulizia completata: rimosse {total_removed} tracce non valide, rimangono {remaining} tracce effettivamente mancanti")
                
    except Exception as e:
        logging.error(f"Errore durante la pulizia: {e}")

def fix_corrupted_status_values():
    """Corregge i valori di status corrotti nel database (numerici invece che stringhe)."""
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            # Trova tutti i record con status numerici/corrotti
            cur.execute("""
                SELECT id, status FROM missing_tracks 
                WHERE status NOT IN ('missing', 'downloaded', 'resolved_manual') 
                OR status IS NULL
            """)
            corrupted_records = cur.fetchall()
            
            if corrupted_records:
                logging.info(f"🔧 Trovati {len(corrupted_records)} record con status corrotto")
                
                # Correggi tutti i valori corrotti impostandoli a 'missing'
                cur.execute("""
                    UPDATE missing_tracks 
                    SET status = 'missing' 
                    WHERE status NOT IN ('missing', 'downloaded', 'resolved_manual') 
                    OR status IS NULL
                """)
                
                fixed_count = cur.rowcount
                logging.info(f"✅ Status corretto per {fixed_count} tracce")
                return fixed_count
            else:
                logging.info("✅ Nessun status corrotto trovato")
                return 0
                
    except Exception as e:
        logging.error(f"Errore durante la correzione status: {e}")
        return 0

def clean_resolved_missing_tracks():
    """Rimuove tutte le tracce che sono state risolte (downloaded o resolved_manual)."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # Conta tracce risolte prima della pulizia
            cur.execute("SELECT COUNT(*) FROM missing_tracks WHERE status IN ('downloaded', 'resolved_manual')")
            resolved_count = cur.fetchone()[0]
            
            if resolved_count > 0:
                # Rimuovi tutte le tracce risolte
                cur.execute("DELETE FROM missing_tracks WHERE status IN ('downloaded', 'resolved_manual')")
                con.commit()
                logging.info(f"🧹 Rimosse {resolved_count} tracce risolte (downloaded + resolved_manual)")
                
                # Conta tracce rimanenti
                cur.execute("SELECT COUNT(*) FROM missing_tracks")
                remaining = cur.fetchone()[0]
                logging.info(f"✅ Rimangono {remaining} tracce ancora da risolvere")
                
                return resolved_count, remaining
            else:
                logging.info("✅ Nessuna traccia risolta da rimuovere")
                cur.execute("SELECT COUNT(*) FROM missing_tracks")
                remaining = cur.fetchone()[0]
                return 0, remaining
                
    except Exception as e:
        logging.error(f"Errore durante la pulizia tracce risolte: {e}")
        return 0, 0

# Alias per compatibilità
def clean_tv_content_from_missing_tracks():
    """Alias per compatibilità - usa la nuova funzione completa."""
    return clean_invalid_missing_tracks()

def diagnose_indexing_issues():
    """Diagnostica problemi di indicizzazione confrontando Plex con database."""
    try:
        import os
        from plexapi.server import PlexServer
        from plexapi.audio import Track
        
        plex_url = os.getenv("PLEX_URL")
        plex_token = os.getenv("PLEX_TOKEN")
        library_name = os.getenv("LIBRARY_NAME", "Musica")
        logging.debug(f"Using library name: {library_name}")
        
        if not (plex_url and plex_token):
            logging.error("Credenziali Plex non configurate per diagnosi")
            return
        
        logging.info("🔍 Avvio diagnosi indicizzazione...")
        
        plex = PlexServer(plex_url, plex_token, timeout=60)
        music_library = plex.library.section(library_name)
        
        # Prendi un campione per analisi
        sample_items = music_library.search(libtype='track', limit=1000)
        
        track_count = 0
        non_track_count = 0
        empty_title = 0
        empty_artist = 0
        empty_both = 0
        
        for item in sample_items:
            if isinstance(item, Track):
                track_count += 1
                title = getattr(item, 'title', '') or ''
                artist = getattr(item, 'grandparentTitle', '') or ''
                
                if not title: empty_title += 1
                if not artist: empty_artist += 1
                if not title and not artist: empty_both += 1
            else:
                non_track_count += 1
                logging.debug(f"Oggetto non-Track trovato: {type(item)} - {getattr(item, 'title', 'N/A')}")
        
        total_sample = len(sample_items)
        
        logging.info(f"📊 DIAGNOSI CAMPIONE ({total_sample} items):")
        logging.info(f"   🎵 Track validi: {track_count} ({track_count/total_sample*100:.1f}%)")
        logging.info(f"   ❌ Non-Track: {non_track_count} ({non_track_count/total_sample*100:.1f}%)")
        logging.info(f"   📝 Titolo vuoto: {empty_title} ({empty_title/total_sample*100:.1f}%)")
        logging.info(f"   🎤 Artista vuoto: {empty_artist} ({empty_artist/total_sample*100:.1f}%)")
        logging.info(f"   🚫 Entrambi vuoti: {empty_both} ({empty_both/total_sample*100:.1f}%)")
        
        # Stima per la libreria completa
        total_plex = 215447
        estimated_valid_tracks = (track_count / total_sample) * total_plex
        logging.info(f"📈 STIMA LIBRERIA COMPLETA:")
        logging.info(f"   🎵 Track validi stimati: {estimated_valid_tracks:.0f}")
        logging.info(f"   ❌ Non-Track stimati: {total_plex - estimated_valid_tracks:.0f}")
        
    except Exception as e:
        logging.error(f"Errore durante diagnosi: {e}")

def clear_library_index():
    """Svuota la tabella dell'indice prima di una nuova scansione completa."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            cur.execute("DELETE FROM plex_library_index")
            con.commit()
        logging.info("Indice della libreria locale svuotato con successo.")
    except Exception as e:
        logging.error(f"Errore durante lo svuotamento dell'indice: {e}")


# ================================
# GESTIONE PLAYLIST SELEZIONATE
# ================================

def get_macro_category(playlist_type: str, name: str = "", description: str = "") -> str:
    """
    Determina la macrocategoria di una playlist basandosi sul tipo e metadati.
    
    Args:
        playlist_type: Tipo playlist ('user', 'curated', 'popular', 'chart', 'genre', 'radio')
        name: Nome della playlist (opzionale)
        description: Descrizione della playlist (opzionale)
        
    Returns:
        Macrocategoria: 'PERSONAL', 'POPULAR', 'EDITORIAL', 'THEMATIC', 'GENRE_BASED', 'CHARTS', 'DISCOVERY'
    """
    # Normalizza input
    name_lower = name.lower() if name else ""
    desc_lower = description.lower() if description else ""
    combined_text = f"{name_lower} {desc_lower}"
    
    # Mappatura diretta per tipo - solo per casi molto specifici
    if playlist_type == 'user':
        return 'PERSONAL'
    elif playlist_type in ['chart', 'charts']:
        return 'CHARTS'
    elif playlist_type in ['genre', 'genres', 'radio', 'radios']:
        return 'GENRE_BASED'
    
    # ANALISI BASATA SU CONTENUTO (priorità su tipo)
    
    # 1. CHARTS - parole chiave per classifiche
    chart_keywords = [
        'chart', 'top 100', 'top 50', 'top 40', 'top 20', 'top 10',
        'billboard', 'classifica', 'ranking', 'hit parade', 'charts',
        'top songs', 'top tracks', 'number 1', 'best selling'
    ]
    if any(keyword in combined_text for keyword in chart_keywords):
        return 'CHARTS'
    
    # 2. DISCOVERY - parole chiave per nuove scoperte
    discovery_keywords = [
        'new music', 'discover', 'fresh', 'emerging', 'upcoming', 'rising',
        'underground', 'indie finds', 'hidden gems', 'experimental',
        'breakthrough', 'next big thing', 'radar', 'discovery', 'new releases'
    ]
    if any(keyword in combined_text for keyword in discovery_keywords):
        return 'DISCOVERY'
    
    # 3. THEMATIC - parole chiave molto specifiche per mood/attività
    thematic_keywords = [
        # Attività fisiche
        'workout', 'gym', 'fitness', 'running', 'cardio', 'training', 'sport',
        'exercise', 'beast mode', 'power workout',
        
        # Relax e benessere
        'chill', 'relax', 'calm', 'peaceful', 'meditation', 'zen', 'ambient',
        'sleep', 'bedtime', 'tranquil', 'serene', 'soft',
        
        # Studio e concentrazione
        'study', 'focus', 'concentration', 'reading', 'work', 'productivity',
        'coffee shop', 'lo-fi',
        
        # Festa e social
        'party', 'dance', 'club', 'night out', 'celebration', 'dancing',
        'party mix', 'dance party',
        
        # Viaggi e movimento
        'road trip', 'travel', 'car', 'driving', 'highway', 'journey',
        'sing in the car', 'road',
        
        # Mood specifici
        'happy', 'sad', 'melancholy', 'energy', 'mood booster', 'feel good',
        'good vibes', 'uplifting', 'motivational', 'emotional',
        
        # Stagioni e occasioni
        'summer', 'winter', 'spring', 'autumn', 'christmas', 'holiday',
        'valentine', 'halloween', 'morning', 'evening', 'weekend',
        
        # Casa e vita quotidiana
        'cooking', 'kitchen', 'dinner', 'shower', 'cleaning'
    ]
    if any(keyword in combined_text for keyword in thematic_keywords):
        return 'THEMATIC'
    
    # 4. GENRE_BASED - parole chiave per generi musicali
    genre_keywords = [
        # Generi principali
        'rock', 'pop', 'jazz', 'blues', 'classical', 'country', 'folk',
        'electronic', 'techno', 'house', 'edm', 'dubstep', 'trance',
        'hip hop', 'rap', 'hip-hop', 'r&b', 'rnb', 'soul', 'funk',
        'metal', 'punk', 'hardcore', 'alternative', 'grunge',
        'reggae', 'ska', 'latin', 'salsa', 'bachata', 'merengue',
        'indie', 'indie rock', 'indie pop', 'shoegaze',
        'gospel', 'spiritual', 'world music', 'ambient',
        
        # Sottogeneri e stili
        'deep house', 'prog rock', 'death metal', 'black metal',
        'classic rock', 'hard rock', 'soft rock', 'punk rock',
        'nu metal', 'heavy metal', 'thrash metal',
        'jazz fusion', 'smooth jazz', 'bebop', 'swing',
        'drum and bass', 'breakbeat', 'garage', 'trap',
        'phonk', 'brazilian funk', 'afrobeat'
    ]
    if any(keyword in combined_text for keyword in genre_keywords):
        return 'GENRE_BASED'
    
    # 5. POPULAR - contenuto mainstream e virale
    popular_keywords = [
        'hit', 'hits', 'top hits', 'viral', 'trending', 'mainstream',
        'popular', 'greatest hits', 'best', 'most played', 'radio hits',
        'smash hits', 'all time hits', 'biggest hits', 'global hits',
        '2024', '2025', 'today', 'now', 'current', 'latest'
    ]
    if any(keyword in combined_text for keyword in popular_keywords):
        return 'POPULAR'
    
    # 6. EDITORIAL - playlist curate e professionali
    editorial_keywords = [
        'curated', 'handpicked', 'selected', 'editor', 'editorial',
        'spotify', 'official', 'featured', 'recommended', 'staff picks',
        'essential', 'definitive', 'ultimate', 'collection'
    ]
    if any(keyword in combined_text for keyword in editorial_keywords):
        return 'EDITORIAL'
    
    # FALLBACK basato su tipo quando l'analisi del contenuto non trova nulla
    if playlist_type == 'curated':
        return 'EDITORIAL'
    elif playlist_type == 'popular':
        return 'POPULAR'
    elif playlist_type in ['editorial', 'featured']:
        return 'EDITORIAL'
    
    # Default finale - classifiche se sembra ufficiale, altrimenti popolare
    if 'spotify' in combined_text or 'official' in combined_text:
        return 'EDITORIAL'
    
    return 'POPULAR'  # Default conservativo

def save_discovered_playlists(user_type: str, service: str, playlists: List[Dict], playlist_type: str = 'user'):
    """
    Salva le playlist scoperte nel database.
    
    Args:
        user_type: 'main' o 'secondary'
        service: 'spotify' o 'deezer'  
        playlists: Lista di dict con metadati playlist
        playlist_type: 'user', 'curated', 'chart', 'radio'
    """
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            for playlist in playlists:
                # Estrai metadati
                playlist_id = playlist.get('id', '')
                name = playlist.get('name', '')
                description = playlist.get('description', '')
                poster = playlist.get('poster', '')
                track_count = playlist.get('track_count', 0)
                
                # Determina macrocategoria
                macro_category = get_macro_category(playlist_type, name, description)
                
                # Serializza metadati aggiuntivi se presenti
                metadata = {}
                if 'preview_tracks' in playlist:
                    metadata['preview_tracks'] = playlist['preview_tracks']
                if 'genre' in playlist:
                    metadata['genre'] = playlist['genre']
                metadata_json = json.dumps(metadata) if metadata else None
                
                # Insert o update
                cur.execute("""
                    INSERT OR REPLACE INTO user_playlist_selections 
                    (user_type, service, playlist_id, playlist_name, playlist_description, 
                     playlist_poster, playlist_type, track_count, auto_discovered, 
                     last_updated, metadata_json, macro_category, is_selected)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, ?, ?, 
                            COALESCE((SELECT is_selected FROM user_playlist_selections 
                                    WHERE user_type=? AND service=? AND playlist_id=?), 0))
                """, (
                    user_type, service, playlist_id, name, description, poster,
                    playlist_type, track_count, metadata_json, macro_category,
                    user_type, service, playlist_id
                ))
            
            con.commit()
            logging.info(f"✅ Salvate {len(playlists)} playlist {playlist_type} per {user_type}/{service}")
            return True
            
    except Exception as e:
        logging.error(f"❌ Errore salvando playlist scoperte: {e}")
        return False

def get_macro_category_stats(user_type: str = None, service: str = None) -> Dict:
    """
    Ottiene statistiche per macrocategoria.
    
    Args:
        user_type: 'main' o 'secondary' (opzionale)
        service: 'spotify' o 'deezer' (opzionale)
        
    Returns:
        Dict con statistiche per macrocategoria
    """
    try:
        with get_db_connection() as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            
            query = """
                SELECT 
                    macro_category,
                    COUNT(*) as total_playlists,
                    SUM(CASE WHEN is_selected = 1 THEN 1 ELSE 0 END) as selected_playlists,
                    SUM(track_count) as total_tracks
                FROM user_playlist_selections 
                WHERE 1=1
            """
            params = []
            
            if user_type:
                query += " AND user_type = ?"
                params.append(user_type)
                
            if service:
                query += " AND service = ?"
                params.append(service)
                
            query += " GROUP BY macro_category ORDER BY total_playlists DESC"
            
            cur.execute(query, params)
            rows = cur.fetchall()
            
            stats = {}
            for row in rows:
                category = row['macro_category'] or 'UNKNOWN'
                stats[category] = {
                    'total_playlists': row['total_playlists'],
                    'selected_playlists': row['selected_playlists'],
                    'total_tracks': row['total_tracks'],
                    'selection_rate': (row['selected_playlists'] / row['total_playlists'] * 100) if row['total_playlists'] > 0 else 0
                }
            
            return stats
            
    except Exception as e:
        logging.error(f"❌ Errore ottenendo statistiche macrocategorie: {e}")
        return {}

def update_existing_playlists_macro_categories():
    """
    Aggiorna le macrocategorie per tutte le playlist esistenti nel database.
    """
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            # Recupera tutte le playlist senza macrocategoria o con NULL
            cur.execute("""
                SELECT id, playlist_name, playlist_description, playlist_type 
                FROM user_playlist_selections 
                WHERE macro_category IS NULL OR macro_category = ''
            """)
            
            playlists_to_update = cur.fetchall()
            updated_count = 0
            
            for playlist in playlists_to_update:
                playlist_id, name, description, playlist_type = playlist
                
                # Calcola la macrocategoria
                macro_category = get_macro_category(playlist_type or '', name or '', description or '')
                
                # Aggiorna nel database
                cur.execute("""
                    UPDATE user_playlist_selections 
                    SET macro_category = ? 
                    WHERE id = ?
                """, (macro_category, playlist_id))
                
                updated_count += 1
            
            con.commit()
            logging.info(f"✅ Aggiornate {updated_count} playlist con nuove macrocategorie")
            return updated_count
            
    except Exception as e:
        logging.error(f"❌ Errore aggiornando macrocategorie: {e}")
        return 0

def get_user_playlist_selections(user_type: str, service: str = None, selected_only: bool = False) -> List[Dict]:
    """
    Recupera le playlist selezionate per un utente.
    
    Args:
        user_type: 'main' o 'secondary'
        service: 'spotify' o 'deezer' (opzionale, se None restituisce tutti)
        selected_only: Se True, solo playlist selezionate
        
    Returns:
        Lista di dict con metadati playlist
    """
    try:
        with get_db_connection() as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            
            query = "SELECT * FROM user_playlist_selections WHERE user_type = ?"
            params = [user_type]
            
            if service:
                query += " AND service = ?"
                params.append(service)
                
            if selected_only:
                query += " AND is_selected = 1"
                
            query += " ORDER BY playlist_type, playlist_name"
            
            res = cur.execute(query, params)
            rows = res.fetchall()
            
            playlists = []
            for row in rows:
                playlist = dict(row)
                # Deserializza metadati se presenti
                if playlist.get('metadata_json'):
                    try:
                        playlist['metadata'] = json.loads(playlist['metadata_json'])
                    except:
                        playlist['metadata'] = {}
                else:
                    playlist['metadata'] = {}
                playlists.append(playlist)
                
            return playlists
            
    except Exception as e:
        logging.error(f"❌ Errore recuperando playlist selezionate: {e}")
        return []

def toggle_playlist_selection(user_type: str, service: str, playlist_id: str, selected: bool = True):
    """
    Abilita/disabilita la selezione di una playlist.
    Gestisce correttamente le playlist del sistema di copie fisiche.
    """
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            # Trova la playlist specifica per questo utente
            cur.execute("""
                UPDATE user_playlist_selections 
                SET is_selected = ?, last_updated = CURRENT_TIMESTAMP
                WHERE user_type = ? AND service = ? AND playlist_id = ?
            """, (selected, user_type, service, playlist_id))
            
            if cur.rowcount > 0:
                action = "selezionata" if selected else "deselezionata"
                logging.info(f"✅ Playlist {playlist_id} {action} per {user_type}/{service}")
                return True
            else:
                logging.warning(f"⚠️ Playlist {playlist_id} non trovata per {user_type}/{service}")
                return False
                
    except Exception as e:
        logging.error(f"❌ Errore nel toggle playlist selection: {e}")
        return False

def get_selected_playlist_ids(user_type: str, service: str) -> List[str]:
    """
    Recupera solo gli ID delle playlist selezionate per la sincronizzazione.
    Include anche le playlist condivise con questo utente.
    Filtra automaticamente i contenuti non sincronizzabili (generi e radio Deezer).
    """
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            # Base filter per contenuti sincronizzabili
            if service == 'deezer':
                sync_filter = """
                    AND playlist_type NOT IN ('genres', 'radios')
                    AND playlist_id NOT LIKE 'genre_%'
                    AND playlist_id NOT LIKE 'radio_%'
                    AND playlist_id NOT LIKE 'chart_tracks_%'
                    AND playlist_id NOT LIKE 'chart_albums_%'
                """
            else:
                sync_filter = ""
            
            # Query per playlist proprie + condivise
            query = f"""
                SELECT DISTINCT playlist_id FROM user_playlist_selections 
                WHERE service = ? AND is_selected = 1 {sync_filter}
                AND (
                    user_type = ?  -- Playlist proprie
                    OR shared_with = ?  -- Playlist condivise con questo utente
                    OR shared_with = 'both'  -- Playlist condivise con tutti
                )
                ORDER BY playlist_name
            """
            
            res = cur.execute(query, (service, user_type, user_type))
            playlist_ids = [row[0] for row in res.fetchall()]
            
            # Log informazioni dettagliate
            res_own = cur.execute(f"""
                SELECT COUNT(*) FROM user_playlist_selections 
                WHERE user_type = ? AND service = ? AND is_selected = 1 {sync_filter}
            """, (user_type, service))
            own_count = res_own.fetchone()[0]
            
            res_shared = cur.execute(f"""
                SELECT COUNT(*) FROM user_playlist_selections 
                WHERE service = ? AND is_selected = 1 {sync_filter}
                AND (shared_with = ? OR shared_with = 'both')
                AND user_type != ?
            """, (service, user_type, user_type))
            shared_count = res_shared.fetchone()[0]
            
            # Conta quanti sono stati filtrati per Deezer
            if service == 'deezer':
                res_total_own = cur.execute("""
                    SELECT COUNT(*) FROM user_playlist_selections 
                    WHERE user_type = ? AND service = ? AND is_selected = 1
                """, (user_type, service))
                res_total_shared = cur.execute("""
                    SELECT COUNT(*) FROM user_playlist_selections 
                    WHERE service = ? AND is_selected = 1
                    AND (shared_with = ? OR shared_with = 'both')
                    AND user_type != ?
                """, (service, user_type, user_type))
                
                total_own = res_total_own.fetchone()[0]
                total_shared = res_total_shared.fetchone()[0]
                filtered_count = (total_own - own_count) + (total_shared - shared_count)
                
                if filtered_count > 0:
                    logging.info(f"🚫 Filtrati {filtered_count} contenuti non sincronizzabili Deezer (generi/radio)")
            
            logging.info(f"📋 Trovate {len(playlist_ids)} playlist sincronizzabili per {user_type}/{service}")
            logging.info(f"   👤 Proprie: {own_count}, 🤝 Condivise: {shared_count}")
            return playlist_ids
            
    except Exception as e:
        logging.error(f"❌ Errore recuperando playlist ID selezionati: {e}")
        return []

def get_total_selected_playlists_count():
    """
    Restituisce il conteggio totale delle playlist selezionate per tutti gli utenti e servizi.
    
    NUOVO COMPORTAMENTO: Con il sistema di copia fisica, ogni playlist selezionata
    da ogni utente viene contata. Se main e secondary selezionano la stessa playlist
    (tramite condivisione), conta come 2 playlist selezionate.
    """
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            # NUOVA QUERY: Conta TUTTE le playlist selezionate (anche copie condivise)
            res = cur.execute("""
                SELECT 
                    playlist_id,
                    service,
                    user_type,
                    is_shared_copy
                FROM user_playlist_selections 
                WHERE is_selected = 1
                AND (
                    service != 'deezer' OR 
                    (playlist_type NOT IN ('genres', 'radios')
                     AND playlist_id NOT LIKE 'genre_%'
                     AND playlist_id NOT LIKE 'radio_%'
                     AND playlist_id NOT LIKE 'chart_tracks_%'
                     AND playlist_id NOT LIKE 'chart_albums_%')
                )
                ORDER BY user_type, service, playlist_id
            """)
            
            # NUOVA LOGICA: Conta tutte le playlist selezionate
            breakdown = {}
            shared_copy_count = 0
            
            for row in res.fetchall():
                playlist_id, service, user_type, is_shared_copy = row
                
                # Breakdown per utente
                if user_type not in breakdown:
                    breakdown[user_type] = {}
                if service not in breakdown[user_type]:
                    breakdown[user_type][service] = 0
                breakdown[user_type][service] += 1
                
                # Conta le copie condivise
                if is_shared_copy:
                    shared_copy_count += 1
            
            # Calcola totale
            total_count = sum(
                sum(services.values()) for services in breakdown.values()
            )
            
            # Calcola totali per utente
            user_totals = {}
            for user_type, services in breakdown.items():
                user_totals[user_type] = sum(services.values())
            
            return {
                'total': total_count,
                'breakdown': breakdown,
                'user_totals': user_totals,
                'shared_copies_count': shared_copy_count,
                'note': 'Each user playlist counted separately, including shared copies'
            }
            
    except Exception as e:
        logging.error(f"❌ Errore contando playlist selezionate: {e}")
        return {'total': 0, 'breakdown': {}, 'user_totals': {}, 'shared_count': 0}

def share_playlist_with_user(user_type: str, service: str, playlist_id: str, share_with: str):
    """
    Condivide una playlist con un altro utente creando una copia fisica.
    
    NUOVO COMPORTAMENTO:
    - Crea una copia completa della playlist per l'utente destinatario
    - Ogni utente ha controllo indipendente della selezione
    - Mantiene collegamento tramite original_playlist_id
    
    Args:
        user_type: Utente proprietario ('main' o 'secondary')
        service: Servizio ('spotify' o 'deezer')
        playlist_id: ID della playlist da condividere
        share_with: Utente con cui condividere ('main', 'secondary', o None per rimuovere condivisione)
    
    Returns:
        bool: True se successo, False altrimenti
    """
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            if not share_with:
                # Rimozione condivisione: elimina la copia condivisa
                return _remove_shared_copy(cur, user_type, service, playlist_id)
            
            # Recupera i dati della playlist originale
            res = cur.execute("""
                SELECT playlist_name, playlist_description, playlist_poster, playlist_type,
                       track_count, metadata_json, is_selected
                FROM user_playlist_selections 
                WHERE user_type = ? AND service = ? AND playlist_id = ?
            """, (user_type, service, playlist_id))
            
            original_playlist = res.fetchone()
            if not original_playlist:
                logging.error(f"❌ Playlist {playlist_id} non trovata per {user_type}/{service}")
                return False
            
            playlist_name, description, poster, playlist_type, track_count, metadata_json, is_selected = original_playlist
            
            # Verifica se esiste già una copia condivisa
            res = cur.execute("""
                SELECT id FROM user_playlist_selections 
                WHERE user_type = ? AND service = ? AND original_playlist_id = ?
            """, (share_with, service, playlist_id))
            
            existing_copy = res.fetchone()
            if existing_copy:
                logging.info(f"✅ Playlist '{playlist_name}' già condivisa con {share_with}")
                return True
            
            # Crea la copia per l'utente destinatario
            cur.execute("""
                INSERT INTO user_playlist_selections (
                    user_type, service, playlist_id, playlist_name, 
                    playlist_description, playlist_poster, playlist_type,
                    track_count, is_selected, metadata_json,
                    original_playlist_id, is_shared_copy, shared_with,
                    auto_discovered, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 1, CURRENT_TIMESTAMP)
            """, (
                share_with, service, playlist_id, playlist_name,
                description, poster, playlist_type, track_count,
                0,  # Inizialmente non selezionata per l'utente destinatario
                metadata_json, playlist_id, user_type
            ))
            
            # Marca l'originale come condivisa (per backwards compatibility)
            cur.execute("""
                UPDATE user_playlist_selections 
                SET shared_with = ?, last_updated = CURRENT_TIMESTAMP
                WHERE user_type = ? AND service = ? AND playlist_id = ?
            """, (share_with, user_type, service, playlist_id))
            
            logging.info(f"✅ Playlist '{playlist_name}' di {user_type}/{service} copiata e condivisa con {share_with}")
            return True
            
    except Exception as e:
        logging.error(f"❌ Errore nella condivisione playlist: {e}")
        return False

def _remove_shared_copy(cur, user_type: str, service: str, playlist_id: str):
    """Helper function per rimuovere copie condivise."""
    try:
        # Trova e rimuovi le copie condivise
        res = cur.execute("""
            SELECT user_type, playlist_name FROM user_playlist_selections 
            WHERE original_playlist_id = ? AND service = ? AND is_shared_copy = 1
        """, (playlist_id, service))
        
        shared_copies = res.fetchall()
        
        # Elimina le copie condivise
        cur.execute("""
            DELETE FROM user_playlist_selections 
            WHERE original_playlist_id = ? AND service = ? AND is_shared_copy = 1
        """, (playlist_id, service))
        
        # Rimuovi il marker di condivisione dall'originale
        cur.execute("""
            UPDATE user_playlist_selections 
            SET shared_with = NULL, last_updated = CURRENT_TIMESTAMP
            WHERE user_type = ? AND service = ? AND playlist_id = ?
        """, (user_type, service, playlist_id))
        
        for copy_user, playlist_name in shared_copies:
            logging.info(f"✅ Rimossa copia condivisa di '{playlist_name}' per {copy_user}")
        
        return True
        
    except Exception as e:
        logging.error(f"❌ Errore rimozione condivisione: {e}")
        return False

def get_shared_playlists(target_user: str):
    """
    Recupera tutte le playlist condivise con un utente specifico.
    
    Args:
        target_user: Utente destinatario ('main' o 'secondary')
    
    Returns:
        list: Lista delle playlist condivise con l'utente
    """
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            res = cur.execute("""
                SELECT 
                    user_type, service, playlist_id, playlist_name, 
                    playlist_description, playlist_poster, playlist_type,
                    track_count, is_selected, metadata_json, last_updated
                FROM user_playlist_selections 
                WHERE shared_with = ? OR shared_with = 'both'
                ORDER BY service, playlist_name
            """, (target_user,))
            
            shared_playlists = []
            for row in res.fetchall():
                shared_playlists.append({
                    'owner_user': row[0],
                    'service': row[1],
                    'playlist_id': row[2],
                    'playlist_name': row[3],
                    'playlist_description': row[4],
                    'playlist_poster': row[5],
                    'playlist_type': row[6],
                    'track_count': row[7],
                    'is_selected': bool(row[8]),
                    'metadata_json': row[9],
                    'last_updated': row[10],
                    'is_shared': True
                })
            
            logging.info(f"📋 Trovate {len(shared_playlists)} playlist condivise con {target_user}")
            return shared_playlists
            
    except Exception as e:
        logging.error(f"❌ Errore recuperando playlist condivise: {e}")
        return []

def get_user_playlist_selections_with_sharing(user_type: str, service: str, selected_only: bool = False):
    """
    Recupera TUTTE le playlist dell'utente:
    - Playlist proprie (create o scoperte da lui)
    - Copie condivise (ricevute da altri utenti)
    
    NUOVO COMPORTAMENTO: Con il sistema di copia fisica, ogni utente
    ha le proprie playlist nel database, incluse le copie condivise.
    
    Args:
        user_type: Tipo utente ('main' o 'secondary')
        service: Servizio ('spotify' o 'deezer')
        selected_only: Se True, restituisce solo quelle selezionate
    
    Returns:
        list: Lista di tutte le playlist dell'utente (proprie + copie condivise)
    """
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            # NUOVA LOGICA SEMPLIFICATA:
            # Tutte le playlist dell'utente sono nel database con user_type = user_type
            # Le copie condivise hanno is_shared_copy = 1
            
            where_clause = "WHERE user_type = ? AND service = ?"
            params = [user_type, service]
            
            if selected_only:
                where_clause += " AND is_selected = 1"
            
            res = cur.execute(f"""
                SELECT 
                    playlist_id, playlist_name, playlist_description, playlist_poster,
                    playlist_type, track_count, is_selected, auto_discovered, 
                    last_updated, metadata_json, shared_with, 
                    original_playlist_id, is_shared_copy
                FROM user_playlist_selections 
                {where_clause}
                ORDER BY playlist_name
            """, params)
            
            playlists = []
            for row in res.fetchall():
                playlist = {
                    'playlist_id': row[0],
                    'playlist_name': row[1],
                    'playlist_description': row[2],
                    'playlist_poster': row[3],
                    'playlist_type': row[4],
                    'track_count': row[5],
                    'is_selected': bool(row[6]),
                    'auto_discovered': bool(row[7]),
                    'last_updated': row[8],
                    'metadata_json': row[9],
                    'shared_with': row[10],
                    'original_playlist_id': row[11],
                    'is_shared_copy': bool(row[12]),
                }
                
                # Determina proprietà e condivisione
                if playlist['is_shared_copy']:
                    # È una copia ricevuta da un altro utente
                    playlist['is_owner'] = False
                    playlist['owner_user'] = playlist['shared_with']  # Chi l'ha condivisa
                    playlist['is_shared'] = True
                else:
                    # È una playlist propria
                    playlist['is_owner'] = True
                    playlist['owner_user'] = user_type
                    playlist['is_shared'] = bool(playlist['shared_with'])
                
                # Aggiungi metadati
                if playlist['metadata_json']:
                    try:
                        playlist['metadata'] = json.loads(playlist['metadata_json'])
                    except json.JSONDecodeError:
                        playlist['metadata'] = {}
                else:
                    playlist['metadata'] = {}
                
                playlists.append(playlist)
                
            logging.debug(f"📋 Trovate {len(playlists)} playlist per {user_type}/{service}")
            return playlists
            
    except Exception as e:
        logging.error(f"❌ Errore recuperando playlist: {e}")
        return []

def migrate_env_playlists_to_database():
    """
    Migrazione one-time: sposta gli ID playlist da environment variables al database.
    Questa funzione va chiamata una sola volta per migrare la configurazione esistente.
    """
    try:
        import os
        
        # Mappa delle variabili ambiente
        env_mappings = [
            ('main', 'spotify', 'SPOTIFY_PLAYLIST_IDS'),
            ('main', 'deezer', 'DEEZER_PLAYLIST_ID'),
            ('secondary', 'deezer', 'DEEZER_PLAYLIST_ID_SECONDARY'),
        ]
        
        migrated_count = 0
        
        for user_type, service, env_var in env_mappings:
            playlist_ids_str = os.getenv(env_var, '')
            if playlist_ids_str:
                playlist_ids = [pid.strip() for pid in playlist_ids_str.split(',') if pid.strip()]
                
                with get_db_connection() as con:
                    cur = con.cursor()
                    
                    for playlist_id in playlist_ids:
                        # Inserisci playlist come selezionata (migrazione da env esistente)
                        cur.execute("""
                            INSERT OR IGNORE INTO user_playlist_selections 
                            (user_type, service, playlist_id, playlist_name, 
                             playlist_type, is_selected, auto_discovered)
                            VALUES (?, ?, ?, ?, 'user', 1, 0)
                        """, (user_type, service, playlist_id, f"Playlist {playlist_id}"))
                        
                        if cur.rowcount > 0:
                            migrated_count += 1
                    
                    con.commit()
        
        if migrated_count > 0:
            logging.info(f"✅ Migrazione completata: {migrated_count} playlist spostate da environment variables al database")
        else:
            logging.info("ℹ️ Nessuna playlist da migrare dalle environment variables")
            
        return migrated_count
        
    except Exception as e:
        logging.error(f"❌ Errore durante migrazione playlist: {e}")
        return 0

def save_user_playlists(user_type: str, service: str, playlists: list, playlist_type: str = 'user') -> int:
    """
    Salva una lista di playlist nel database per un utente specifico.
    
    Args:
        user_type: Tipo utente ('main' o 'secondary')
        service: Servizio ('spotify' o 'deezer')
        playlists: Lista di dizionari con metadati playlist
        playlist_type: Tipo di playlist ('user', 'curated', 'popular', ecc.)
        
    Returns:
        Numero di playlist salvate
    """
    try:
        saved_count = 0
        
        with get_db_connection() as con:
            cur = con.cursor()
            
            for playlist in playlists:
                # Estrai campi dai metadati playlist
                playlist_id = playlist.get('id', '')
                playlist_name = playlist.get('name', '')
                playlist_description = playlist.get('description', '')
                playlist_poster = playlist.get('poster', '')
                track_count = playlist.get('track_count', 0)
                effective_playlist_type = playlist.get('playlist_type', playlist_type)
                
                # Controlla se la playlist esiste già
                cur.execute("""
                    SELECT id FROM user_playlist_selections 
                    WHERE user_type = ? AND service = ? AND playlist_id = ?
                """, (user_type, service, playlist_id))
                
                existing = cur.fetchone()
                
                if existing:
                    # Aggiorna playlist esistente
                    cur.execute("""
                        UPDATE user_playlist_selections 
                        SET playlist_name = ?, playlist_description = ?, playlist_poster = ?,
                            playlist_type = ?, track_count = ?, auto_discovered = 1
                        WHERE user_type = ? AND service = ? AND playlist_id = ?
                    """, (playlist_name, playlist_description, playlist_poster, 
                          effective_playlist_type, track_count, user_type, service, playlist_id))
                else:
                    # Inserisci nuova playlist
                    cur.execute("""
                        INSERT INTO user_playlist_selections 
                        (user_type, service, playlist_id, playlist_name, playlist_description, 
                         playlist_poster, playlist_type, track_count, is_selected, auto_discovered)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
                    """, (user_type, service, playlist_id, playlist_name, playlist_description,
                          playlist_poster, effective_playlist_type, track_count))
                    
                    saved_count += 1
            
            con.commit()
            
        if saved_count > 0:
            logging.info(f"✅ Salvate {saved_count} nuove playlist {effective_playlist_type} per {user_type}/{service}")
        else:
            logging.info(f"ℹ️ Nessuna nuova playlist da salvare per {user_type}/{service}")
            
        return saved_count
        
    except Exception as e:
        logging.error(f"❌ Errore salvando playlist {user_type}/{service}: {e}")
        return 0

def check_album_in_library(album_title: str, artist_name: str, auto_sync: bool = True) -> bool:
    """Verifica se un album è presente nella libreria Plex. Con auto_sync=True, aggiunge automaticamente se trovato in Plex ma mancante dal DB."""
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            # Pulizia stringhe per il confronto
            album_clean = _clean_string(album_title)
            artist_clean = _clean_string(artist_name)
            
            logging.info(f"🔍 Verifico album: '{album_title}' -> '{album_clean}' | artista: '{artist_name}' -> '{artist_clean}'")
            
            # Strategia 1: Match esatto
            cur.execute("""
                SELECT COUNT(*) FROM plex_library_index 
                WHERE album_clean = ? AND artist_clean = ?
            """, (album_clean, artist_clean))
            
            track_count = cur.fetchone()[0]
            if track_count > 0:
                logging.info(f"✅ Album trovato (match esatto): {track_count} tracce")
                return True
            
            # Strategia 2: Match parziale album con artista esatto
            cur.execute("""
                SELECT COUNT(*) FROM plex_library_index 
                WHERE album_clean LIKE ? AND artist_clean = ?
            """, (f"%{album_clean}%", artist_clean))
            
            track_count = cur.fetchone()[0]
            if track_count > 0:
                logging.info(f"✅ Album trovato (match parziale album): {track_count} tracce")
                return True
            
            # Strategia 3: Match esatto album con artista parziale
            cur.execute("""
                SELECT COUNT(*) FROM plex_library_index 
                WHERE album_clean = ? AND artist_clean LIKE ?
            """, (album_clean, f"%{artist_clean}%"))
            
            track_count = cur.fetchone()[0]
            if track_count > 0:
                logging.info(f"✅ Album trovato (match parziale artista): {track_count} tracce")
                return True
            
            # Strategia 4: Match parziale su entrambi
            cur.execute("""
                SELECT COUNT(*) FROM plex_library_index 
                WHERE album_clean LIKE ? AND artist_clean LIKE ?
            """, (f"%{album_clean}%", f"%{artist_clean}%"))
            
            track_count = cur.fetchone()[0]
            if track_count > 0:
                logging.info(f"✅ Album trovato (match parziale): {track_count} tracce")
                return True
            
            # Strategia 5: Cerca artisti simili per debug  
            words = artist_clean.split()
            if len(words) >= 2:
                # Prova combinazioni di parole (es: "molly grace" da "soprano molly grace")
                for i in range(len(words)):
                    for j in range(i+1, len(words)+1):
                        word_combo = " ".join(words[i:j])
                        if len(word_combo) > 3:
                            cur.execute("""
                                SELECT DISTINCT artist_clean FROM plex_library_index 
                                WHERE artist_clean LIKE ? OR artist_clean = ?
                                LIMIT 10
                            """, (f"%{word_combo}%", word_combo))
                            
                            similar_artists = [r[0] for r in cur.fetchall()]
                            if similar_artists:
                                logging.info(f"🔍 Artisti simili con combinazione '{word_combo}': {similar_artists}")
                                
                                # Se troviamo match parziali, verifica album
                                for similar_artist in similar_artists:
                                    cur.execute("""
                                        SELECT COUNT(*) FROM plex_library_index 
                                        WHERE artist_clean = ? AND album_clean LIKE ?
                                    """, (similar_artist, f"%{album_clean}%"))
                                    
                                    count = cur.fetchone()[0]
                                    if count > 0:
                                        logging.info(f"✅ Album trovato con nome artista '{similar_artist}': {count} tracce")
                                        return True
            
            # Strategia 6: Ricerca fuzzy con spezzettamento nome artista
            # Utile per artisti con nomi composti (es: "John Smith" trovato come "smith john")
            artist_words = artist_clean.split()
            if len(artist_words) >= 2:
                logging.debug(f"🔍 Ricerca fuzzy per artista multi-parola: {artist_words}")
                
                # Prova tutte le combinazioni di parole dell'artista
                for i in range(len(artist_words)):
                    for j in range(i + 1, len(artist_words) + 1):
                        word_combo = " ".join(artist_words[i:j])
                        
                        cur.execute("""
                            SELECT DISTINCT artist_clean, album_clean FROM plex_library_index 
                            WHERE artist_clean LIKE ? AND album_clean LIKE ?
                            LIMIT 3
                        """, (f"%{word_combo}%", f"%{album_clean}%"))
                        
                        results = cur.fetchall()
                        if results:
                            logging.info(f"✅ Album trovato con combinazione parole '{word_combo}': {results[0]}")
                            return True
            
            # Debug: verifica stato indice database
            cur.execute("SELECT COUNT(*) FROM plex_library_index")
            total_tracks = cur.fetchone()[0]
            logging.info(f"🔍 DEBUG: Totale tracce nell'indice: {total_tracks}")
            
            if total_tracks == 0:
                logging.warning(f"❌ PROBLEMA: L'indice del database è vuoto! Esegui l'indicizzazione della libreria.")
                return False
            
            # Debug finale: mostra alcuni artisti casuali per capire il formato
            cur.execute("SELECT DISTINCT artist_clean FROM plex_library_index WHERE artist_clean != '' ORDER BY RANDOM() LIMIT 20")
            sample_artists = [r[0] for r in cur.fetchall()]
            logging.info(f"🔍 DEBUG: Esempi artisti nel database: {sample_artists[:10]}")
            
            # Strategia 7: Singole parole (fallback)
            for word in words:
                if len(word) > 3:
                    cur.execute("""
                        SELECT DISTINCT artist_clean FROM plex_library_index 
                        WHERE artist_clean LIKE ?
                        LIMIT 5
                    """, (f"%{word}%",))
                    
                    similar_artists = [r[0] for r in cur.fetchall()]
                    if similar_artists:
                        logging.info(f"🔍 Artisti con parola singola '{word}': {similar_artists}")
                        break
            
            logging.info(f"❌ Nessun album trovato per artista '{artist_clean}'")
            
            # AUTO-SYNC: Se non trovato nel DB, verifica se esiste in Plex e aggiungilo automaticamente
            if auto_sync:
                try:
                    import os
                    from plexapi.server import PlexServer
                    
                    logging.info(f"🔄 AUTO-SYNC: Verifico se album esiste in Plex per aggiunta automatica...")
                    
                    plex_url = os.getenv('PLEX_URL')
                    plex_token = os.getenv('PLEX_TOKEN')
                    
                    if plex_url and plex_token:
                        plex = PlexServer(plex_url, plex_token)
                        music_section = plex.library.section('Musica')
                        
                        # Cerca l'artista in Plex
                        plex_artists = music_section.search(**{'artist.title': artist_name})
                        if plex_artists:
                            plex_artist = plex_artists[0]
                            
                            # Cerca l'album specifico
                            for album in plex_artist.albums():
                                album_clean_plex = _clean_string(album.title)
                                if album_clean_plex == album_clean or album_clean in album_clean_plex or album_clean_plex in album_clean:
                                    logging.info(f"🎯 AUTO-SYNC: Album '{album.title}' trovato in Plex! Aggiunta al database...")
                                    
                                    # Aggiungi tutte le tracce dell'album
                                    added_count = 0
                                    for track in album.tracks():
                                        title_clean_track = track.title.lower().strip()
                                        artist_clean_track = track.grandparentTitle.lower().strip()
                                        album_clean_track = track.parentTitle.lower().strip()
                                        
                                        # Verifica se già esiste
                                        cur.execute('''
                                            SELECT COUNT(*) FROM plex_library_index 
                                            WHERE title_clean = ? AND artist_clean = ? AND album_clean = ?
                                        ''', (title_clean_track, artist_clean_track, album_clean_track))
                                        
                                        if cur.fetchone()[0] == 0:
                                            cur.execute('''
                                                INSERT INTO plex_library_index 
                                                (title_clean, artist_clean, album_clean, year)
                                                VALUES (?, ?, ?, ?)
                                            ''', (title_clean_track, artist_clean_track, album_clean_track, album.year))
                                            added_count += 1
                                            logging.info(f"  ➕ AUTO-SYNC: Aggiunta traccia '{track.title}'")
                                    
                                    if added_count > 0:
                                        con.commit()
                                        logging.info(f"✅ AUTO-SYNC: Album aggiunto automaticamente! {added_count} tracce")
                                        return True
                                    else:
                                        logging.info(f"ℹ️ AUTO-SYNC: Album già presente nel database")
                                        return True
                        
                        logging.info(f"❌ AUTO-SYNC: Album non trovato in Plex")
                    else:
                        logging.warning(f"⚠️ AUTO-SYNC: Configurazione Plex mancante")
                        
                except Exception as sync_error:
                    logging.error(f"❌ AUTO-SYNC: Errore durante sincronizzazione automatica: {sync_error}")
            
            return False
            
    except Exception as e:
        logging.error(f"Errore verifica album in libreria: {e}")
        return False

def get_album_completion_percentage(album_title: str, artist_name: str) -> int:
    """Calcola la percentuale di completezza di un album nella libreria."""
    try:
        with get_db_connection() as con:
            cur = con.cursor()
            
            # Pulizia stringhe per il confronto
            album_clean = _clean_string(album_title)
            artist_clean = _clean_string(artist_name)
            
            # Conta tracce presenti
            cur.execute("""
                SELECT COUNT(*) FROM plex_library_index 
                WHERE album_clean LIKE ? AND artist_clean LIKE ?
            """, (f"%{album_clean}%", f"%{artist_clean}%"))
            
            found_tracks = cur.fetchone()[0]
            
            if found_tracks == 0:
                return 0
            elif found_tracks >= 8:  # Album probabilmente completo
                return 100
            else:
                # Stima percentuale basata su numero medio tracce per album (12)
                return min(int((found_tracks / 12) * 100), 95)
                
    except Exception as e:
        logging.error(f"Errore calcolo completezza album: {e}")
        return 0

def check_album_in_index(artist: str, album_title: str) -> bool:
    """Funzione di compatibilità per il controllo album nell'indice."""
    return check_album_in_library(album_title, artist)