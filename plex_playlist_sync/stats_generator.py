import os
import logging
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from plexapi.server import PlexServer
import time
import random
from collections import Counter
import re
from datetime import datetime
from .utils.i18n import get_i18n

# Sample size to analyze for very large libraries
SAMPLE_SIZE = 5000
CACHE_DIR = "./state_data"
CACHE_DURATION = 86400

# Modern colors for charts (Spotify-inspired)
SPOTIFY_COLORS = [
    '#1DB954', '#1ed760', '#1fdf64', '#FF6B6B', '#4ECDC4', 
    '#FFE66D', '#A8E6CF', '#FF8E53', '#6C5CE7', '#FD79A8',
    '#00B894', '#FDCB6E', '#E17055', '#74B9FF', '#A29BFE'
]

# Genre mapping for normalization
GENRE_MAPPING = {
    # Rock and derivatives
    'rock': 'Rock',
    'classic rock': 'Rock Classico', 
    'alternative rock': 'Rock Alternativo',
    'hard rock': 'Hard Rock',
    'indie rock': 'Indie Rock',
    'punk rock': 'Punk Rock',
    'progressive rock': 'Progressive Rock',
    'folk rock': 'Folk Rock',
    'pop rock': 'Pop Rock',
    
    # Pop
    'pop': 'Pop',
    'synth-pop': 'Synth-Pop',
    'indie pop': 'Indie Pop',
    'dance-pop': 'Dance Pop',
    'electropop': 'Electro Pop',
    
    # Electronic/Dance
    'electronic': 'Electronic',
    'dance': 'Dance',
    'house': 'House',
    'techno': 'Techno',
    'ambient': 'Ambient',
    'edm': 'EDM',
    'trance': 'Trance',
    'dubstep': 'Dubstep',
    
    # Hip-Hop/Rap
    'hip hop': 'Hip-Hop',
    'hip-hop': 'Hip-Hop',
    'rap': 'Rap',
    'trap': 'Trap',
    
    # R&B/Soul
    'r&b': 'R&B',
    'rnb': 'R&B',
    'soul': 'Soul',
    'funk': 'Funk',
    'disco': 'Disco',
    
    # Folk/Country/Acoustic
    'folk': 'Folk',
    'country': 'Country',
    'acoustic': 'Acoustic',
    'singer-songwriter': 'Singer-Songwriter',
    
    # Jazz
    'jazz': 'Jazz',
    'blues': 'Blues',
    
    # Metal
    'metal': 'Metal',
    'heavy metal': 'Heavy Metal',
    'death metal': 'Death Metal',
    'black metal': 'Black Metal',
    
    # Classica
    'classical': 'Classica',
    'orchestral': 'Orchestrale',
    
    # Italiana
    'cantautori': 'Cantautori Italiani',
    'musica italiana': 'Musica Italiana',
    'pop italiano': 'Pop Italiano',
    
    # Altro
    'world': 'World Music',
    'latin': 'Latino',
    'reggae': 'Reggae',
    'ska': 'Ska'
}

def normalize_genre(genre_str, language='en'):
    """Normalizes genre names for better categorization with language support"""
    unknown_label = "Unknown" if language == 'en' else "Sconosciuto"
    
    if not genre_str or genre_str in ["Sconosciuto", "Unknown"]:
        return unknown_label
    
    genre_lower = genre_str.lower().strip()
    
    # Search for exact matches
    if genre_lower in GENRE_MAPPING:
        mapped_value = GENRE_MAPPING[genre_lower]
        return mapped_value.get(language, mapped_value['en']) if isinstance(mapped_value, dict) else mapped_value
    
    # Search for partial matches
    for key, value in GENRE_MAPPING.items():
        if key in genre_lower or genre_lower in key:
            return value.get(language, value['en']) if isinstance(value, dict) else value
    
    # If no matches found, capitalize original genre
    return genre_str.title()

