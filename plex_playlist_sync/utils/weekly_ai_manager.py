"""
Weekly AI playlist management with JSON persistence.
Reads NO_DELETE playlists to analyze user taste and generates weekly playlists.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

from .gemini_ai import configure_gemini, generate_playlist_prompt, get_gemini_playlist_data
from .helperClasses import Playlist as PlexPlaylist, Track as PlexTrack, UserInputs
from .plex import update_or_create_plex_playlist
from .database import get_library_index_stats
from .i18n import i18n, translate_log_message

logger = logging.getLogger(__name__)

def log_translated(level, message_key, *args, **kwargs):
    """Helper for translated logs"""
    current_lang = i18n.get_language()
    translated_msg = i18n.get_translation(message_key, current_lang, **kwargs)
    getattr(logger, level)(translated_msg)

# Path per la persistenza JSON
WEEKLY_AI_STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "state_data")
WEEKLY_AI_STATE_FILE = os.path.join(WEEKLY_AI_STATE_DIR, "weekly_ai_playlists.json")

def ensure_state_directory():
    """Ensures that the state_data directory exists."""
    os.makedirs(WEEKLY_AI_STATE_DIR, exist_ok=True)

def load_weekly_ai_state() -> Dict:
    """Loads weekly AI playlists state from JSON."""
    ensure_state_directory()
    try:
        if os.path.exists(WEEKLY_AI_STATE_FILE):
            with open(WEEKLY_AI_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading weekly AI state: {e}")
    
    return {"playlists": {}, "last_update": None}

def save_weekly_ai_state(state: Dict):
    """Saves weekly AI playlists state to JSON."""
    ensure_state_directory()
    try:
        with open(WEEKLY_AI_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        logger.info("Weekly AI state saved successfully")
    except Exception as e:
        logger.error(f"Error saving weekly AI state: {e}")

def get_current_week_info() -> Dict[str, int]:
    """Returns information about the current week."""
    now = datetime.now()
    year, week_num, _ = now.isocalendar()
    return {"year": year, "week": week_num}

def read_no_delete_playlist_for_taste_analysis(plex: PlexServer, favorites_playlist_id: str) -> Optional[List[str]]:
    """
    Legge una playlist NO_DELETE per analizzare i gusti dell'utente.
    IMPORTANTE: Questa funzione è SOLO LETTURA, non modifica la playlist.
    """
    if not favorites_playlist_id:
        logger.warning("No favorites playlist ID provided for taste analysis.")
        return None
    
    try:
        playlist = plex.fetchItem(int(favorites_playlist_id))
        
        # Verifica che sia una playlist protetta
        preserve_tag = os.getenv("PRESERVE_TAG", "NO_DELETE")
        if preserve_tag.lower() not in playlist.title.lower():
            logger.warning(f"⚠️ Playlist '{playlist.title}' does not contain tag '{preserve_tag}' - might not be protected")
        
        logger.info(f"📖 Reading protected playlist '{playlist.title}' for taste analysis (READ ONLY)")
        
        # Estrae le tracce per l'analisi dei gusti
        tracks = []
        for track in playlist.items():
            try:
                artist = track.grandparentTitle if hasattr(track, 'grandparentTitle') else track.artist().title
                title = track.title
                tracks.append(f"{artist} - {title}")
            except Exception as track_error:
                logger.debug(f"Error reading track: {track_error}")
                continue
        
        logger.info(f"✅ Analyzed {len(tracks)} tracks from protected playlist '{playlist.title}'")
        return tracks
        
    except NotFound:
        logger.error(f"❌ Playlist with ID '{favorites_playlist_id}' not found on Plex server")
        return None
    except Exception as e:
        logger.error(f"❌ Error reading protected playlist with ID '{favorites_playlist_id}': {e}")
        return None

def should_update_weekly_playlist(current_week_info: Dict, state: Dict, user_key: str) -> bool:
    """Determines if the weekly playlist should be updated."""
    user_playlist_key = f"{user_key}_weekly"
    
    # If no playlist exists for this user yet
    if user_playlist_key not in state["playlists"]:
        logger.info(f"🆕 First weekly playlist creation for user '{user_key}'")
        return True
    
    # Check if we're in a new week
    last_playlist = state["playlists"][user_playlist_key]
    last_week = last_playlist.get("week_info", {})
    
    if (current_week_info["year"] != last_week.get("year") or 
        current_week_info["week"] != last_week.get("week")):
        logger.info(f"🗓️ New week detected for '{user_key}': Week {current_week_info['week']}, Year {current_week_info['year']}")
        return True
    
    logger.info(f"⏭️ Same week for '{user_key}': Week {current_week_info['week']}, Year {current_week_info['year']}")
    return False

def recreate_playlist_from_state(
    plex: PlexServer,
    user_inputs: UserInputs, 
    playlist_data: Dict,
    user_key: str
) -> Optional[object]:
    """Recreates an identical playlist using data saved in JSON state."""
    logger.info(f"🔄 Recreating identical playlist from JSON state for '{user_key}'")
    
    try:
        # Crea oggetto playlist
        playlist_obj = PlexPlaylist(
            id=None,
            name=playlist_data["name"],
            description=playlist_data.get("description", ""),
            poster=None,
        )
        
        # Converte le tracce salvate in oggetti Track
        tracks = []
        for track_data in playlist_data["tracks"]:
            track = PlexTrack(
                title=track_data.get("title", ""),
                artist=track_data.get("artist", ""),
                album=track_data.get("album", ""),
                url=""
            )
            tracks.append(track)
        
        logger.info(f"🎵 Recreating playlist '{playlist_data['name']}' with {len(tracks)} saved tracks")
        
        # Crea/aggiorna la playlist su Plex
        created_playlist = update_or_create_plex_playlist(plex, playlist_obj, tracks, user_inputs)
        
        if created_playlist:
            logger.info(f"✅ Playlist '{playlist_data['name']}' recreated successfully from JSON state")
            return created_playlist
        else:
            logger.error(f"❌ Failed to recreate playlist '{playlist_data['name']}' from JSON state")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error recreating playlist from JSON state: {e}")
        return None

def create_new_weekly_playlist(
    plex: PlexServer,
    user_inputs: UserInputs,
    favorites_playlist_id: str,
    user_key: str,
    current_week_info: Dict,
    previous_week_tracks: Optional[List[Dict]] = None
) -> Optional[Dict]:
    """Creates a new weekly playlist using Gemini AI."""
    logger.info(f"🎨 Creating new weekly AI playlist for '{user_key}' - Week {current_week_info['week']}")
    
    # Configura Gemini
    model = configure_gemini()
    if not model:
        logger.error("❌ Gemini not configured, cannot create AI playlist")
        return None
    
    # Read user taste from NO_DELETE playlist (READ ONLY)
    favorite_tracks = read_no_delete_playlist_for_taste_analysis(plex, favorites_playlist_id)
    if not favorite_tracks:
        logger.error("❌ Unable to read favorites playlist for taste analysis")
        return None
    
    # Get current language (default to 'en' for background tasks)
    try:
        current_language = i18n.get_language()
    except RuntimeError:
        # Working outside request context
        current_language = 'en'
    
    # Generate localized prompt for new weekly playlist
    if current_language == 'en':
        custom_prompt_text = f"Create a weekly playlist of 25 tracks for week {current_week_info['week']} of year {current_week_info['year']}. " \
                           f"Base it on user preferences but add variety and novelty to make this specific week interesting. " \
                           f"Include some tracks from current charts to keep the playlist up-to-date."
    else:
        custom_prompt_text = f"Crea una playlist settimanale di 25 brani per la settimana {current_week_info['week']} dell'anno {current_week_info['year']}. " \
                           f"Basati sui gusti dell'utente ma aggiungi varietà e novità per rendere interessante questa settimana specifica. " \
                           f"Includi alcuni brani dalle classifiche attuali per mantenere la playlist aggiornata."
    
    # Generate prompt for new weekly playlist with updated data
    prompt = generate_playlist_prompt(
        favorite_tracks,
        custom_prompt=custom_prompt_text,
        previous_week_tracks=previous_week_tracks,
        include_charts_data=True,
        language=current_language
    )
    
    # Request to Gemini
    playlist_data = get_gemini_playlist_data(model, prompt)
    if not playlist_data:
        logger.error("❌ Gemini did not return valid playlist data")
        return None
    
    # Add localized weekly suffix to name
    original_name = playlist_data["playlist_name"]
    if current_language == 'en':
        weekly_name = f"{original_name} - Week {current_week_info['week']}"
    else:
        weekly_name = f"{original_name} - Settimana {current_week_info['week']}"
    playlist_data["playlist_name"] = weekly_name
    
    # Create Plex objects
    playlist_obj = PlexPlaylist(
        id=None,
        name=weekly_name,
        description=playlist_data.get("description", ""),
        poster=None,
    )
    
    tracks = []
    for track_data in playlist_data["tracks"]:
        track = PlexTrack(
            title=track_data.get("title", ""),
            artist=track_data.get("artist", ""),
            album=track_data.get("album", ""),
            url=""
        )
        tracks.append(track)
    
    # Create playlist on Plex
    created_playlist = update_or_create_plex_playlist(plex, playlist_obj, tracks, user_inputs)
    
    if created_playlist:
        # Prepare data for state saving
        state_data = {
            "name": weekly_name,
            "description": playlist_data.get("description", ""),
            "tracks": playlist_data["tracks"],
            "week_info": current_week_info,
            "created_at": datetime.now().isoformat(),
            "plex_rating_key": created_playlist.ratingKey
        }
        
        logger.info(f"✅ Weekly playlist '{weekly_name}' created successfully")
        return state_data
    else:
        logger.error(f"❌ Failed to create weekly playlist '{weekly_name}'")
        return None

def manage_weekly_ai_playlist(
    plex: PlexServer,
    user_inputs: UserInputs,
    favorites_playlist_id: str,
    user_key: str
) -> bool:
    """
    Manages weekly AI playlist for a user:
    - Creates new playlist if it's a new week
    - Recreates existing playlist if we're in the same week
    """
    logger.info(f"🤖 Managing weekly AI playlist for user '{user_key}'")
    
    # Check library index status
    index_stats = get_library_index_stats()
    if index_stats['total_tracks_indexed'] == 0:
        logger.error(f"❌ Plex library index EMPTY! ({index_stats['total_tracks_indexed']} tracks indexed)")
        logger.error(f"⚠️ AI playlists will fail - run 'Index Library' from homepage first")
        return False
    else:
        logger.info(f"✅ Library index: {index_stats['total_tracks_indexed']} tracks indexed")
    
    current_week_info = get_current_week_info()
    state = load_weekly_ai_state()
    user_playlist_key = f"{user_key}_weekly"
    
    try:
        if should_update_weekly_playlist(current_week_info, state, user_key):
            # NEW WEEK: Create completely new playlist
            
            # Retrieve previous week tracks for continuity
            previous_week_tracks = None
            if user_playlist_key in state["playlists"]:
                previous_week_tracks = state["playlists"][user_playlist_key].get("tracks", [])
            
            # Create new playlist
            new_playlist_data = create_new_weekly_playlist(
                plex, user_inputs, favorites_playlist_id, user_key, 
                current_week_info, previous_week_tracks
            )
            
            if new_playlist_data:
                # Save to state
                state["playlists"][user_playlist_key] = new_playlist_data
                state["last_update"] = datetime.now().isoformat()
                save_weekly_ai_state(state)
                return True
            else:
                return False
                
        else:
            # SAME WEEK: Recreate identical playlist from JSON
            if user_playlist_key in state["playlists"]:
                playlist_data = state["playlists"][user_playlist_key]
                recreated_playlist = recreate_playlist_from_state(
                    plex, user_inputs, playlist_data, user_key
                )
                return recreated_playlist is not None
            else:
                logger.warning(f"⚠️ No saved data for weekly playlist of '{user_key}'")
                return False
                
    except Exception as e:
        logger.error(f"❌ Error managing weekly AI playlist for '{user_key}': {e}")
        return False