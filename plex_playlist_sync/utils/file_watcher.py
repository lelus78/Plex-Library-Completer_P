#!/usr/bin/env python3
"""
File System Watcher per il monitoraggio automatico della libreria musicale
Aggiorna il database quando rileva modifiche nella cartella musicale
"""
import os
import time
import logging
import threading
from typing import Optional, Callable, Set
from datetime import datetime, timedelta
from pathlib import Path
import queue
from plexapi.server import PlexServer
from plexapi.audio import Track

from .database import DatabasePool, add_track_to_index, _clean_string

logger = logging.getLogger(__name__)

class MusicLibraryWatcher:
    """
    Monitora la cartella musicale per modifiche e aggiorna automaticamente il database.
    Simile al comportamento di Plex, ma per il nostro database interno.
    """
    
    def __init__(self, music_path: str, plex_server: PlexServer, library_name: str = "Musica"):
        self.music_path = Path(music_path)
        self.plex_server = plex_server
        self.library_name = library_name
        self.is_running = False
        self.watch_thread = None
        self.update_queue = queue.Queue()
        self.last_check = datetime.now()
        self.debounce_time = 30  # Secondi di debounce per evitare troppi refresh
        
        # Configurazione
        self.check_interval = int(os.getenv("MUSIC_WATCHER_INTERVAL", "60"))  # Check ogni 60 secondi
        self.max_batch_size = int(os.getenv("MUSIC_WATCHER_BATCH_SIZE", "50"))  # Max 50 file per batch
        
        logger.info(f"🎵 Music Watcher inizializzato: {music_path}")
        logger.info(f"⏱️ Intervallo check: {self.check_interval}s, Debounce: {self.debounce_time}s")
    
    def start(self):
        """Avvia il monitoraggio in background"""
        if self.is_running:
            logger.warning("🔄 Music Watcher già in esecuzione")
            return
        
        self.is_running = True
        self.watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.watch_thread.start()
        logger.info("🚀 Music Watcher avviato")
    
    def stop(self):
        """Ferma il monitoraggio"""
        self.is_running = False
        if self.watch_thread and self.watch_thread.is_alive():
            self.watch_thread.join(timeout=5)
        logger.info("🛑 Music Watcher fermato")
    
    def _watch_loop(self):
        """Loop principale di monitoraggio"""
        while self.is_running:
            try:
                # Controlla modifiche nella cartella musicale
                self._check_for_changes()
                
                # Processa eventuali aggiornamenti in coda
                self._process_update_queue()
                
                # Aspetta prima del prossimo check
                time.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"❌ Errore nel watcher loop: {e}")
                time.sleep(30)  # Aspetta di più in caso di errore
    
    def _check_for_changes(self):
        """
        Controlla se ci sono nuovi file musicali aggiunti di recente.
        Simile a come Plex monitora le cartelle.
        """
        try:
            if not self.music_path.exists():
                logger.warning(f"📁 Cartella musicale non trovata: {self.music_path}")
                return
            
            # Trova file musicali aggiunti di recente
            current_time = datetime.now()
            since_last_check = current_time - timedelta(seconds=self.check_interval * 2)  # Buffer extra
            
            music_extensions = {'.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aac'}
            new_files = []
            
            # Scansiona ricorsivamente la cartella musicale
            for file_path in self.music_path.rglob('*'):
                if file_path.is_file() and file_path.suffix.lower() in music_extensions:
                    # Controlla data di creazione/modifica
                    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if mtime > since_last_check:
                        new_files.append(file_path)
            
            if new_files:
                logger.info(f"🎵 Trovati {len(new_files)} nuovi file musicali")
                self._queue_plex_refresh()
            
            self.last_check = current_time
            
        except Exception as e:
            logger.error(f"❌ Errore durante check modifiche: {e}")
    
    def _queue_plex_refresh(self):
        """
        Mette in coda un refresh del database dopo aver rilevato nuovi file.
        Usa debounce per evitare troppi refresh consecutivi.
        """
        current_time = datetime.now()
        self.update_queue.put(current_time)
        logger.debug(f"⏰ Refresh database in coda per: {current_time}")
    
    def _process_update_queue(self):
        """
        Processa la coda degli aggiornamenti con debounce.
        Aggiorna il database solo se sono passati abbastanza secondi dall'ultimo update.
        """
        if self.update_queue.empty():
            return
        
        current_time = datetime.now()
        should_update = False
        
        # Svuota la coda e controlla se è tempo di aggiornare
        while not self.update_queue.empty():
            try:
                queued_time = self.update_queue.get_nowait()
                if current_time - queued_time >= timedelta(seconds=self.debounce_time):
                    should_update = True
            except queue.Empty:
                break
        
        if should_update:
            self._refresh_database()
    
    def _refresh_database(self):
        """
        Refresha il database cercando nuove tracce in Plex.
        Simile a rescan_and_update_missing() ma ottimizzato per il watcher.
        """
        try:
            logger.info("🔄 Avvio refresh automatico database...")
            
            # Connetti alla libreria Plex
            music_library = self.plex_server.library.section(self.library_name)
            
            # Cerca tracce aggiunte di recente (ultimi 5 minuti)
            five_minutes_ago = datetime.now() - timedelta(minutes=5)
            recent_tracks = music_library.search(
                sort="addedAt:desc",
                limit=self.max_batch_size
            )
            
            new_tracks_added = 0
            
            for track in recent_tracks:
                # Controlla se la traccia è stata aggiunta di recente
                if track.addedAt and track.addedAt >= five_minutes_ago:
                    # Aggiungi al nostro database se non esiste già
                    try:
                        success = add_track_to_index(track)
                        if success:
                            new_tracks_added += 1
                            logger.debug(f"➕ Aggiunta traccia: {track.title} - {track.artist().title}")
                    except Exception as e:
                        logger.debug(f"⚠️ Traccia già presente o errore: {e}")
                else:
                    # Le tracce sono ordinate per data, se questa non è recente usciamo
                    break
            
            if new_tracks_added > 0:
                logger.info(f"✅ Refresh completato: {new_tracks_added} nuove tracce aggiunte al database")
                
                # Opzionale: Notifica che il database è stato aggiornato
                self._notify_database_updated(new_tracks_added)
            else:
                logger.debug("ℹ️ Refresh completato: nessuna nuova traccia trovata")
                
        except Exception as e:
            logger.error(f"❌ Errore durante refresh database: {e}")
    
    def _notify_database_updated(self, count: int):
        """
        Notifica opzionale che il database è stato aggiornato.
        Può essere usata per triggare altri processi.
        """
        logger.info(f"📢 Database aggiornato automaticamente: +{count} tracce")
        
        # Qui si possono aggiungere altre azioni come:
        # - Aggiornare le statistiche
        # - Notificare altri componenti del sistema
        # - Aggiornare cache
    
    def force_refresh(self):
        """
        Forza un refresh immediato del database.
        Utile per refresh manuali o dopo download.
        """
        logger.info("🔄 Refresh forzato del database...")
        self._refresh_database()
    
    def get_status(self) -> dict:
        """Ritorna lo status attuale del watcher"""
        return {
            "running": self.is_running,
            "music_path": str(self.music_path),
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "queue_size": self.update_queue.qsize(),
            "check_interval": self.check_interval,
            "debounce_time": self.debounce_time
        }


class WatcherManager:
    """
    Manager per gestire il watcher a livello di applicazione.
    Singleton per evitare multiple istanze.
    """
    
    _instance = None
    _watcher = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self, music_path: str, plex_server: PlexServer, library_name: str = "Musica"):
        """Inizializza il watcher (chiamare una sola volta)"""
        if self._watcher is None:
            self._watcher = MusicLibraryWatcher(music_path, plex_server, library_name)
            logger.info("🎵 WatcherManager inizializzato")
        return self._watcher
    
    def get_watcher(self) -> Optional[MusicLibraryWatcher]:
        """Ottiene l'istanza del watcher"""
        return self._watcher
    
    def start(self):
        """Avvia il watcher se inizializzato"""
        if self._watcher:
            self._watcher.start()
        else:
            logger.warning("⚠️ Watcher non inizializzato")
    
    def stop(self):
        """Ferma il watcher"""
        if self._watcher:
            self._watcher.stop()
    
    def force_refresh(self):
        """Forza un refresh del database"""
        if self._watcher:
            self._watcher.force_refresh()
        else:
            logger.warning("⚠️ Watcher non inizializzato per force_refresh")


# Istanza globale del manager
watcher_manager = WatcherManager()