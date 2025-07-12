import os
import logging
import json
import requests
import time
from typing import List, Dict, Optional
from datetime import datetime, timedelta

import google.generativeai as genai
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

from .helperClasses import Playlist as PlexPlaylist, Track as PlexTrack, UserInputs
from .plex import update_or_create_plex_playlist
from .database import add_managed_ai_playlist, get_managed_ai_playlists_for_user
from .music_charts import music_charts_searcher
from .playlist_cover_generator import generate_playlist_cover_ai, extract_genres_from_playlist_data, is_cover_generation_enabled
from .i18n import i18n, translate_genre

# Otteniamo il logger che è già stato configurato in app.py
logger = logging.getLogger(__name__)

# Global state manager for Gemini availability
class GeminiStateManager:
    def __init__(self):
        # Primary model state (Gemini 2.5 Flash - 250 RPD, 10 RPM)
        self.is_blocked = False
        self.blocked_until = None
        self.last_error = None
        self.consecutive_failures = 0
        self.total_requests_today = 0
        self.daily_limit_reached = False
        self.last_reset_date = datetime.now().date()
        
        # Secondary model state (Gemini 2.0 Flash - 200 RPD, 15 RPM)
        self.secondary_is_blocked = False
        self.secondary_blocked_until = None
        self.secondary_last_error = None
        self.secondary_consecutive_failures = 0
        self.secondary_requests_today = 0
        self.secondary_daily_limit_reached = False
    
    def record_success(self, model_type: str = "primary"):
        """Record a successful Gemini request"""
        if model_type == "primary":
            self.is_blocked = False
            self.blocked_until = None
            self.consecutive_failures = 0
            self.total_requests_today += 1
        else:
            self.secondary_is_blocked = False
            self.secondary_blocked_until = None
            self.secondary_consecutive_failures = 0
            self.secondary_requests_today += 1
        
    def record_failure(self, error_message: str, is_rate_limit: bool = False, model_type: str = "primary"):
        """Record a failed Gemini request"""
        if model_type == "primary":
            self.last_error = error_message
            self.consecutive_failures += 1
            
            # Check if it's a daily limit or quota error
            if is_rate_limit or "quota" in error_message.lower() or "rate limit" in error_message.lower():
                if "per day" in error_message.lower() or "daily" in error_message.lower():
                    # Daily limit reached - block until tomorrow
                    self.daily_limit_reached = True
                    tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    self.blocked_until = tomorrow
                    logger.warning(f"🚫 Gemini 2.5 Flash daily limit reached (250 RPD). Blocked until {tomorrow}")
                else:
                    # Temporary rate limit - block for 1 hour
                    self.blocked_until = datetime.now() + timedelta(hours=1)
                    logger.warning(f"⏸️ Gemini 2.5 Flash rate limited. Blocked until {self.blocked_until}")
            
            self.is_blocked = True
        else:
            self.secondary_last_error = error_message
            self.secondary_consecutive_failures += 1
            
            # Check if it's a daily limit or quota error for secondary model
            if is_rate_limit or "quota" in error_message.lower() or "rate limit" in error_message.lower():
                if "per day" in error_message.lower() or "daily" in error_message.lower():
                    # Daily limit reached - block until tomorrow
                    self.secondary_daily_limit_reached = True
                    tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    self.secondary_blocked_until = tomorrow
                    logger.warning(f"🚫 Gemini 2.0 Flash daily limit reached (200 RPD). Blocked until {tomorrow}")
                else:
                    # Temporary rate limit - block for 1 hour
                    self.secondary_blocked_until = datetime.now() + timedelta(hours=1)
                    logger.warning(f"⏸️ Gemini 2.0 Flash rate limited. Blocked until {self.secondary_blocked_until}")
            
            self.secondary_is_blocked = True
    
    def is_available(self, model_type: str = "primary") -> bool:
        """Check if Gemini is currently available"""
        # Reset daily counters if new day
        if datetime.now().date() > self.last_reset_date:
            self.daily_limit_reached = False
            self.secondary_daily_limit_reached = False
            self.total_requests_today = 0
            self.secondary_requests_today = 0
            self.last_reset_date = datetime.now().date()
            if self.daily_limit_reached:
                self.is_blocked = False
                self.blocked_until = None
            if self.secondary_daily_limit_reached:
                self.secondary_is_blocked = False
                self.secondary_blocked_until = None
        
        if model_type == "primary":
            # Check if block period has expired for primary
            if self.blocked_until and datetime.now() >= self.blocked_until:
                self.is_blocked = False
                self.blocked_until = None
                logger.info("✅ Gemini 2.5 Flash block period expired. Service available again.")
            
            return not self.is_blocked
        else:
            # Check if block period has expired for secondary
            if self.secondary_blocked_until and datetime.now() >= self.secondary_blocked_until:
                self.secondary_is_blocked = False
                self.secondary_blocked_until = None
                logger.info("✅ Gemini 2.0 Flash block period expired. Service available again.")
            
            return not self.secondary_is_blocked
    
    def get_best_available_model(self) -> tuple[str, bool]:
        """Get the best available Gemini model"""
        if self.is_available("primary"):
            return "gemini-2.5-flash", True
        elif self.is_available("secondary"):
            return "gemini-2.0-flash", True
        else:
            return None, False
    
    def get_status_info(self) -> Dict:
        """Get current status information for UI"""
        return {
            "primary": {
                "available": self.is_available("primary"),
                "blocked": self.is_blocked,
                "blocked_until": self.blocked_until.isoformat() if self.blocked_until else None,
                "last_error": self.last_error,
                "consecutive_failures": self.consecutive_failures,
                "requests_today": self.total_requests_today,
                "daily_limit_reached": self.daily_limit_reached,
                "model": "gemini-2.5-flash"
            },
            "secondary": {
                "available": self.is_available("secondary"),
                "blocked": self.secondary_is_blocked,
                "blocked_until": self.secondary_blocked_until.isoformat() if self.secondary_blocked_until else None,
                "last_error": self.secondary_last_error,
                "consecutive_failures": self.secondary_consecutive_failures,
                "requests_today": self.secondary_requests_today,
                "daily_limit_reached": self.secondary_daily_limit_reached,
                "model": "gemini-2.0-flash"
            },
            "any_available": self.is_available("primary") or self.is_available("secondary"),
            "best_model": self.get_best_available_model()[0]
        }

