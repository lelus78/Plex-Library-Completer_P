# Integrazione Qobuz e Tidal nel Sistema Plex-Library-Completer

## Panoramica
Questo documento descrive come integrare i servizi Qobuz e Tidal nel sistema esistente mantenendo la compatibilità e offrendo controllo granulare sulle funzionalità.

## Architettura di Integrazione

### Principio di Design
Il sistema attuale (Spotify + Deezer) rimarrà invariato. Qobuz e Tidal verranno aggiunti come moduli opzionali e completamente disabilitabili.

### Pattern di Integrazione
```
Sistema Esistente:
Spotify ← app.py → Deezer
    ↓         ↓
sync_logic.py → downloader.py

Sistema Esteso:
Spotify ← app.py → Deezer → Qobuz → Tidal
    ↓         ↓        ↓       ↓
sync_logic.py → downloader.py (con fallback chain)
```

## Implementazione Dettagliata

### 1. Moduli di Servizio

#### File: `plex_playlist_sync/utils/qobuz.py`
```python
# Struttura base identica a spotify.py/deezer.py
def get_qobuz_credentials():
    """Ottiene credenziali Qobuz da environment variables"""
    pass

def discover_all_qobuz_content(user_id, country='IT'):
    """Scopre playlist, album, favoriti Qobuz"""
    pass

def get_qobuz_playlist_tracks(playlist_id):
    """Ottiene tracce da playlist Qobuz"""
    pass

def qobuz_playlist_sync(playlist_id, user_type):
    """Sincronizza playlist Qobuz → missing_tracks"""
    pass
```

#### File: `plex_playlist_sync/utils/tidal.py`
```python
# Struttura identica con pattern unificato
def get_tidal_credentials():
    """Ottiene credenziali Tidal da environment variables"""
    pass

def discover_all_tidal_content(user_id, country='IT'):
    """Scopre contenuti Tidal"""
    pass

def get_tidal_playlist_tracks(playlist_id):
    """Ottiene tracce da playlist Tidal"""
    pass

def tidal_playlist_sync(playlist_id, user_type):
    """Sincronizza playlist Tidal → missing_tracks"""
    pass
```

### 2. Configurazione Environment Variables

#### File: `.env` (aggiunte)
```bash
# Feature flags per controllo servizi
ENABLE_QOBUZ=false
ENABLE_TIDAL=false

# Credenziali Qobuz
QOBUZ_EMAIL=""
QOBUZ_PASSWORD=""
QOBUZ_USER_ID=""
QOBUZ_COUNTRY="IT"

# Credenziali Tidal
TIDAL_USERNAME=""
TIDAL_PASSWORD=""
TIDAL_USER_ID=""
TIDAL_COUNTRY="IT"

# Configurazione download
QOBUZ_QUALITY="27"    # Hi-Res
TIDAL_QUALITY="HiFi"  # Lossless
```

### 3. Integrazione Backend

#### File: `app.py` (estensioni)
```python
# Estensione endpoint discovery
@app.route('/api/discover_playlists/<user_type>/<service>')
def discover_playlists(user_type, service):
    if service == 'qobuz' and os.getenv('ENABLE_QOBUZ') == 'true':
        return discover_qobuz_playlists(user_type)
    elif service == 'tidal' and os.getenv('ENABLE_TIDAL') == 'true':
        return discover_tidal_playlists(user_type)
    # ... codice esistente per spotify/deezer
```

#### File: `sync_logic.py` (estensioni)
```python
def run_sync_for_all_services():
    """Esegue sync per tutti i servizi abilitati"""
    # Codice esistente per Spotify/Deezer
    
    # Nuove sezioni opzionali
    if os.getenv('ENABLE_QOBUZ') == 'true':
        try:
            run_qobuz_sync()
        except Exception as e:
            logging.error(f"Errore sync Qobuz: {e}")
    
    if os.getenv('ENABLE_TIDAL') == 'true':
        try:
            run_tidal_sync()
        except Exception as e:
            logging.error(f"Errore sync Tidal: {e}")
```