def _extract_year(track) -> int | None:
    """Try to retrieve a reliable release year from a Plex track."""
    # Prova prima originallyAvailableAt (più accurato)
    if hasattr(track, 'originallyAvailableAt') and track.originallyAvailableAt:
        try:
            return track.originallyAvailableAt.year
        except Exception:
            pass
    
    # Poi prova year dell'album
    if hasattr(track, 'parentYear') and track.parentYear:
        return track.parentYear
    
    # Poi year della traccia
    if hasattr(track, 'year') and track.year:
        return track.year
    
    # Prova con l'album parent
    try:
        if hasattr(track, 'album'):
            album = track.album()
            if hasattr(album, 'year') and album.year:
                return album.year
            if hasattr(album, 'originallyAvailableAt') and album.originallyAvailableAt:
                return album.originallyAvailableAt.year
    except Exception:
        pass
    
    # Fallback su addedAt come ultima risorsa (anno in cui è stato aggiunto)
    if hasattr(track, 'addedAt') and track.addedAt:
        try:
            return track.addedAt.year
        except Exception:
            pass
    
    return None

def _extract_genre(track, language='en') -> str:
    """Return the first genre tag for a track or 'Unknown'/'Sconosciuto'."""
    unknown_label = "Unknown" if language == 'en' else "Sconosciuto"
    
    # Try first with album genres (often more accurate)
    if hasattr(track, 'parentTitle'):
        try:
            album = track.album()
            if hasattr(album, 'genres') and album.genres:
                album_genres = [g.tag for g in album.genres if hasattr(g, 'tag') and g.tag]
                if album_genres:
                    return normalize_genre(album_genres[0], language)
        except:
            pass
    
    # Then track genres
    if hasattr(track, 'genres') and track.genres:
        track_genres = [g.tag for g in track.genres if hasattr(g, 'tag') and g.tag]
        if track_genres:
            return normalize_genre(track_genres[0], language)
    
    # Try with moods if no genres
    if hasattr(track, 'moods') and track.moods:
        mood_tags = [m.tag for m in track.moods if hasattr(m, 'tag') and m.tag]
        if mood_tags:
            return normalize_genre(mood_tags[0], language)

    return unknown_label

def _extract_additional_metadata(track):
    """Estrae metadati aggiuntivi per statistiche avanzate"""
    data = {}
    
    # Durata in minuti
    if hasattr(track, 'duration') and track.duration:
        data['duration_minutes'] = round(track.duration / (1000 * 60), 2)
    else:
        data['duration_minutes'] = None
    
    # Rating se disponibile
    if hasattr(track, 'userRating') and track.userRating:
        data['rating'] = track.userRating
    elif hasattr(track, 'rating') and track.rating:
        data['rating'] = track.rating
    else:
        data['rating'] = None
    
    # Bitrate/qualità
    if hasattr(track, 'bitrate') and track.bitrate:
        data['bitrate'] = track.bitrate
    else:
        data['bitrate'] = None
    
    # Artista e album
    data['artist'] = getattr(track, 'grandparentTitle', 'Sconosciuto')
    data['album'] = getattr(track, 'parentTitle', 'Sconosciuto')
    data['title'] = getattr(track, 'title', 'Unknown')
    
    # Play count se disponibile
    if hasattr(track, 'viewCount') and track.viewCount:
        data['play_count'] = track.viewCount
    else:
        data['play_count'] = 0
    
    # Data aggiunta alla libreria
    if hasattr(track, 'addedAt') and track.addedAt:
        data['added_year'] = track.addedAt.year
    else:
        data['added_year'] = None
    
    return data

def _get_cache_path(playlist_id: str | None) -> str:
    name = playlist_id if playlist_id else "library"
    return os.path.join(CACHE_DIR, f"stats_cache_v2_{name}.pkl")

