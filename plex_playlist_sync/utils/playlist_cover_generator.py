"""
Playlist Cover Generator using Hugging Face Gradio API
Generates professional playlist covers with custom text overlay
"""

import os
import logging
import tempfile
import requests
import json
import time
import uuid
from typing import Optional, Dict, List
from PIL import Image
import io

logger = logging.getLogger(__name__)

# Import Gemini AI function for creative prompts
def _get_gemini_prompt_function():
    try:
        from .gemini_ai import generate_creative_cover_prompt
        return generate_creative_cover_prompt
    except ImportError:
        return None

def _is_gemini_available():
    """Check if Gemini is available dynamically"""
    return _get_gemini_prompt_function() is not None

# Global pipeline cache (FaceSwapApp style)
_cached_pipeline = None
_cached_model_name = None

class SwarmUIClient:
    """
    Client per SwarmUI API con gestione sessioni e generazione immagini
    """
    
    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = os.getenv("SWARMUI_URL", "http://host.docker.internal:7801")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.timeout = int(os.getenv("SWARMUI_TIMEOUT", "300"))
        self.session_id = None
        self.user_id = None
        
    def get_session(self) -> bool:
        """Ottiene una nuova sessione SwarmUI"""
        try:
            response = self.session.post(f"{self.base_url}/API/GetNewSession", json={})
            if response.status_code == 200:
                data = response.json()
                self.session_id = data.get("session_id")
                self.user_id = data.get("user_id")
                logger.info(f"✅ SwarmUI sessione ottenuta: {self.session_id}")
                return True
            else:
                logger.error(f"❌ Errore ottenimento sessione SwarmUI: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Errore connessione SwarmUI: {e}")
            return False
    
    def is_available(self) -> bool:
        """Verifica se SwarmUI è disponibile"""
        try:
            response = self.session.get(f"{self.base_url}/API/GetNewSession", timeout=10)
            return response.status_code == 200
        except:
            return False
    
    def generate_image(self, prompt: str, negative_prompt: str = "", **kwargs) -> Optional[bytes]:
        """
        Genera immagine usando SwarmUI
        
        Args:
            prompt: Prompt per generazione
            negative_prompt: Prompt negativo
            **kwargs: Parametri aggiuntivi (width, height, steps, cfgscale, etc.)
            
        Returns:
            bytes: Dati immagine PNG o None se fallimento
        """
        try:
            # Ottieni sessione se non presente
            if not self.session_id:
                if not self.get_session():
                    return None
            
            # Parametri di default
            payload = {
                "session_id": self.session_id,
                "prompt": prompt,
                "negativeprompt": negative_prompt,
                "width": kwargs.get("width", 1024),
                "height": kwargs.get("height", 1024),
                "images": 1,
                "seed": kwargs.get("seed", -1),
                "donotsave": False
            }
            
            # Usa preset se specificato, altrimenti parametri manuali
            if "preset" in kwargs:
                payload["preset"] = kwargs["preset"]
            else:
                # Parametri ottimizzati per Fluxmania Legacy come default
                payload.update({
                    "model": kwargs.get("model", "Fluxmania_Legacy.safetensors"),
                    "steps": kwargs.get("steps", 25),
                    "guidance": kwargs.get("guidance", 3.5),  # Flux guidance per Flux.1 Dev
                    "cfgscale": kwargs.get("cfgscale", 3.5),  # CFG Scale anche per Fluxmania Legacy
                    "sampler": kwargs.get("sampler", "dpmpp_2m"),
                    "scheduler": kwargs.get("scheduler", "sgm_uniform")
                })
            
            # Rimuovi parametri None
            payload = {k: v for k, v in payload.items() if v is not None}
            
            logger.info(f"🎨 Generando immagine SwarmUI: {prompt[:100]}...")
            
            # Invia richiesta generazione
            response = self.session.post(f"{self.base_url}/API/GenerateText2Image", json=payload)
            
            if response.status_code != 200:
                logger.error(f"❌ Errore generazione SwarmUI: {response.status_code}")
                return None
            
            result = response.json()
            
            # Verifica se c'è un errore
            if "error" in result:
                logger.error(f"❌ Errore SwarmUI: {result['error']}")
                return None
            
            # Ottieni percorsi immagini
            images = result.get("images", [])
            if not images:
                logger.error("❌ Nessuna immagine generata da SwarmUI")
                return None
            
            # Scarica la prima immagine
            image_path = images[0]
            
            # Controlla se è un data URL base64
            if image_path.startswith("data:image/"):
                logger.info("📥 Decodificando immagine base64")
                import base64
                try:
                    # Estrai i dati base64
                    header, encoded = image_path.split(',', 1)
                    image_data = base64.b64decode(encoded)
                    logger.info(f"✅ Immagine SwarmUI generata con successo (base64)")
                    return image_data
                except Exception as e:
                    logger.error(f"❌ Errore decodifica base64: {e}")
                    return None
            else:
                # Gestione normale con percorso file
                # Encode URL per gestire caratteri speciali e spazi
                from urllib.parse import quote
                encoded_path = quote(image_path, safe='/:')
                image_url = f"{self.base_url}/{encoded_path}"
                
                logger.debug(f"📥 Scaricando da: {image_url}")
                
                img_response = self.session.get(image_url, timeout=60)
                
                if img_response.status_code == 200:
                    logger.info(f"✅ Immagine SwarmUI generata con successo")
                    return img_response.content
                else:
                    logger.error(f"❌ Errore download immagine SwarmUI: {img_response.status_code}")
                    logger.error(f"URL: {image_url}")
                    return None
                
        except Exception as e:
            logger.error(f"❌ Errore generazione SwarmUI: {e}")
            return None

