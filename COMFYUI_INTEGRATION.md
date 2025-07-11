# ComfyUI Integration per Plex-Library-Completer

## 🎨 Panoramica

Il sistema di generazione copertine ora supporta l'integrazione con ComfyUI esterno per creare copertine AI di alta qualità per le playlist. Questo documento descrive come configurare e utilizzare l'integrazione.

## 🔧 Configurazione

### 1. Configurazione ComfyUI

Assicurati che il tuo container ComfyUI sia configurato correttamente:

```bash
# Il tuo ComfyUI deve essere raggiungibile all'indirizzo:
# http://comfyui:8188 (se stesso network Docker)
# oppure
# http://localhost:8188 (se exposed su host)
```

### 2. Configurazione .env

Nel file `.env` del progetto Plex-Library-Completer, configura:

```bash
# ===== ComfyUI External AI Cover Generation =====
COMFYUI_URL=http://comfyui:8188
COMFYUI_API_KEY=                    # Opzionale se richiesta
COMFYUI_TIMEOUT=300                 # Timeout in secondi per generazione
COMFYUI_WORKFLOW=flux_album_cover   # Nome del workflow da utilizzare
COMFYUI_OUTPUT_NODE=9              # Nodo di output per l'immagine

# Abilita la generazione copertine
ENABLE_PLAYLIST_COVERS=1
```

### 3. Workflow ComfyUI

Il sistema cerca automaticamente il workflow in questi percorsi:
- `/app/workflows/flux_album_cover.json`
- `/app/state_data/workflows/flux_album_cover.json`

Un workflow di default basato su Flux Schnell (8 steps) è incluso nel progetto.

## 🚀 Utilizzo

### Generazione Automatica

Le copertine vengono generate automaticamente quando:
1. Viene creata una nuova playlist AI
2. `ENABLE_PLAYLIST_COVERS=1` è attivo
3. ComfyUI è disponibile

### Test Manuale

Per testare l'integrazione ComfyUI:

```bash
# Accedi al container
docker exec -it plex-library-completer /bin/bash

# Test completo dell'integrazione
python test_comfyui_integration.py

# Test specifico ComfyUI
python -c "
from plex_playlist_sync.utils.playlist_cover_generator import ComfyUIClient
client = ComfyUIClient()
print('ComfyUI Available:', client.is_available())
print('Queue Status:', client.get_queue_status())
"

# Test generazione copertina
python -c "
from plex_playlist_sync.utils.playlist_cover_generator import generate_ai_cover_comfyui
cover_path = generate_ai_cover_comfyui(
    playlist_name='Test Reggae Vibes',
    description='One hour of island heat',
    genres=['reggae', 'tropical']
)
print('Generated cover:', cover_path)
"
```

## 📋 Architettura

### Flusso di Generazione

```
Playlist Data → Genre Analysis → ComfyUI Prompt Generation
                    ↓
            ComfyUI Client → Workflow Execution → Image Generation
                    ↓
            PNG Download → File Save → Cover Application
```

### Fallback System

Il sistema usa un fallback a tre livelli:
1. **ComfyUI**: Generazione AI esterna (migliore qualità)
2. **Simple**: Copertine Pillow avanzate (sempre disponibile)
3. **Disabled**: Nessuna generazione copertine

### Classe ComfyUIClient

```python
class ComfyUIClient:
    def is_available() -> bool              # Verifica connettività
    def queue_prompt(workflow) -> str       # Invia workflow
    def wait_for_completion(id) -> bool     # Attende completamento
    def get_image_output(id) -> bytes       # Scarica immagine
    def generate_cover(prompt, workflow) -> bytes  # Processo completo
```

## 🎯 Prompt Engineering

### Prompt Automatici

Il sistema genera automaticamente prompt ottimizzati basati su:
- **Nome playlist**: Titolo principale
- **Generi musicali**: Stile visivo (reggae → tropical, electronic → neon)
- **Descrizione**: Elementi aggiuntivi

### Esempio Prompt Ottimizzato per Flux Schnell

```
album cover artwork, album cover for "Reggae Vibes". tropical, sunset, palm trees, ocean. Bold title text "Reggae Vibes". No copyrighted content. Instagram-ready design, tropical, sunset, clean design, high contrast
```

**Caratteristiche**:
- **Conciso**: Max 150 caratteri per Flux Schnell
- **Diretto**: Nessuna parola superflua
- **Efficace**: Keywords che Flux comprende bene

## 🔍 Troubleshooting

### Problemi Comuni

**ComfyUI non disponibile**
```bash
# Verifica che ComfyUI sia running
docker logs comfyui

# Testa connettività
curl http://comfyui:8188/system_stats
```

**Workflow non trovato**
```bash
# Verifica presenza workflow
ls -la /app/workflows/
ls -la /app/state_data/workflows/
```

**Timeout generazione**
```bash
# Aumenta timeout in .env
COMFYUI_TIMEOUT=600
```

### Log di Debug

```bash
# Abilita debug logging
export PYTHONPATH=/app
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from plex_playlist_sync.utils.playlist_cover_generator import generate_ai_cover_comfyui
generate_ai_cover_comfyui('Test', genres=['rock'])
"
```

## 📊 Performance

### Tempi di Generazione

- **Flux Schnell (8 steps)**: ~8-15 secondi per copertina 1024x1024
- **Flux Dev (20 steps)**: ~30-60 secondi per copertina 1024x1024
- **Fallback Simple**: ~2-5 secondi per copertina Pillow

### Ottimizzazioni

1. **Caching**: I workflow vengono cachati in memoria
2. **Timeout**: Configurabile per evitare attese eccessive
3. **Fallback**: Automatico se ComfyUI non disponibile

## 🔮 Estensioni Future

### Workflow Personalizzati

È possibile aggiungere workflow personalizzati:

```bash
# Aggiungi nuovo workflow
cp my_workflow.json /app/workflows/

# Configura in .env
COMFYUI_WORKFLOW=my_workflow
```

### Stili per Genere

Il sistema supporta stili differenti per genere:
- `reggae` → tropical, warm colors
- `electronic` → neon, geometric
- `jazz` → vintage, sophisticated
- `rock` → bold, dynamic

## 🤝 Contribuire

Per migliorare l'integrazione ComfyUI:

1. Testa nuovi workflow
2. Ottimizza i prompt per genere
3. Aggiungi supporto per nuovi modelli
4. Migliora l'error handling

---

**Nota**: Questa integrazione è ottimizzata per ComfyUI con Flux, ma può essere adattata per altri modelli Stable Diffusion compatibili.