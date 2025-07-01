import os
import logging
import requests

USE_SOULSEEK = os.getenv("USE_SOULSEEK", "false").lower() == "true"
SLSKD_URL = os.getenv("SLSKD_URL", "http://localhost:5030")
SLSKD_TOKEN = os.getenv("SLSKD_TOKEN")

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    return USE_SOULSEEK and bool(SLSKD_TOKEN)


def queue_search(query: str) -> bool:
    """Send a search request to slskd."""
    if not is_enabled():
        logger.debug("Soulseek integration disabled or missing token")
        return False

    try:
        headers = {"Authorization": f"Bearer {SLSKD_TOKEN}"}
        payload = {"searchText": query}
        resp = requests.post(f"{SLSKD_URL}/api/v0/searches", json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        logger.info(f"Soulseek search queued for '{query}'")
        return True
    except Exception as e:
        logger.error(f"Soulseek request failed for '{query}': {e}")
        return False