### 4. Integrazione Frontend

#### File: `templates/playlist_management.html`
```html
<!-- Sezioni dinamiche per servizi abilitati -->
{% if qobuz_enabled %}
<div class="service-section qobuz-section">
    <h3>Qobuz Playlists</h3>
    <!-- Struttura identica a Spotify/Deezer -->
</div>
{% endif %}

{% if tidal_enabled %}
<div class="service-section tidal-section">
    <h3>Tidal Playlists</h3>
    <!-- Struttura identica a Spotify/Deezer -->
</div>
{% endif %}
```

#### JavaScript Extensions
```javascript
// Estensione funzioni esistenti
function updatePlaylistSelections() {
    // Codice esistente per Spotify/Deezer
    
    // Gestione condizionale nuovi servizi
    if (qobuzEnabled) {
        updateQobuzPlaylists();
    }
    if (tidalEnabled) {
        updateTidalPlaylists();
    }
}
```

### 5. Integrazione Download System

#### File: `plex_playlist_sync/utils/downloader.py`
```python
def find_qobuz_equivalent(artist, title, album=None):
    """Cerca equivalente Qobuz per download"""
    pass

def find_tidal_equivalent(artist, title, album=None):
    """Cerca equivalente Tidal per download"""
    pass

def convert_url_for_streamrip(url):
    """Supporta URL Qobuz/Tidal"""
    if 'qobuz.com' in url:
        return f"qobuz:{extract_qobuz_id(url)}"
    elif 'tidal.com' in url:
        return f"tidal:{extract_tidal_id(url)}"
    # ... codice esistente
```

#### Fallback Chain Strategy
```python
def download_with_fallback_chain(artist, title, album=None):
    """Download con fallback Deezer → Qobuz → Tidal"""
    # 1. Prova Deezer (sistema esistente)
    if try_deezer_download(artist, title, album):
        return True
    
    # 2. Fallback Qobuz (se abilitato)
    if os.getenv('ENABLE_QOBUZ') == 'true':
        if try_qobuz_download(artist, title, album):
            return True
    
    # 3. Fallback Tidal (se abilitato)
    if os.getenv('ENABLE_TIDAL') == 'true':
        if try_tidal_download(artist, title, album):
            return True
    
    return False
```

#### Attivazione Dinamica dei Servizi
**CRITICO**: Il sistema di download e missing tracks deve riflettere dinamicamente i servizi abilitati.

```python
def get_enabled_download_services():
    """Restituisce lista dei servizi abilitati per download"""
    services = ['deezer']  # Deezer sempre abilitato (sistema base)
    
    if os.getenv('ENABLE_QOBUZ') == 'true':
        services.append('qobuz')
    
    if os.getenv('ENABLE_TIDAL') == 'true':
        services.append('tidal')
    
    return services

def get_download_service_priority():
    """Restituisce priorità download basata sui servizi abilitati"""
    priority = ['deezer']  # Default sempre per primo
    
    # Aggiunge servizi Hi-Res se disponibili
    if os.getenv('ENABLE_QOBUZ') == 'true':
        priority.append('qobuz')
    
    if os.getenv('ENABLE_TIDAL') == 'true':
        priority.append('tidal')
    
    return priority
```

#### Interfaccia Missing Tracks Dinamica
**File: `templates/missing_tracks.html`** (estensioni)

