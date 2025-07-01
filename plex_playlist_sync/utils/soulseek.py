# soulseek.py
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# --- Config -----------------------------
USE_SOULSEEK = os.getenv("USE_SOULSEEK", "0") == "1"
SLSKD_URL = os.getenv("SLSKD_URL", "http://localhost:5030").rstrip("/")
SLSKD_TOKEN = os.getenv("SLSKD_TOKEN", "")

# --- Helpers ----------------------------
def _raise_for_status(resp: requests.Response) -> None:
    """Wrap raise_for_status so we can log e abbassare la copertura nei test."""
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:      # pragma: no cover
        logger.error("Soulseek request failed: %s", exc)
        raise


# --- Client OO --------------------------
class SoulseekClient:
    """High-level wrapper around slskd REST API."""

    def __init__(
        self,
        base_url: str = SLSKD_URL,
        token: str | None = SLSKD_TOKEN,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.enabled = USE_SOULSEEK and bool(base_url)
        self.base_url = base_url
        self.session = session or requests.Session()
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})

    # ---------- raw HTTP ----------
    def _post(self, path: str, data: dict | list) -> requests.Response:
        resp = self.session.post(f"{self.base_url}{path}", json=data, timeout=10)
        _raise_for_status(resp)
        return resp

    def _get(self, path: str) -> requests.Response:
        resp = self.session.get(f"{self.base_url}{path}", timeout=10)
        _raise_for_status(resp)
        return resp

    # ---------- public API ----------
    def search(self, query: str) -> str | None:
        try:
            r = self._post("/api/v0/searches", {"searchText": query})
            return r.json().get("id")
        except Exception as e:
            logger.warning("Soulseek search failed for %s: %s", query, e)
            return None

    def get_search_responses(self, search_id: str) -> List[Dict[str, Any]]:
        try:
            r = self._get(f"/api/v0/searches/{search_id}/responses")
            return r.json()
        except Exception as e:
            logger.warning("Failed to fetch responses %s: %s", search_id, e)
            return []

    def queue_download(self, username: str, filename: str, size: int) -> bool:
        try:
            self._post(
                f"/api/v0/transfers/downloads/{username}",
                [{"Filename": filename, "Size": size}],
            )
            return True
        except Exception as e:
            logger.error("Queue download failed (%s): %s", filename, e)
            return False

    def get_queue_position(self, username: str, filename: str) -> Optional[int]:
        download_id = hashlib.sha1(filename.encode()).hexdigest()
        try:
            r = self._get(
                f"/api/v0/transfers/downloads/{username}/{download_id}/position"
            )
            return r.json()
        except requests.HTTPError as e:
            if e.response.status_code == 404:  # not queued anymore
                return None
            raise
        except Exception as e:
            logger.warning("Queue pos error %s: %s", filename, e)
            return None

    # ---------- convenience high-level ----------
    def search_and_download(self, artist: str, title: str) -> bool:
        if not self.enabled:
            return False

        query = f"{artist} - {title}"
        search_id = self.search(query)
        if not search_id:
            return False

        time.sleep(2)  # let slskd gather results
        responses = self.get_search_responses(search_id)
        if not responses:
            logger.info("No Soulseek results for '%s'", query)
            return False

        # pick best response
        best = min(
            (
                r
                for r in responses
                if r.get("files") and r.get("queueLength") is not None
            ),
            key=lambda r: (not r.get("hasFreeUploadSlot"), r["queueLength"]),
            default=None,
        )
        if best is None:
            return False

        file = best["files"][0]
        if not self.queue_download(best["username"], file["filename"], file.get("size", 0)):
            return False

        # poll queue position
        wait = 10
        for _ in range(5):
            pos = self.get_queue_position(best["username"], file["filename"])
            if pos is None or pos == 0:
                logger.info("Download started for '%s'", file["filename"])
                return True
            logger.info("Still queued (%s) – retry in %ss", pos, wait)
            time.sleep(wait)
            wait *= 2
        logger.warning("Download still queued after retries")
        return False


# --- Thin functional façade (test-friendly) ----------
_default_client = SoulseekClient()  # singleton, lazily configured

def search(query: str) -> Dict[str, Any]:
    """Functional wrapper used by legacy code and unit-tests."""
    sid = _default_client.search(query)
    if not sid:
        return {}
    time.sleep(1)
    res = _default_client.get_search_responses(sid)
    return {"search_id": sid, "responses": res}

def queue_download(file_id: str) -> str:
    # kept for backward-compat – maps to new client
    return _default_client.queue_download("dummy", file_id, 0)  # adjust if needed

def download_with_retry(file_id: str, retries: int = 3, delay: float = 1.0) -> bool:
    for _ in range(retries):
        ok = _default_client.queue_download("dummy", file_id, 0)
        if ok:
            return True
        time.sleep(delay)
    return False