def get_plex_tracks_as_df(
    plex: PlexServer, playlist_id: str | None, force_refresh: bool = False, language: str = 'en'
) -> pd.DataFrame:
    """
    Retrieves tracks from Plex and returns them as DataFrame with extended metadata.
    """
    target_object = None
    
    if playlist_id:
        try:
            playlist_rating_key = int(playlist_id)
            target_object = plex.fetchItem(playlist_rating_key)
            logging.info(f"Found playlist '{target_object.title}' via ID: {playlist_id}")
        except Exception as e:
            logging.error(f"Unable to find playlist with ID {playlist_id}. Error: {e}")
            raise e
    else:
        target_object = plex.library.section('Musica')

    cache_name_suffix = str(playlist_id) if playlist_id else "library_sampled"
    cache_path = _get_cache_path(cache_name_suffix)

    if not force_refresh and os.path.exists(cache_path):
        file_age = time.time() - os.path.getmtime(cache_path)
        if file_age < CACHE_DURATION:
            logging.info(f"Loading statistics from cache: {cache_path}")
            return pd.read_pickle(cache_path)

    logging.info(f"Updating cache for '{target_object.title}'. Starting track retrieval...")
    
    # Usa metodo diverso per libreria vs playlist
    if hasattr(target_object, 'items'):
        tracks = target_object.items()
    else:
        # Per MusicSection usa search per ottenere tracce specificatamente
        tracks = target_object.search(libtype='track')
    logging.info(f"Retrieved {len(tracks)} tracks from '{target_object.title}'")
    
    # Sampling intelligente per librerie grandi
    total_tracks = len(tracks) if hasattr(tracks, '__len__') else target_object.totalSize
    if not playlist_id and total_tracks > SAMPLE_SIZE:
        logging.info(f"Very large library ({total_tracks} tracks). Analyzing sample of {SAMPLE_SIZE} tracks.")
        tracks = random.sample(list(tracks), SAMPLE_SIZE)
    
    logging.info(f"Starting analysis of {len(tracks)} tracks...")

    data = []
    track_count = 0
    for i, track in enumerate(tracks):
        # Per library: accetta tracce senza controllo di type (potrebbero non averlo)
        # Per playlist: mantieni controllo type == 'track'
        is_track = False
        if playlist_id:
            # Per playlist, controlla il type
            is_track = hasattr(track, 'type') and track.type == 'track'
        else:
            # Per library, ora che usiamo search(libtype='track') tutti dovrebbero essere tracce
            # Ma manteniamo un controllo di sicurezza
            is_track = hasattr(track, 'type') and track.type == 'track'
        
        if is_track:
            track_count += 1
            try:
                track_data = {
                    "year": _extract_year(track), 
                    "genre": _extract_genre(track, language)
                }
                # Add extended metadata
                track_data.update(_extract_additional_metadata(track))
                data.append(track_data)
                
                # Log del progresso ogni 10 tracce per debug
                if track_count <= 10 or track_count % 50 == 0:
                    logging.info(f"Processata traccia {track_count}: '{track_data.get('title', 'N/A')}' - Anno: {track_data.get('year', 'N/A')}, Genere: {track_data.get('genre', 'N/A')}")
                    
            except Exception as e:
                logging.warning(f"Error processing track {i} ('{getattr(track, 'title', 'N/A')}'): {e}")
                continue
        else:
            if track_count <= 5:  # Log solo i primi 5 elementi non traccia
                logging.debug(f"Element {i} is not a track: type='{getattr(track, 'type', 'N/A')}', title='{getattr(track, 'title', 'N/A')}'")
    
    logging.info(f"Processed {len(data)} valid tracks out of {track_count} total music tracks")
    
    if not data:
        logging.warning("Nessuna traccia trovata da elaborare.")
        return pd.DataFrame()

    df = pd.DataFrame(data)
    
    # Data cleaning and validation - less restrictive
    # Remove only tracks without genre (year can be None)
    df = df.dropna(subset=['genre'])
    
    # Filter years only if present
    if 'year' in df.columns:
        # Replace None with 0 for missing years
        df['year'] = df['year'].fillna(0)
        # Filter only explicitly invalid years (keeping 0 for "unknown")
        df = df[(df['year'] == 0) | ((df['year'] >= 1900) & (df['year'] <= datetime.now().year + 1))]
    
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_pickle(cache_path)
    logging.info(f"Cache updated and saved to: {cache_path} ({len(df)} valid tracks)")
    
    return df