```html
<!-- Dropdown servizi dinamico -->
<div class="service-selector">
    <label>Servizio per Download:</label>
    <select id="downloadService" class="form-select">
        <option value="deezer">Deezer (Default)</option>
        {% if qobuz_enabled %}
        <option value="qobuz">Qobuz (Hi-Res)</option>
        {% endif %}
        {% if tidal_enabled %}
        <option value="tidal">Tidal (Lossless)</option>
        {% endif %}
    </select>
</div>

<!-- Indicators qualità dinamici -->
<div class="quality-indicators">
    <span class="badge badge-primary">Deezer: FLAC</span>
    {% if qobuz_enabled %}
    <span class="badge badge-success">Qobuz: Hi-Res</span>
    {% endif %}
    {% if tidal_enabled %}
    <span class="badge badge-info">Tidal: Master</span>
    {% endif %}
</div>

<!-- Bulk actions per servizio -->
<div class="bulk-actions">
    <button class="btn btn-primary" onclick="bulkDownload('deezer')">
        Download tutto con Deezer
    </button>
    {% if qobuz_enabled %}
    <button class="btn btn-success" onclick="bulkDownload('qobuz')">
        Download tutto con Qobuz (Hi-Res)
    </button>
    {% endif %}
    {% if tidal_enabled %}
    <button class="btn btn-info" onclick="bulkDownload('tidal')">
        Download tutto con Tidal (Lossless)
    </button>
    {% endif %}
</div>
```

#### API Endpoints Dinamici
**File: `app.py`** (estensioni missing tracks)

```python
@app.route('/api/download_options')
def get_download_options():
    """Restituisce opzioni download disponibili"""
    options = {
        'deezer': {
            'enabled': True,
            'quality': 'FLAC',
            'priority': 1,
            'description': 'Servizio base sempre attivo'
        }
    }
    
    if os.getenv('ENABLE_QOBUZ') == 'true':
        options['qobuz'] = {
            'enabled': True,
            'quality': 'Hi-Res',
            'priority': 2,
            'description': 'Qualità studio fino a 24bit/192kHz'
        }
    
    if os.getenv('ENABLE_TIDAL') == 'true':
        options['tidal'] = {
            'enabled': True,
            'quality': 'Master',
            'priority': 3,
            'description': 'Qualità lossless e Master'
        }
    
    return jsonify(options)

@app.route('/api/download_direct/<service>', methods=['POST'])
def download_direct_service(service):
    """Download diretto con servizio specifico"""
    enabled_services = get_enabled_download_services()
    
    if service not in enabled_services:
        return jsonify({
            'error': f'Servizio {service} non abilitato',
            'available_services': enabled_services
        }), 400
    
    # Procede con download specifico per servizio
    return process_service_download(service, request.json)
```

#### JavaScript per Gestione Dinamica
**File: `templates/missing_tracks.html`** (JavaScript)

```javascript
// Caricamento dinamico opzioni servizio
async function loadServiceOptions() {
    try {
        const response = await fetch('/api/download_options');
        const options = await response.json();
        
        const serviceSelect = document.getElementById('downloadService');
        serviceSelect.innerHTML = '';
        
        // Popola dropdown con servizi disponibili
        Object.entries(options).forEach(([service, config]) => {
            if (config.enabled) {
                const option = document.createElement('option');
                option.value = service;
                option.textContent = `${service.charAt(0).toUpperCase() + service.slice(1)} (${config.quality})`;
                serviceSelect.appendChild(option);
            }
        });
        
        // Aggiorna indicatori qualità
        updateQualityIndicators(options);
        
    } catch (error) {
        console.error('Errore caricamento opzioni servizio:', error);
    }
}

// Aggiorna badge qualità
function updateQualityIndicators(options) {
    const container = document.querySelector('.quality-indicators');
    container.innerHTML = '';
    
    Object.entries(options).forEach(([service, config]) => {
        if (config.enabled) {
            const badge = document.createElement('span');
            badge.className = `badge badge-${getBadgeClass(service)}`;
            badge.textContent = `${service}: ${config.quality}`;
            container.appendChild(badge);
        }
    });
}

// Download con servizio specifico
async function downloadWithService(trackId, service) {
    const enabledServices = await getEnabledServices();
    
    if (!enabledServices.includes(service)) {
        showError(`Servizio ${service} non disponibile`);
        return;
    }
    
    try {
        const response = await fetch(`/api/download_direct/${service}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({track_id: trackId})
        });
        
        if (response.ok) {
            showSuccess(`Download avviato con ${service}`);
            updateTrackStatus(trackId, 'downloading', service);
        } else {
            const error = await response.json();
            showError(`Errore ${service}: ${error.message}`);
        }
    } catch (error) {
        showError(`Errore connessione ${service}: ${error.message}`);
    }
}

