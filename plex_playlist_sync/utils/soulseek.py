import os
import time
import logging
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

SLSKD_URL = os.getenv("SLSKD_URL", "http://localhost:5030")


def _raise_for_status(response: requests.Response) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:  # pragma: no cover - network errors
        logger.error("Soulseek request failed: %s", exc)
        raise


def search(query: str) -> Dict[str, Any]:
    """Search files on slskd."""
    resp = requests.get(f"{SLSKD_URL}/search", params={"q": query}, timeout=5)
    _raise_for_status(resp)
    return resp.json()


def queue_download(file_id: str) -> str:
    """Queue a file for download and return download id."""
    resp = requests.post(f"{SLSKD_URL}/queue", json={"file_id": file_id}, timeout=5)
    _raise_for_status(resp)
    data = resp.json()
    return data.get("download_id", "")


def download_with_retry(file_id: str, retries: int = 3, delay: float = 1.0) -> bool:
    """Queue download and poll status until complete or failed."""
    download_id = queue_download(file_id)
    for _ in range(retries):
        status_resp = requests.get(f"{SLSKD_URL}/status/{download_id}", timeout=5)
        _raise_for_status(status_resp)
        status = status_resp.json().get("status")
        if status == "queued":
            time.sleep(delay)
            continue
        return status == "complete"
    return False
