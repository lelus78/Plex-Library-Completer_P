import os
import logging
import json
from typing import List, Dict, Optional

import google.generativeai as genai
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

from .helperClasses import Playlist as PlexPlaylist, Track as PlexTrack, UserInputs
from .plex import update_or_create_plex_playlist
from .database import add_managed_ai_playlist, get_managed_ai_playlists_for_user
from .music_charts import music_charts_searcher
from .i18n import i18n, translate_genre

# Otteniamo il logger che è già stato configurato in app.py
logger = logging.getLogger(__name__)

def configure_gemini() -> Optional[genai.GenerativeModel]:
    """Configura e restituisce il modello Gemini se la chiave API è presente."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY non trovata nel file .env. La creazione della playlist AI verrà saltata.")
        return None
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        return model
    except Exception as e:
        logger.error(f"Errore nella configurazione di Gemini: {e}")
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

def get_gemini_playlist_data(model: genai.GenerativeModel, prompt: str) -> Optional[Dict]:
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
        
        logger.info(f"Playlist generata da Gemini: '{title}' con {len(tracks)} tracce")
        return normalized_data
        
    except json.JSONDecodeError as e:
        logger.error(f"Errore parsing JSON: {e}")
        logger.error(f"JSON che ha causato l'errore:\n{json_str if 'json_str' in locals() else 'Non disponibile'}")
        return None
    except Exception as e:
        logger.error(f"Errore nel parsing della risposta di Gemini: {e}")
        logger.error(f"Risposta ricevuta:\n{response.text if 'response' in locals() else 'Nessuna risposta ricevuta'}")
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

    model = configure_gemini()
    if not model: return False

    favorite_tracks = get_plex_favorites_by_id(plex, favorites_playlist_id)
    if not favorite_tracks: return False

    # Per richieste specifiche, riduci l'influenza dei chart data
    if custom_prompt and any(keyword in custom_prompt.lower() for keyword in ['anime', 'giapponesi', 'japanese', 'k-pop', 'metal', 'classical', 'jazz', 'blues']):
        logger.info("Detected specific genre request - minimizing chart data influence")
        include_charts_data = False

    prompt = generate_playlist_prompt(favorite_tracks, custom_prompt, language='en', include_charts_data=include_charts_data, requested_track_count=requested_track_count)
    playlist_data = get_gemini_playlist_data(model, prompt)

    if not (playlist_data and playlist_data.get("tracks") and playlist_data.get("playlist_name")):
        logger.error("Dati playlist Gemini mancanti o non validi.")
        return False

    new_playlist_obj = PlexPlaylist(
        id=None,
        name=playlist_data["playlist_name"],
        description=playlist_data.get("description", ""),
        poster=None,
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