// Carica opzioni all'avvio
document.addEventListener('DOMContentLoaded', loadServiceOptions);
```

#### Configurazione Template Context
**File: `app.py`** (estensioni template context)

```python
@app.route('/missing_tracks')
def missing_tracks():
    """Pagina missing tracks con contesto servizi"""
    context = {
        'missing_tracks': get_missing_tracks(),
        'deezer_enabled': True,  # Sempre attivo
        'qobuz_enabled': os.getenv('ENABLE_QOBUZ') == 'true',
        'tidal_enabled': os.getenv('ENABLE_TIDAL') == 'true',
        'download_services': get_enabled_download_services(),
        'service_priorities': get_download_service_priority()
    }
    return render_template('missing_tracks.html', **context)
```

### 6. Database Schema Extensions

#### Estensioni alle tabelle esistenti
```sql
-- Aggiunta colonne per tracking multi-servizio
ALTER TABLE missing_tracks ADD COLUMN qobuz_id TEXT;
ALTER TABLE missing_tracks ADD COLUMN tidal_id TEXT;
ALTER TABLE missing_tracks ADD COLUMN download_source TEXT; -- 'deezer', 'qobuz', 'tidal'

-- Tabelle per playlist multi-servizio
CREATE TABLE IF NOT EXISTS service_playlists (
    id TEXT PRIMARY KEY,
    service TEXT NOT NULL, -- 'qobuz', 'tidal'
    user_type TEXT NOT NULL,
    playlist_id TEXT NOT NULL,
    name TEXT NOT NULL,
    is_selected BOOLEAN DEFAULT 0,
    -- Altri campi esistenti
);
```

### 7. Configurazione Streamrip

#### File: `config.toml` (estensioni)
```toml
# Sezioni Qobuz/Tidal già presenti, da configurare
[qobuz]
email = ""
password = ""
format_id = 27  # Hi-Res
download_booklets = true

[tidal]
username = ""
password = ""
format_id = 3  # HiFi
download_videos = false
```

### 8. Gestione Errori e Fallback

#### Pattern di Fallback Graceful
```python
def safe_service_call(service_function, service_name):
    """Wrapper per chiamate sicure ai servizi"""
    try:
        return service_function()
    except ImportError:
        logging.warning(f"Libreria {service_name} non installata")
        return None
    except Exception as e:
        logging.error(f"Errore {service_name}: {e}")
        return None
```

#### Gestione Credenziali Mancanti
```python
def validate_service_credentials(service):
    """Verifica credenziali prima dell'utilizzo"""
    if service == 'qobuz':
        required = ['QOBUZ_EMAIL', 'QOBUZ_PASSWORD']
    elif service == 'tidal':
        required = ['TIDAL_USERNAME', 'TIDAL_PASSWORD']
    
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        logging.warning(f"Credenziali {service} mancanti: {missing}")
        return False
    return True
```

## Problematiche e Soluzioni

### 1. Librerie Python Required
**Problema**: Qobuz e Tidal richiedono librerie aggiuntive
**Soluzione**: Installazione opzionale con try/except per import

```python
# Pattern import opzionale
try:
    import qobuz_dl
    QOBUZ_AVAILABLE = True
except ImportError:
    QOBUZ_AVAILABLE = False
    logging.debug("qobuz_dl non installata")
```

### 2. Autenticazione Complessa
**Problema**: Qobuz/Tidal hanno autenticazione più complessa di Spotify
**Soluzione**: Caching delle sessioni e token refresh automatico

```python
class QobuzSession:
    def __init__(self):
        self.session = None
        self.token_expiry = None
    
    def get_authenticated_session(self):
        if self.session and self.token_expiry > time.time():
            return self.session
        return self.refresh_session()
