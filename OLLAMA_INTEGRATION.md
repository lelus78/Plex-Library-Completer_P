# 🤖 Ollama AI Integration - Fallback per Playlist Generation

## Overview
Il sistema ora supporta **Ollama** come fallback locale per la generazione di playlist AI quando **Google Gemini** raggiunge i limiti di utilizzo o non è disponibile.

## 🚀 Vantaggi dell'integrazione Ollama

- **✅ Nessun limite API**: Ollama funziona localmente senza restrizioni
- **✅ Funziona offline**: Non richiede connessione internet
- **✅ Gratuito**: Nessun costo aggiuntivo
- **✅ Fallback automatico**: Si attiva automaticamente quando Gemini fallisce
- **✅ Qualità elevata**: Modelli come Hermes-3 offrono risultati eccellenti

## 📋 Configurazione

### 1. Installazione Ollama

**Windows/macOS/Linux:**
```bash
# Scarica da https://ollama.ai
# Oppure usando curl:
curl -fsSL https://ollama.ai/install.sh | sh
```

### 2. Download modelli compatibili

```bash
# Modello consigliato (4.7GB)
ollama pull hermes3:8b

# Alternative
ollama pull llama3.1:8b
ollama pull mistral:7b
```

### 3. Verifica installazione

```bash
# Lista modelli installati
ollama list

# Test rapido
ollama run hermes3:8b --format json
```

### 4. Configurazione .env

Aggiungi al tuo file `.env`:

```bash
# Configurazione Ollama (opzionale)
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=hermes3:8b
```

## 🔧 Come funziona il Cascading Fallback

### ✅ Per NUOVE Playlist (On-Demand Generation):
1. **Prima tentativo**: Il sistema prova con **Gemini 2.5 Flash** (250 RPD, 10 RPM)
2. **Secondo tentativo**: Se il primo fallisce, prova **Gemini 2.0 Flash** (200 RPD, 15 RPM)  
3. **Fallback finale**: Se entrambi i Gemini falliscono, passa a **Ollama**

### 🔄 Per RESCAN Playlist Esistenti (Quota-Saving):
1. **Database lookup**: Legge i brani salvati dalla generazione originale
2. **Library search**: Cerca ogni brano nella libreria Plex corrente  
3. **Smart update**: Aggiorna solo i brani trovati, senza sprecare quota AI
4. **No AI calls**: Zero chiamate a Gemini/Ollama durante il rescan

### 📊 Benefici del Sistema:
- **Logging dettagliato**: I log mostrano quale AI viene utilizzata
- **Formato consistente**: Tutti i sistemi producono playlist nel stesso formato
- **Quota preservation**: Rescan intelligente senza sprecare API calls

## 🧪 Test del Sistema

### Via Web Interface
Visita: `http://localhost:5000/test_ai_services`

### Via Docker Logs
```bash
docker logs plex-library-completer | grep -E "(Gemini|Ollama)"
```

### Test manuale
```bash
# Test Ollama diretto
docker exec -it ollama ollama run hermes3:8b --format json
>>> Genera una playlist di 5 hit dance del 2020...
```

## 📊 Log Output Examples

**Gemini 2.5 Flash disponibile:**
```
✅ Tentativo con Gemini 2.5 Flash...
✅ Playlist generata con successo usando Gemini 2.5 Flash
```

**Cascading fallback in azione:**
```
🔄 Tentativo con Gemini 2.5 Flash...
⚠️ Gemini 2.5 Flash fallito: Rate limit exceeded
🔄 Tentativo con Gemini 2.0 Flash...
✅ Playlist generata con successo usando Gemini 2.0 Flash
```

**Fallback completo su Ollama:**
```
⚠️ Gemini 2.5 Flash fallito: Daily limit reached (250 RPD)
⚠️ Gemini 2.0 Flash fallito: Daily limit reached (200 RPD)
🤖 Usando Ollama come fallback finale...
✅ Playlist generata con successo usando Ollama
```

## 🐳 Docker Integration

Se Ollama è in un container separato, modifica la configurazione:

```bash
# Se Ollama è in un altro container
OLLAMA_URL=http://ollama:11434

# Se Ollama è sul host (Docker Desktop)
OLLAMA_URL=http://host.docker.internal:11434
```

## 🎵 Qualità delle Playlist

**Gemini**: Ottimizzato per preferenze musicali e dati chart attuali
**Ollama**: Focussato su diversità e richieste specifiche dell'utente

Entrambi producono playlist di alta qualità con:
- Diversificazione artisti
- Rispetto del numero di tracce richieste
- Formattazione JSON consistente
- Metadati completi (titolo, artista, anno)

## 🔧 Troubleshooting

### Ollama non si connette
```bash
# Verifica servizio
curl http://localhost:11434/api/tags

# Restart Ollama
ollama serve
```

### Modello non trovato
```bash
# Lista modelli disponibili
ollama list

# Download modello mancante
ollama pull hermes3:8b
```

### Performance lenta
- Usa modelli più piccoli (7B invece di 13B)
- Assicurati che Ollama abbia accesso alla GPU
- Chiudi altre applicazioni pesanti

## 📈 Metriche Performance

| Modello | Dimensione | Tempo Generazione | Qualità |
|---------|------------|-------------------|---------|
| hermes3:8b | 4.7GB | ~30-60s | ⭐⭐⭐⭐⭐ |
| llama3.1:8b | 4.7GB | ~45-90s | ⭐⭐⭐⭐ |
| mistral:7b | 4.1GB | ~20-45s | ⭐⭐⭐ |

## 🆕 Sviluppi Futuri

- [ ] Support per modelli Ollama più grandi (13B, 70B)
- [ ] Caching intelligente delle risposte
- [ ] Fine-tuning per playlist musicali
- [ ] Integrazione con altri servizi AI locali