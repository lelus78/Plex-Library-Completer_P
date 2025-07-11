# 🎨 AI Cover Generation Setup Guide

## 📋 Overview

Il sistema di generazione copertine supporta multiple modalità:
- **🎯 Simple (Pillow)**: Sempre disponibile, veloce, funziona su qualsiasi hardware
- **🚀 AI Locale**: FLUX.1-schnell, SDXL - richiede GPU con 6GB+ VRAM
- **🔧 Auto-Detection**: Rileva automaticamente la migliore opzione disponibile

## ⚡ Quick Setup

### 1. Configurazione Base (.env)
```bash
# Modalità semplice (attiva per default)
AI_COVER_GENERATION=simple

# Opzioni alternative:
# AI_COVER_GENERATION=auto        # GPU auto-detect (richiede 10GB+ spazio disco)
# AI_COVER_GENERATION=gpu-only    # Solo se GPU disponibile
# AI_COVER_GENERATION=disabled    # Nessuna copertina
```

### ⚠️ Requisiti Spazio Disco
- **Modalità Simple**: 50MB (solo Pillow)
- **Modalità AI**: 10GB+ (modelli AI + cache)
- **Problema Comune**: Se disco pieno, usare modalità `simple`

### 2. Auto-Installazione AI (Opzionale)
```bash
# Abilita installazione automatica dipendenze
AI_AUTO_INSTALL=true
```

### 3. Restart Container
```bash
docker-compose restart
```

## 🎮 Requisiti Hardware

### GPU Requirements
| Modello | VRAM Minima | Tempo Generazione | Qualità |
|---------|-------------|-------------------|---------|
| FLUX.1-schnell | 8GB+ | 5-10 secondi | Eccellente |
| Stable Diffusion XL | 6GB+ | 15-25 secondi | Molto Buona |
| Pillow Fallback | Qualsiasi | 2-3 secondi | Buona |

### GPU Supportate
- ✅ NVIDIA RTX 30/40 series (6GB+)
- ✅ NVIDIA RTX 20 series (8GB+)  
- ✅ Workstation GPU (Quadro, Tesla)
- ❌ CPU-only (troppo lento - usa Simple)

## 🔧 Installazione Manuale

Se preferisci installare manualmente le dipendenze AI:

```bash
# Entra nel container
docker exec -it plex-library-completer bash

# Installa dipendenze AI
pip install torch>=2.0.0 diffusers>=0.30.0 transformers>=4.40.0 accelerate>=0.30.0

# Opzionale: ottimizzazioni VRAM
pip install xformers>=0.0.20

# Restart container
exit
docker-compose restart
```

## ⚙️ Configurazione Avanzata

### Force Specific Model
```bash
# Forza modello specifico
AI_FORCE_MODEL=flux-schnell    # flux-schnell, flux-dev, sdxl
```

### Performance Tuning
```bash
# Override parametri generazione
AI_INFERENCE_STEPS=4           # 4 per schnell, 20 per SDXL
AI_GUIDANCE_SCALE=7.5          # 0.0 per schnell, 7.5 per SDXL
```

## 🧪 Testing

Testa il sistema dopo la configurazione:

```bash
# Test rilevamento GPU
docker exec plex-library-completer python -c "
from plex_playlist_sync.utils.playlist_cover_generator import detect_gpu_capabilities
print(f'GPU Capability: {detect_gpu_capabilities()}')
"

# Test generazione copertina
docker exec plex-library-completer python -c "
from plex_playlist_sync.utils.playlist_cover_generator import test_cover_generation
result = test_cover_generation()
print('✅ Working!' if result else '❌ Failed!')
"
```

## 🎯 Modalità Operazione

### Auto Mode (Consigliata)
- Rileva automaticamente GPU disponibile
- Usa il modello migliore possibile
- Fallback garantito a Pillow se problemi

### GPU-Only Mode  
- Genera copertine SOLO se GPU disponibile
- Nessuna copertina se hardware insufficiente
- Ideale per setup dedicati

### Simple Mode
- Usa sempre Pillow (copertine semplici)
- Garantito funzionamento su qualsiasi hardware
- Veloce e leggero

## 🚨 Troubleshooting

### GPU Non Rilevata
```bash
# Check NVIDIA drivers
nvidia-smi

# Check CUDA in container
docker exec plex-library-completer python -c "import torch; print(torch.cuda.is_available())"
```

### Out of Memory
```bash
# Riduci dimensioni immagine
AI_IMAGE_SIZE=512              # Default: 1024

# Usa SDXL invece di FLUX
AI_FORCE_MODEL=sdxl
```

### Installazione Fallita
```bash
# Pulisci cache pip
docker exec plex-library-completer pip cache purge

# Reinstalla dipendenze
AI_AUTO_INSTALL=true
docker-compose restart
```

## 📊 Performance Comparison

| Sistema | Hardware | Tempo | Qualità | VRAM |
|---------|----------|--------|---------|------|
| FLUX.1-schnell | RTX 4090 | 6s | ⭐⭐⭐⭐⭐ | 4GB |
| FLUX.1-schnell | RTX 3080 | 10s | ⭐⭐⭐⭐⭐ | 6GB |
| SDXL | RTX 3070 | 20s | ⭐⭐⭐⭐ | 6GB |
| Pillow | Qualsiasi | 3s | ⭐⭐⭐ | 50MB |

## 🎨 Esempi Risultati

### FLUX.1-schnell
- Copertine ultra-realistiche
- Dettagli perfetti
- Colori vividi e moderni
- Testo integrato naturalmente

### Stable Diffusion XL  
- Copertine professionali
- Buona qualità artistica
- Stile consistente
- Buona integrazione testo

### Pillow Simple
- Design pulito e moderno
- Colori coordinati per genere
- Gradients professionali
- Tipografia chiara

## 💡 Tips

1. **Prima Generazione**: Il primo caricamento modello richiede 1-2 minuti
2. **Caching**: I modelli vengono scaricati automaticamente e cached
3. **Multi-GPU**: Il sistema usa automaticamente la GPU più potente
4. **Background**: La generazione avviene in background senza bloccare l'app
5. **Fallback**: Sempre garantito il fallback a Pillow in caso di problemi

---

Per supporto: controllare i logs del container con `docker logs plex-library-completer`