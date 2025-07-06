# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

LABEL org.opencontainers.image.source="https://github.com/lelus78/Plex-Library-Completer_P" \
      org.opencontainers.image.licenses="GPL-3.0" \
      org.opencontainers.image.authors="Lelus78" \
      org.opencontainers.image.title="Plex Library Completer" \
      org.opencontainers.image.description="Syncs Plex music playlists, generates stats, and completes missing tracks via Docker."

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ---- System packages ----
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libmagic1 gosu wget curl unzip gnupg \
    && wget -qO- https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- Python deps from requirements.txt ----
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
 && pip install -r requirements.txt

# ---- EXTRA: spotifyscraper con supporto selenium ----
RUN pip install "spotifyscraper[selenium]"

# ---- (facoltativo) verifica che sia installato ----
RUN echo '📦 spotifyscraper version:' \
 && pip show spotifyscraper || (echo '❌  spotifyscraper STILL missing!' && exit 1)

# ---- Application code ----
COPY . .

RUN mkdir -p /app/state_data /app/logs

COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