```

### 3. Rate Limiting
**Problema**: API limits più stringenti
**Soluzione**: Implementazione backoff exponential

```python
def api_call_with_backoff(func, *args, **kwargs):
    """Chiamata API con retry e backoff"""
    for attempt in range(5):
        try:
            return func(*args, **kwargs)
        except RateLimitError:
            wait_time = 2 ** attempt
            time.sleep(wait_time)
    raise Exception("Rate limit exceeded")
```

### 4. Qualità Audio
**Problema**: Qobuz/Tidal offrono qualità superiori
**Soluzione**: Configurazione qualità per servizio

```python
QUALITY_MAPPING = {
    'qobuz': {
        'mp3': 5,
        'flac': 6,
        'hi_res': 27
    },
    'tidal': {
        'normal': 'LOW',
        'high': 'HIGH',
        'hifi': 'LOSSLESS',
        'master': 'HI_RES'
    }
}
```

### 5. Metadata Inconsistencies
**Problema**: Formati metadata diversi tra servizi
**Soluzione**: Normalizzazione unificata

```python
def normalize_track_metadata(track_data, service):
    """Normalizza metadata per compatibilità"""
    normalized = {
        'title': track_data.get('title', ''),
        'artist': track_data.get('artist', ''),
        'album': track_data.get('album', ''),
        'duration': track_data.get('duration', 0),
        'service': service
    }
    
    # Normalizzazioni specifiche per servizio
    if service == 'qobuz':
        normalized['quality'] = track_data.get('format_id', 6)
    elif service == 'tidal':
        normalized['quality'] = track_data.get('audioQuality', 'HIGH')
    
    return normalized
```

### 6. UI Overload
**Problema**: Troppi servizi possono sovraccaricare l'interfaccia
**Soluzione**: Tabs e filtri avanzati

```html
<!-- Sistema a tabs per servizi -->
<ul class="nav nav-tabs" id="servicesTabs">
    <li class="nav-item">
        <a class="nav-link active" data-bs-toggle="tab" href="#spotify">Spotify</a>
    </li>
    <li class="nav-item">
        <a class="nav-link" data-bs-toggle="tab" href="#deezer">Deezer</a>
    </li>
    {% if qobuz_enabled %}
    <li class="nav-item">
        <a class="nav-link" data-bs-toggle="tab" href="#qobuz">Qobuz</a>
    </li>
    {% endif %}
    {% if tidal_enabled %}
    <li class="nav-item">
        <a class="nav-link" data-bs-toggle="tab" href="#tidal">Tidal</a>
    </li>
    {% endif %}
</ul>
```

### 7. Gestione Servizi Dinamici
**Problema**: Interfaccia deve adattarsi ai servizi abilitati senza causare errori
**Soluzione**: Sistema di rilevamento e adattamento automatico

```python
def get_service_status():
    """Verifica status completo dei servizi"""
    status = {
        'deezer': {
            'enabled': True,
            'configured': check_deezer_config(),
            'available': test_deezer_connection()
        },
        'qobuz': {
            'enabled': os.getenv('ENABLE_QOBUZ') == 'true',
            'configured': validate_service_credentials('qobuz'),
            'available': test_qobuz_connection() if os.getenv('ENABLE_QOBUZ') == 'true' else False
        },
        'tidal': {
            'enabled': os.getenv('ENABLE_TIDAL') == 'true',
            'configured': validate_service_credentials('tidal'),
            'available': test_tidal_connection() if os.getenv('ENABLE_TIDAL') == 'true' else False
        }
    }
    return status

def get_working_services():
    """Restituisce solo i servizi funzionanti"""
    status = get_service_status()
    return [service for service, config in status.items() 
            if config['enabled'] and config['configured'] and config['available']]