# Global instance
gemini_state = GeminiStateManager()

def configure_gemini() -> tuple[Optional[genai.GenerativeModel], str]:
    """Configura e restituisce il miglior modello Gemini disponibile per retrocompatibilità."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY non trovata nel file .env. La creazione della playlist AI verrà saltata.")
        return None, ""
    
    # Get best available model
    model_name, is_available = gemini_state.get_best_available_model()
    
    if not is_available:
        logger.warning("🚫 Tutti i modelli Gemini sono temporaneamente non disponibili")
        return None, ""
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        logger.info(f"✅ Configurato modello Gemini: {model_name}")
        return model, model_name
    except Exception as e:
        logger.error(f"Errore nella configurazione di Gemini {model_name}: {e}")
        model_type = "primary" if model_name == "gemini-2.5-flash" else "secondary"
        gemini_state.record_failure(str(e), model_type=model_type)
        return None, ""

def configure_gemini_simple() -> Optional[genai.GenerativeModel]:
    """Versione semplificata per retrocompatibilità che restituisce solo il modello."""
    model, _ = configure_gemini()
    return model

def configure_ollama() -> bool:
    """Verifica se Ollama è disponibile come fallback."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "hermes3:8b")
    
    try:
        # Test di connessione a Ollama
        response = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [model["name"] for model in models]
            if any(ollama_model in name for name in model_names):
                logger.info(f"✅ Ollama disponibile: {ollama_model} su {ollama_url}")
                return True
            else:
                logger.warning(f"⚠️ Modello {ollama_model} non trovato in Ollama. Modelli disponibili: {model_names}")
                return False
        else:
            logger.warning(f"⚠️ Ollama non risponde su {ollama_url}")
            return False
    except Exception as e:
        logger.warning(f"⚠️ Ollama non disponibile: {e}")
        return False

def get_gemini_status() -> Dict:
    """Ottiene lo stato dettagliato di Gemini per l'interfaccia web."""
    status_info = gemini_state.get_status_info()
    
    # Aggiungi informazioni sulla disponibilità generale
    primary_available = status_info["primary"]["available"]
    secondary_available = status_info["secondary"]["available"]
    
    # Determina lo stato generale per l'UI
    if primary_available:
        current_status = "available"
        active_model = "gemini-2.5-flash"
        blocked = False
        blocked_until = None
    elif secondary_available:
        current_status = "available"
        active_model = "gemini-2.0-flash"
        blocked = False
        blocked_until = None
    else:
        current_status = "blocked"
        active_model = None
        blocked = True
        # Usa il tempo di blocco del modello primario se disponibile
        blocked_until = status_info["primary"]["blocked_until"]
    
    # Aggiungi informazioni compatibili con l'UI esistente
    status_info.update({
        "available": current_status == "available",
        "blocked": blocked,
        "blocked_until": blocked_until,
        "active_model": active_model,
        "daily_limit_reached": status_info["primary"]["daily_limit_reached"] and status_info["secondary"]["daily_limit_reached"],
        "last_error": status_info["primary"]["last_error"] or status_info["secondary"]["last_error"]
    })
    
    return status_info

def test_ai_services() -> Dict[str, bool]:
    """Testa la disponibilità di entrambi i servizi AI (Gemini e Ollama)."""
    results = {
        "gemini": False,
        "gemini_primary": False,
        "gemini_secondary": False,
        "ollama": False,
        "gemini_error": None,
        "ollama_error": None,
        "gemini_status": get_gemini_status()
    }
    
    # Test Gemini 2.5 Flash (primary)
    try:
        if gemini_state.is_available("primary"):
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel("gemini-2.5-flash")
            results["gemini_primary"] = True
            logger.info("✅ Gemini 2.5 Flash disponibile")
        else:
            logger.info("⚠️ Gemini 2.5 Flash temporaneamente bloccato")
    except Exception as e:
        logger.warning(f"⚠️ Errore test Gemini 2.5 Flash: {e}")
    
    # Test Gemini 2.0 Flash (secondary)
    try:
        if gemini_state.is_available("secondary"):
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel("gemini-2.0-flash")
            results["gemini_secondary"] = True
            logger.info("✅ Gemini 2.0 Flash disponibile")
        else:
            logger.info("⚠️ Gemini 2.0 Flash temporaneamente bloccato")
    except Exception as e:
        logger.warning(f"⚠️ Errore test Gemini 2.0 Flash: {e}")
    
    # Test generale Gemini
    results["gemini"] = results["gemini_primary"] or results["gemini_secondary"]
    
    if not results["gemini"]:
        if not gemini_state.is_available("primary") and not gemini_state.is_available("secondary"):
            results["gemini_error"] = "Tutti i modelli Gemini sono temporaneamente bloccati per rate limits"
        else:
            results["gemini_error"] = "Configurazione fallita per tutti i modelli"
    else:
        logger.info("✅ Almeno un modello Gemini è disponibile")
    
    # Test Ollama
    try:
        if configure_ollama():
            results["ollama"] = True
            logger.info("✅ Ollama configurato correttamente")
        else:
            results["ollama_error"] = "Servizio non disponibile"
    except Exception as e:
        results["ollama_error"] = str(e)
        logger.error(f"❌ Errore test Ollama: {e}")
    
    return results

def generate_playlist_with_ollama(prompt: str, track_count: int = 25) -> Optional[Dict]:
    """Genera una playlist usando Ollama come fallback."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "hermes3:8b")
    
    # Schema JSON per forzare il formato di output
    json_schema = {
        "type": "object",
        "properties": {
            "playlist_name": {"type": "string"},
            "description": {"type": "string"},
            "genre": {"type": "string"},
            "tracks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "artist": {"type": "string"},
                        "year": {"type": "integer"}
                    },
                    "required": ["title", "artist"]
                }
            }
        },
        "required": ["playlist_name", "tracks"]
    }
    
    # Prompt ottimizzato per Ollama con schema JSON
    ollama_prompt = f"""Genera una playlist di {track_count} brani basata su questa richiesta:
{prompt}

