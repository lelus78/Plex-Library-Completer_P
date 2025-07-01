# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

# ---- Metadata ----
LABEL org.opencontainers.image.source="https://github.com/lelus78/Plex-Library-Completer_P" \
      org.opencontainers.image.licenses="GPL-3.0" \
      org.opencontainers.image.authors="Lelus78" \
      org.opencontainers.image.title="Plex Library Completer" \
      org.opencontainers.image.description="Syncs Plex music playlists, generates stats, and completes missing tracks via Docker."

# ---- Runtime settings ----
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ---- System prep ----
RUN useradd -m -u 1000 app
WORKDIR /app

# ---- Dependencies ----
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -r requirements.txt

# ---- Application ----
COPY . .

# Create directories with correct permissions
RUN mkdir -p /app/state_data /app/logs && \
    chown -R app:app /app && \
    chmod -R 755 /app

# Create entrypoint script for runtime permission fix
RUN echo '#!/bin/bash\n\
mkdir -p /app/state_data /app/logs\n\
# Try to set permissions but ignore errors if directories are owned by root\n\
chmod 755 /app/state_data /app/logs 2>/dev/null || true\n\
python app.py' > /app/entrypoint.sh && \
    chmod +x /app/entrypoint.sh && \
    chown app:app /app/entrypoint.sh

USER app
EXPOSE 5000
ENTRYPOINT ["/app/entrypoint.sh"]