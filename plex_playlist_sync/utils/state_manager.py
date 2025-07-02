# utils/state_manager.py
import json
import os
import logging

# Il percorso del file di stato ora è letto da una variabile d'ambiente, 
# con un default a /app/state_data/playlist_state.json.
# Usiamo una sottocartella "state" per mantenere il tutto più ordinato.
STATE_FILE_PATH = os.getenv("STATE_FILE_PATH", "/app/state_data/playlist_state.json")

def load_playlist_state() -> dict:
    """
    Carica lo stato della playlist dal file JSON.
    Se il file non esiste, restituisce un dizionario vuoto.
    """
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
                logging.info(f"Caricato stato dal file: {STATE_FILE_PATH}")
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"Errore nel caricare o parsare il file di stato {STATE_FILE_PATH}: {e}. Verrà creato un nuovo stato.")
            return {}
    else:
        logging.info(f"File di stato non trovato in '{STATE_FILE_PATH}'. Verrà creato un nuovo stato alla prima generazione di playlist.")
        return {}

def save_playlist_state(state: dict):
    """
    Salva il dizionario di stato aggiornato nel file JSON.
    Crea la directory se non esiste.
    """
    try:
        # Estrae la directory dal percorso completo del file
        state_directory = os.path.dirname(STATE_FILE_PATH)
        # Crea la directory se non esiste
        os.makedirs(state_directory, exist_ok=True)
        
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=4, ensure_ascii=False)
            logging.info(f"Stato della playlist salvato correttamente in: {STATE_FILE_PATH}")
    except IOError as e:
        logging.error(f"Impossibile salvare il file di stato {STATE_FILE_PATH}: {e}")