Rispondi SOLO con un JSON valido nel formato esatto:
{{
  "playlist_name": "Nome della playlist",
  "description": "Breve descrizione",
  "genre": "Genere principale",
  "tracks": [
    {{"title": "Titolo Canzone", "artist": "Nome Artista", "year": 2020}}
  ]
}}

IMPORTANTE:
- Includi ESATTAMENTE {track_count} tracce
- JSON valido senza testo aggiuntivo
- Diversifica gli artisti
- Rispetta le preferenze musicali nella richiesta"""

    try:
        payload = {
            "model": ollama_model,
            "prompt": ollama_prompt,
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9
            }
        }
        
        logger.info(f"🤖 Generando playlist con Ollama ({ollama_model})...")
        response = requests.post(f"{ollama_url}/api/generate", json=payload, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            playlist_json = result.get("response", "")
            
            try:
                playlist_data = json.loads(playlist_json)
                logger.info(f"✅ Playlist generata con Ollama: {playlist_data.get('playlist_name', 'Senza nome')}")
                return playlist_data
            except json.JSONDecodeError as e:
                logger.error(f"❌ Errore parsing JSON da Ollama: {e}")
                logger.debug(f"Risposta raw: {playlist_json}")
                return None
        else:
            logger.error(f"❌ Errore Ollama: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Errore nella chiamata a Ollama: {e}")
        return None

def get_plex_favorites_by_id(plex: PlexServer, playlist_id: str) -> Optional[List[str]]:
    """Recupera le tracce da una playlist Plex usando il suo ID univoco (ratingKey)."""
    if not playlist_id:
        logger.warning("Nessun ID per la playlist dei preferiti fornito. Salto il recupero tracce.")
        return None
    logger.info(f"Tento di recuperare la playlist dei preferiti con ID: {playlist_id}")
    try:
        playlist = plex.fetchItem(int(playlist_id))
        tracks = [f"{track.artist().title} - {track.title}" for track in playlist.items()]
        logger.info(f"Trovate {len(tracks)} tracce nella playlist '{playlist.title}'.")
        return tracks
    except NotFound:
        logger.error(f"ERRORE: Playlist con ID '{playlist_id}' non trovata sul server Plex. Controlla l'ID nel file .env.")
        return None
    except Exception as e:
        logger.error(f"Errore imprevisto durante il recupero della playlist con ID '{playlist_id}': {e}")
        return None

def get_localized_prompt_base(language: Optional[str] = None) -> str:
    """Ottiene il prompt base nella lingua specificata"""
    lang = language or i18n.get_language()
    
    if lang == 'en':
        return """You are an expert music curator with vast knowledge of all genres and eras. Create a diverse, exciting playlist based on the given information.

CRITICAL PLAYLIST RULES:
1. TRACK COUNT: Generate EXACTLY the number of tracks requested by the user (if specified), otherwise default to 30-40 tracks
2. VARIETY IS KEY: Use favorites as a STARTING POINT for taste, but explore widely beyond them
3. DISCOVERY FOCUS: 60-70% should be tracks NOT in the favorites list to encourage musical discovery
4. GENRE EXPANSION: If user likes rock, include subgenres (indie rock, alternative, classic rock, etc.)
5. ERA MIXING: Blend different decades while respecting the user's taste profile
6. HIDDEN GEMS: Include some lesser-known tracks alongside popular ones

MUSIC CURATION STRATEGY:
- Favorites = taste indicators, NOT a shopping list to repeat
- Think like a radio DJ: surprise and delight while staying true to preferences
- Include artists similar to favorites but not necessarily the same songs
- Add complementary genres that flow well together
- Balance energy levels throughout the playlist

FORMATTING RULES:
1. Respond ONLY in valid JSON format
2. Include a creative, catchy title that reflects the playlist's unique character
3. Add an engaging description (2-3 sentences) that explains the playlist's journey
4. Each track must have "title" and "artist" fields
5. Prioritize tracks likely to exist in a typical music library
6. When given a specific request, fulfill it EXACTLY while adding creative touches

JSON Format:
{
  "title": "Creative Playlist Title",
  "description": "Brief description of the playlist theme and musical journey",
  "tracks": [
    {"artist": "Artist Name", "title": "Song Title"},
    // ... 25 tracks total
  ]
}"""
    else:
        return """Sei un esperto curatore musicale con vasta conoscenza di tutti i generi ed epoche. Crea una playlist diversificata ed entusiasmante basata sulle informazioni fornite.

REGOLE CRITICHE PER LA PLAYLIST:
1. NUMERO BRANI: Genera ESATTAMENTE il numero di tracce richiesto dall'utente (se specificato), altrimenti 30-40 brani
2. LA VARIETÀ È FONDAMENTALE: Usa i preferiti come PUNTO DI PARTENZA per i gusti, ma esplora ampiamente oltre di essi
3. FOCUS SULLA SCOPERTA: 60-70% dovrebbero essere brani NON nella lista dei preferiti per incoraggiare la scoperta musicale
4. ESPANSIONE DEI GENERI: Se l'utente ama il rock, includi sottogeneri (indie rock, alternative, classic rock, ecc.)
5. MIXAGGIO DI EPOCHE: Mescola decenni diversi rispettando il profilo dei gusti dell'utente
6. GEMME NASCOSTE: Includi alcuni brani meno conosciuti insieme a quelli popolari

STRATEGIA DI CURATELA MUSICALE:
- Preferiti = indicatori di gusto, NON una lista della spesa da ripetere
- Pensa come un DJ radiofonico: sorprendi e diletta rimanendo fedele alle preferenze
- Includi artisti simili ai preferiti ma non necessariamente le stesse canzoni
- Aggiungi generi complementari che fluiscono bene insieme
- Bilancia i livelli di energia in tutta la playlist

REGOLE DI FORMATTAZIONE:
1. Rispondi SOLO in formato JSON valido
2. Includi un titolo creativo e accattivante che riflette il carattere unico della playlist
3. Aggiungi una descrizione coinvolgente (2-3 frasi) che spiega il viaggio della playlist
4. Ogni brano deve avere i campi "title" e "artist"
5. Prioritizza brani che probabilmente esistono in una libreria musicale tipica
6. Quando ricevi una richiesta specifica, soddisfala ESATTAMENTE aggiungendo tocchi creativi

