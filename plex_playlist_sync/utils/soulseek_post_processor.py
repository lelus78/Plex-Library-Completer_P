# soulseek_post_processor.py
"""
Post-processing system for Soulseek downloads.
Reorganizes downloaded files from slskd format to match Deezer structure.
"""

import os
import logging
import shutil
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import mutagen
from mutagen.id3 import ID3NoHeaderError

logger = logging.getLogger(__name__)

class SoulseekPostProcessor:
    """Post-processor for organizing Soulseek downloads into standardized format."""
    
    def __init__(self):
        self.source_path = os.getenv("SOULSEEK_DOWNLOADS_PATH", "E:\\Docker image\\slskd\\downloads\\")
        self.target_path = os.getenv("SOULSEEK_ORGANIZED_PATH", "M:\\Organizzata\\")
        
        # Ensure paths end with separator
        if not self.source_path.endswith(('\\', '/')):
            self.source_path += '\\'
        if not self.target_path.endswith(('\\', '/')):
            self.target_path += '\\'
    
    def extract_metadata_from_file(self, file_path: str) -> Dict[str, str]:
        """Extract metadata from audio file using mutagen."""
        try:
            audio_file = mutagen.File(file_path)
            if audio_file is None:
                return self._fallback_metadata_extraction(file_path)
            
            metadata = {
                'artist': '',
                'album': '',
                'title': '',
                'track_number': ''
            }
            
            # Handle different tag formats
            if hasattr(audio_file, 'tags') and audio_file.tags:
                tags = audio_file.tags
                
                # Try ID3 tags first
                if 'TPE1' in tags:  # Artist
                    metadata['artist'] = str(tags['TPE1'][0])
                elif 'ARTIST' in tags:
                    metadata['artist'] = str(tags['ARTIST'][0])
                
                if 'TALB' in tags:  # Album
                    metadata['album'] = str(tags['TALB'][0])
                elif 'ALBUM' in tags:
                    metadata['album'] = str(tags['ALBUM'][0])
                
                if 'TIT2' in tags:  # Title
                    metadata['title'] = str(tags['TIT2'][0])
                elif 'TITLE' in tags:
                    metadata['title'] = str(tags['TITLE'][0])
                
                if 'TRCK' in tags:  # Track number
                    track = str(tags['TRCK'][0])
                    # Extract just the number part (before any slash)
                    metadata['track_number'] = track.split('/')[0].zfill(2)
                elif 'TRACKNUMBER' in tags:
                    track = str(tags['TRACKNUMBER'][0])
                    metadata['track_number'] = track.split('/')[0].zfill(2)
            
            # If metadata is still empty, try fallback
            if not any(metadata.values()):
                metadata = self._fallback_metadata_extraction(file_path)
            
            return metadata
            
        except Exception as e:
            logger.warning(f"Error extracting metadata from {file_path}: {e}")
            return self._fallback_metadata_extraction(file_path)
    
    def _fallback_metadata_extraction(self, file_path: str) -> Dict[str, str]:
        """Extract metadata from folder structure and filename."""
        try:
            path_obj = Path(file_path)
            filename = path_obj.stem
            parent_folder = path_obj.parent.name
            
            metadata = {
                'artist': '',
                'album': '',
                'title': '',
                'track_number': ''
            }
            
            # Try to parse folder name: "Artist - Album" format
            if ' - ' in parent_folder:
                parts = parent_folder.split(' - ', 1)
                metadata['artist'] = parts[0].strip()
                metadata['album'] = parts[1].strip()
            else:
                # Use folder name as album
                metadata['album'] = parent_folder
            
            # Try to parse filename: "01 - Track Title.ext" or "Artist - Title.ext"
            track_patterns = [
                r'^(\d+)\s*[-\.\s]+(.+)$',  # "01 - Title" or "01. Title"
                r'^(.+?)\s*-\s*(.+)$',     # "Artist - Title"
                r'^(.+)$'                   # Just title
            ]
            
            for pattern in track_patterns:
                match = re.match(pattern, filename)
                if match:
                    if len(match.groups()) == 2:
                        if match.group(1).isdigit():
                            # Pattern with track number
                            metadata['track_number'] = match.group(1).zfill(2)
                            metadata['title'] = match.group(2).strip()
                        else:
                            # Pattern with artist - title
                            if not metadata['artist']:
                                metadata['artist'] = match.group(1).strip()
                            metadata['title'] = match.group(2).strip()
                    else:
                        # Just title
                        metadata['title'] = match.group(1).strip()
                    break
            
            return metadata
            
        except Exception as e:
            logger.error(f"Fallback metadata extraction failed for {file_path}: {e}")
            return {'artist': 'Unknown Artist', 'album': 'Unknown Album', 'title': 'Unknown Title', 'track_number': '01'}
    
    def sanitize_filename(self, name: str) -> str:
        """Sanitize filename for Windows compatibility."""
        # Remove or replace invalid Windows filename characters
        invalid_chars = r'[<>:"/\\|?*]'
        name = re.sub(invalid_chars, '_', name)
        
        # Remove leading/trailing spaces and dots
        name = name.strip(' .')
        
        # Limit length
        if len(name) > 200:
            name = name[:200]
        
        return name
    
    def process_downloaded_files(self) -> List[Dict[str, str]]:
        """Process all downloaded files in the source directory."""
        if not os.path.exists(self.source_path):
            logger.error(f"Source path does not exist: {self.source_path}")
            return []
        
        processed_files = []
        audio_extensions = {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac'}
        
        try:
            # Walk through all subdirectories
            for root, dirs, files in os.walk(self.source_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_ext = Path(file_path).suffix.lower()
                    
                    if file_ext in audio_extensions:
                        try:
                            result = self.process_single_file(file_path)
                            if result:
                                processed_files.append(result)
                        except Exception as e:
                            logger.error(f"Error processing file {file_path}: {e}")
                            continue
            
            logger.info(f"Processed {len(processed_files)} files")
            return processed_files
            
        except Exception as e:
            logger.error(f"Error during file processing: {e}")
            return processed_files
    
    def process_single_file(self, source_file_path: str) -> Optional[Dict[str, str]]:
        """Process a single audio file."""
        try:
            # Extract metadata
            metadata = self.extract_metadata_from_file(source_file_path)
            
            # Sanitize metadata for filesystem
            artist = self.sanitize_filename(metadata['artist']) or 'Unknown Artist'
            album = self.sanitize_filename(metadata['album']) or 'Unknown Album'
            title = self.sanitize_filename(metadata['title']) or 'Unknown Title'
            track_num = metadata['track_number'] or '01'
            
            # Get file extension
            file_ext = Path(source_file_path).suffix
            
            # Build target path: M:\Organizzata\Artist\Album\TrackNum - Title.ext
            target_filename = f"{track_num} - {title}{file_ext}"
            target_dir = os.path.join(self.target_path, artist, album)
            target_file_path = os.path.join(target_dir, target_filename)
            
            # Create target directory if it doesn't exist
            os.makedirs(target_dir, exist_ok=True)
            
            # Check if file already exists
            if os.path.exists(target_file_path):
                logger.info(f"File already exists, skipping: {target_file_path}")
                return {
                    'source': source_file_path,
                    'target': target_file_path,
                    'status': 'skipped',
                    'artist': artist,
                    'album': album,
                    'title': title
                }
            
            # Copy file to target location
            shutil.copy2(source_file_path, target_file_path)
            logger.info(f"Organized file: {artist} - {album} - {title}")
            
            return {
                'source': source_file_path,
                'target': target_file_path,
                'status': 'processed',
                'artist': artist,
                'album': album,
                'title': title
            }
            
        except Exception as e:
            logger.error(f"Error processing single file {source_file_path}: {e}")
            return None
    
    def cleanup_empty_directories(self):
        """Remove empty directories from source path after processing."""
        try:
            for root, dirs, files in os.walk(self.source_path, topdown=False):
                # Skip the root directory itself
                if root == self.source_path.rstrip('\\').rstrip('/'):
                    continue
                
                try:
                    # Try to remove directory if it's empty
                    if not os.listdir(root):
                        os.rmdir(root)
                        logger.info(f"Removed empty directory: {root}")
                except OSError:
                    # Directory not empty or other error
                    pass
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


def process_soulseek_downloads() -> List[Dict[str, str]]:
    """Main function to process Soulseek downloads."""
    processor = SoulseekPostProcessor()
    processed_files = processor.process_downloaded_files()
    
    if processed_files:
        # Cleanup empty directories after processing
        processor.cleanup_empty_directories()
        
        logger.info(f"Soulseek post-processing completed. Processed {len(processed_files)} files.")
    else:
        logger.info("No files found to process in Soulseek downloads directory.")
    
    return processed_files


if __name__ == "__main__":
    # Test the processor
    logging.basicConfig(level=logging.INFO)
    process_soulseek_downloads()