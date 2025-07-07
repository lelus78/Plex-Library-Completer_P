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

# Copy config.toml to writable directory if it exists
if [ -f "/app/config.toml" ]; then
    cp /app/config.toml /app/state_data/config.toml
    echo "Copied config.toml to /app/state_data/"
fi

chown -R "$USER_ID":"$GROUP_ID" /app/state_data /app/logs

# Fix downloads directory permissions if it exists
if [ -d "/downloads" ]; then
    echo "🔧 Fixing /downloads directory permissions..."
    # Only fix ownership of the root directory and make it writable
    chown "$USER_ID":"$GROUP_ID" /downloads
    chmod 755 /downloads
    echo "✅ Downloads directory permissions fixed (root level only)"
fi

exec gosu "$USER_ID":"$GROUP_ID" python app.py