def generate_genre_pie_chart(df: pd.DataFrame, language='en'):
    """Generates a modern pie chart with genre distribution."""
    logging.info("Starting genre chart generation...")
    if df.empty or 'genre' not in df.columns:
        return "<div class='alert alert-warning'>No genre data available.</div>"
    
    # Filter "Unknown", "Other", "Various" from genres
    pattern = r'^(?:Sconosciuto|Altri|Vari|Unknown|N/A)$'
    df_filtered = df[~df['genre'].str.contains(pattern, case=False, na=False, regex=True)]
    
    if df_filtered.empty:
        return "<div class='alert alert-warning'>No valid genre data available.</div>"
    
    genre_counts = df_filtered['genre'].value_counts().nlargest(12).reset_index()
    genre_counts.columns = ['genre', 'count']
    
    # Raggruppa i generi minori in "Altri" (ma solo quelli con dati validi)
    total_tracks = len(df_filtered)
    other_count = total_tracks - genre_counts['count'].sum()
    if other_count > 0:
        genre_counts = pd.concat([
            genre_counts,
            pd.DataFrame({'genre': ['Altri'], 'count': [other_count]})
        ], ignore_index=True)
    
    fig = px.pie(
        genre_counts, 
        values='count', 
        names='genre', 
        title=get_chart_title('genre_distribution', total_tracks, language),
        template='plotly_dark',
        color_discrete_sequence=SPOTIFY_COLORS
    )
    
    fig.update_traces(
        textposition='inside', 
        textinfo='percent+label',
        hovertemplate=get_hover_template('genre', language)
    )
    
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.05),
        margin=dict(l=0, r=150, t=60, b=0),
        height=400,
        font=dict(size=12)
    )
    
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={
        'toImageButtonOptions': {'format': 'png', 'filename': 'generi_musicali'},
        'displayModeBar': True,
        'responsive': True
    })

def generate_decade_bar_chart(df: pd.DataFrame, language='en'):
    """Generates a modern bar chart with decade distribution."""
    logging.info("Starting decade chart generation...")
    if df.empty or 'year' not in df.columns:
        return "<div class='alert alert-warning'>No year data available.</div>"
    
    df_filtered = df.dropna(subset=['year']).copy()
    df_filtered['decade'] = (df_filtered['year'] // 10 * 10).astype(int)
    
    decade_counts = df_filtered['decade'].value_counts().sort_index().reset_index()
    decade_counts.columns = ['decade', 'count']
    
    # Filtra decadi significative
    decade_counts = decade_counts[decade_counts['decade'] >= 1950]
    decade_counts['decade_label'] = decade_counts['decade'].astype(str) + "s"
    
    total_tracks = len(df_filtered)
    
    fig = px.bar(
        decade_counts, 
        x='decade_label', 
        y='count',
        title=get_chart_title('decade_distribution', total_tracks, language),
        template='plotly_dark',
        labels={'decade_label': 'Decennio', 'count': 'Numero di Tracce'},
        color='count',
        color_continuous_scale='Viridis'
    )
    
    fig.update_traces(
        hovertemplate='<b>%{x}</b><br>Tracce: %{y:,}<br>Percentuale: %{customdata:.1f}%<extra></extra>',
        customdata=decade_counts['count'] / total_tracks * 100
    )
    
    fig.update_layout(
        xaxis_title=get_axis_title('decade', language),
        yaxis_title=get_axis_title('track_count', language),
        coloraxis_showscale=False,
        height=400,
        margin=dict(l=60, r=20, t=60, b=60)
    )
    
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={
        'toImageButtonOptions': {'format': 'png', 'filename': 'distribuzione_decenni'},
        'displayModeBar': True,
        'responsive': True
    })

def generate_top_artists_chart(df: pd.DataFrame, top_n: int = 15, language='en'):
    """Genera un grafico con i top artisti."""
    if df.empty or 'artist' not in df.columns:
        return "<div class='alert alert-warning'>Nessun dato sugli artisti disponibile.</div>"
    
    # Filtra "Various Artists" e varianti simili
    pattern = r'Various Artists?|Vari|V\.A\.|Artisti Vari|AA\.VV\.|Compilation'
    df_filtered = df[~df['artist'].str.contains(pattern, case=False, na=False, regex=True)]
    
    if df_filtered.empty:
        return "<div class='alert alert-warning'>Nessun dato sugli artisti valido disponibile.</div>"
    
    artist_counts = df_filtered['artist'].value_counts().nlargest(top_n).reset_index()
    artist_counts.columns = ['artist', 'count']
    
    fig = px.bar(
        artist_counts,
        x='count',
        y='artist',
        orientation='h',
        title=get_chart_title('top_artists', top_n, language),
        template='plotly_dark',
        labels={'count': get_axis_title('track_count', language), 'artist': get_axis_title('artist', language)},
        color='count',
        color_continuous_scale='plasma'
    )
    
    fig.update_layout(
        yaxis={'categoryorder': 'total ascending'},
        height=max(400, top_n * 25),
        coloraxis_showscale=False,
        margin=dict(l=150, r=20, t=60, b=60)
    )
    
    fig.update_traces(
        hovertemplate=get_hover_template('top_artists', language)
    )
    
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={
        'toImageButtonOptions': {'format': 'png', 'filename': 'top_artisti'},
        'displayModeBar': True,
        'responsive': True
    })