Formato JSON:
{
  "title": "Titolo Creativo della Playlist",
  "description": "Breve descrizione del tema e del viaggio musicale della playlist",
  "tracks": [
    {"artist": "Nome Artista", "title": "Titolo Canzone"},
    // ... numero di brani richiesto
  ]
}"""

def generate_playlist_prompt(
    favorite_tracks: List[str],
    custom_prompt: Optional[str] = None,
    previous_week_tracks: Optional[List[Dict]] = None,
    include_charts_data: bool = True,
    language: Optional[str] = None,
    requested_track_count: Optional[int] = None
) -> str:
    """Crea un prompt robusto per produrre una playlist in JSON valido con dati di classifiche aggiornati."""
    tracks_str = "\n".join(favorite_tracks)
    
    # Raccolta dati musicali aggiornati
    charts_section = ""
    if include_charts_data:
        try:
            logger.info("Raccogliendo dati musicali aggiornati per informare Gemini...")
            music_data = music_charts_searcher.get_comprehensive_music_data(
                context=custom_prompt if custom_prompt else "playlist generation"
            )
            
            charts_info = []
            
            # Billboard Hot 100
            if music_data.get("charts", {}).get("billboard_hot_100"):
                billboard_top_10 = music_data["charts"]["billboard_hot_100"][:10]
                billboard_str = "\n".join([f"{t['position']}. {t['artist']} - {t['title']}" for t in billboard_top_10])
                charts_info.append(f"BILLBOARD HOT 100 (Top 10 attuale):\n{billboard_str}")
            
            # Spotify Global
            if music_data.get("charts", {}).get("spotify_global"):
                spotify_top_10 = music_data["charts"]["spotify_global"][:10]
                spotify_str = "\n".join([f"{t['position']}. {t['artist']} - {t['title']}" for t in spotify_top_10])
                charts_info.append(f"SPOTIFY GLOBAL TOP 10:\n{spotify_str}")
            
            # Classifiche italiane
            if music_data.get("charts", {}).get("italian"):
                italian_top_10 = music_data["charts"]["italian"][:10]
                italian_str = "\n".join([f"{t['position']}. {t['artist']} - {t['title']}" for t in italian_top_10])
                charts_info.append(f"CLASSIFICA ITALIANA (Top 10):\n{italian_str}")
            
            # Tendenze stagionali
            if music_data.get("trends", {}).get("seasonal"):
                seasonal_data = music_data["trends"]["seasonal"]
                season = seasonal_data.get("season", "unknown")
                trends = seasonal_data.get("trends", [])[:5]
                if trends:
                    seasonal_str = "\n".join([f"- {t['artist']} - {t['title']}" for t in trends])
                    charts_info.append(f"TENDENZE STAGIONALI ({season.upper()}):\n{seasonal_str}")
            
            # Tendenze per genere (se il prompt contiene riferimenti a generi)
            genre_keywords = {
                "rock": ["rock", "metal", "alternative", "indie"],
                "pop": ["pop", "mainstream", "radio"],
                "electronic": ["electronic", "edm", "house", "techno", "dance"],
                "hip-hop": ["hip-hop", "rap", "trap", "urban"]
            }
            
            prompt_lower = (custom_prompt or "").lower()
            for genre, keywords in genre_keywords.items():
                if any(keyword in prompt_lower for keyword in keywords):
                    genre_trends = music_data.get("trends", {}).get(genre, [])[:5]
                    if genre_trends:
                        genre_str = "\n".join([f"- {t['artist']} - {t['title']} ({t['trend']})" for t in genre_trends])
                        charts_info.append(f"TENDENZE {genre.upper()}:\n{genre_str}")
            
            if charts_info:
                charts_section = f"""
DATI MUSICALI AGGIORNATI (usa solo come ispirazione secondaria se compatibile con la richiesta utente):
---
{chr(10).join(charts_info)}
---
ISTRUZIONE: Usa questi dati SOLO se compatibili con la richiesta specifica dell'utente. Non includere tracce dalle classifiche se non si allineano perfettamente con il genere/stile richiesto.
"""
            logger.info("Dati musicali aggiornati integrati nel prompt con successo")
        except Exception as e:
            logger.warning(f"Impossibile recuperare dati musicali aggiornati: {e}")
            charts_section = ""
    
    # Determina il numero di tracce da generare
    if requested_track_count:
        track_count_instruction = f"ESATTAMENTE {requested_track_count} brani"
    else:
        track_count_instruction = "35-40 brani"
    
    if custom_prompt:
        # Assicurati che il prompt custom includa il numero di tracce richiesto
        if requested_track_count and str(requested_track_count) not in custom_prompt:
            core_prompt = f"{custom_prompt.strip()}. Genera {track_count_instruction}."
        else:
            core_prompt = custom_prompt.strip()
        previous_week_section = ""
    else:
        core_prompt = f"Genera una playlist completamente nuova di {track_count_instruction} basata sui gusti dimostrati nella lista sottostante. Prioritizza la scoperta di nuovi brani (60-70% dovrebbero essere diversi dai preferiti) mantenendo coerenza con i gusti. Includi un mix equilibrato di classici, novità, e gemme nascoste."
        if previous_week_tracks:
            previous_tracks_str = "\n".join([f"- {track['artist']} - {track['title']}" for track in previous_week_tracks])
            previous_week_section = f"""
