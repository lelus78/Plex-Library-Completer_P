#!/usr/bin/env python3
"""
Debug script to check download paths and Docker volume mounting.
This script helps identify why downloads aren't appearing in the host directory.
"""

import os
import logging
import subprocess
import time

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def check_environment():
    """Check environment variables and paths"""
    logging.info("=== ENVIRONMENT VARIABLES ===")
    
    env_vars = [
        'MUSIC_DOWNLOAD_PATH',
        'DEEZER_ARL',
        'PUID',
        'PGID',
        'HOME',
        'XDG_DATA_HOME',
        'XDG_CACHE_HOME'
    ]
    
    for var in env_vars:
        value = os.getenv(var, 'NOT SET')
        logging.info(f"{var}: {value}")

def check_directories():
    """Check if directories exist and their permissions"""
    logging.info("\n=== DIRECTORY CHECKS ===")
    
    directories = [
        '/downloads',
        '/app/state_data',
        '/app/logs',
        '/root/.config/streamrip',
        '/app/state_data/.config/streamrip',
        '/app/state_data/.local/share/streamrip'
    ]
    
    for directory in directories:
        if os.path.exists(directory):
            try:
                stat_info = os.stat(directory)
                writable = os.access(directory, os.W_OK)
                logging.info(f"✅ {directory} - permissions: {oct(stat_info.st_mode)[-3:]} (owner: {stat_info.st_uid}, group: {stat_info.st_gid}) - writable: {writable}")
            except Exception as e:
                logging.error(f"❌ {directory} - error getting info: {e}")
        else:
            logging.warning(f"⚠️ {directory} - NOT FOUND")

def check_config_files():
    """Check streamrip configuration files"""
    logging.info("\n=== CONFIG FILE CHECKS ===")
    
    config_files = [
        '/root/.config/streamrip/config.toml',
        '/app/state_data/config.toml',
        '/app/config.toml',
        '/app/state_data/.config/streamrip/config.toml'
    ]
    
    for config_file in config_files:
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    content = f.read()
                    # Extract folder setting
                    import re
                    folder_match = re.search(r'folder\s*=\s*["\']([^"\']+)["\']', content)
                    if folder_match:
                        folder_path = folder_match.group(1)
                        logging.info(f"✅ {config_file} - folder: {folder_path}")
                    else:
                        logging.warning(f"⚠️ {config_file} - no folder setting found")
            except Exception as e:
                logging.error(f"❌ {config_file} - error reading: {e}")
        else:
            logging.info(f"❌ {config_file} - NOT FOUND")

def test_write_permissions():
    """Test if we can write to the downloads directory"""
    logging.info("\n=== WRITE PERMISSION TESTS ===")
    
    downloads_dir = os.getenv("MUSIC_DOWNLOAD_PATH", "/downloads")
    
    test_file = os.path.join(downloads_dir, f"test_write_{int(time.time())}.txt")
    
    try:
        # Test writing a file
        with open(test_file, 'w') as f:
            f.write("Test file to verify write permissions")
        
        logging.info(f"✅ Successfully created test file: {test_file}")
        
        # Test reading it back
        with open(test_file, 'r') as f:
            content = f.read()
        
        logging.info(f"✅ Successfully read test file content: {content[:50]}...")
        
        # Clean up
        os.remove(test_file)
        logging.info(f"✅ Successfully removed test file")
        
        return True
        
    except Exception as e:
        logging.error(f"❌ Failed to write/read test file: {e}")
        return False

def check_docker_mounts():
    """Check Docker volume mounts"""
    logging.info("\n=== DOCKER MOUNT CHECKS ===")
    
    try:
        # Check if we can see mount information
        result = subprocess.run(['mount'], capture_output=True, text=True)
        
        if result.returncode == 0:
            mount_lines = result.stdout.split('\n')
            downloads_mounts = [line for line in mount_lines if '/downloads' in line]
            
            if downloads_mounts:
                logging.info("✅ Found /downloads mounts:")
                for mount in downloads_mounts:
                    logging.info(f"  {mount}")
            else:
                logging.warning("⚠️ No /downloads mounts found")
        else:
            logging.error(f"❌ Failed to run mount command: {result.stderr}")
            
    except Exception as e:
        logging.error(f"❌ Error checking mounts: {e}")

def main():
    """Main function to run all checks"""
    logging.info("🔧 Starting download path debugging...")
    
    check_environment()
    check_directories()
    check_config_files()
    write_success = test_write_permissions()
    check_docker_mounts()
    
    logging.info("\n=== SUMMARY ===")
    if write_success:
        logging.info("✅ Downloads directory is writable - Docker volume mounting is working")
    else:
        logging.error("❌ Downloads directory is NOT writable - Docker volume mounting issue")
        
    logging.info("🔧 Debug complete")

if __name__ == "__main__":
    main()