def generate_duration_distribution(df: pd.DataFrame, language='en'):
    """Genera un istogramma della distribuzione delle durate."""
    if df.empty or 'duration_minutes' not in df.columns:
        return "<div class='alert alert-warning'>Nessun dato sulla durata disponibile.</div>"
    
    df_clean = df.dropna(subset=['duration_minutes'])
    df_clean = df_clean[df_clean['duration_minutes'] > 0]
    df_clean = df_clean[df_clean['duration_minutes'] < 20]  # Rimuovi outlier
    
    fig = px.histogram(
        df_clean,
        x='duration_minutes',
        nbins=30,
        title=get_chart_title('duration_distribution', None, language),
        template='plotly_dark',
        labels={'duration_minutes': 'Durata (minuti)', 'count': 'Numero di Tracce'},
        color_discrete_sequence=['#1DB954']
    )
    
    # Add average line
    mean_duration = df_clean['duration_minutes'].mean()
    fig.add_vline(
        x=mean_duration,
        line_dash="dash",
        line_color="red",
        annotation_text=f"Media: {mean_duration:.1f} min"
    )
    
    fig.update_layout(
        xaxis_title=get_axis_title('duration_minutes', language),
        yaxis_title=get_axis_title('track_count', language),
        height=350
    )
    
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={
        'toImageButtonOptions': {'format': 'png', 'filename': 'distribuzione_durata'},
        'displayModeBar': True,
        'responsive': True
    })

def generate_year_trend_chart(df: pd.DataFrame, language='en'):
    """Generates a year trend chart."""
    if df.empty or 'year' not in df.columns:
        return "<div class='alert alert-warning'>No year data available.</div>"
    
    df_filtered = df.dropna(subset=['year']).copy()
    df_filtered = df_filtered[df_filtered['year'] >= 1960]
    
    year_counts = df_filtered['year'].value_counts().sort_index().reset_index()
    year_counts.columns = ['year', 'count']
    
    fig = px.line(
        year_counts,
        x='year',
        y='count',
        title=get_chart_title('year_trend', None, language),
        template='plotly_dark',
        labels={'year': get_axis_title('year', language), 'count': get_axis_title('track_count', language)},
        line_shape='spline'
    )
    
    fig.update_traces(
        line_color='#1DB954',
        line_width=3,
        hovertemplate=get_hover_template('year_trend', language)
    )
    
    fig.update_layout(
        height=350,
        margin=dict(l=60, r=20, t=60, b=60)
    )
    
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={
        'toImageButtonOptions': {'format': 'png', 'filename': 'trend_annuale'},
        'displayModeBar': True,
        'responsive': True
    })