LISTA TRACCE SETTIMANA PRECEDENTE (per continuità):
---
{previous_tracks_str}
---
ISTRUZIONE SPECIALE: Per creare una "storia musicale", includi nella nuova playlist tra i 5 e i 10 brani dalla lista della settimana precedente che si legano meglio con le nuove canzoni che sceglierai.
"""
        else:
            previous_week_section = ""

    # Usa lingua predefinita se non specificata (safe per background tasks)
    lang = language or 'en'  # Default a inglese per background tasks
    # Ottieni il prompt base localizzato
    base_prompt = get_localized_prompt_base(lang)
    
    # Sezioni tradotte con priorità alle richieste utente
    if lang == 'en':
        favorites_header = "FAVORITE TRACKS (for reference on general tastes):"
        charts_header = "CURRENT MUSIC TRENDS (use sparingly for inspiration only):"
        previous_header = "PREVIOUS WEEK TRACKS (for continuity):"
        instruction_text = "INSTRUCTION: To create a \"musical story\", include 5-10 tracks from the previous week's list that best connect with the new songs you choose."
        if custom_prompt:
            balance_note = "CRITICAL: The user's specific request takes ABSOLUTE PRIORITY. Chart data should only be used for minor inspiration if it aligns with the user's request. Focus entirely on fulfilling the user's exact musical preferences and genre requirements."
        else:
            balance_note = "IMPORTANT: Skillfully balance the user's personal tastes with current trends to create a modern and personalized playlist."
    else:
        favorites_header = "LISTA TRACCE PREFERITE (per riferimento sui gusti generali):"
        charts_header = "TENDENZE MUSICALI ATTUALI (usa solo per ispirazione minima):"
        previous_header = "LISTA TRACCE SETTIMANA PRECEDENTE (per continuità):"
        instruction_text = "ISTRUZIONE SPECIALE: Per creare una \"storia musicale\", includi nella nuova playlist tra i 5 e i 10 brani dalla lista della settimana precedente che si legano meglio con le nuove canzoni che sceglierai."
        if custom_prompt:
            balance_note = "CRITICO: La richiesta specifica dell'utente ha PRIORITÀ ASSOLUTA. I dati delle classifiche dovrebbero essere usati solo per ispirazione minima se allineati con la richiesta dell'utente. Concentrati interamente nel soddisfare le esatte preferenze musicali e requisiti di genere dell'utente."
        else:
            balance_note = "IMPORTANTE: Bilancia sapientemente i gusti personali dell'utente con le tendenze attuali per creare una playlist moderna e personalizzata."

    # Aggiorna le sezioni charts per la lingua
    if charts_section and lang == 'en':
        charts_section = charts_section.replace("DATI MUSICALI AGGIORNATI", "CURRENT MUSIC TRENDS")
        charts_section = charts_section.replace("TENDENZE", "TRENDS")
        charts_section = charts_section.replace("utilizza questi per ispirazione", "use sparingly for inspiration only")
        charts_section = charts_section.replace("ISTRUZIONE: Usa questi dati", "INSTRUCTION: Use this data sparingly")
    
    if previous_week_section and lang == 'en':
        previous_week_section = previous_week_section.replace("LISTA TRACCE SETTIMANA PRECEDENTE", "PREVIOUS WEEK TRACKS")
        previous_week_section = previous_week_section.replace("ISTRUZIONE SPECIALE", "SPECIAL INSTRUCTION")

    # Aggiungi enfasi sul numero di tracce al prompt finale
    track_emphasis = f"\n🎵 CRITICAL: Generate {track_count_instruction} - this is mandatory!"
    
    # Riordina prompt per dare priorità alla richiesta utente
    if custom_prompt:
        prompt = f"""{base_prompt}

CUSTOM USER REQUEST (THIS IS THE PRIMARY GOAL):
{core_prompt}{track_emphasis}

{favorites_header}
---
{tracks_str}
---
{previous_week_section}
{charts_section}

{balance_note}

FINAL REMINDER: Respect the exact number of tracks requested and prioritize discovery over repetition of favorites!
"""
    else:
        prompt = f"""{base_prompt}

{core_prompt}{track_emphasis}

{favorites_header}
---
{tracks_str}
---
{charts_section}
{previous_week_section}

{balance_note}

