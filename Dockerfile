# syntax=docker/dockerfile:1
FROM python:3.11-slim as base

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
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libmagic1 gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- Dependencies ----
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -r requirements.txt

# ---- Application ----
COPY . .

RUN mkdir -p /app/state_data /app/logs

# Copy entrypoint
COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