def get_library_statistics(df: pd.DataFrame):
    """Genera statistiche avanzate sulla libreria."""
    if df.empty:
        return {}
    
    stats = {}
    
    # Statistiche base
    stats['total_tracks'] = len(df)
    stats['unique_artists'] = df['artist'].nunique() if 'artist' in df.columns else 0
    stats['unique_albums'] = df['album'].nunique() if 'album' in df.columns else 0
    stats['unique_genres'] = df['genre'].nunique() if 'genre' in df.columns else 0
    
    # Statistiche temporali
    if 'year' in df.columns and not df['year'].empty:
        valid_years = df.dropna(subset=['year'])['year']
        stats['oldest_track'] = int(valid_years.min()) if len(valid_years) > 0 else None
        stats['newest_track'] = int(valid_years.max()) if len(valid_years) > 0 else None
        stats['average_year'] = round(valid_years.mean(), 1) if len(valid_years) > 0 else None
    
    # Statistiche di durata
    if 'duration_minutes' in df.columns and not df['duration_minutes'].empty:
        valid_durations = df.dropna(subset=['duration_minutes'])['duration_minutes']
        if len(valid_durations) > 0:
            stats['total_duration_hours'] = round(valid_durations.sum() / 60, 1)
            stats['average_duration'] = round(valid_durations.mean(), 2)
            stats['shortest_track'] = round(valid_durations.min(), 2)
            stats['longest_track'] = round(valid_durations.max(), 2)
    
    # Top elementi (filtrati)
    if 'artist' in df.columns:
        # Filtra "Various Artists" per statistiche top artist
        pattern = r'Various Artists?|Vari|V\.A\.|Artisti Vari|AA\.VV\.|Compilation'
        df_artist_filtered = df[~df['artist'].str.contains(pattern, case=False, na=False, regex=True)]
        if len(df_artist_filtered) > 0:
            stats['top_artist'] = df_artist_filtered['artist'].value_counts().index[0]
            stats['top_artist_count'] = df_artist_filtered['artist'].value_counts().iloc[0]
        else:
            stats['top_artist'] = None
            stats['top_artist_count'] = 0
    
    if 'genre' in df.columns:
        # Filtra "Sconosciuto", "Altri", "Vari" per statistiche top genre
        pattern = r'^(?:Sconosciuto|Altri|Vari|Unknown|N/A)$'
        df_genre_filtered = df[~df['genre'].str.contains(pattern, case=False, na=False, regex=True)]
        if len(df_genre_filtered) > 0:
            stats['top_genre'] = df_genre_filtered['genre'].value_counts().index[0]
            stats['top_genre_count'] = df_genre_filtered['genre'].value_counts().iloc[0]
        else:
            stats['top_genre'] = None
            stats['top_genre_count'] = 0
    
    # Decade più rappresentata
    if 'year' in df.columns and not df['year'].empty:
        decades = (df.dropna(subset=['year'])['year'] // 10 * 10).astype(int)
        if len(decades) > 0:
            top_decade = decades.value_counts().index[0]
            stats['top_decade'] = f"{top_decade}s"
            stats['top_decade_count'] = decades.value_counts().iloc[0]
    
    return stats

def get_chart_title(chart_type, count_or_value, language='en'):
    """Get translated chart titles."""
    i18n = get_i18n()
    
    if chart_type == 'genre_distribution':
        if language == 'en':
            return f'Musical Genre Distribution ({count_or_value:,} tracks)'
        else:
            return f'Distribuzione Generi Musicali ({count_or_value:,} tracce)'
    elif chart_type == 'decade_distribution':
        if language == 'en':
            return f'Track Distribution by Decade ({count_or_value:,} tracks)'
        else:
            return f'Distribuzione Tracce per Decennio ({count_or_value:,} tracce)'
    elif chart_type == 'top_artists':
        if language == 'en':
            return f'Top {count_or_value} Artists by Track Count'
        else:
            return f'Top {count_or_value} Artisti per Numero di Tracce'
    elif chart_type == 'duration_distribution':
        if language == 'en':
            return 'Track Duration Distribution'
        else:
            return 'Distribuzione Durata Tracce'
    elif chart_type == 'year_trend':
        if language == 'en':
            return 'Track Trend by Year'
        else:
            return 'Trend Tracce per Anno'
    
    return chart_type

def get_axis_title(axis_type, language='en'):
    """Get translated axis titles."""
    if axis_type == 'decade':
        return 'Decade' if language == 'en' else 'Decennio'
    elif axis_type == 'track_count':
        return 'Number of Tracks' if language == 'en' else 'Numero di Tracce'
    elif axis_type == 'duration_minutes':
        return 'Duration (minutes)' if language == 'en' else 'Durata (minuti)'
    elif axis_type == 'artist':
        return 'Artist' if language == 'en' else 'Artista'
    elif axis_type == 'year':
        return 'Year' if language == 'en' else 'Anno'
    
    return axis_type

def get_hover_template(chart_type, language='en'):
    """Get translated hover templates."""
    if chart_type == 'genre':
        if language == 'en':
            return '<b>%{label}</b><br>Tracks: %{value:,}<br>Percentage: %{percent}<extra></extra>'
        else:
            return '<b>%{label}</b><br>Tracce: %{value:,}<br>Percentuale: %{percent}<extra></extra>'
    elif chart_type == 'top_artists':
        if language == 'en':
            return '<b>%{y}</b><br>Tracks: %{x:,}<extra></extra>'
        else:
            return '<b>%{y}</b><br>Tracce: %{x:,}<extra></extra>'
    elif chart_type == 'year_trend':
        if language == 'en':
            return '<b>Year %{x}</b><br>Tracks: %{y:,}<extra></extra>'
        else:
            return '<b>Anno %{x}</b><br>Tracce: %{y:,}<extra></extra>'
    
    return '%{label}: %{value}<extra></extra>'