FINAL REMINDER: Generate the exact number of tracks specified and focus on musical discovery!
"""
    return prompt.strip()

def get_gemini_playlist_data(model: genai.GenerativeModel, prompt: str, model_name: str = "gemini-1.5-flash") -> Optional[Dict]:
    """Invia il prompt a Gemini e restituisce il JSON parsificato."""
    logger.info("Invio richiesta a Gemini per la creazione della playlist...")
    try:
        response = model.generate_content(prompt)
        if not response or not response.text:
            logger.error("Gemini ha restituito una risposta vuota")
            return None
            
        cleaned_text = response.text.strip()
        logger.info(f"Risposta Gemini (primi 500 char): {cleaned_text[:500]}...")
        
        start_index = cleaned_text.find('{')
        end_index = cleaned_text.rfind('}') + 1
        if start_index == -1 or end_index == 0:
            logger.error("Nessun oggetto JSON trovato nella risposta")
            logger.error(f"Risposta completa:\n{cleaned_text}")
            return None
        
        json_str = cleaned_text[start_index:end_index]
        logger.info(f"JSON estratto: {json_str[:200]}...")
        
        playlist_data = json.loads(json_str)
        
        # Verifica campi essenziali
        title = playlist_data.get('title') or playlist_data.get('playlist_name') or playlist_data.get('name')
        tracks = playlist_data.get('tracks', [])
        
        if not title:
            logger.error(f"Playlist senza titolo. Chiavi disponibili: {list(playlist_data.keys())}")
            return None
            
        if not tracks:
            logger.error(f"Playlist senza tracce. Dati: {playlist_data}")
            return None
        
        # Normalizza i nomi dei campi
        normalized_data = {
            'playlist_name': title,
            'tracks': tracks,
            'description': playlist_data.get('description', ''),
            'title': title
        }
        
        logger.info(f"Playlist generata da {model_name}: '{title}' con {len(tracks)} tracce")
        model_type = "primary" if model_name == "gemini-2.5-flash" else "secondary"
        gemini_state.record_success(model_type)  # Record successful request
        return normalized_data
        
    except json.JSONDecodeError as e:
        logger.error(f"Errore parsing JSON da {model_name}: {e}")
        logger.error(f"JSON che ha causato l'errore:\n{json_str if 'json_str' in locals() else 'Non disponibile'}")
        model_type = "primary" if model_name == "gemini-2.5-flash" else "secondary"
        gemini_state.record_failure(f"JSON parsing error: {e}", model_type=model_type)
        return None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Errore nel parsing della risposta di {model_name}: {error_msg}")
        logger.error(f"Risposta ricevuta:\n{response.text if 'response' in locals() else 'Nessuna risposta ricevuta'}")
        
        # Check if it's a rate limit error
        is_rate_limit = "429" in error_msg or "quota" in error_msg.lower() or "rate limit" in error_msg.lower()
        model_type = "primary" if model_name == "gemini-2.5-flash" else "secondary"
        gemini_state.record_failure(error_msg, is_rate_limit, model_type)
        return None

def list_ai_playlists(plex: PlexServer) -> List[Dict]:
    """Recupera la lista delle playlist AI gestite dal database locale."""
    logger.info("Recupero delle playlist AI gestite dal database...")
    # La logica ora è centralizzata nel DB per coerenza
    return get_managed_ai_playlists_for_user(plex.myPlexAccount().username)


def generate_on_demand_playlist(
    plex: PlexServer,
    user_inputs: UserInputs,
    favorites_playlist_id: str,
    custom_prompt: Optional[str],
    selected_user_key: str,
    include_charts_data: bool = True,
    requested_track_count: Optional[int] = None
):
    """Genera una playlist on-demand, la crea su Plex e la salva nel DB locale."""
    logger.info(f"Generazione playlist on-demand avviata per utente {selected_user_key}…")

    # Implementa cascading fallback: Gemini 1.5-flash → Gemini 1.5-pro → Ollama
    playlist_data = None
    
    # Ottieni i preferiti una volta sola
    favorite_tracks = get_plex_favorites_by_id(plex, favorites_playlist_id)
    if not favorite_tracks: 
        logger.error("❌ Impossibile recuperare i preferiti dall'utente")
        return False

    # Per richieste specifiche, riduci l'influenza dei chart data
    if custom_prompt and any(keyword in custom_prompt.lower() for keyword in ['anime', 'giapponesi', 'japanese', 'k-pop', 'metal', 'classical', 'jazz', 'blues']):
        logger.info("Detected specific genre request - minimizing chart data influence")
        include_charts_data = False

    prompt = generate_playlist_prompt(favorite_tracks, custom_prompt, language='en', include_charts_data=include_charts_data, requested_track_count=requested_track_count)
    
    # Step 1: Prova con Gemini 2.5 Flash (primary model - 250 RPD, 10 RPM)
    if gemini_state.is_available("primary"):
        logger.info("🔄 Tentativo con Gemini 2.5 Flash...")
        try:
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel("gemini-2.5-flash")
            playlist_data = get_gemini_playlist_data(model, prompt, "gemini-2.5-flash")
            
            if playlist_data and playlist_data.get("tracks") and playlist_data.get("playlist_name"):
                logger.info("✅ Playlist generata con successo usando Gemini 2.5 Flash")
            else:
                raise Exception("Dati playlist Gemini 2.5 Flash mancanti o non validi")
        except Exception as e:
            logger.warning(f"⚠️ Gemini 2.5 Flash fallito: {e}")
            playlist_data = None
    
    # Step 2: Fallback su Gemini 2.0 Flash (secondary model - 200 RPD, 15 RPM)
    if not playlist_data and gemini_state.is_available("secondary"):
        logger.info("🔄 Tentativo con Gemini 2.0 Flash...")
        try:
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel("gemini-2.0-flash")
            playlist_data = get_gemini_playlist_data(model, prompt, "gemini-2.0-flash")
            
            if playlist_data and playlist_data.get("tracks") and playlist_data.get("playlist_name"):
                logger.info("✅ Playlist generata con successo usando Gemini 2.0 Flash")
            else:
                raise Exception("Dati playlist Gemini 2.0 Flash mancanti o non validi")
        except Exception as e:
            logger.warning(f"⚠️ Gemini 2.0 Flash fallito: {e}")
            playlist_data = None
    
    # Step 3: Fallback finale su Ollama
    if not playlist_data:
        if configure_ollama():
            logger.info("🤖 Usando Ollama come fallback finale per la generazione playlist...")
            
            # Semplifica il prompt per Ollama
            simple_prompt = custom_prompt if custom_prompt else "Genera una playlist di musica popolare varia e bilanciata"
            track_count = requested_track_count if requested_track_count else 25
            
            playlist_data = generate_playlist_with_ollama(simple_prompt, track_count)
            
            if playlist_data and playlist_data.get("tracks") and playlist_data.get("playlist_name"):
                logger.info("✅ Playlist generata con successo usando Ollama")
            else:
                logger.error("❌ Anche Ollama ha fallito nella generazione della playlist")
                return False
        else:
            logger.error("❌ Tutti i servizi AI sono non disponibili (Gemini 2.5 Flash, Gemini 2.0 Flash, Ollama)")
            return False

    # Genera copertina se abilitata
    cover_path = None
    if is_cover_generation_enabled():
        try:
            logger.info("🎨 Generando copertina per playlist AI...")
            genres = extract_genres_from_playlist_data(playlist_data)
            cover_path = generate_playlist_cover_ai(
                playlist_name=playlist_data["playlist_name"],
                description=playlist_data.get("description", ""),
                genres=genres
            )
            if cover_path:
                logger.info(f"✅ Copertina generata: {cover_path}")
            else:
                logger.warning("⚠️ Generazione copertina fallita")
        except Exception as e:
            logger.warning(f"⚠️ Errore generazione copertina: {e}")
    
    new_playlist_obj = PlexPlaylist(
        id=None,
        name=playlist_data["playlist_name"],
        description=playlist_data.get("description", ""),
        poster=cover_path,  # Passa il path della copertina
    )
    new_tracks = [PlexTrack(title=t.get("title", ""), artist=t.get("artist", ""), album="", url="") for t in playlist_data["tracks"]]
    
    created_plex_playlist = update_or_create_plex_playlist(plex, new_playlist_obj, new_tracks, user_inputs)

    if created_plex_playlist:
        db_playlist_info = {
            'plex_rating_key': created_plex_playlist.ratingKey,
            'title': created_plex_playlist.title,
            'description': created_plex_playlist.summary,
            'user': selected_user_key,
            'tracklist': playlist_data.get("tracks", [])
        }
        add_managed_ai_playlist(db_playlist_info)
        return True
    else:
        logger.error("La creazione della playlist su Plex è fallita, non la aggiungo al DB.")
        return False

def get_music_charts_preview() -> Dict:
    """Restituisce un'anteprima dei dati delle classifiche musicali disponibili."""
    logger.info("Generando anteprima dati classifiche musicali...")
    try:
        data = music_charts_searcher.get_comprehensive_music_data("preview")
        
        preview = {
            "timestamp": data.get("timestamp"),
            "charts_available": [],
            "trends_available": [],
            "news_count": len(data.get("news", []))
        }
        
        # Riassunto classifiche
        charts = data.get("charts", {})
        for chart_name, chart_data in charts.items():
            if chart_data:
                preview["charts_available"].append({
                    "name": chart_name,
                    "count": len(chart_data),
                    "top_3": chart_data[:3] if len(chart_data) >= 3 else chart_data
                })
        
        # Riassunto tendenze
        trends = data.get("trends", {})
        for trend_name, trend_data in trends.items():
            if trend_data:
                if trend_name == "seasonal":
                    preview["trends_available"].append({
                        "name": f"seasonal_{trend_data.get('season', 'unknown')}",
                        "count": len(trend_data.get("trends", [])),
                        "season": trend_data.get("season")
                    })
                else:
                    preview["trends_available"].append({
                        "name": trend_name,
                        "count": len(trend_data),
                        "sample": trend_data[:2] if len(trend_data) >= 2 else trend_data
                    })
        
        logger.info(f"Anteprima generata: {len(preview['charts_available'])} classifiche, {len(preview['trends_available'])} tendenze")
        return preview
        
    except Exception as e:
        logger.error(f"Errore nella generazione anteprima classifiche: {e}")
        return {"error": str(e)}

