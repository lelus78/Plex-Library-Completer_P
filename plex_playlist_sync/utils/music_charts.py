import os
import logging
import requests
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import time

logger = logging.getLogger(__name__)

class MusicChartsSearcher:
    """Classe per ricercare classifiche musicali online e fornire dati aggiornati a Gemini."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        self.cache = {}
        self.cache_duration = 3600  # 1 ora di cache
        
    def _is_cache_valid(self, cache_key: str) -> bool:
        """Verifica se la cache è ancora valida."""
        if cache_key not in self.cache:
            return False
        return time.time() - self.cache[cache_key]['timestamp'] < self.cache_duration
    
    def _get_from_cache(self, cache_key: str) -> Optional[Dict]:
        """Recupera dati dalla cache se validi."""
        if self._is_cache_valid(cache_key):
            logger.info(f"Utilizzando dati dalla cache per: {cache_key}")
            return self.cache[cache_key]['data']
        return None
    
    def _save_to_cache(self, cache_key: str, data: Dict):
        """Salva dati nella cache."""
        self.cache[cache_key] = {
            'data': data,
            'timestamp': time.time()
        }
    
    def get_billboard_hot_100(self) -> Optional[List[Dict]]:
        """Recupera la classifica Billboard Hot 100 attuale (simulata)."""
        cache_key = "billboard_hot_100"
        cached_data = self._get_from_cache(cache_key)
        if cached_data:
            return cached_data
        
        try:
            # Per ora simuliamo dati Billboard, in futuro si può integrare con API reali
            hot_100 = [
                {"position": 1, "artist": "Taylor Swift", "title": "Anti-Hero", "weeks": 8},
                {"position": 2, "artist": "Harry Styles", "title": "As It Was", "weeks": 15},
                {"position": 3, "artist": "Bad Bunny", "title": "Tití Me Preguntó", "weeks": 12},
                {"position": 4, "artist": "Post Malone", "title": "I Like You", "weeks": 6},
                {"position": 5, "artist": "Dua Lipa", "title": "Levitating", "weeks": 20},
                {"position": 6, "artist": "The Weeknd", "title": "Blinding Lights", "weeks": 4},
                {"position": 7, "artist": "Olivia Rodrigo", "title": "Good 4 U", "weeks": 7},
                {"position": 8, "artist": "Doja Cat", "title": "Woman", "weeks": 5},
                {"position": 9, "artist": "Billie Eilish", "title": "Happier Than Ever", "weeks": 9},
                {"position": 10, "artist": "Ed Sheeran", "title": "Shivers", "weeks": 11}
            ]
            
            self._save_to_cache(cache_key, hot_100)
            logger.info(f"Recuperata classifica Billboard Hot 100: {len(hot_100)} tracce")
            return hot_100
            
        except Exception as e:
            logger.error(f"Errore nel recupero Billboard Hot 100: {e}")
            return None
    
    def get_spotify_global_top_50(self) -> Optional[List[Dict]]:
        """Recupera la top 50 globale di Spotify (simulata)."""
        cache_key = "spotify_global_top_50"
        cached_data = self._get_from_cache(cache_key)
        if cached_data:
            return cached_data
        
        try:
            # Simulazione dati Spotify Global Top 50
            global_top_50 = [
                {"position": 1, "artist": "Bad Bunny", "title": "Tití Me Preguntó", "streams": "15,234,567"},
                {"position": 2, "artist": "Harry Styles", "title": "Music for a Sushi Restaurant", "streams": "12,845,234"},
                {"position": 3, "artist": "Taylor Swift", "title": "Lavender Haze", "streams": "11,567,890"},
                {"position": 4, "artist": "The Weeknd", "title": "Popular", "streams": "10,234,567"},
                {"position": 5, "artist": "Drake", "title": "Jimmy Cooks", "streams": "9,876,543"},
                {"position": 6, "artist": "Dua Lipa", "title": "Don't Start Now", "streams": "9,234,567"},
                {"position": 7, "artist": "Post Malone", "title": "Circles", "streams": "8,765,432"},
                {"position": 8, "artist": "Ariana Grande", "title": "positions", "streams": "8,345,678"},
                {"position": 9, "artist": "Billie Eilish", "title": "bad guy", "streams": "8,123,456"},
                {"position": 10, "artist": "Justin Bieber", "title": "Ghost", "streams": "7,987,654"}
            ]
            
            self._save_to_cache(cache_key, global_top_50)
            logger.info(f"Recuperata Spotify Global Top 50: {len(global_top_50)} tracce")
            return global_top_50
            
        except Exception as e:
            logger.error(f"Errore nel recupero Spotify Global Top 50: {e}")
            return None
    
    def get_italian_charts(self) -> Optional[List[Dict]]:
        """Recupera le classifiche italiane (FIMI/Spotify Italia)."""
        cache_key = "italian_charts"
        cached_data = self._get_from_cache(cache_key)
        if cached_data:
            return cached_data
        
        try:
            # Simulazione classifica italiana
            italian_charts = [
                {"position": 1, "artist": "Ultimo", "title": "Altrove", "weeks": 3},
                {"position": 2, "artist": "Pinguini Tattici Nucleari", "title": "Ricordi", "weeks": 5},
                {"position": 3, "artist": "Måneskin", "title": "MAMMAMIA", "weeks": 8},
                {"position": 4, "artist": "Blanco", "title": "Nostalgia", "weeks": 12},
                {"position": 5, "artist": "Tananai", "title": "Sesso Occasionale", "weeks": 7},
                {"position": 6, "artist": "Marco Mengoni", "title": "Due Vite", "weeks": 4},
                {"position": 7, "artist": "Irama", "title": "Ovunque Sarai", "weeks": 6},
                {"position": 8, "artist": "Sangiovanni", "title": "Farfalle", "weeks": 9},
                {"position": 9, "artist": "Madame", "title": "Tu Mi Hai Capito", "weeks": 11},
                {"position": 10, "artist": "Ghali", "title": "Good Times", "weeks": 2}
            ]
            
            self._save_to_cache(cache_key, italian_charts)
            logger.info(f"Recuperata classifica italiana: {len(italian_charts)} tracce")
            return italian_charts
            
        except Exception as e:
            logger.error(f"Errore nel recupero classifiche italiane: {e}")
            return None
    
    def get_genre_trending(self, genre: str) -> Optional[List[Dict]]:
        """Recupera le tendenze per un genere specifico."""
        cache_key = f"genre_trending_{genre.lower()}"
        cached_data = self._get_from_cache(cache_key)
        if cached_data:
            return cached_data
        
        try:
            # Database di tendenze per genere (simulato)
            genre_trends = {
                "rock": [
                    {"artist": "Foo Fighters", "title": "Times Like These", "trend": "rising"},
                    {"artist": "Imagine Dragons", "title": "Bones", "trend": "stable"},
                    {"artist": "Arctic Monkeys", "title": "Do I Wanna Know?", "trend": "rising"},
                    {"artist": "The Killers", "title": "Mr. Brightside", "trend": "stable"},
                    {"artist": "Red Hot Chili Peppers", "title": "Black Summer", "trend": "rising"}
                ],
                "pop": [
                    {"artist": "Taylor Swift", "title": "Karma", "trend": "rising"},
                    {"artist": "Dua Lipa", "title": "Physical", "trend": "stable"},
                    {"artist": "Ariana Grande", "title": "7 rings", "trend": "falling"},
                    {"artist": "Olivia Rodrigo", "title": "vampire", "trend": "rising"},
                    {"artist": "Harry Styles", "title": "Watermelon Sugar", "trend": "stable"}
                ],
                "electronic": [
                    {"artist": "Calvin Harris", "title": "Miracle", "trend": "rising"},
                    {"artist": "David Guetta", "title": "I'm Good", "trend": "stable"},
                    {"artist": "Swedish House Mafia", "title": "Don't You Worry Child", "trend": "rising"},
                    {"artist": "Martin Garrix", "title": "Animals", "trend": "stable"},
                    {"artist": "Tiësto", "title": "The Business", "trend": "falling"}
                ],
                "hip-hop": [
                    {"artist": "Drake", "title": "Rich Flex", "trend": "rising"},
                    {"artist": "Kendrick Lamar", "title": "N95", "trend": "stable"},
                    {"artist": "Travis Scott", "title": "K-POP", "trend": "rising"},
                    {"artist": "Future", "title": "Wait for U", "trend": "stable"},
                    {"artist": "21 Savage", "title": "Jimmy Cooks", "trend": "falling"}
                ]
            }
            
            trending = genre_trends.get(genre.lower(), [])
            if trending:
                self._save_to_cache(cache_key, trending)
                logger.info(f"Recuperate tendenze per genere {genre}: {len(trending)} tracce")
            
            return trending
            
        except Exception as e:
            logger.error(f"Errore nel recupero tendenze per genere {genre}: {e}")
            return None
    
    def get_seasonal_trends(self) -> Optional[List[Dict]]:
        """Recupera le tendenze stagionali basate sul periodo dell'anno."""
        cache_key = "seasonal_trends"
        cached_data = self._get_from_cache(cache_key)
        if cached_data:
            return cached_data
        
        try:
            current_month = datetime.now().month
            
            # Tendenze stagionali
            if current_month in [12, 1, 2]:  # Inverno
                season = "winter"
                trends = [
                    {"artist": "Mariah Carey", "title": "All I Want for Christmas Is You", "season": "winter"},
                    {"artist": "Adele", "title": "Set Fire to the Rain", "season": "winter"},
                    {"artist": "Sam Smith", "title": "Stay With Me", "season": "winter"},
                    {"artist": "John Legend", "title": "All of Me", "season": "winter"}
                ]
            elif current_month in [3, 4, 5]:  # Primavera
                season = "spring"
                trends = [
                    {"artist": "Pharrell Williams", "title": "Happy", "season": "spring"},
                    {"artist": "Justin Timberlake", "title": "Can't Stop the Feeling!", "season": "spring"},
                    {"artist": "Bruno Mars", "title": "Count on Me", "season": "spring"},
                    {"artist": "Ed Sheeran", "title": "Perfect", "season": "spring"}
                ]
            elif current_month in [6, 7, 8]:  # Estate
                season = "summer"
                trends = [
                    {"artist": "Calvin Harris", "title": "Summer", "season": "summer"},
                    {"artist": "Dua Lipa", "title": "Levitating", "season": "summer"},
                    {"artist": "The Chainsmokers", "title": "Closer", "season": "summer"},
                    {"artist": "Luis Fonsi", "title": "Despacito", "season": "summer"}
                ]
            else:  # Autunno
                season = "autumn"
                trends = [
                    {"artist": "Billie Eilish", "title": "bad guy", "season": "autumn"},
                    {"artist": "Taylor Swift", "title": "cardigan", "season": "autumn"},
                    {"artist": "The Weeknd", "title": "Blinding Lights", "season": "autumn"},
                    {"artist": "Lorde", "title": "Royals", "season": "autumn"}
                ]
            
            result = {"season": season, "trends": trends}
            self._save_to_cache(cache_key, result)
            logger.info(f"Recuperate tendenze stagionali per {season}: {len(trends)} tracce")
            return result
            
        except Exception as e:
            logger.error(f"Errore nel recupero tendenze stagionali: {e}")
            return None
    
    def search_music_news(self, query: str) -> Optional[List[Dict]]:
        """Cerca notizie musicali recenti per genere o artista."""
        cache_key = f"music_news_{query.lower()}"
        cached_data = self._get_from_cache(cache_key)
        if cached_data:
            return cached_data
        
        try:
            # Simulazione notizie musicali
            news_db = {
                "rock": [
                    {"title": "Foo Fighters announce new album for 2024", "date": "2024-01-15", "source": "Rolling Stone"},
                    {"title": "Arctic Monkeys world tour dates revealed", "date": "2024-01-10", "source": "NME"}
                ],
                "pop": [
                    {"title": "Taylor Swift breaks streaming records again", "date": "2024-01-20", "source": "Billboard"},
                    {"title": "Dua Lipa announces Italian concert dates", "date": "2024-01-18", "source": "Music News"}
                ],
                "italian": [
                    {"title": "Sanremo 2024: vincitori e nuove hit", "date": "2024-02-10", "source": "FIMI"},
                    {"title": "Måneskin tour mondiale: date italiane confermate", "date": "2024-01-25", "source": "Rockol"}
                ]
            }
            
            news = news_db.get(query.lower(), [])
            if news:
                self._save_to_cache(cache_key, news)
                logger.info(f"Recuperate notizie musicali per {query}: {len(news)} articoli")
            
            return news
            
        except Exception as e:
            logger.error(f"Errore nella ricerca notizie musicali per {query}: {e}")
            return None
    
    def get_comprehensive_music_data(self, context: str = "") -> Dict:
        """Recupera una raccolta completa di dati musicali per informare Gemini."""
        logger.info("Avvio raccolta dati musicali completa per Gemini...")
        
        data = {
            "timestamp": datetime.now().isoformat(),
            "context": context,
            "charts": {},
            "trends": {},
            "news": []
        }
        
        try:
            # Classifiche principali
            data["charts"]["billboard_hot_100"] = self.get_billboard_hot_100()
            data["charts"]["spotify_global"] = self.get_spotify_global_top_50()
            data["charts"]["italian"] = self.get_italian_charts()
            
            # Tendenze per genere
            for genre in ["rock", "pop", "electronic", "hip-hop"]:
                data["trends"][genre] = self.get_genre_trending(genre)
            
            # Tendenze stagionali
            data["trends"]["seasonal"] = self.get_seasonal_trends()
            
            # Notizie recenti
            for topic in ["rock", "pop", "italian"]:
                news = self.search_music_news(topic)
                if news:
                    data["news"].extend(news)
            
            logger.info("Raccolta dati musicali completata con successo")
            return data
            
        except Exception as e:
            logger.error(f"Errore nella raccolta dati musicali completa: {e}")
            return data

# Istanza globale del searcher
music_charts_searcher = MusicChartsSearcher()