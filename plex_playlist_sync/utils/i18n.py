"""
Internationalization (i18n) service for Plex Library Completer
Supports Italian (it) and English (en) with easy extensibility for future languages
"""

import json
import os
from typing import Dict, Any, Optional
from flask import session, request
import logging

logger = logging.getLogger(__name__)

class I18nService:
    """Servizio di internazionalizzazione per gestire traduzioni multilingue"""
    
    def __init__(self, default_language: str = 'it'):
        self.default_language = default_language
        self.supported_languages = ['it', 'en']
        self.translations = {}
        self.translations_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 
            'translations'
        )
        self._load_translations()
    
    def _load_translations(self) -> None:
        """Carica tutti i file di traduzione disponibili"""
        try:
            for lang in self.supported_languages:
                translation_file = os.path.join(self.translations_dir, f'{lang}.json')
                if os.path.exists(translation_file):
                    with open(translation_file, 'r', encoding='utf-8') as f:
                        self.translations[lang] = json.load(f)
                    logger.info(f"Loaded translations for language: {lang}")
                else:
                    logger.warning(f"Translation file not found: {translation_file}")
                    self.translations[lang] = {}
        except Exception as e:
            logger.error(f"Error loading translations: {e}")
            # Fallback to empty translations
            for lang in self.supported_languages:
                self.translations[lang] = {}
    
    def get_language(self) -> str:
        """Ottiene la lingua corrente dell'utente (safe per background tasks)"""
        try:
            # 1. Controlla la sessione Flask (solo se nel contesto request)
            from flask import has_request_context
            if has_request_context() and 'language' in session:
                lang = session['language']
                if lang in self.supported_languages:
                    return lang
            
            # 2. Controlla i parametri della query (solo se nel contesto request)
            if has_request_context() and request and hasattr(request, 'args'):
                lang = request.args.get('lang')
                if lang in self.supported_languages:
                    return lang
            
            # 3. Controlla l'header Accept-Language del browser (solo se nel contesto request)
            if has_request_context() and request and hasattr(request, 'headers'):
                accept_language = request.headers.get('Accept-Language', '')
                for lang in self.supported_languages:
                    if lang in accept_language.lower():
                        return lang
        except RuntimeError:
            # Working outside request context - usa lingua predefinita
            pass
        
        # 4. Ritorna la lingua predefinita (safe fallback)
        return self.default_language
    
    def set_language(self, language: str) -> bool:
        """Imposta la lingua per la sessione corrente"""
        if language in self.supported_languages:
            session['language'] = language
            logger.info(f"Language set to: {language}")
            return True
        else:
            logger.warning(f"Unsupported language requested: {language}")
            return False
    
    def get_translation(self, key: str, language: Optional[str] = None, **kwargs) -> str:
        """
        Ottiene una traduzione per la chiave specificata
        
        Args:
            key: Chiave della traduzione (es. 'dashboard.title' o 'common.loading')
            language: Lingua specifica (opzionale, usa quella corrente se non specificata)
            **kwargs: Parametri per la formattazione della stringa
        
        Returns:
            Stringa tradotta o la chiave originale se non trovata
        """
        if not language:
            language = self.get_language()
        
        try:
            # Naviga attraverso le chiavi nidificate (es. 'dashboard.title')
            keys = key.split('.')
            translation = self.translations.get(language, {})
            
            for k in keys:
                if isinstance(translation, dict) and k in translation:
                    translation = translation[k]
                else:
                    # Fallback alla lingua predefinita
                    if language != self.default_language:
                        return self.get_translation(key, self.default_language, **kwargs)
                    # Se neanche nella lingua predefinita, ritorna la chiave
                    logger.warning(f"Translation not found for key: {key} in language: {language}")
                    return key
            
            # Formatta la stringa se necessario
            if isinstance(translation, str) and kwargs:
                try:
                    return translation.format(**kwargs)
                except KeyError as e:
                    logger.warning(f"Missing format parameter {e} for key: {key}")
                    return translation
            
            return str(translation)
            
        except Exception as e:
            logger.error(f"Error getting translation for key {key}: {e}")
            return key
    
    def get_all_translations(self, language: Optional[str] = None) -> Dict[str, Any]:
        """Ottiene tutte le traduzioni per una lingua"""
        if not language:
            language = self.get_language()
        return self.translations.get(language, {})
    
    def get_supported_languages(self) -> Dict[str, str]:
        """Ottiene la lista delle lingue supportate con i loro nomi nativi"""
        return {
            'it': 'Italiano',
            'en': 'English'
        }
    
    def t(self, key: str, **kwargs) -> str:
        """Shorthand per get_translation"""
        return self.get_translation(key, **kwargs)

# Istanza globale del servizio i18n
i18n = I18nService()

def get_i18n() -> I18nService:
    """Ottiene l'istanza del servizio i18n"""
    return i18n

def _(key: str, **kwargs) -> str:
    """Funzione di traduzione shorthand per i template"""
    return i18n.get_translation(key, **kwargs)

def translate_genre(genre_name: str, language: Optional[str] = None) -> str:
    """Traduce un nome di genere musicale"""
    if not genre_name:
        return genre_name
        
    # Normalizza il nome del genere
    normalized = genre_name.lower().strip()
    # Rimuovi caratteri speciali comuni
    normalized = normalized.replace('&', '-').replace(' ', '-')
    
    # Cerca la traduzione
    translation_key = f"genres.{normalized}"
    translation = i18n.get_translation(translation_key, language)
    
    # Se non trovata, ritorna il nome originale
    if translation == translation_key:
        return genre_name
    return translation

