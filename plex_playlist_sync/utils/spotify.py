import logging
from typing import List
import os

import spotipy
from plexapi.server import PlexServer

from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist


def _get_sp_user_playlists(
    sp: spotipy.Spotify, user_id: str, userInputs: UserInputs, suffix: str = " - Spotify"
) -> List[Playlist]:
    """Get metadata for playlists in the given user_id."""
    playlists = []
    # Legge il limite di playlist dal file .env
    limit = int(os.getenv("TEST_MODE_PLAYLIST_LIMIT", 0))

    try:
        sp_playlists = sp.user_playlists(user_id)
        # Aggiunto 'enumerate' per poter contare le playlist
        for i, playlist in enumerate(sp_playlists["items"]):
            # Se il limite è impostato e lo abbiamo raggiunto, esce dal ciclo
            if limit > 0 and i >= limit:
                logging.warning(f"MODALITÀ TEST: Limite di {limit} playlist raggiunto per Spotify. Interrompo.")
                break

            playlists.append(
                Playlist(
                    id=playlist["id"],
                    name=playlist["name"] + suffix,
                    description=playlist.get("description", ""),
                    poster=playlist.get("images", [{}])[0].get("url", ""),
                )
            )
    except Exception as e:
        logging.error(f"Spotify User ID Error: {e}")
    return playlists


def _get_sp_playlists_by_ids(
    sp: spotipy.Spotify, ids: List[str], suffix: str = " - Spotify"
) -> List[Playlist]:
    """Fetch specific playlists by ID."""
    playlists = []
    for pid in ids:
        pid = pid.strip()
        if not pid:
            continue
        try:
            pl = sp.playlist(pid)
            playlists.append(
                Playlist(
                    id=pl["id"],
                    name=pl["name"] + suffix,
                    description=pl.get("description", ""),
                    poster=pl.get("images", [{}])[0].get("url", ""),
                )
            )
        except Exception as e:
            logging.error(f"Error fetching playlist {pid}: {e}")
    return playlists


def _get_sp_tracks_from_playlist(
    sp: spotipy.Spotify, playlist: Playlist
) -> List[Track]:
    """Return list of tracks with metadata."""

    def extract_sp_track_metadata(track) -> Track:
        title = track["track"]["name"]
        artist = track["track"]["artists"][0]["name"]
        album = track["track"]["album"]["name"]
        url = track["track"]["external_urls"].get("spotify", "")
        return Track(title, artist, album, url)

    sp_playlist_tracks = sp.playlist_items(playlist.id)

    tracks = list(
        map(
            extract_sp_track_metadata,
            [i for i in sp_playlist_tracks["items"] if i.get("track")],
        )
    )

    while sp_playlist_tracks["next"]:
        sp_playlist_tracks = sp.next(sp_playlist_tracks)
        tracks.extend(
            list(
                map(
                    extract_sp_track_metadata,
                    [i for i in sp_playlist_tracks["items"] if i.get("track")],
                )
            )
        )
    return tracks


def spotify_playlist_sync_with_discovery(sp: spotipy.Spotify, plex: PlexServer, userInputs: UserInputs) -> None:
    """
    Discovers and syncs all user playlists from Spotify (auto-discovery mode).
    This function fetches all user playlists instead of using pre-configured IDs.
    """
    if not userInputs.spotify_user_id:
        logging.error("SPOTIFY_USER_ID not configured; cannot discover playlists")
        return

    logging.info(f"🔍 Auto-discovering Spotify playlists for user: {userInputs.spotify_user_id}")
    
    try:
        # Use existing function to get all user playlists
        discovered_playlists = _get_sp_user_playlists(sp, userInputs.spotify_user_id, userInputs)
        
        if not discovered_playlists:
            logging.warning("No Spotify playlists found for auto-discovery")
            return
        
        logging.info(f"📋 Discovered {len(discovered_playlists)} Spotify playlists:")
        for playlist in discovered_playlists:
            logging.info(f"   - {playlist.name} (ID: {playlist.id})")
        
        # Process each discovered playlist
        for playlist in discovered_playlists:
            try:
                logging.info(f"🎵 Processing discovered playlist: {playlist.name}")
                
                # Get tracks for this playlist  
                tracks = _get_sp_tracks_from_playlist(sp, playlist)
                if tracks:
                    update_or_create_plex_playlist(plex, playlist, tracks, userInputs)
                    logging.info(f"✅ Synced playlist '{playlist.name}' with {len(tracks)} tracks")
                else:
                    logging.warning(f"⚠️ No tracks found in playlist '{playlist.name}'")
                    
            except Exception as playlist_error:
                logging.error(f"❌ Error processing playlist '{playlist.name}': {playlist_error}")
                continue
        
        logging.info(f"🎉 Auto-discovery completed: processed {len(discovered_playlists)} Spotify playlists")
        
    except Exception as e:
        logging.error(f"❌ Error during Spotify auto-discovery: {e}")


def spotify_playlist_sync(
    sp: spotipy.Spotify, plex: PlexServer, userInputs: UserInputs
) -> None:
    """Create/Update plex playlists with playlists from spotify."""
    if not userInputs.spotify_user_id:
        logging.error("SPOTIFY_USER_ID not configured; skipping Spotify sync")
        return

    suffix = " - Spotify" if userInputs.append_service_suffix else ""
    if userInputs.spotify_playlist_ids:
        ids = userInputs.spotify_playlist_ids.split(",")
        playlists = _get_sp_playlists_by_ids(sp, ids, suffix)
    else:
        playlists = _get_sp_user_playlists(sp, userInputs.spotify_user_id, userInputs, suffix)
    if playlists:
        for playlist in playlists:
            # Check if stop was requested
            from ..sync_logic import check_stop_flag_direct
            if check_stop_flag_direct():
                logging.info("🛑 Stop requested during Spotify playlist sync")
                return
            
            tracks = _get_sp_tracks_from_playlist(sp, playlist)
            update_or_create_plex_playlist(plex, playlist, tracks, userInputs)
    else:
        logging.error("No spotify playlists found for given user")
