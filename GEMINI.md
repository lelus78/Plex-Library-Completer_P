# Istruzioni e Contesto per il Progetto: Plex Sync Dashboard

Questo file serve come "memoria" per il modello Gemini che assiste nello sviluppo di questo progetto. Contiene le convenzioni, i comandi e le informazioni chiave sull'architettura.

## 1. Architettura Generale del Progetto

Il progetto è un'applicazione web basata su **Flask** e gestita tramite **Docker**.

-   **Entry Point Principale:** `app.py`. Questo file avvia il server Flask e gestisce tutte le route e gli endpoint API.
-   **Database Centrale:** Il cuore del sistema è un database **SQLite** situato in `/app/state_data/sync_database.db`. Questo file è mappato sulla cartella host `./state_data` ed è fondamentale per la persistenza. Contiene le seguenti tabelle principali:
    -   `plex_library_index`: Un indice locale dell'intera libreria musicale di Plex. **È il prerequisito per quasi tutte le operazioni.**
    -   `user_playlist_selections`: Memorizza tutte le playlist (di Spotify, Deezer, etc.) scoperte e selezionate dagli utenti per la sincronizzazione. Sostituisce la vecchia configurazione basata su file `.env`.
    -   `missing_tracks`: Elenca tutti i brani che risultano presenti nelle playlist sorgente ma non nell'indice della libreria Plex.
    -   `managed_ai_playlists`: Salva le playlist generate on-demand dall'utente tramite l'interfaccia AI Lab.
-   **Operazioni in Background:** Le operazioni lunghe (sincronizzazione, indicizzazione, download) vengono eseguite in thread separati tramite la funzione `run_task_in_background` in `app.py` per non bloccare l'interfaccia web. Lo stato è gestito dalla variabile `app_state`.
-   **Interfaccia Utente:** I template si trovano nella cartella `templates` e usano l'ereditarietà di Jinja2 a partire da `base.html`.

## 2. Flussi di Lavoro Chiave

L'applicazione segue una serie di passaggi logici per funzionare correttamente.

### A. Primo Avvio: Indicizzazione
1.  **Indicizzazione Libreria:** L'operazione più importante da eseguire al primo avvio è **"Index Library"** dalla pagina principale.
2.  **Processo:** Questa azione esegue `build_library_index`, che scansiona l'intera libreria musicale di Plex e popola la tabella `plex_library_index`.
3.  **Risultato:** Senza un indice completo, il sistema non può confrontare le playlist e rileverebbe erroneamente tutte le tracce come mancanti.

### B. Configurazione: Gestione Playlist
1.  **Pagina di Gestione:** L'utente configura quali playlist sincronizzare dalla pagina **"Playlist Management"**.
2.  **Scoperta:** L'utente può avviare una "scoperta" per trovare tutte le playlist disponibili su Spotify e Deezer (proprie, curate, popolari, etc.).
3.  **Selezione:** Le playlist scoperte vengono salvate nella tabella `user_playlist_selections`. L'utente può quindi selezionare o deselezionare quali includere nella sincronizzazione. È anche possibile condividere playlist tra utenti.

### C. Operatività: Sincronizzazione e Download
1.  **Avvio Sincronizzazione:** L'utente avvia una sincronizzazione (completa o selettiva).
2.  **Confronto:** Il sistema legge le playlist selezionate da `user_playlist_selections`, ne recupera le tracce e le confronta con l'indice locale in `plex_library_index`.
3.  **Popolamento Mancanti:** Le tracce non trovate nell'indice vengono aggiunte alla tabella `missing_tracks`.
4.  **Verifica Falsi Positivi:** Dalla pagina **"Missing Tracks"**, l'utente può lanciare una **"Verifica Completa"**. Questo è un passaggio fondamentale che usa un sistema a 3 livelli per ridurre i falsi positivi:
    -   **Livello 1: Exact Match:** Controllo esatto nell'indice.
    -   **Livello 2: Fuzzy Match:** Controllo di similarità (es. per "Song (Remastered)").
    -   **Livello 3: Filesystem Check:** Controllo diretto dei file in `M:\Organizzata`.
5.  **Risoluzione Manuale/Download:** Per le tracce "veramente" mancanti, l'utente può:
    -   Cercarle manualmente in Plex per associarle.
    -   Cercarle su Deezer e avviare il download automatico.

### D. Funzionalità AI
1.  **Generazione On-Demand (AI Lab):** L'utente può creare playlist personalizzate tramite un prompt.
    -   Il prompt viene arricchito con dati aggiornati dalle **classifiche musicali** (Billboard, Spotify, etc.) per risultati più pertinenti.
    -   Le playlist generate vengono salvate in `managed_ai_playlists` e create su Plex.
2.  **Sistema di Fallback a Cascata:** Per garantire la disponibilità, il sistema usa una gerarchia di modelli AI:
    -   **1° Tentativo:** Google Gemini 2.5 Flash (veloce e con alta quota).
    -   **2° Tentativo:** Google Gemini 2.0 Flash (se il primo fallisce).
    -   **3° Tentativo (Fallback):** **Ollama** (modello locale, senza limiti di quota).
3.  **Playlist Settimanali:** Esiste un sistema (`weekly_ai_manager.py`) che genera automaticamente playlist settimanali basandosi sui gusti dell'utente (analizzati da una playlist protetta con tag `NO_DELETE`). Lo stato di queste playlist è salvato in `state_data/weekly_ai_playlists.json`.

## 3. Convenzioni di Stile e Codice

-   **Import Relativi:** Tutti gli import all'interno del pacchetto `plex_playlist_sync` devono essere relativi (es. `from .utils.database import ...`).
-   **Logging Centralizzato:** Il logging è configurato solo in `app.py`. Tutti gli altri moduli devono ottenere il logger con `logger = logging.getLogger(__name__)`.
-   **Variabili d'Ambiente:** Tutte le configurazioni e le chiavi API devono essere gestite tramite il file `.env`.

## 4. Comandi Comuni del Progetto

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

## 5. Informazioni Utente Importanti

-   **Utente Principale:** Emanuele (Lele).
-   **Utente Secondario:** Ambra.
-   **Hobby e Interessi:** Modellismo (RC auto, aerei, droni), PC, tecnologia, stampa 3D (FDM), laser (xTool S1 40W). Questo contesto è utile per suggerire idee creative o analogie.