def translate_log_message(message: str, language: Optional[str] = None, **kwargs) -> str:
    """Traduce un messaggio di log mantenendo i parametri dinamici"""
    # Identifica pattern comuni nei log per mappare alle traduzioni
    log_patterns = {
        r"Avvio di un nuovo ciclo di sincronizzazione completo": "logs.sync_started",
        r"Ciclo di sincronizzazione e AI completato": "logs.sync_completed", 
        r"Processing user: (.+)": "logs.processing_user",
        r"Playlist '(.+)' trovata\. Aggiornamento in corso": "logs.playlist_found",
        r"Playlist '(.+)' aggiornata con (\d+) tracce": "logs.playlist_updated",
        r"Track found via API: (.+) - (.+)": "logs.track_found",
        r"Database inizializzato con successo: (\d+) tabelle": "logs.database_initialized",
        r"Indici database creati con successo": "logs.index_created",
        r"Indice libreria: (\d+) tracce indicizzate": "logs.library_stats",
        r"Gestione Playlist AI Settimanale per (.+)": "logs.ai_playlist_management",
        r"Scheduler: Avvio del ciclo di sincronizzazione automatica": "logs.scheduler_start",
        r"Scheduler: Salto il ciclo automatico": "logs.scheduler_skip",
        r"Scheduler: In attesa per (\d+) secondi": "logs.scheduler_wait",
        r"Avvio download per (.+) \(ID traccia: (.+)\)": "logs.download_started",
        r"Download completato per (.+) \(ID traccia: (.+)\)": "logs.download_completed",
        r"Errore durante il download di (.+) \(ID traccia: (.+)\): (.+)": "logs.download_failed",
        r"Recuperate (\d+) tracce mancanti dal database": "logs.missing_tracks_retrieved",
        r"Scansione playlist: (.+)": "logs.playlist_scan",
        r"Saltata playlist NO_DELETE: '(.+)'": "logs.playlist_skip_nodelete",
        r"Saltata playlist TV/Film: '(.+)'": "logs.playlist_skip_tv",
        r"Generazione statistiche per (.+) - tipo: (.+)": "logs.stats_generation",
        r"Statistiche generate con successo per (\d+) tracce": "logs.stats_generated",
        r"Scheduler: Avvio del ciclo di sincronizzazione automatica\.": "logs.scheduler_start",
        r"Scheduler: Salto il ciclo automatico, operazione già in corso\.": "logs.scheduler_skip",
        r"Errore critico durante il ciclo '(.+)': (.+)": "logs.critical_error"
    }
    
    import re
    for pattern, translation_key in log_patterns.items():
        match = re.search(pattern, message)
        if match:
            # Estrai i parametri dal messaggio originale
            params = {f"param{i+1}": group for i, group in enumerate(match.groups())}
            params.update(kwargs)
            return i18n.get_translation(translation_key, language, **params)
    
    # Se non trova un pattern, ritorna il messaggio originale
    return message

def translate_status(status: str, language: Optional[str] = None) -> str:
    """Traduce un messaggio di stato"""
    status_map = {
        "in attesa": "status_messages.waiting",
        "operazione in corso": "status_messages.running", 
        "errore": "status_messages.error",
        "mai eseguito": "status_messages.never_executed",
        "completato": "status_messages.completed",
        "fallito": "status_messages.failed",
        "pending": "status_messages.pending",
        "downloading": "status_messages.downloading",
        "searching": "status_messages.searching",
        "processing": "status_messages.processing",
        "indexing": "status_messages.indexing",
        "scanning": "status_messages.scanning",
        "updating": "status_messages.updating",
        "cleaning": "status_messages.cleaning",
        "syncing": "status_messages.syncing"
    }
    
    status_lower = status.lower().strip()
    translation_key = status_map.get(status_lower)
    if translation_key:
        return i18n.get_translation(translation_key, language)
    return status

def init_i18n_for_app(app):
    """Inizializza il servizio i18n per l'app Flask"""
    
    @app.context_processor
    def inject_i18n():
        """Inietta le funzioni di traduzione nei template"""
        return {
            '_': _,
            'get_language': i18n.get_language,
            'get_supported_languages': i18n.get_supported_languages,
            'current_language': i18n.get_language(),
            'translate_genre': translate_genre,
            'translate_status': translate_status
        }
    
    @app.route('/api/language', methods=['POST'])
    def set_language():
        """Endpoint API per cambiare lingua"""
        from flask import request, jsonify
        
        data = request.get_json()
        language = data.get('language') if data else None
        
        if not language:
            return jsonify({'error': 'Language parameter required'}), 400
        
        if i18n.set_language(language):
            return jsonify({
                'success': True, 
                'language': language,
                'message': _('messages.changes_saved')
            })
        else:
            return jsonify({
                'error': _('errors.operation_failed'),
                'supported_languages': list(i18n.get_supported_languages().keys())
            }), 400
    
    @app.route('/api/translations')
    def get_translations():
        """Endpoint API per ottenere tutte le traduzioni correnti"""
        from flask import jsonify
        
        current_language = i18n.get_language()
        translations = i18n.get_all_translations(current_language)
        
        return jsonify({
            'language': current_language,
            'translations': translations
        })
    
    logger.info("I18n service initialized for Flask app")