class ComfyUIClient:
    """
    Client per ComfyUI API con gestione workflow e generazione immagini
    """
    
    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = os.getenv("COMFYUI_URL", "http://comfyui:8188")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.timeout = int(os.getenv("COMFYUI_TIMEOUT", "300"))
        
    def is_available(self) -> bool:
        """Verifica se ComfyUI è disponibile"""
        try:
            response = self.session.get(f"{self.base_url}/system_stats", timeout=10)
            return response.status_code == 200
        except:
            return False
    
    def queue_prompt(self, workflow: dict, client_id: str = None) -> Optional[str]:
        """
        Invia workflow alla coda di ComfyUI
        
        Args:
            workflow: Workflow ComfyUI da eseguire
            client_id: ID cliente per tracking
            
        Returns:
            str: Prompt ID se successo, None se fallimento
        """
        try:
            if not client_id:
                client_id = str(uuid.uuid4())
            
            payload = {
                "prompt": workflow,
                "client_id": client_id
            }
            
            response = self.session.post(
                f"{self.base_url}/prompt",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                result = response.json()
                prompt_id = result.get("prompt_id")
                logger.info(f"✅ Workflow inviato a ComfyUI: {prompt_id}")
                return prompt_id
            else:
                logger.error(f"❌ Errore invio workflow ComfyUI: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Errore connessione ComfyUI: {e}")
            return None
    
    def get_queue_status(self) -> dict:
        """Ottiene stato della coda ComfyUI"""
        try:
            response = self.session.get(f"{self.base_url}/queue")
            return response.json() if response.status_code == 200 else {}
        except:
            return {}
    
    def wait_for_completion(self, prompt_id: str, timeout: int = 300) -> bool:
        """
        Attende completamento del workflow
        
        Args:
            prompt_id: ID del prompt da monitorare
            timeout: Timeout in secondi
            
        Returns:
            bool: True se completato, False se timeout/errore
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # Verifica se il prompt è ancora in coda
                queue_status = self.get_queue_status()
                
                # Controlla running queue
                running = queue_status.get("queue_running", [])
                pending = queue_status.get("queue_pending", [])
                
                # Verifica se il prompt è ancora in elaborazione
                is_running = any(item[1] == prompt_id for item in running)
                is_pending = any(item[1] == prompt_id for item in pending)
                
                if not is_running and not is_pending:
                    logger.info(f"✅ Workflow completato: {prompt_id}")
                    return True
                
                logger.debug(f"⏳ Workflow in elaborazione: {prompt_id}")
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"❌ Errore controllo stato workflow: {e}")
                time.sleep(5)
        
        logger.error(f"⏰ Timeout workflow ComfyUI: {prompt_id}")
        return False
    
    def get_image_output(self, prompt_id: str, node_id: str = None) -> Optional[bytes]:
        """
        Recupera immagine generata da ComfyUI
        
        Args:
            prompt_id: ID del prompt
            node_id: ID del nodo output (default: from env COMFYUI_OUTPUT_NODE)
            
        Returns:
            bytes: Dati immagine PNG o None se fallimento
        """
        try:
            if node_id is None:
                node_id = os.getenv("COMFYUI_OUTPUT_NODE", "9")
            # Ottiene la lista dei file generati
            response = self.session.get(f"{self.base_url}/history/{prompt_id}")
            
            if response.status_code != 200:
                logger.error(f"❌ Errore recupero history: {response.status_code}")
                return None
            
            history = response.json()
            
            if prompt_id not in history:
                logger.error(f"❌ Prompt ID non trovato nella history: {prompt_id}")
                return None
            
            # Trova i file output
            outputs = history[prompt_id].get("outputs", {})
            
            if node_id not in outputs:
                logger.error(f"❌ Node ID {node_id} non trovato negli output")
                return None
            
            node_output = outputs[node_id]
            
            # Cerca immagini generate
            images = node_output.get("images", [])
            
            if not images:
                logger.error("❌ Nessuna immagine trovata negli output")
                return None
            
            # Prende la prima immagine
            image_info = images[0]
            filename = image_info.get("filename")
            subfolder = image_info.get("subfolder", "")
            
            # Costruisce URL per download
            if subfolder:
                image_url = f"{self.base_url}/view?filename={filename}&subfolder={subfolder}"
            else:
                image_url = f"{self.base_url}/view?filename={filename}"
            
            # Scarica l'immagine
            img_response = self.session.get(image_url)
            
            if img_response.status_code == 200:
                logger.info(f"✅ Immagine recuperata: {filename}")
                return img_response.content
            else:
                logger.error(f"❌ Errore download immagine: {img_response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Errore recupero immagine: {e}")
            return None
    
    def generate_cover(self, prompt: str, workflow_template: dict) -> Optional[bytes]:
        """
        Genera copertina usando ComfyUI
        
        Args:
            prompt: Prompt per generazione
            workflow_template: Template workflow ComfyUI
            
        Returns:
            bytes: Dati immagine PNG o None se fallimento
        """
        try:
            # Modifica il workflow con il prompt
            workflow = workflow_template.copy()
            
            # Trova il nodo del prompt (di solito node "6" per CLIPTextEncode)
            for node_id, node in workflow.items():
                if node.get("class_type") == "CLIPTextEncode":
                    if "inputs" in node and "text" in node["inputs"]:
                        workflow[node_id]["inputs"]["text"] = prompt
                        logger.info(f"🎨 Prompt impostato nel nodo {node_id}: {prompt[:100]}...")
                        break
            
            # Invia workflow
            prompt_id = self.queue_prompt(workflow)
            
            if not prompt_id:
                return None
            
            # Attende completamento
            if not self.wait_for_completion(prompt_id):
                return None
            
            # Recupera immagine
            image_data = self.get_image_output(prompt_id)
            
            return image_data
            
        except Exception as e:
            logger.error(f"❌ Errore generazione copertina ComfyUI: {e}")
            return None

def get_mood_prompt_from_genres(genres: List[str], description: str = "") -> str:
    """
    Genera un prompt visivo basato sui generi musicali e descrizione
    """
    genre_prompts = {
        'reggae': 'tropical paradise, warm golden hues, island sunset, palm trees silhouette, ocean waves',
        'dancehall': 'vibrant caribbean colors, tropical energy, beach party, dancehall vibes',
        'electronic': 'neon cyberpunk aesthetic, geometric patterns, futuristic design, electric blue and pink',
        'edm': 'electric energy, dynamic light rays, club atmosphere, bass waves visualization',
        'house': 'nightclub lighting, pulsing neon, urban electronic, modern minimalist design',
        'techno': 'industrial metallic, dark ambient, minimalist geometric, chrome textures',
        'trance': 'ethereal lights, spiral patterns, cosmic energy, transcendent vibes',
        'dubstep': 'bass drops visualization, glitch art, digital chaos, heavy beats',
        'drum and bass': 'fast motion blur, jungle atmosphere, urban underground, breakbeat energy',
        'synthwave': 'retro 80s neon, grid patterns, sunset aesthetic, cyberpunk nostalgia',
        'pop': 'bright colors, playful design, modern aesthetic, trendy vibes',
        'k-pop': 'colorful korean aesthetic, cute design, idol vibes, kawaii elements',
        'j-pop': 'japanese pop culture, anime style, colorful manga, kawaii aesthetic',
        'rock': 'bold textures, dynamic energy, powerful colors, edgy design',
        'metal': 'dark atmosphere, industrial textures, powerful imagery, intense colors',
        'black metal': 'dark gothic, norwegian forest, corpse paint, atmospheric darkness',
        'death metal': 'brutal imagery, dark red tones, aggressive design, metal aesthetic',
        'power metal': 'fantasy themes, epic design, medieval aesthetic, heroic vibes',
        'progressive rock': 'complex geometric, surreal art, psychedelic colors, artistic complexity',
        'jazz': 'sophisticated colors, vintage aesthetic, smooth textures, elegant design',
        'blues': 'deep colors, vintage vibes, emotional atmosphere, classic aesthetic',
        'classical': 'elegant design, gold accents, sophisticated textures, timeless beauty',
        'opera': 'dramatic baroque, ornate design, classical elegance, theatrical vibes',
        'hip-hop': 'urban aesthetic, street art vibes, bold typography, dynamic colors',
        'rap': 'street energy, graffiti style, urban colors, powerful design',
        'trap': 'urban trap aesthetic, dark vibes, street culture, modern hip-hop',
        'r&b': 'smooth colors, soulful atmosphere, warm tones, sophisticated design',
        'funk': 'groovy colors, retro vibes, playful patterns, energetic design',
        'soul': 'warm colors, emotional depth, vintage soul, rich textures',
        'indie': 'artistic design, unique aesthetic, alternative colors, creative vibes',
        'alternative': 'edgy design, non-conventional colors, artistic expression',
        'punk': 'rebellious energy, bold colors, rough textures, anarchic design',
        'emo': 'emotional darkness, gothic elements, teenage angst, alternative aesthetic',
        'screamo': 'intense emotions, chaotic design, aggressive colors, raw energy',
        'country': 'rustic colors, americana vibes, vintage aesthetic, warm tones',
        'folk': 'natural colors, organic textures, earthy tones, acoustic vibes',
        'bluegrass': 'rural americana, banjo vibes, countryside aesthetic, traditional folk',
        'ambient': 'ethereal colors, flowing textures, dreamy atmosphere, peaceful vibes',
        'chill': 'soft colors, relaxing atmosphere, gentle textures, calming vibes',
        'lofi': 'vintage aesthetic, warm tones, nostalgic vibes, cozy atmosphere',
        'vaporwave': 'retro aesthetic, pink and blue, 80s nostalgia, glitch art',
        'chillwave': 'dreamy synthetics, pastel colors, nostalgic vibes, summer aesthetic',
        'world': 'global fusion, multicultural elements, ethnic patterns, world music vibes',
        'latin': 'vibrant latin colors, tropical energy, salsa vibes, passionate design',
        'reggaeton': 'urban latin, party vibes, colorful energy, modern latin aesthetic',
        'bossa nova': 'smooth brazilian, cafe culture, elegant simplicity, sophisticated jazz',
        'flamenco': 'spanish passion, red and black, dramatic energy, traditional dance',
        'celtic': 'irish green, folk patterns, medieval aesthetic, traditional celtic',
        'middle eastern': 'arabic patterns, desert colors, traditional ornaments, mystical vibes',
        'indian': 'bollywood colors, traditional patterns, spiritual vibes, cultural richness',
        'anime': 'japanese animation, colorful characters, manga style, otaku culture',
        'video game': 'pixel art, gaming aesthetic, retro games, digital adventure',
        'soundtrack': 'cinematic mood, movie poster style, dramatic lighting, epic scenes',
        'experimental': 'abstract art, avant-garde design, unconventional colors, artistic chaos',
        'noise': 'chaotic patterns, harsh textures, industrial noise, abstract aggression',
        'drone': 'minimalist waves, meditative patterns, deep textures, atmospheric drone',
        'post-rock': 'cinematic landscapes, instrumental beauty, atmospheric design, epic soundscapes',
        'shoegaze': 'dreamy blur, wall of sound, ethereal textures, atmospheric haze',
        'grunge': 'dirty aesthetic, 90s nostalgia, alternative rock, raw energy',
        'nu-metal': 'aggressive modern, metalcore vibes, industrial elements, heavy design',
        'hardcore': 'intense energy, aggressive design, punk attitude, hardcore aesthetic',
        'ska': 'black and white check, jamaican vibes, upbeat energy, ska culture',
        'swing': 'big band era, art deco style, 1940s aesthetic, jazz age glamour',
        'disco': 'mirror ball, 70s glamour, dance floor energy, disco lights',
        'new wave': '80s synthetics, post-punk vibes, new romantic, alternative 80s',
        'gothic': 'dark romantic, victorian elements, gothic architecture, dramatic shadows',
        'industrial': 'mechanical textures, factory aesthetic, harsh industrial, cyberpunk elements',
        'dark ambient': 'haunting atmosphere, mysterious textures, dark soundscapes, ambient horror',
        'witch house': 'occult symbols, dark electronic, mystical vibes, underground aesthetic',
        'future bass': 'kawaii future, colorful drops, cute electronic, modern bass music',
        'phonk': 'memphis rap, dark lo-fi, underground hip-hop, nostalgic beats',
        'breakcore': 'chaotic breakbeats, digital hardcore, glitch chaos, intense electronic'
    }
    
    # Trova i prompt per i generi presenti
    visual_elements = []
    for genre in genres:
        genre_lower = genre.lower()
        for key, prompt in genre_prompts.items():
            if key in genre_lower:
                visual_elements.append(prompt)
                break
    
    # Se non trova generi specifici, usa un design generico
    if not visual_elements:
        visual_elements = ['modern music aesthetic, vibrant colors, artistic design']
    
    # Aggiungi elementi dalla descrizione
    description_lower = description.lower()
    if 'tropical' in description_lower or 'island' in description_lower:
        visual_elements.append('tropical paradise, palm trees, ocean vibes')
    if 'energy' in description_lower or 'workout' in description_lower:
        visual_elements.append('dynamic energy, powerful colors, motivational vibes')
    if 'chill' in description_lower or 'relax' in description_lower:
        visual_elements.append('calming atmosphere, soft colors, peaceful vibes')
    if 'party' in description_lower:
        visual_elements.append('party atmosphere, celebration vibes, festive colors')
    
    return ', '.join(visual_elements[:3])  # Usa max 3 elementi per non sovraccaricare

def get_text_prompt_style(genres: List[str] = None) -> str:
    """
    Restituisce lo stile del prompt per il testo basato sui generi
    """
    if not genres:
        return 'with bold title text: "{playlist_name}"'
    
    # Stili specifici per genere
    text_styles = {
        'electronic': 'with neon sign text: "{playlist_name}"',
        'edm': 'with glowing neon text: "{playlist_name}"',
        'house': 'with club neon sign text: "{playlist_name}"',
        'techno': 'with digital display text: "{playlist_name}"',
        'synthwave': 'with retro neon sign text: "{playlist_name}"',
        'vaporwave': 'with aesthetic neon text: "{playlist_name}"',
        'dubstep': 'with glitch effect text: "{playlist_name}"',
        'trance': 'with ethereal glowing text: "{playlist_name}"',
        
        'rock': 'with bold graffiti text: "{playlist_name}"',
        'metal': 'with heavy metal logo text: "{playlist_name}"',
        'punk': 'with rebellious spray paint text: "{playlist_name}"',
        'hardcore': 'with aggressive bold text: "{playlist_name}"',
        
        'hip-hop': 'with street art graffiti text: "{playlist_name}"',
        'rap': 'with urban graffiti text: "{playlist_name}"',
        'trap': 'with modern street sign text: "{playlist_name}"',
        
        'jazz': 'with elegant vintage sign text: "{playlist_name}"',
        'blues': 'with classic blues club sign text: "{playlist_name}"',
        'classical': 'with ornate golden text: "{playlist_name}"',
        'opera': 'with baroque elegant text: "{playlist_name}"',
        
        'reggae': 'with tropical wooden sign text: "{playlist_name}"',
        'reggaeton': 'with colorful party sign text: "{playlist_name}"',
        'latin': 'with vibrant fiesta text: "{playlist_name}"',
        
        'pop': 'with bright colorful text: "{playlist_name}"',
        'k-pop': 'with cute kawaii text: "{playlist_name}"',
        'j-pop': 'with anime style text: "{playlist_name}"',
        
        'country': 'with rustic wooden sign text: "{playlist_name}"',
        'folk': 'with handwritten folk text: "{playlist_name}"',
        
        'ambient': 'with floating ethereal text: "{playlist_name}"',
        'chill': 'with soft glowing text: "{playlist_name}"',
        'lofi': 'with vintage typewriter text: "{playlist_name}"',
        
        'indie': 'with artistic handwritten text: "{playlist_name}"',
        'alternative': 'with creative typography text: "{playlist_name}"',
        
        'dance': 'with party lights text: "{playlist_name}"',
        'disco': 'with mirror ball reflection text: "{playlist_name}"',
        
        'r&b': 'with smooth neon text: "{playlist_name}"',
        'soul': 'with soulful vintage text: "{playlist_name}"',
        'funk': 'with groovy retro text: "{playlist_name}"',
        
        'gothic': 'with dark gothic text: "{playlist_name}"',
        'industrial': 'with metallic industrial text: "{playlist_name}"',
        
        'world': 'with cultural traditional text: "{playlist_name}"',
        'celtic': 'with ancient runes text: "{playlist_name}"',
        
        'soundtrack': 'with cinematic title text: "{playlist_name}"',
        'anime': 'with manga style text: "{playlist_name}"',
        'video game': 'with pixel art text: "{playlist_name}"'
    }
    
    # Trova il primo genere che corrisponde
    for genre in genres:
        genre_lower = genre.lower()
        for key, style in text_styles.items():
            if key in genre_lower:
                return style
    
    # Default style
    return 'with bold title text: "{playlist_name}"'

def optimize_prompt_for_flux(base_prompt: str, genres: List[str]) -> str:
    """
    Ottimizza il prompt specificatamente per Flux Schnell (8 steps, no CFG)
    """
    # Prefissi ottimizzati per Flux Schnell - più diretti e concisi
    flux_prefixes = [
        "album cover artwork with text",
        "music cover design with title", 
        "stylized album art with readable text"
    ]
    
    # Suffissi per migliorare la qualità con Flux Schnell
    flux_suffixes = [
        "clean design",
        "high contrast",
        "vibrant colors",
        "modern style",
        "readable typography"
    ]
    
    # Parole chiave ottimizzate per Flux Schnell (generi estesi)
    flux_keywords = {
        'reggae': ['tropical', 'sunset', 'palm trees', 'ocean'],
        'electronic': ['neon', 'cyberpunk', 'geometric', 'electric'],
        'edm': ['electric', 'club', 'bass', 'energy'],
        'house': ['nightclub', 'neon', 'urban', 'minimal'],
        'techno': ['industrial', 'minimal', 'dark', 'geometric'],
        'trance': ['ethereal', 'spiral', 'cosmic', 'transcendent'],
        'dubstep': ['bass', 'glitch', 'digital', 'chaos'],
        'synthwave': ['retro', 'neon', 'grid', 'cyberpunk'],
        'rock': ['bold', 'dynamic', 'powerful', 'edgy'],
        'metal': ['dark', 'industrial', 'powerful', 'intense'],
        'black metal': ['gothic', 'forest', 'darkness', 'atmospheric'],
        'death metal': ['brutal', 'dark red', 'aggressive', 'metal'],
        'power metal': ['fantasy', 'epic', 'medieval', 'heroic'],
        'jazz': ['vintage', 'elegant', 'smooth', 'classic'],
        'blues': ['deep', 'vintage', 'emotional', 'classic'],
        'classical': ['elegant', 'gold', 'timeless', 'ornate'],
        'opera': ['baroque', 'ornate', 'theatrical', 'dramatic'],
        'pop': ['bright', 'playful', 'colorful', 'modern'],
        'k-pop': ['colorful', 'cute', 'kawaii', 'idol'],
        'j-pop': ['anime', 'manga', 'kawaii', 'colorful'],
        'hip-hop': ['urban', 'street', 'bold', 'dynamic'],
        'rap': ['street', 'graffiti', 'urban', 'powerful'],
        'trap': ['urban', 'dark', 'street', 'modern'],
        'r&b': ['smooth', 'soulful', 'warm', 'sophisticated'],
        'funk': ['groovy', 'retro', 'playful', 'energetic'],
        'soul': ['warm', 'emotional', 'vintage', 'rich'],
        'indie': ['artistic', 'unique', 'alternative', 'creative'],
        'alternative': ['edgy', 'artistic', 'unconventional', 'alternative'],
        'punk': ['rebellious', 'bold', 'rough', 'anarchic'],
        'emo': ['emotional', 'gothic', 'dark', 'alternative'],
        'country': ['rustic', 'americana', 'vintage', 'warm'],
        'folk': ['natural', 'organic', 'earthy', 'acoustic'],
        'ambient': ['ethereal', 'flowing', 'dreamy', 'peaceful'],
        'chill': ['soft', 'relaxing', 'gentle', 'calming'],
        'lofi': ['vintage', 'warm', 'nostalgic', 'cozy'],
        'vaporwave': ['retro', 'pink', 'blue', 'glitch'],
        'latin': ['vibrant', 'tropical', 'salsa', 'passionate'],
        'reggaeton': ['urban', 'party', 'colorful', 'modern'],
        'bossa nova': ['smooth', 'brazilian', 'cafe', 'elegant'],
        'flamenco': ['spanish', 'red', 'black', 'dramatic'],
        'celtic': ['irish', 'green', 'folk', 'medieval'],
        'anime': ['japanese', 'colorful', 'manga', 'otaku'],
        'video game': ['pixel', 'gaming', 'retro', 'digital'],
        'soundtrack': ['cinematic', 'movie', 'dramatic', 'epic'],
        'experimental': ['abstract', 'avant-garde', 'unconventional', 'artistic'],
        'gothic': ['dark', 'romantic', 'victorian', 'shadows'],
        'industrial': ['mechanical', 'factory', 'harsh', 'cyberpunk'],
        'synthwave': ['retro', '80s', 'neon', 'grid'],
        'vaporwave': ['aesthetic', 'pink', 'blue', 'nostalgia'],
        'future bass': ['kawaii', 'colorful', 'cute', 'modern'],
        'phonk': ['memphis', 'dark', 'lo-fi', 'underground'],
        'breakcore': ['chaotic', 'digital', 'glitch', 'intense']
    }
    
    # Seleziona keywords per generi presenti
    selected_keywords = []
    for genre in genres:
        genre_lower = genre.lower()
        for key, keywords in flux_keywords.items():
            if key in genre_lower:
                selected_keywords.extend(keywords[:2])  # Max 2 per genere
                break
    
    # Costruisce prompt ottimizzato per Flux Schnell (più conciso)
    optimized_parts = []
    
    # Prefisso breve
    optimized_parts.append(flux_prefixes[0])
    
    # Prompt base 
    optimized_parts.append(base_prompt)
    
    # Keywords specifiche (ridotte per Schnell)
    if selected_keywords:
        optimized_parts.append(', '.join(selected_keywords[:3]))
    
    # Suffisso qualità breve
    optimized_parts.append(', '.join(flux_suffixes[:3]))
    
    # Prompt finale ottimizzato per Flux Schnell (max 180 caratteri per includere testo)
    final_prompt = ', '.join(optimized_parts)
    
    # Tronca se troppo lungo per Flux Schnell
    if len(final_prompt) > 180:
        final_prompt = final_prompt[:177] + "..."
    
    return final_prompt

def generate_simple_cover_fallback(
    playlist_name: str,
    genres: List[str] = None,
    save_path: Optional[str] = None
) -> Optional[str]:
    """
    Genera una copertina professionale usando Pillow avanzato con font bellissimi
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        import textwrap
        import math
        import random
        
        # Palette colori moderne per generi
        genre_palettes = {
            'reggae': {
                'colors': ['#FFD700', '#FF6B35', '#4ECDC4', '#45B7D1'],
                'style': 'tropical'
            },
            'electronic': {
                'colors': ['#00D4FF', '#FF0080', '#8A2BE2', '#1E1E2E'],
                'style': 'neon'
            },
            'rock': {
                'colors': ['#FF4444', '#222222', '#CCCCCC', '#660000'],
                'style': 'edgy'
            },
            'pop': {
                'colors': ['#FF69B4', '#00BFFF', '#FFD700', '#FF6B9D'],
                'style': 'vibrant'
            },
            'jazz': {
                'colors': ['#8B4513', '#FFD700', '#2F1B14', '#DAA520'],
                'style': 'vintage'
            },
            'classical': {
                'colors': ['#8B0000', '#FFD700', '#F5F5DC', '#2F2F2F'],
                'style': 'elegant'
            },
            'chill': {
                'colors': ['#87CEEB', '#DDA0DD', '#F0E68C', '#98FB98'],
                'style': 'gradient'
            },
            'hip-hop': {
                'colors': ['#FFD700', '#000000', '#FF4500', '#8B008B'],
                'style': 'urban'
            }
        }
        
        # Seleziona palette
        palette_key = 'pop'  # default
        if genres:
            for genre in genres:
                genre_lower = genre.lower()
                for key in genre_palettes:
                    if key in genre_lower:
                        palette_key = key
                        break
        
        palette = genre_palettes[palette_key]
        colors = palette['colors']
        style = palette['style']
        
        # Crea immagine 1024x1024
        size = (1024, 1024)
        
        # Background avanzato basato sullo stile
        if style == 'gradient':
            image = create_advanced_gradient(size, colors)
        elif style == 'neon':
            image = create_neon_background(size, colors)
        elif style == 'vintage':
            image = create_vintage_background(size, colors)
        elif style == 'urban':
            image = create_urban_background(size, colors)
        else:
            image = create_modern_background(size, colors)
        
        draw = ImageDraw.Draw(image)
        
        # Font system avanzati con stili bellissimi
        try:
            # Lista di font alternativi con stili specifici per genere
            font_paths = get_genre_fonts(genres)
            
            font_large = None
            selected_font_name = "default"
            
            for font_info in font_paths:
                font_path = font_info['path']
                font_name = font_info['name']
                try:
                    font_large = ImageFont.truetype(font_path, 80)
                    selected_font_name = font_name
                    logger.info(f"🎨 Font selezionato: {font_name} per generi {genres}")
                    break
                except:
                    continue
            
            if not font_large:
                font_large = ImageFont.load_default()
                
        except:
            font_large = ImageFont.load_default()
        
        # Text processing avanzato
        lines = smart_text_wrap(playlist_name, max_width=18)
        
        # Calcola dimensioni testo
        total_height = len(lines) * 100
        start_y = (size[1] - total_height) // 2
        
        # Effetti testo avanzati con stili bellissimi
        text_style = get_text_style_for_genre(palette_key)
        
        for i, line in enumerate(lines):
            # Misura testo
            bbox = draw.textbbox((0, 0), line, font=font_large)
            text_width = bbox[2] - bbox[0]
            x = (size[0] - text_width) // 2
            y = start_y + i * 100
            
            # Applica stile testo specifico per genere
            apply_text_style(draw, x, y, line, font_large, text_style, colors)
            
            # Log dello stile applicato
            logger.debug(f"🎨 Stile testo applicato: {text_style['name']} per '{line}'")
        
        # Aggiungi elementi decorativi basati sul genere
        add_genre_decorations(draw, size, palette_key, colors)
        
        # Determina il path di salvataggio
        if not save_path:
            temp_dir = tempfile.gettempdir()
            safe_name = "".join(c for c in playlist_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            save_path = os.path.join(temp_dir, f"playlist_cover_{safe_name}_advanced.png")
        
        # Salva l'immagine
        image.save(save_path, 'PNG', quality=95)
        
        logger.info(f"✅ Copertina avanzata generata ({style}): {save_path}")
        return save_path
        
    except Exception as e:
        logger.error(f"❌ Errore nella generazione copertina avanzata: {e}")
        return None

def get_genre_fonts(genres: List[str] = None) -> List[Dict[str, str]]:
    """
    Restituisce lista di font ottimizzati per genere con fallback
    """
    # Font base sempre disponibili
    base_fonts = [
        {"name": "DejaVu Sans Bold", "path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"},
        {"name": "Liberation Sans Bold", "path": "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"},
        {"name": "Ubuntu Bold", "path": "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"},
        {"name": "Arial Bold", "path": "/Windows/Fonts/arialbd.ttf"},
        {"name": "Helvetica Bold", "path": "/System/Library/Fonts/Helvetica.ttc"},
    ]
    
    # Font specifici per genere
    genre_fonts = {
        'reggae': [
            {"name": "Ubuntu Condensed", "path": "/usr/share/fonts/truetype/ubuntu/Ubuntu-C.ttf"},
            {"name": "Liberation Sans", "path": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"},
        ],
        'electronic': [
            {"name": "DejaVu Sans Bold", "path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"},
            {"name": "Liberation Mono Bold", "path": "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"},
        ],
        'jazz': [
            {"name": "Liberation Serif Bold", "path": "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"},
            {"name": "DejaVu Serif Bold", "path": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"},
        ],
        'rock': [
            {"name": "Ubuntu Bold", "path": "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"},
            {"name": "DejaVu Sans Bold", "path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"},
        ],
        'classical': [
            {"name": "Liberation Serif Bold", "path": "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"},
            {"name": "DejaVu Serif Bold", "path": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"},
        ],
        'hip-hop': [
            {"name": "Ubuntu Bold", "path": "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"},
            {"name": "DejaVu Sans Bold", "path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"},
        ],
        'chill': [
            {"name": "Ubuntu Light", "path": "/usr/share/fonts/truetype/ubuntu/Ubuntu-L.ttf"},
            {"name": "Liberation Sans", "path": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"},
        ]
    }
    
    # Seleziona font basati sui generi
    selected_fonts = []
    if genres:
        for genre in genres:
            genre_lower = genre.lower()
            for key, fonts in genre_fonts.items():
                if key in genre_lower:
                    selected_fonts.extend(fonts)
                    break
    
    # Aggiungi font base come fallback
    selected_fonts.extend(base_fonts)
    
    # Rimuovi duplicati mantenendo l'ordine
    seen = set()
    unique_fonts = []
    for font in selected_fonts:
        if font['path'] not in seen:
            seen.add(font['path'])
            unique_fonts.append(font)
    
    return unique_fonts

def get_text_style_for_genre(genre: str) -> Dict[str, any]:
    """
    Restituisce stile del testo ottimizzato per genere (esteso con molte varianti)
    """
    text_styles = {
        'reggae': {
            'name': 'Tropical Wave',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#FFD700',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FFD700', '#FF6B35']
        },
        'electronic': {
            'name': 'Neon Glow',
            'shadow_style': 'neon',
            'glow_enabled': True,
            'glow_color': '#00D4FF',
            'outline_width': 1,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#00D4FF', '#FF0080']
        },
        'edm': {
            'name': 'Electric Pulse',
            'shadow_style': 'neon',
            'glow_enabled': True,
            'glow_color': '#FF0080',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF0080', '#00D4FF']
        },
        'house': {
            'name': 'Club Minimal',
            'shadow_style': 'soft',
            'glow_enabled': True,
            'glow_color': '#8A2BE2',
            'outline_width': 1,
            'text_color': '#FFFFFF',
            'gradient_text': False,
            'gradient_colors': ['#8A2BE2', '#4169E1']
        },
        'techno': {
            'name': 'Industrial Grid',
            'shadow_style': 'hard',
            'glow_enabled': False,
            'glow_color': '#333333',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': False,
            'gradient_colors': ['#FFFFFF', '#333333']
        },
        'trance': {
            'name': 'Ethereal Spiral',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#9370DB',
            'outline_width': 1,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#9370DB', '#4169E1']
        },
        'dubstep': {
            'name': 'Glitch Chaos',
            'shadow_style': 'neon',
            'glow_enabled': True,
            'glow_color': '#00FF00',
            'outline_width': 3,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#00FF00', '#FF4500']
        },
        'synthwave': {
            'name': 'Retro Grid',
            'shadow_style': 'neon',
            'glow_enabled': True,
            'glow_color': '#FF1493',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF1493', '#00BFFF']
        },
        'jazz': {
            'name': 'Vintage Elegant',
            'shadow_style': 'soft',
            'glow_enabled': False,
            'glow_color': '#DAA520',
            'outline_width': 1,
            'text_color': '#FFD700',
            'gradient_text': False,
            'gradient_colors': ['#FFD700', '#8B4513']
        },
        'blues': {
            'name': 'Deep Soul',
            'shadow_style': 'soft',
            'glow_enabled': False,
            'glow_color': '#4682B4',
            'outline_width': 2,
            'text_color': '#4682B4',
            'gradient_text': False,
            'gradient_colors': ['#4682B4', '#191970']
        },
        'rock': {
            'name': 'Bold Impact',
            'shadow_style': 'hard',
            'glow_enabled': False,
            'glow_color': '#FF4444',
            'outline_width': 3,
            'text_color': '#FFFFFF',
            'gradient_text': False,
            'gradient_colors': ['#FF4444', '#660000']
        },
        'metal': {
            'name': 'Dark Force',
            'shadow_style': 'hard',
            'glow_enabled': False,
            'glow_color': '#8B0000',
            'outline_width': 4,
            'text_color': '#FFFFFF',
            'gradient_text': False,
            'gradient_colors': ['#8B0000', '#000000']
        },
        'black metal': {
            'name': 'Gothic Darkness',
            'shadow_style': 'hard',
            'glow_enabled': False,
            'glow_color': '#000000',
            'outline_width': 5,
            'text_color': '#FFFFFF',
            'gradient_text': False,
            'gradient_colors': ['#000000', '#8B0000']
        },
        'death metal': {
            'name': 'Brutal Aggression',
            'shadow_style': 'hard',
            'glow_enabled': False,
            'glow_color': '#8B0000',
            'outline_width': 4,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#8B0000', '#000000']
        },
        'power metal': {
            'name': 'Epic Fantasy',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#FFD700',
            'outline_width': 3,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FFD700', '#8B4513']
        },
        'classical': {
            'name': 'Elegant Serif',
            'shadow_style': 'soft',
            'glow_enabled': False,
            'glow_color': '#DAA520',
            'outline_width': 1,
            'text_color': '#FFD700',
            'gradient_text': False,
            'gradient_colors': ['#FFD700', '#8B0000']
        },
        'opera': {
            'name': 'Baroque Ornate',
            'shadow_style': 'soft',
            'glow_enabled': True,
            'glow_color': '#FFD700',
            'outline_width': 2,
            'text_color': '#8B0000',
            'gradient_text': True,
            'gradient_colors': ['#FFD700', '#8B0000']
        },
        'pop': {
            'name': 'Vibrant Fun',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#FF69B4',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF69B4', '#00BFFF']
        },
        'k-pop': {
            'name': 'Kawaii Idol',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#FF69B4',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF69B4', '#FFD700']
        },
        'j-pop': {
            'name': 'Anime Style',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#FF1493',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF1493', '#00BFFF']
        },
        'hip-hop': {
            'name': 'Street Bold',
            'shadow_style': 'hard',
            'glow_enabled': True,
            'glow_color': '#FFD700',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FFD700', '#FF4500']
        },
        'rap': {
            'name': 'Graffiti Style',
            'shadow_style': 'hard',
            'glow_enabled': True,
            'glow_color': '#FF4500',
            'outline_width': 3,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF4500', '#8B0000']
        },
        'trap': {
            'name': 'Urban Dark',
            'shadow_style': 'hard',
            'glow_enabled': True,
            'glow_color': '#8B008B',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#8B008B', '#000000']
        },
        'punk': {
            'name': 'Rebellious Chaos',
            'shadow_style': 'hard',
            'glow_enabled': False,
            'glow_color': '#FF0000',
            'outline_width': 4,
            'text_color': '#FFFFFF',
            'gradient_text': False,
            'gradient_colors': ['#FF0000', '#000000']
        },
        'emo': {
            'name': 'Emotional Gothic',
            'shadow_style': 'soft',
            'glow_enabled': True,
            'glow_color': '#8B008B',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#8B008B', '#000000']
        },
        'indie': {
            'name': 'Artistic Alternative',
            'shadow_style': 'soft',
            'glow_enabled': True,
            'glow_color': '#9370DB',
            'outline_width': 1,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#9370DB', '#32CD32']
        },
        'alternative': {
            'name': 'Unconventional Edge',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#FF6347',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF6347', '#4169E1']
        },
        'chill': {
            'name': 'Soft Dreamy',
            'shadow_style': 'soft',
            'glow_enabled': True,
            'glow_color': '#87CEEB',
            'outline_width': 1,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#87CEEB', '#DDA0DD']
        },
        'lofi': {
            'name': 'Vintage Cozy',
            'shadow_style': 'soft',
            'glow_enabled': True,
            'glow_color': '#D2691E',
            'outline_width': 1,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#D2691E', '#8B4513']
        },
        'vaporwave': {
            'name': 'Retro Aesthetic',
            'shadow_style': 'neon',
            'glow_enabled': True,
            'glow_color': '#FF1493',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF1493', '#00BFFF']
        },
        'ambient': {
            'name': 'Ethereal Flow',
            'shadow_style': 'soft',
            'glow_enabled': True,
            'glow_color': '#9370DB',
            'outline_width': 1,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#9370DB', '#87CEEB']
        },
        'country': {
            'name': 'Rustic Americana',
            'shadow_style': 'soft',
            'glow_enabled': False,
            'glow_color': '#D2691E',
            'outline_width': 2,
            'text_color': '#8B4513',
            'gradient_text': False,
            'gradient_colors': ['#D2691E', '#8B4513']
        },
        'folk': {
            'name': 'Natural Organic',
            'shadow_style': 'soft',
            'glow_enabled': False,
            'glow_color': '#228B22',
            'outline_width': 1,
            'text_color': '#8B4513',
            'gradient_text': False,
            'gradient_colors': ['#228B22', '#8B4513']
        },
        'latin': {
            'name': 'Passionate Fire',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#FF4500',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF4500', '#FFD700']
        },
        'reggaeton': {
            'name': 'Urban Latin',
            'shadow_style': 'hard',
            'glow_enabled': True,
            'glow_color': '#FF1493',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF1493', '#32CD32']
        },
        'flamenco': {
            'name': 'Spanish Passion',
            'shadow_style': 'hard',
            'glow_enabled': True,
            'glow_color': '#8B0000',
            'outline_width': 3,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#8B0000', '#000000']
        },
        'celtic': {
            'name': 'Irish Folk',
            'shadow_style': 'soft',
            'glow_enabled': True,
            'glow_color': '#228B22',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#228B22', '#DAA520']
        },
        'anime': {
            'name': 'Otaku Culture',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#FF1493',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF1493', '#00BFFF']
        },
        'video game': {
            'name': 'Pixel Art',
            'shadow_style': 'hard',
            'glow_enabled': True,
            'glow_color': '#00FF00',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': False,
            'gradient_colors': ['#00FF00', '#0000FF']
        },
        'soundtrack': {
            'name': 'Cinematic Epic',
            'shadow_style': 'multiple',
            'glow_enabled': True,
            'glow_color': '#FFD700',
            'outline_width': 3,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FFD700', '#8B0000']
        },
        'gothic': {
            'name': 'Dark Romance',
            'shadow_style': 'soft',
            'glow_enabled': True,
            'glow_color': '#8B008B',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#8B008B', '#000000']
        },
        'industrial': {
            'name': 'Mechanical Harsh',
            'shadow_style': 'hard',
            'glow_enabled': False,
            'glow_color': '#696969',
            'outline_width': 3,
            'text_color': '#FFFFFF',
            'gradient_text': False,
            'gradient_colors': ['#696969', '#000000']
        },
        'future bass': {
            'name': 'Kawaii Future',
            'shadow_style': 'neon',
            'glow_enabled': True,
            'glow_color': '#FF69B4',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#FF69B4', '#00BFFF']
        },
        'phonk': {
            'name': 'Memphis Underground',
            'shadow_style': 'hard',
            'glow_enabled': True,
            'glow_color': '#8B008B',
            'outline_width': 2,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#8B008B', '#000000']
        },
        'breakcore': {
            'name': 'Digital Chaos',
            'shadow_style': 'neon',
            'glow_enabled': True,
            'glow_color': '#00FF00',
            'outline_width': 3,
            'text_color': '#FFFFFF',
            'gradient_text': True,
            'gradient_colors': ['#00FF00', '#FF0000']
        }
    }
    
    return text_styles.get(genre, text_styles['pop'])

def apply_text_style(draw, x: int, y: int, text: str, font, style: Dict, colors: List[str]):
    """
    Applica stile testo avanzato con effetti bellissimi
    """
    # Applica ombra basata sullo stile
    if style['shadow_style'] == 'multiple':
        # Ombra multipla graduata
        shadow_offsets = [(6, 6), (4, 4), (2, 2)]
        shadow_opacities = [0.8, 0.5, 0.3]
        
        for offset, opacity in zip(shadow_offsets, shadow_opacities):
            shadow_color = tuple(int(c * opacity) for c in [0, 0, 0])
            draw.text((x + offset[0], y + offset[1]), text, fill=shadow_color, font=font)
    
    elif style['shadow_style'] == 'neon':
        # Effetto neon con glow
        glow_color = hex_to_rgb(style['glow_color'])
        for radius in [4, 3, 2, 1]:
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if dx*dx + dy*dy <= radius*radius:
                        alpha = 1 - (dx*dx + dy*dy) / (radius*radius)
                        glow_with_alpha = tuple(int(c * alpha * 0.7) for c in glow_color)
                        draw.text((x + dx, y + dy), text, fill=glow_with_alpha, font=font)
    
    elif style['shadow_style'] == 'hard':
        # Ombra dura per rock/hip-hop
        draw.text((x + 3, y + 3), text, fill='#000000', font=font)
        draw.text((x + 2, y + 2), text, fill='#333333', font=font)
    
    elif style['shadow_style'] == 'soft':
        # Ombra morbida per jazz/classical
        draw.text((x + 2, y + 2), text, fill='#00000080', font=font)
    
    # Outline se richiesto
    if style['outline_width'] > 0:
        outline_color = get_contrast_color(colors[0])
        width = style['outline_width']
        
        for dx in range(-width, width + 1):
            for dy in range(-width, width + 1):
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), text, fill=outline_color, font=font)
    
    # Testo principale
    if style['gradient_text']:
        # Testo con gradiente (simulato)
        text_color = style['gradient_colors'][0]
        draw.text((x, y), text, fill=text_color, font=font)
    else:
        # Testo colore solido
        draw.text((x, y), text, fill=style['text_color'], font=font)

def hex_to_rgb(hex_color: str) -> tuple:
    """Converte colore hex in RGB"""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def get_contrast_color(hex_color: str) -> str:
    """Calcola colore contrastante"""
    r, g, b = hex_to_rgb(hex_color)
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return '#000000' if brightness > 128 else '#FFFFFF'

def smart_text_wrap(text: str, max_width: int = 18) -> List[str]:
    """Text wrapping intelligente che mantiene parole intere"""
    words = text.split()
    lines = []
    current_line = []
    current_length = 0
    
    for word in words:
        if current_length + len(word) + len(current_line) <= max_width:
            current_line.append(word)
            current_length += len(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
                current_line = [word]
                current_length = len(word)
            else:
                lines.append(word[:max_width])
                if len(word) > max_width:
                    current_line = [word[max_width:]]
                    current_length = len(word) - max_width
                else:
                    current_line = []
                    current_length = 0
    
    if current_line:
        lines.append(' '.join(current_line))
    
    return lines

def create_advanced_gradient(size: tuple, colors: List[str]) -> Image.Image:
    """Crea gradiente multi-colore avanzato"""
    from PIL import Image
    import math
    
    image = Image.new('RGB', size)
    pixels = image.load()
    
    width, height = size
    color_positions = [i / (len(colors) - 1) for i in range(len(colors))]
    
    for y in range(height):
        for x in range(width):
            # Gradiente diagonale
            progress = (x + y) / (width + height)
            
            # Trova colori adiacenti
            for i in range(len(color_positions) - 1):
                if progress <= color_positions[i + 1]:
                    # Interpola tra i due colori
                    local_progress = (progress - color_positions[i]) / (color_positions[i + 1] - color_positions[i])
                    
                    rgb1 = hex_to_rgb(colors[i])
                    rgb2 = hex_to_rgb(colors[i + 1])
                    
                    r = int(rgb1[0] * (1 - local_progress) + rgb2[0] * local_progress)
                    g = int(rgb1[1] * (1 - local_progress) + rgb2[1] * local_progress)
                    b = int(rgb1[2] * (1 - local_progress) + rgb2[2] * local_progress)
                    
                    pixels[x, y] = (r, g, b)
                    break
    
    return image

def create_neon_background(size: tuple, colors: List[str]) -> Image.Image:
    """Crea background neon/cyber"""
    from PIL import Image, ImageDraw
    import random
    import math
    
    image = Image.new('RGB', size, hex_to_rgb(colors[-1]))  # Base scura
    draw = ImageDraw.Draw(image)
    
    # Linee neon
    for _ in range(8):
        start_x = random.randint(0, size[0])
        start_y = random.randint(0, size[1])
        end_x = random.randint(0, size[0])
        end_y = random.randint(0, size[1])
        
        color = colors[random.randint(0, len(colors) - 2)]
        draw.line([(start_x, start_y), (end_x, end_y)], fill=color, width=3)
    
    # Cerchi neon
    for _ in range(5):
        x = random.randint(0, size[0])
        y = random.randint(0, size[1])
        radius = random.randint(20, 100)
        color = colors[random.randint(0, len(colors) - 2)]
        
        draw.ellipse([x-radius, y-radius, x+radius, y+radius], outline=color, width=2)
    
    return image

def create_vintage_background(size: tuple, colors: List[str]) -> Image.Image:
    """Crea background vintage/jazz"""
    from PIL import Image, ImageDraw
    import random
    
    # Gradiente base
    image = create_advanced_gradient(size, colors[:2])
    draw = ImageDraw.Draw(image)
    
    # Texture vintage con cerchi
    for _ in range(15):
        x = random.randint(-50, size[0] + 50)
        y = random.randint(-50, size[1] + 50)
        radius = random.randint(30, 150)
        
        # Colore semi-trasparente
        color_base = hex_to_rgb(colors[2] if len(colors) > 2 else colors[0])
        alpha = random.randint(20, 60)
        
        draw.ellipse([x-radius, y-radius, x+radius, y+radius], 
                    fill=(*color_base, alpha), outline=None)
    
    return image

def create_urban_background(size: tuple, colors: List[str]) -> Image.Image:
    """Crea background urban/hip-hop"""
    from PIL import Image, ImageDraw
    import random
    
    image = Image.new('RGB', size, hex_to_rgb(colors[1]))  # Base scura
    draw = ImageDraw.Draw(image)
    
    # Blocchi geometrici urban
    for _ in range(12):
        x1 = random.randint(0, size[0] // 2)
        y1 = random.randint(0, size[1] // 2)
        x2 = x1 + random.randint(50, 200)
        y2 = y1 + random.randint(50, 200)
        
        color = colors[random.randint(0, len(colors) - 1)]
        alpha = random.randint(30, 80)
        
        draw.rectangle([x1, y1, x2, y2], fill=color)
    
    # Linee oblique
    for _ in range(6):
        x = random.randint(0, size[0])
        color = colors[0]
        draw.line([(x, 0), (x + 200, size[1])], fill=color, width=5)
    
    return image

def create_modern_background(size: tuple, colors: List[str]) -> Image.Image:
    """Crea background moderno/pop"""
    from PIL import Image, ImageDraw
    import math
    
    # Gradiente radiale
    image = Image.new('RGB', size)
    pixels = image.load()
    
    center_x, center_y = size[0] // 2, size[1] // 2
    max_distance = math.sqrt(center_x**2 + center_y**2)
    
    for y in range(size[1]):
        for x in range(size[0]):
            distance = math.sqrt((x - center_x)**2 + (y - center_y)**2)
            progress = distance / max_distance
            
            if progress <= 0.5:
                # Interno
                local_progress = progress * 2
                rgb1 = hex_to_rgb(colors[0])
                rgb2 = hex_to_rgb(colors[1])
            else:
                # Esterno
                local_progress = (progress - 0.5) * 2
                rgb1 = hex_to_rgb(colors[1])
                rgb2 = hex_to_rgb(colors[2] if len(colors) > 2 else colors[0])
            
            r = int(rgb1[0] * (1 - local_progress) + rgb2[0] * local_progress)
            g = int(rgb1[1] * (1 - local_progress) + rgb2[1] * local_progress)
            b = int(rgb1[2] * (1 - local_progress) + rgb2[2] * local_progress)
            
            pixels[x, y] = (r, g, b)
    
    return image

def add_genre_decorations(draw, size: tuple, genre: str, colors: List[str]):
    """Aggiungi decorazioni specifiche per genere"""
    import random
    import math
    
    if genre == 'reggae':
        # Onde tropicali
        for i in range(3):
            y_base = size[1] - 100 - i * 30
            for x in range(0, size[0], 10):
                y = y_base + math.sin(x / 50) * 20
                draw.ellipse([x-3, y-3, x+3, y+3], fill=colors[2])
    
    elif genre == 'electronic':
        # Pixel art decorations
        pixel_size = 8
        for _ in range(20):
            x = random.randint(0, size[0] - pixel_size)
            y = random.randint(0, size[1] - pixel_size)
            color = colors[random.randint(0, len(colors) - 1)]
            draw.rectangle([x, y, x + pixel_size, y + pixel_size], fill=color)
    
    elif genre == 'jazz':
        # Note musicali stilizzate
        for _ in range(8):
            x = random.randint(50, size[0] - 50)
            y = random.randint(50, size[1] - 50)
            draw.ellipse([x-5, y-5, x+5, y+5], fill=colors[1])
            draw.line([(x+5, y), (x+5, y-30)], fill=colors[1], width=2)
    
    elif genre == 'rock':
        # Saette rock
        for _ in range(4):
            x = random.randint(100, size[0] - 100)
            y = random.randint(100, size[1] - 100)
            points = [(x, y), (x+10, y-20), (x+5, y-20), (x+15, y-40), 
                     (x-5, y-25), (x, y-25)]
            draw.polygon(points, fill=colors[2])


def detect_gpu_capabilities() -> str:
    """
    Rileva le capacità per generazione copertine
    
    Returns:
        str: 'swarmui' | 'comfyui' | 'simple' | 'disabled'
    """
    # Controlla se SwarmUI è disponibile
    swarmui_url = os.getenv("SWARMUI_URL", "http://host.docker.internal:7801")
    
    try:
        client = SwarmUIClient(swarmui_url)
        if client.is_available():
            logger.info("🎨 SwarmUI disponibile - usando generazione AI con SwarmUI")
            return "swarmui"
    except Exception as e:
        logger.debug(f"SwarmUI non disponibile: {e}")
    
    # Fallback a ComfyUI
    comfyui_url = os.getenv("COMFYUI_URL", "http://comfyui:8188")
    
    try:
        client = ComfyUIClient(comfyui_url)
        if client.is_available():
            logger.info("🎨 ComfyUI disponibile - usando generazione AI esterna")
            return "comfyui"
    except Exception as e:
        logger.debug(f"ComfyUI non disponibile: {e}")
    
    # Fallback a copertine semplici
    logger.info("🖼️ Nessun AI disponibile - usando copertine semplici")
    return "simple"

def load_comfyui_workflow(workflow_name: str = None) -> Optional[dict]:
    """
    Carica workflow ComfyUI da file JSON
    
    Args:
        workflow_name: Nome del workflow da caricare (default: from env COMFYUI_WORKFLOW)
        
    Returns:
        dict: Workflow ComfyUI o None se non trovato
    """
    try:
        if workflow_name is None:
            workflow_name = os.getenv("COMFYUI_WORKFLOW", "flux_album_cover")
        # Cerca il workflow nella directory workflows
        workflow_paths = [
            f"/app/workflows/{workflow_name}.json",
            f"/app/state_data/workflows/{workflow_name}.json",
            f"workflows/{workflow_name}.json",
            f"state_data/workflows/{workflow_name}.json"
        ]
        
        for path in workflow_paths:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    workflow = json.load(f)
                    logger.info(f"✅ Workflow caricato: {path}")
                    return workflow
        
        # Se non trova file, usa workflow di default
        logger.warning(f"⚠️ Workflow {workflow_name} non trovato, usando default")
        return get_default_flux_workflow()
        
    except Exception as e:
        logger.error(f"❌ Errore caricamento workflow: {e}")
        return None

def get_default_flux_workflow() -> dict:
    """
    Restituisce workflow ComfyUI di default per Flux
    Basato su 01_flux_gguf_optimal
    """
    return {
        "5": {
            "inputs": {
                "width": 1024,
                "height": 1024,
                "batch_size": 1
            },
            "class_type": "EmptyLatentImage",
            "_meta": {
                "title": "Empty Latent Image"
            }
        },
        "6": {
            "inputs": {
                "text": "album cover placeholder",
                "clip": ["11", 0]
            },
            "class_type": "CLIPTextEncode",
            "_meta": {
                "title": "CLIP Text Encode (Prompt)"
            }
        },
        "8": {
            "inputs": {
                "samples": ["13", 0],
                "vae": ["10", 0]
            },
            "class_type": "VAEDecode",
            "_meta": {
                "title": "VAE Decode"
            }
        },
        "9": {
            "inputs": {
                "filename_prefix": "ComfyUI",
                "images": ["8", 0]
            },
            "class_type": "SaveImage",
            "_meta": {
                "title": "Save Image"
            }
        },
        "10": {
            "inputs": {
                "vae_name": "ae.safetensors"
            },
            "class_type": "VAELoader",
            "_meta": {
                "title": "Load VAE"
            }
        },
        "11": {
            "inputs": {
                "clip_name1": "t5xxl_fp8_e4m3fn.safetensors",
                "clip_name2": "clip_l.safetensors",
                "type": "flux"
            },
            "class_type": "DualCLIPLoader",
            "_meta": {
                "title": "DualCLIPLoader"
            }
        },
        "12": {
            "inputs": {
                "unet_name": "flux/flux1-schnell-Q5_K_S.gguf",
                "weight_dtype": "default"
            },
            "class_type": "UnetLoaderGGUF",
            "_meta": {
                "title": "Unet Loader (GGUF)"
            }
        },
        "13": {
            "inputs": {
                "noise": ["25", 0],
                "guider": ["22", 0],
                "sampler": ["16", 0],
                "sigmas": ["17", 0],
                "latent_image": ["5", 0]
            },
            "class_type": "SamplerCustomAdvanced",
            "_meta": {
                "title": "SamplerCustomAdvanced"
            }
        },
        "16": {
            "inputs": {
                "sampler_name": "euler"
            },
            "class_type": "KSamplerSelect",
            "_meta": {
                "title": "KSamplerSelect"
            }
        },
        "17": {
            "inputs": {
                "scheduler": "simple",
                "steps": 8,
                "denoise": 1,
                "model": ["12", 0]
            },
            "class_type": "BasicScheduler",
            "_meta": {
                "title": "BasicScheduler"
            }
        },
        "22": {
            "inputs": {
                "model": ["12", 0],
                "conditioning": ["6", 0]
            },
            "class_type": "BasicGuider",
            "_meta": {
                "title": "BasicGuider"
            }
        },
        "25": {
            "inputs": {
                "noise_seed": 42
            },
            "class_type": "RandomNoise",
            "_meta": {
                "title": "RandomNoise"
            }
        }
    }

def generate_ai_cover_swarmui(
    playlist_name: str,
    description: str = "",
    genres: List[str] = None,
    save_path: Optional[str] = None
) -> Optional[str]:
    """
    Genera copertina usando SwarmUI
    
    Args:
        playlist_name: Nome della playlist
        description: Descrizione della playlist
        genres: Lista dei generi musicali
        save_path: Percorso dove salvare l'immagine
        
    Returns:
        str: Percorso dell'immagine generata o None se fallisce
    """
    try:
        swarmui_url = os.getenv("SWARMUI_URL", "http://host.docker.internal:7801")
        
        # Crea client SwarmUI
        client = SwarmUIClient(swarmui_url)
        
        if not client.is_available():
            logger.warning("⚠️ SwarmUI non disponibile")
            return None
        
        # Genera prompt per SwarmUI
        use_gemini_prompts = os.getenv("USE_GEMINI_PROMPTS", "true").lower() == "true"
        
        if use_gemini_prompts and _is_gemini_available() and playlist_name:
            # Usa Gemini per generare un prompt creativo
            logger.info("🎨 Usando Gemini per generare prompt creativo")
            gemini_func = _get_gemini_prompt_function()
            if gemini_func:
                base_prompt = gemini_func(
                    playlist_name=playlist_name,
                    description=description or "",
                    genres=genres or [],
                    language='en'
                )
            else:
                logger.warning("Gemini non disponibile, usando prompt tradizionale")
                # Fallback al metodo tradizionale
                mood_prompt = get_mood_prompt_from_genres(genres, description) if genres else ""
                text_style = get_text_prompt_style(genres)
                title_text = text_style.format(playlist_name=playlist_name)
                base_prompt = f'{title_text}, album cover artwork with {mood_prompt}, professional layout'
        else:
            # Metodo tradizionale
            mood_prompt = get_mood_prompt_from_genres(genres, description) if genres else ""
            
            # Costruisce prompt base con testo all'inizio
            if playlist_name:
                # Sceglie la struttura del testo basata sul genere
                text_style = get_text_prompt_style(genres)
                title_text = text_style.format(playlist_name=playlist_name)
                base_prompt = f'{title_text}, album cover artwork with {mood_prompt}, professional layout'
            else:
                base_prompt = f'album cover, {mood_prompt}, professional design, clean layout'
        
        # Prompt negativo per migliorare la qualità (rimosso text artifacts perché vogliamo il testo)
        negative_prompt = "blurry, low quality, watermark, copyright, signature, username, bad composition, distorted, malformed, unreadable text"
        
        # Ottimizza per migliore qualità
        full_prompt = optimize_prompt_for_flux(base_prompt, genres or [])
        
        # Limita lunghezza del prompt per evitare nomi file troppo lunghi
        if len(full_prompt) > 120:
            full_prompt = full_prompt[:117] + "..."
        
        logger.info(f"🎨 Generando copertina SwarmUI per: {playlist_name}")
        logger.info(f"📝 Prompt: {full_prompt[:200]}...")
        
        # Parametri di generazione ottimizzati per Fluxmania Legacy
        generation_params = {
            "model": os.getenv("SWARMUI_MODEL", "Fluxmania_Legacy.safetensors"),
            "width": 1024,
            "height": 1024,
            "steps": int(os.getenv("SWARMUI_STEPS", "25")),
            "guidance": float(os.getenv("SWARMUI_GUIDANCE", "3.5")),  # Flux guidance per Flux.1 Dev
            "cfgscale": float(os.getenv("SWARMUI_CFG_SCALE", "3.5")),  # CFG Scale per Fluxmania Legacy
            "sampler": os.getenv("SWARMUI_SAMPLER", "dpmpp_2m"),
            "scheduler": os.getenv("SWARMUI_SCHEDULER", "sgm_uniform"),
            "seed": -1  # Random seed
        }
        
        # Genera immagine
        image_data = client.generate_image(full_prompt, negative_prompt, **generation_params)
        
        if not image_data:
            logger.error("❌ Generazione SwarmUI fallita")
            return None
        
        # Determina path di salvataggio
        if not save_path:
            temp_dir = tempfile.gettempdir()
            safe_name = "".join(c for c in playlist_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            save_path = os.path.join(temp_dir, f"playlist_cover_{safe_name}_swarmui.png")
        
        # Salva l'immagine
        with open(save_path, 'wb') as f:
            f.write(image_data)
        
        logger.info(f"✅ Copertina SwarmUI generata: {save_path}")
        return save_path
        
    except Exception as e:
        logger.error(f"❌ Errore generazione SwarmUI: {e}")
        return None

def generate_ai_cover_comfyui(
    playlist_name: str,
    description: str = "",
    genres: List[str] = None,
    save_path: Optional[str] = None
) -> Optional[str]:
    """
    Genera copertina usando ComfyUI esterno via API
    
    Args:
        playlist_name: Nome della playlist
        description: Descrizione della playlist
        genres: Lista dei generi musicali
        save_path: Percorso dove salvare l'immagine
        
    Returns:
        str: Percorso dell'immagine generata o None se fallisce
    """
    try:
        comfyui_url = os.getenv("COMFYUI_URL", "http://comfyui:8188")
        
        # Crea client ComfyUI
        client = ComfyUIClient(comfyui_url)
        
        if not client.is_available():
            logger.warning("⚠️ ComfyUI non disponibile")
            return None
        
        # Genera prompt per ComfyUI
        mood_prompt = get_mood_prompt_from_genres(genres, description) if genres else ""
        
        # Costruisce prompt base
        base_prompt = f'album cover for "{playlist_name}". {mood_prompt}. Bold title text "{playlist_name}". No copyrighted content. Instagram-ready design.'
        
        # Ottimizza per Flux
        full_prompt = optimize_prompt_for_flux(base_prompt, genres or [])
        
        logger.info(f"🎨 Generando copertina ComfyUI per: {playlist_name}")
        logger.info(f"📝 Prompt: {full_prompt[:200]}...")
        
        # Carica workflow
        workflow = load_comfyui_workflow()
        
        if not workflow:
            logger.error("❌ Impossibile caricare workflow ComfyUI")
            return None
        
        # Genera immagine
        image_data = client.generate_cover(full_prompt, workflow)
        
        if not image_data:
            logger.error("❌ Generazione ComfyUI fallita")
            return None
        
        # Determina path di salvataggio
        if not save_path:
            temp_dir = tempfile.gettempdir()
            safe_name = "".join(c for c in playlist_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            save_path = os.path.join(temp_dir, f"playlist_cover_{safe_name}_comfyui.png")
        
        # Salva l'immagine
        with open(save_path, 'wb') as f:
            f.write(image_data)
        
        logger.info(f"✅ Copertina ComfyUI generata: {save_path}")
        return save_path
        
    except Exception as e:
        logger.error(f"❌ Errore generazione ComfyUI: {e}")
        return None

def generate_ai_cover_local(
    playlist_name: str,
    description: str = "",
    genres: List[str] = None,
    save_path: Optional[str] = None
) -> Optional[str]:
    """
    Genera copertina AI (supporta SwarmUI e ComfyUI)
    """
    try:
        # Rileva capacità del sistema
        capability = detect_gpu_capabilities()
        
        if capability == "swarmui":
            # Usa SwarmUI (preferito)
            return generate_ai_cover_swarmui(playlist_name, description, genres, save_path)
        elif capability == "comfyui":
            # Usa ComfyUI come fallback
            return generate_ai_cover_comfyui(playlist_name, description, genres, save_path)
        else:
            # Nessun AI disponibile - fallback a copertine semplici
            logger.info("🔄 Fallback copertina semplice - AI non disponibile")
            return None
        
    except Exception as e:
        logger.warning(f"⚠️ Errore generazione AI: {e}")
        return None

# ===== STUB FUNCTIONS per compatibilità con codice esistente =====

def generate_playlist_cover_ai(playlist_name: str, description: str = "", genres: List[str] = None, save_path: Optional[str] = None) -> Optional[str]:
    """
    Wrapper per generate_ai_cover_local per compatibilità
    """
    return generate_ai_cover_local(playlist_name, description, genres, save_path)

def extract_genres_from_playlist_data(playlist_data: Dict) -> List[str]:
    """
    Estrae i generi dai dati della playlist
    """
    genres = []
    
    # Estrai generi dalla tracks
    tracks = playlist_data.get('tracks', [])
    for track in tracks:
        if isinstance(track, dict):
            track_genres = track.get('genres', [])
            if isinstance(track_genres, list):
                genres.extend(track_genres)
    
    # Rimuovi duplicati e limita a 5 generi più comuni
    unique_genres = list(set(genres))
    return unique_genres[:5]

def is_cover_generation_enabled() -> bool:
    """
    Verifica se la generazione copertine è abilitata
    """
    return os.getenv("ENABLE_PLAYLIST_COVERS", "1") == "1"

def test_cover_generation() -> bool:
    """
    Testa la funzionalità di generazione copertine con sistema semplificato
    """
    logger.info("🧪 Test generazione copertina playlist...")
    
    try:
        # Test AI cover generation prima
        capability = detect_gpu_capabilities()
        
        if capability == "swarmui":
            logger.info("🧪 Test SwarmUI...")
            test_path = generate_ai_cover_swarmui(
                playlist_name="Test SwarmUI",
                description="Test playlist for SwarmUI generation",
                genres=["electronic", "chill"]
            )
            
            if test_path and os.path.exists(test_path):
                logger.info(f"✅ Test SwarmUI completato: {test_path}")
                try:
                    os.remove(test_path)
                    logger.debug("🧹 File test SwarmUI rimosso")
                except:
                    pass
                return True
        
        elif capability == "comfyui":
            logger.info("🧪 Test ComfyUI...")
            test_path = generate_ai_cover_comfyui(
                playlist_name="Test ComfyUI",
                description="Test playlist for ComfyUI generation",
                genres=["electronic", "chill"]
            )
            
            if test_path and os.path.exists(test_path):
                logger.info(f"✅ Test ComfyUI completato: {test_path}")
                try:
                    os.remove(test_path)
                    logger.debug("🧹 File test ComfyUI rimosso")
                except:
                    pass
                return True
        
        # Fallback a test copertine semplici
        logger.info("🧪 Test copertine semplici...")
        test_path = generate_simple_cover_fallback(
            playlist_name="Test Playlist",
            genres=["electronic", "chill"]
        )
        
        if test_path and os.path.exists(test_path):
            logger.info(f"✅ Test copertina completato con successo: {test_path}")
            # Cleanup del file test
            try:
                os.remove(test_path)
                logger.debug("🧹 File test rimosso")
            except:
                pass
            return True
        else:
            logger.error("❌ Test copertina fallito")
            return False
            
    except Exception as e:
        logger.error(f"❌ Errore nel test copertina: {e}")
        return False