def test_music_charts_integration() -> bool:
    """Testa l'integrazione delle classifiche musicali."""
    logger.info("Test integrazione classifiche musicali...")
    try:
        # Test ricerca classifiche
        billboard = music_charts_searcher.get_billboard_hot_100()
        spotify = music_charts_searcher.get_spotify_global_top_50()
        italian = music_charts_searcher.get_italian_charts()
        seasonal = music_charts_searcher.get_seasonal_trends()
        
        # Test ricerca per genere
        rock_trends = music_charts_searcher.get_genre_trending("rock")
        pop_trends = music_charts_searcher.get_genre_trending("pop")
        
        # Test ricerca notizie
        rock_news = music_charts_searcher.search_music_news("rock")
        
        # Test dati completi
        full_data = music_charts_searcher.get_comprehensive_music_data("test")
        
        results = {
            "billboard_count": len(billboard) if billboard else 0,
            "spotify_count": len(spotify) if spotify else 0,
            "italian_count": len(italian) if italian else 0,
            "seasonal_available": seasonal is not None,
            "rock_trends_count": len(rock_trends) if rock_trends else 0,
            "pop_trends_count": len(pop_trends) if pop_trends else 0,
            "rock_news_count": len(rock_news) if rock_news else 0,
            "full_data_sections": len(full_data.keys()) if full_data else 0
        }
        
        success = all([
            results["billboard_count"] > 0,
            results["spotify_count"] > 0,
            results["italian_count"] > 0,
            results["seasonal_available"],
            results["full_data_sections"] > 0
        ])
        
        logger.info(f"Test completato. Successo: {success}, Risultati: {results}")
        return success
        
    except Exception as e:
        logger.error(f"Errore nel test integrazione classifiche: {e}")
        return False

def generate_playlist_description(playlist_name: str, genres: List[str], track_count: int, 
                                 style: str = "casual", language: str = "en", 
                                 original_description: str = None) -> Optional[str]:
    """
    Genera una descrizione AI per una playlist esistente.
    
    Args:
        playlist_name: Nome della playlist
        genres: Lista dei generi musicali presenti
        track_count: Numero di tracce nella playlist
        style: Stile della descrizione ('casual', 'formal', 'poetic', 'energetic')
        language: Lingua della descrizione ('en', 'it')
        original_description: Descrizione originale della playlist (opzionale)
    
    Returns:
        Descrizione generata o None se fallisce
    """
    try:
        # Configura Gemini
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY non configurata")
            return None
        
        genai.configure(api_key=api_key)
        
        # Controllo stato globale
        global_state = GeminiStateManager()
        if global_state.is_blocked:
            logger.warning("Gemini attualmente bloccato")
            return None
        
        # Mappa degli stili
        style_descriptions = {
            "casual": {
                "en": "casual and friendly",
                "it": "casual e amichevole"
            },
            "formal": {
                "en": "formal and professional",
                "it": "formale e professionale"
            },
            "poetic": {
                "en": "poetic and artistic",
                "it": "poetico e artistico"
            },
            "energetic": {
                "en": "energetic and exciting",
                "it": "energico ed entusiasmante"
            }
        }
        
        style_desc = style_descriptions.get(style, style_descriptions["casual"])[language]
        
        # Aggiungi variabilità per evitare descrizioni ripetitive
        import random
        import time
        variability_seed = int(time.time()) % 1000
        
        variation_prompts = {
            "en": [
                "Create a compelling description",
                "Write an engaging summary", 
                "Generate an attractive description",
                "Craft an appealing overview",
                "Compose an enticing description"
            ],
            "it": [
                "Crea una descrizione accattivante",
                "Scrivi un riassunto coinvolgente",
                "Genera una descrizione attraente", 
                "Componi una panoramica allettante",
                "Crea una descrizione invitante"
            ]
        }
        
        chosen_starter = random.choice(variation_prompts[language])
        
        # Costruisci il prompt con descrizione originale se disponibile
        original_context = ""
        if original_description and original_description.strip():
            original_context = f"\n- Descrizione originale: \"{original_description.strip()}\""
        
        if language == "it":
            prompt = f"""
{chosen_starter} per una playlist musicale chiamata "{playlist_name}".

Seed variabilità: {variability_seed}

Dettagli della playlist:
- Nome: {playlist_name}
- Generi musicali: {', '.join(genres) if genres else 'Vari'}
- Numero di tracce: {track_count}
- Stile descrizione: {style_desc}{original_context}

Istruzioni speciali:
{'- Se presente una descrizione originale, usala come ispirazione mantenendone il tema e lo spirito' if original_description else ''}
{'- Migliora e arricchisci il contenuto originale senza stravolgerne il significato' if original_description else ''}
{'- Il titolo della playlist contiene indizi importanti sul tema e mood desiderato' if playlist_name else ''}

Requisiti:
1. Lunghezza: 2-3 frasi (massimo 150 caratteri)
2. Stile: {style_desc}
3. Linguaggio: italiano naturale
4. Evita elenchi o punti
5. Concentrati sull'atmosfera e sul mood della playlist
6. Non ripetere esattamente il nome della playlist
7. Usa termini musicali appropriati per i generi
8. {'Mantieni coerenza con la descrizione originale se presente' if original_description else 'Ispirati al titolo per comprendere il tema'}

Esempio di output desiderato:
"Una selezione di brani che cattura l'essenza del rock moderno con sfumature alternative. Perfetta per accompagnare momenti di concentrazione o per energizzare le tue giornate."

Genera solo la descrizione migliorata, senza prefissi o spiegazioni aggiuntive.
"""
        else:
            prompt = f"""
{chosen_starter} for a music playlist called "{playlist_name}".

Variability seed: {variability_seed}

Playlist details:
- Name: {playlist_name}
- Music genres: {', '.join(genres) if genres else 'Various'}
- Track count: {track_count}
- Description style: {style_desc}{original_context}

Special instructions:
{'- If an original description exists, use it as inspiration while maintaining its theme and spirit' if original_description else ''}
{'- Enhance and enrich the original content without completely changing its meaning' if original_description else ''}
{'- The playlist title contains important clues about the desired theme and mood' if playlist_name else ''}

Requirements:
1. Length: 2-3 sentences (max 150 characters)
2. Style: {style_desc}
3. Language: natural English
4. Avoid lists or bullet points
5. Focus on atmosphere and mood of the playlist
6. Don't repeat the playlist name exactly
7. Use appropriate musical terminology for the genres
8. {'Maintain consistency with the original description if present' if original_description else 'Draw inspiration from the title to understand the theme'}

Example desired output:
"A selection of tracks that captures the essence of modern rock with alternative nuances. Perfect for focusing moments or energizing your days."

Generate only the enhanced description, without prefixes or additional explanations.
"""
        
        # Inizializza il modello
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        # Genera la descrizione
        response = model.generate_content(prompt)
        
        if response and response.text:
            description = response.text.strip()
            
            # Pulisci la descrizione
            description = description.replace('"', '').replace("'", "")
            
            # Tronca se troppo lunga
            if len(description) > 180:
                description = description[:177] + "..."
            
            context_info = " (considerando descrizione originale)" if original_description else " (basata su titolo e generi)"
            logger.info(f"Descrizione generata per playlist '{playlist_name}'{context_info}: {description}")
            return description
        else:
            logger.error("Risposta vuota da Gemini")
            return None
            
    except Exception as e:
        logger.error(f"Errore generazione descrizione playlist: {e}")
        return None

