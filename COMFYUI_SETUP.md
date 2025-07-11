# ComfyUI Setup per Plex-Library-Completer

## Panoramica

Questo progetto ora supporta la generazione di copertine AI tramite **ComfyUI esterno** invece di AI locale. Questo approccio offre maggiore flessibilità e performance migliori.

## Architettura

```
┌─────────────────────────────────────┐    ┌─────────────────────────────────────┐
│   Plex-Library-Completer            │    │         ComfyUI Server              │
│   (Container principale)            │    │       (Container separato)          │
│                                     │    │                                     │
│  ┌─────────────────────────────────┐│    │  ┌─────────────────────────────────┐│
│  │ playlist_cover_generator.py     ││    │  │        ComfyUI API             ││
│  │ - generate_ai_cover_comfyui()   ││────┼──┤ - Workflow execution           ││
│  │ - HTTP REST API calls           ││    │  │ - FLUX/SDXL models             ││
│  │ - Prompt generation             ││    │  │ - High-quality output          ││
│  └─────────────────────────────────┘│    │  └─────────────────────────────────┘│
│                                     │    │                                     │
│  Fallback: Simple PIL covers       │    │  GPU-accelerated generation         │
└─────────────────────────────────────┘    └─────────────────────────────────────┘
```

## Configurazione

### 1. Variabili d'ambiente

Configura queste variabili nel file `.env`:

```bash
# ===== ComfyUI External AI Cover Generation =====
# URL del server ComfyUI esterno (lascia vuoto per disabilitare AI)
COMFYUI_URL=http://localhost:8188

# API key per ComfyUI se richiesta (opzionale)
COMFYUI_API_KEY=

# Timeout per richieste ComfyUI in secondi (default: 60)
COMFYUI_TIMEOUT=60

# Modello ComfyUI da usare (configurabile)
COMFYUI_MODEL=flux-schnell
```

### 2. Setup ComfyUI Server

#### Opzione A: Docker Compose (Raccomandato)

Crea un file `docker-compose.comfyui.yml`:

```yaml
version: '3.8'

services:
  comfyui:
    image: yanwk/comfyui-boot:latest
    container_name: comfyui-server
    ports:
      - "8188:8188"
    environment:
      - CLI_ARGS=--listen 0.0.0.0 --port 8188
    volumes:
      - comfyui_data:/app
      - ./comfyui_workflows:/app/workflows
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  comfyui_data:
```

Avvia con:
```bash
docker-compose -f docker-compose.comfyui.yml up -d
```

#### Opzione B: Installazione Locale

```bash
# Clona ComfyUI
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI

# Installa dipendenze
pip install -r requirements.txt

# Avvia il server
python main.py --listen 0.0.0.0 --port 8188
```

### 3. Workflow ComfyUI

Il sistema invierà richieste con questo formato:

```json
{
  "prompt": "professional music playlist cover art, energetic electronic music, album cover design, high quality, vibrant colors, modern aesthetic, clean layout",
  "model": "flux-schnell",
  "width": 1024,
  "height": 1024,
  "steps": 20,
  "cfg_scale": 7.5
}
```

### 4. Endpoint API

Il sistema chiamerà questi endpoint ComfyUI:

- `POST /prompt` - Invia workflow di generazione
- `GET /history/{prompt_id}` - Controlla stato generazione
- `GET /view` - Scarica immagine generata

## Implementazione Attuale

### Funzioni Principali

1. **`detect_gpu_capabilities()`**
   - Controlla se `COMFYUI_URL` è configurato
   - Restituisce `"comfyui"` se disponibile, `"simple"` altrimenti

2. **`generate_ai_cover_comfyui()`**
   - Genera prompt ottimizzato dai generi musicali
   - Invia richiesta HTTP a ComfyUI
   - Salva immagine ricevuta
   - Gestisce timeout e errori

3. **`generate_ai_cover_local()`**
   - Punto di ingresso principale
   - Sceglie tra ComfyUI e copertine semplici
   - Fallback automatico in caso di errori

### Flusso di Generazione

```
1. Playlist creata → 2. Estrazione generi → 3. Generazione prompt
                                              ↓
6. Salvataggio ← 5. Download immagine ← 4. Chiamata ComfyUI API
                                              ↓
                                          7. Fallback copertine semplici (se errore)
```

## Vantaggi del Nuovo Sistema

### ✅ **Benefici**
- **Performance**: GPU dedicata per ComfyUI
- **Scalabilità**: ComfyUI può girare su server separato
- **Flessibilità**: Modelli AI configurabili
- **Stabilità**: Nessuna dipendenza AI pesante nel container principale
- **Manutenibilità**: Codice AI separato dal core business logic

### ⚠️ **Considerazioni**
- **Dipendenza esterna**: Richiede ComfyUI server separato
- **Configurazione**: Setup iniziale più complesso
- **Latenza**: Chiamate di rete per ogni generazione

## Stato Attuale

### ✅ **Completato**
- [x] Rimosso codice AI locale (torch, diffusers, transformers)
- [x] Implementato scheletro ComfyUI integration
- [x] Configurate variabili d'ambiente
- [x] Fallback a copertine semplici funzionante
- [x] Documentazione setup

### 🔄 **TODO (quando ComfyUI è pronto)**
- [ ] Implementare `generate_ai_cover_comfyui()` completa
- [ ] Testare workflow ComfyUI
- [ ] Ottimizzare prompt generation
- [ ] Aggiungere retry logic e error handling
- [ ] Configurare workflow ComfyUI ottimali

## Testing

### Test Base (Senza ComfyUI)
```bash
# Testa generazione copertine semplici
docker exec plex-library-completer python -c "
from plex_playlist_sync.utils.playlist_cover_generator import test_cover_generation
result = test_cover_generation()
print('✅ Cover generation working!' if result else '❌ Cover generation failed!')
"
```

### Test ComfyUI (Quando disponibile)
```bash
# Configura ComfyUI URL
export COMFYUI_URL=http://localhost:8188

# Testa generazione AI
docker exec plex-library-completer python -c "
from plex_playlist_sync.utils.playlist_cover_generator import generate_ai_cover_comfyui
result = generate_ai_cover_comfyui('Test Playlist', 'Electronic music', ['electronic', 'ambient'])
print(f'ComfyUI result: {result}')
"
```

## Supporto

Per problemi o domande:
1. Verifica che ComfyUI server sia raggiungibile
2. Controlla logs per errori di connessione
3. Testa con copertine semplici per verificare che il fallback funzioni
4. Verifica configurazione variabili d'ambiente

---

**Stato**: 🚧 Scheletro implementato - In attesa di setup ComfyUI