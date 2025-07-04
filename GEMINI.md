# Istruzioni e Contesto per il Progetto: Plex Sync Dashboard

Questo file serve come "memoria" per il modello Gemini che assiste nello sviluppo di questo progetto. Contiene le convenzioni, i comandi e le informazioni chiave sull'architettura.

## 1. Architettura Generale del Progetto

Il progetto è un'applicazione web basata su **Flask** e gestita tramite **Docker**.

-   **Entry Point Principale:** `app.py`. Questo file avvia il server Flask e gestisce tutte le route e gli endpoint API. Il vecchio `run.py` è obsoleto.
-   **Logica di Business:** La logica principale delle operazioni (sincronizzazione, indicizzazione, pulizia) è separata in `plex_playlist_sync/sync_logic.py`.
-   **Operazioni in Background:** Le operazioni lunghe (sincronizzazione, indicizzazione, download) vengono eseguite in thread separati per non bloccare l'interfaccia web. La gestione avviene tramite la funzione `run_task_in_background` in `app.py`.
-   **Database:** Usiamo un database **SQLite** situato in `/app/state_data/sync_database.db` per la persistenza dei dati. Questo file è mappato sulla cartella host `./state_data`. Contiene le seguenti tabelle principali:
    -   `missing_tracks`: Per i brani mancanti.
    -   `plex_library_index`: Per l'indice completo della libreria Plex.
    -   `managed_ai_playlists`: Per le playlist AI permanenti generate on-demand.
-   **Interfaccia Utente:** I template si trovano nella cartella `templates` e usano l'ereditarietà di Jinja2 a partire da `base.html`.

## 2. Convenzioni di Stile e Codice

-   **Import Relativi:** Tutti gli import all'interno del pacchetto `plex_playlist_sync` devono essere relativi (es. `from .utils.database import ...`).
-   **Logging Centralizzato:** Il logging è configurato solo in `app.py`. Tutti gli altri moduli devono ottenere il logger con `logger = logging.getLogger(__name__)`.
-   **Variabili d'Ambiente:** Tutte le configurazioni e le chiavi API devono essere gestite tramite il file `.env`.

## 3. Comandi Comuni del Progetto

Elenco dei comandi Docker da eseguire dalla cartella principale del progetto.

-   **Avvio/Riavvio dopo modifiche al codice:**
    ```bash
    docker-compose up -d --build
    ```
-   **Avvio/Riavvio semplice (senza modifiche al codice):**
    ```bash
    docker-compose up -d
    ```
-   **Visualizzazione dei log in tempo reale:**
    ```bash
    docker-compose logs -f
    ```
-   **Fermare e rimuovere i container:**
    ```bash
    docker-compose down
    ```

## 4. Informazioni Utente Importanti

-   **Utente Principale:** Emanuele (Lele).
-   **Utente Secondario:** Ambra.
-   **Hobby e Interessi:** Modellismo (RC auto, aerei, droni), PC, tecnologia, stampa 3D (FDM), laser (xTool S1 40W). Questo contesto è utile per suggerire idee creative o analogie.

## 5. Flussi di Lavoro Chiave da Ricordare

-   **De-duplicazione:** La funzione `update_or_create_plex_playlist` in `plex.py` deve prima controllare l'indice locale tramite `check_track_in_index` prima di aggiungere un brano alla lista dei mancanti.
-   **Risolutore Interattivo:** La pagina `missing_tracks.html` ha una doppia funzione: ricerca manuale su Plex (con associazione e aggiunta alla playlist di origine) e ricerca su Deezer (con download). L'associazione avviene tramite `ratingKey` della traccia Plex e `source_playlist_id` della traccia mancante.
-   **Playlist AI Permanenti:** Le playlist create on-demand vengono salvate nella tabella `managed_ai_playlists`. La funzione `list_ai_playlists` in `gemini_ai.py` è stata deprecata in favore di `get_managed_ai_playlists_for_user` in `database.py`.