def analyze_playlist_genres(plex_server, playlist_id: str) -> List[str]:
    """
    Analizza i generi di una playlist Plex.
    
    Args:
        plex_server: Istanza del server Plex
        playlist_id: ID della playlist
    
    Returns:
        Lista dei generi più frequenti
    """
    try:
        playlist = plex_server.playlist(playlist_id)
        
        genre_count = {}
        
        # Analizza i generi delle tracce
        for track in playlist.items():
            if hasattr(track, 'genres'):
                for genre in track.genres:
                    genre_name = genre.tag.lower()
                    genre_count[genre_name] = genre_count.get(genre_name, 0) + 1
        
        # Ordina per frequenza e prendi i primi 5
        sorted_genres = sorted(genre_count.items(), key=lambda x: x[1], reverse=True)
        top_genres = [genre[0] for genre in sorted_genres[:5]]
        
        logger.info(f"Generi analizzati per playlist {playlist_id}: {top_genres}")
        return top_genres
        
    except Exception as e:
        logger.error(f"Errore analisi generi playlist: {e}")
        return []

def generate_creative_cover_prompt(playlist_name: str, description: str, genres: List[str], language: str = 'en') -> str:
    """
    Genera un prompt creativo per la cover usando Gemini AI
    
    Args:
        playlist_name: Nome della playlist
        description: Descrizione della playlist
        genres: Lista dei generi musicali
        language: Lingua per il prompt
    
    Returns:
        Prompt creativo per la generazione della cover
    """
    try:
        # Configura l'API Gemini
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY non configurata - usando prompt base")
            return f'album cover artwork with text: "{playlist_name}", {", ".join(genres)} style, professional design'
        
        genai.configure(api_key=api_key)
        
        # Importa la funzione per lo stile del testo
        from .playlist_cover_generator import get_text_prompt_style
        
        # Ottieni lo stile del testo basato sui generi
        text_style = get_text_prompt_style(genres)
        title_text = text_style.format(playlist_name=playlist_name)
        
        # Costruisci il prompt per Gemini
        system_prompt = f"""
        You are a creative AI expert specialized in generating prompts for album cover artwork.
        
        Your task is to create a detailed, artistic prompt for generating a music playlist cover.
        
        Guidelines:
        1. Create a visual concept that captures the essence of the music
        2. Include artistic style, colors, mood, and composition details
        3. ALWAYS start the prompt with the specific text format: "{title_text}"
        4. Make it professional and eye-catching
        5. Consider the music genres and playlist theme
        6. Be creative and descriptive but concise
        7. Focus on visual elements that work well for album covers
        8. Use modern, professional design language
        
        Playlist Information:
        - Name: "{playlist_name}"
        - Description: "{description}"
        - Genres: {", ".join(genres) if genres else "mixed"}
        
        Create a detailed prompt for generating this playlist cover artwork. 
        The prompt MUST start with: "{title_text}"
        The prompt should be in English and ready to use with AI image generation tools.
        """
        
        # Usa il modello Gemini 1.5 Flash per velocità
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        logger.info(f"🎨 Generando prompt creativo per playlist: {playlist_name}")
        
        response = model.generate_content(system_prompt)
        
        if response and response.text:
            creative_prompt = response.text.strip()
            logger.info(f"✅ Prompt creativo generato: {creative_prompt[:100]}...")
            
            # Assicurati che il prompt includa sempre il nome della playlist
            if playlist_name not in creative_prompt:
                creative_prompt = f'album cover artwork with text: "{playlist_name}", {creative_prompt}'
            
            return creative_prompt
        else:
            logger.warning("Gemini non ha restituito un prompt valido")
            return f'album cover artwork with text: "{playlist_name}", {", ".join(genres)} style, professional design'
            
    except Exception as e:
        logger.error(f"Errore generazione prompt creativo: {e}")
        # Fallback al prompt base
        return f'album cover artwork with text: "{playlist_name}", {", ".join(genres)} style, professional design'