#!/bin/bash
set -e

USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

# Create group if needed
if ! getent group "$GROUP_ID" >/dev/null; then
    groupadd -g "$GROUP_ID" plexgrp
fi

# Create user if needed
if ! id -u "$USER_ID" >/dev/null 2>&1; then
    useradd -u "$USER_ID" -g "$GROUP_ID" -M plexusr
fi

mkdir -p /app/state_data /app/logs /app/Downloads

# Create streamrip directory structure
mkdir -p /app/state_data/.local/share/streamrip
mkdir -p /app/state_data/.cache/streamrip

# Copy config.toml to writable directory if it exists
if [ -f "/app/config.toml" ]; then
    cp /app/config.toml /app/state_data/config.toml
    echo "Copied config.toml to /app/state_data/"
fi

chown -R "$USER_ID":"$GROUP_ID" /app/state_data /app/logs /app/Downloads

exec gosu "$USER_ID":"$GROUP_ID" python app.py
