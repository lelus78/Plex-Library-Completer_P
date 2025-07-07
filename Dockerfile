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
    && rm -rf /var/lib/apt/lists/*

# ---- Install Google Chrome (AMD64 only) ----
RUN ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "amd64" ]; then \
        echo "Installing Google Chrome for AMD64..." \
        && wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor > /etc/apt/trusted.gpg.d/google-chrome.gpg \
        && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
        && apt-get update \
        && apt-get install -y google-chrome-stable \
        && rm -rf /var/lib/apt/lists/*; \
    else \
        echo "Skipping Google Chrome installation for $ARCH architecture (not supported)"; \
    fi

WORKDIR /app

# ---- Python deps from requirements.txt ----
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
 && pip install -r requirements.txt

# ---- EXTRA: spotifyscraper con supporto selenium (solo su AMD64) ----
RUN ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "amd64" ]; then \
        echo "Installing SpotifyScraper with selenium support for AMD64..." \
        && pip install "selenium==4.20.0" "webdriver-manager==4.0.1" "spotifyscraper[selenium]" \
        && echo '📦 spotifyscraper version:' \
        && pip show spotifyscraper; \
    else \
        echo "Installing SpotifyScraper without selenium for $ARCH architecture..." \
        && pip install "spotifyscraper" \
        && echo '📦 spotifyscraper version (no selenium):' \
        && pip show spotifyscraper; \
    fi

# ---- Application code ----
COPY . .

RUN mkdir -p /app/state_data /app/logs

COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
