#!/bin/bash
set -e

USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

# Create group if needed
if ! getent group "$GROUP_ID" >/dev/null; then
    groupadd -g "$GROUP_ID" plexgrp
fi

# Create user if needed, suppress warnings for low UIDs (common in Unraid)
if ! id -u "$USER_ID" >/dev/null 2>&1; then
    # For UIDs below 1000 (like Unraid's 99), add --system flag to suppress warnings
    if [ "$USER_ID" -lt 1000 ]; then
        useradd -u "$USER_ID" -g "$GROUP_ID" -M --system plexusr 2>/dev/null || \
        useradd -u "$USER_ID" -g "$GROUP_ID" -M plexusr 2>/dev/null
    else
        useradd -u "$USER_ID" -g "$GROUP_ID" -M plexusr
    fi
fi

mkdir -p /app/state_data /app/logs

# Create streamrip directory structure
mkdir -p /app/state_data/.local/share/streamrip
mkdir -p /app/state_data/.cache/streamrip

# Copy config.toml to writable directory and substitute environment variables
if [ -f "/app/config.toml" ]; then
    # Get the download path from environment variable, default to /downloads
    DOWNLOAD_PATH="${MUSIC_DOWNLOAD_PATH:-/downloads}"
    
    # Copy and substitute the placeholder with actual path
    sed "s#{MUSIC_DOWNLOAD_PATH}#${DOWNLOAD_PATH}#g" /app/config.toml > /app/state_data/config.toml
    echo "Copied config.toml to /app/state_data/ with MUSIC_DOWNLOAD_PATH=${DOWNLOAD_PATH}"
fi

chown -R "$USER_ID":"$GROUP_ID" /app/state_data /app/logs

# Fix downloads directory permissions if it exists
if [ -d "/downloads" ]; then
    echo "🔧 Fixing /downloads directory permissions..."
    
    # Check if this is Windows host (Docker Desktop on Windows)
    if [ "$DOCKER_HOST_OS" = "windows" ]; then
        echo "🪟 Windows host detected - using Windows-compatible permissions"
        # On Windows, just ensure directory is writable without changing ownership
        chmod 777 /downloads
        echo "✅ Downloads directory made world-writable for Windows compatibility"
    else
        echo "🐧 Linux host detected - using standard permissions"
        # Only fix ownership of the root directory and make it writable
        chown "$USER_ID":"$GROUP_ID" /downloads
        chmod 755 /downloads
        echo "✅ Downloads directory permissions fixed (root level only)"
    fi
fi

exec gosu "$USER_ID":"$GROUP_ID" python app.py