```

**Gestione Errori per Servizi Disabilitati**:
```python
def safe_download_with_service(service, track_data):
    """Download sicuro con verifica servizio"""
    if not is_service_enabled(service):
        return {'error': f'Servizio {service} non abilitato', 'fallback': True}
    
    if not validate_service_credentials(service):
        return {'error': f'Credenziali {service} mancanti', 'fallback': True}
    
    try:
        return download_track_with_service(service, track_data)
    except Exception as e:
        logging.error(f"Errore download {service}: {e}")
        return {'error': str(e), 'fallback': True}
```

**Frontend Error Handling**:
```javascript
// Gestione errori servizi non disponibili
function handleServiceError(service, error) {
    if (error.includes('non abilitato')) {
        showWarning(`${service} non è configurato. Usando fallback automatico.`);
        return true; // Continua con fallback
    }
    
    if (error.includes('credenziali')) {
        showError(`Configurare credenziali ${service} nel file .env`);
        return false; // Blocca operazione
    }
    
    showError(`Errore ${service}: ${error}`);
    return true; // Continua con fallback
}

// Aggiornamento dinamico UI
function updateUIForServices(availableServices) {
    // Nascondi/mostra elementi basati sui servizi disponibili
    document.querySelectorAll('.service-dependent').forEach(element => {
        const requiredService = element.getAttribute('data-service');
        if (availableServices.includes(requiredService)) {
            element.style.display = 'block';
        } else {
            element.style.display = 'none';
        }
    });
    
    // Aggiorna dropdown servizi
    updateServiceDropdown(availableServices);
}
```

### 8. Scenario di Utilizzo Reali

#### Scenario 1: Solo Deezer Attivo
```bash
# .env configuration
ENABLE_QOBUZ=false
ENABLE_TIDAL=false
```

**Risultato**:
- Missing tracks mostra solo opzioni Deezer
- Download fallback non disponibile
- UI mostra "Deezer (Solo servizio attivo)"
- Nessun overhead per servizi non utilizzati

#### Scenario 2: Deezer + Qobuz
```bash
# .env configuration
ENABLE_QOBUZ=true
ENABLE_TIDAL=false
```

**Risultato**:
- Missing tracks mostra Deezer + Qobuz
- Download fallback: Deezer → Qobuz
- UI mostra qualità indicators per entrambi
- Bulk download per servizio specifico

#### Scenario 3: Tutti i Servizi Attivi
```bash
# .env configuration
ENABLE_QOBUZ=true
ENABLE_TIDAL=true
```

**Risultato**:
- Missing tracks mostra tutti e tre i servizi
- Download fallback: Deezer → Qobuz → Tidal
- UI con tabs per organizzare i servizi
- Scelta granulare per ogni download

#### Scenario 4: Servizio Parzialmente Configurato
```bash
# .env configuration
ENABLE_QOBUZ=true
QOBUZ_EMAIL=""  # Credenziali mancanti
```

**Risultato**:
- Qobuz mostrato come "Non configurato"
- Warning sulla mancanza di credenziali
- Fallback automatico a Deezer
- Guida configurazione nel UI

## Piano di Implementazione

### Fase 1: Preparazione (2-3 ore)
- [ ] Creare struttura moduli `qobuz.py` e `tidal.py`
- [ ] Aggiungere variabili environment
- [ ] Testare importazioni opzionali

### Fase 2: Backend Integration (4-6 ore)
- [ ] Implementare discovery endpoints
- [ ] Estendere sync_logic.py
- [ ] Aggiornare database schema
- [ ] Implementare download fallback chain
- [ ] **Aggiungere `get_enabled_download_services()` e `get_service_status()`**
- [ ] **Implementare API endpoints dinamici (`/api/download_options`, `/api/service_status`)**
- [ ] **Estendere missing_tracks template context con servizi abilitati**

### Fase 3: Frontend Integration (3-4 ore)
- [ ] Estendere playlist_management.html
- [ ] Aggiornare JavaScript functions
- [ ] Implementare tabs/filtri per servizi
- [ ] **Implementare interfaccia missing_tracks dinamica**
- [ ] **Aggiungere dropdown servizi e quality indicators**
- [ ] **Implementare JavaScript per gestione servizi dinamici**
- [ ] **Aggiungere bulk actions per servizio specifico**

### Fase 4: Testing & Documentation (2-3 ore)
- [ ] Test con/senza credenziali
- [ ] Test fallback graceful
- [ ] Documentazione setup e troubleshooting
- [ ] **Test scenari: solo Deezer, Deezer+Qobuz, tutti i servizi**
- [ ] **Test servizi parzialmente configurati**
- [ ] **Verificare UI adattiva per ogni configurazione**

## Benefici del Sistema

### 1. Compatibilità Totale
- Sistema esistente invariato
- Nessun breaking change
- Rollback immediato disabilitando feature flags

### 2. Flessibilità
- Utenti possono abilitare solo i servizi desiderati
- Configurazione granulare per servizio
- Fallback chain intelligente

### 3. Qualità Superior
- Accesso a Hi-Res con Qobuz
- Lossless con Tidal
- Maggiore availability di contenuti

### 4. Robustezza
- Gestione errori per servizio
- Fallback graceful
- Logging dettagliato per debug

### 5. Adattabilità Dinamica
- **Interfaccia adattiva**: UI si adatta automaticamente ai servizi abilitati
- **Zero overhead**: Servizi disabilitati non impattano performance
- **Configurazione graduale**: Utenti possono abilitare servizi uno alla volta
- **Fallback intelligente**: Sistema degrada gracefully quando servizi non sono disponibili
- **Indicatori di stato**: UI mostra chiaramente quali servizi sono configurati e funzionanti

## Troubleshooting Guide

### Problema: Servizio non si avvia
**Soluzione**:
1. Verificare `ENABLE_QOBUZ=true` / `ENABLE_TIDAL=true`
2. Verificare credenziali in `.env`
3. Controllare log per errori import
4. Testare connessione API manualmente

### Problema: Download fallisce
**Soluzione**:
1. Verificare config.toml per servizio
2. Controllare validità credenziali
3. Verificare rate limiting
4. Testare singolo download manualmente

### Problema: UI non mostra servizi
**Soluzione**:
1. Verificare feature flags nel template context
2. Controllare JavaScript errors
3. Verificare backend endpoints
4. Testare discovery API manually

### Problema: Missing tracks non mostra servizi abilitati
**Soluzione**:
1. Verificare che i servizi siano abilitati in `.env`
2. Controllare che le credenziali siano configurate correttamente
3. Verificare connessione API con endpoint `/api/download_options`
4. Controllare console JavaScript per errori di caricamento
5. Testare con `curl http://localhost:5000/api/download_options`

### Problema: Download fallisce con servizi specifici
**Soluzione**:
1. Verificare status servizio con `/api/service_status`
2. Controllare ordine di fallback: Deezer → Qobuz → Tidal
3. Testare singolo servizio con `/api/download_direct/<service>`
4. Verificare log per errori di autenticazione specifici
5. Utilizzare endpoint `/api/download_options` per debug

### Problema: UI mostra servizi non configurati
**Soluzione**:
1. Verificare template context variables (`qobuz_enabled`, `tidal_enabled`)
2. Controllare che `get_enabled_download_services()` funzioni correttamente
3. Verificare che JavaScript carichi correttamente le opzioni servizio
4. Testare manualmente: `docker exec container python -c "from plex_playlist_sync.utils.downloader import get_enabled_download_services; print(get_enabled_download_services())"`

## Conclusione

Questa integrazione mantiene l'architettura esistente solida e testata, aggiungendo funzionalità avanzate in modo modulare e sicuro. Gli utenti possono scegliere il loro livello di complessità e i servizi desiderati, mentre il sistema rimane robusto e facilmente mantenibile.