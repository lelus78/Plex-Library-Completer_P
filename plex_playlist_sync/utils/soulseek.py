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
SLSKD_USERNAME = os.getenv("SLSKD_USERNAME", "")
SLSKD_PASSWORD = os.getenv("SLSKD_PASSWORD", "")

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
        username: str | None = SLSKD_USERNAME,
        password: str | None = SLSKD_PASSWORD,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.enabled = USE_SOULSEEK and bool(base_url)
        self.base_url = base_url
        self.session = session or requests.Session()
        # Prioritize API token over username/password
        if token:
            # Use both Authorization Bearer and X-API-Key for maximum compatibility
            self.session.headers.update({
                "Authorization": f"Bearer {token}",
                "X-API-Key": token
            })
        elif username and password:
            import base64
            credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
            self.session.headers.update({"Authorization": f"Basic {credentials}"})

    # ---------- raw HTTP ----------
    def _post(self, path: str, data: dict | list) -> requests.Response:
        resp = self.session.post(f"{self.base_url}{path}", json=data, timeout=120)
        _raise_for_status(resp)
        return resp

    def _get(self, path: str) -> requests.Response:
        resp = self.session.get(f"{self.base_url}{path}", timeout=120)
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
            responses = r.json()
            logger.debug(f"Search {search_id} returned {len(responses)} responses")
            if responses:
                # Log first response structure for debugging
                first_response = responses[0]
                logger.debug(f"First response structure: username={first_response.get('username')}, files_count={len(first_response.get('files', []))}")
            return responses
        except Exception as e:
            logger.warning("Failed to fetch responses %s: %s", search_id, e)
            return []

    def get_search_status(self, search_id: str) -> Dict[str, Any]:
        """Get search status and metadata"""
        try:
            r = self._get(f"/api/v0/searches/{search_id}")
            return r.json()
        except Exception as e:
            logger.warning("Failed to fetch search status %s: %s", search_id, e)
            return {}

    def wait_for_search_completion(self, search_id: str, max_wait_time: int = 120, check_interval: int = 3) -> List[Dict[str, Any]]:
        """Wait for search completion with intelligent polling"""
        logger.info(f"Waiting for search {search_id} to complete (max {max_wait_time}s)...")
        
        start_time = time.time()
        last_response_count = 0
        stable_count = 0
        
        while time.time() - start_time < max_wait_time:
            # Get current responses
            responses = self.get_search_responses(search_id)
            current_count = len(responses)
            
            # Get search status if available
            status = self.get_search_status(search_id)
            search_state = status.get("state", "Unknown")
            
            logger.debug(f"Search {search_id}: {current_count} responses, state: {search_state}")
            
            # Check if search is explicitly complete
            if search_state in ["Completed", "Complete", "TimedOut", "Cancelled"]:
                logger.info(f"Search {search_id} completed with state '{search_state}', {current_count} responses")
                return responses
            
            # Check if we have results and they're stable
            if current_count > 0:
                if current_count == last_response_count:
                    stable_count += 1
                    # If results are stable for 3 checks (9 seconds), consider search complete
                    if stable_count >= 3:
                        logger.info(f"Search {search_id} stabilized at {current_count} responses after {time.time() - start_time:.1f}s")
                        return responses
                else:
                    stable_count = 0  # Reset if count changed
                    logger.debug(f"Search {search_id}: responses increased from {last_response_count} to {current_count}")
            
            last_response_count = current_count
            time.sleep(check_interval)
        
        # Timeout reached
        final_responses = self.get_search_responses(search_id)
        logger.info(f"Search {search_id} timed out after {max_wait_time}s with {len(final_responses)} responses")
        return final_responses

    def queue_download(self, username: str, filename: str, size: int) -> bool:
        import urllib.parse
        
        try:
            # Clean and encode username for URL
            encoded_username = urllib.parse.quote(username, safe='')
            clean_filename = filename.strip().encode('utf-8', errors='ignore').decode('utf-8')
            
            self._post(
                f"/api/v0/transfers/downloads/{encoded_username}",
                [{"Filename": clean_filename, "Size": size}],
            )
            return True
        except Exception as e:
            logger.error("Queue download failed (%s): %s", filename, e)
            return False

    def get_queue_position(self, username: str, filename: str) -> Optional[int]:
        import urllib.parse
        
        try:
            # Clean filename and encode properly
            clean_filename = filename.strip().encode('utf-8', errors='ignore').decode('utf-8')
            download_id = hashlib.sha1(clean_filename.encode()).hexdigest()
            
            # URL encode username and download_id
            encoded_username = urllib.parse.quote(username, safe='')
            encoded_download_id = urllib.parse.quote(download_id, safe='')
            
            path = f"/api/v0/transfers/downloads/{encoded_username}/{encoded_download_id}/position"
            logger.debug(f"Soulseek queue position request: {path}")
            
            r = self._get(path)
            return r.json()
        except requests.HTTPError as e:
            if e.response.status_code == 404:  # not queued anymore
                logger.debug(f"Download not queued anymore: {filename}")
                return None
            elif e.response.status_code == 400:
                logger.error(f"Soulseek API 400 error for user '{username}', file '{filename}': {e}")
                logger.error(f"Generated path: {path}")
                return None
            else:
                logger.error(f"Soulseek request failed: {e}")
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

        logger.info(f"Searching Soulseek P2P network for '{query}' (intelligent polling enabled)...")
        responses = self.wait_for_search_completion(search_id, max_wait_time=120, check_interval=3)
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
            try:
                pos = self.get_queue_position(best["username"], file["filename"])
                if pos is None or pos == 0:
                    logger.info("Download started for '%s'", file["filename"])
                    return True
                logger.info("Still queued (%s) – retry in %ss", pos, wait)
                time.sleep(wait)
                wait *= 2
            except Exception as e:
                logger.error("Error checking queue position: %s", e)
                return False
        logger.warning("Download still queued after retries")
        return False


# --- Thin functional façade (test-friendly) ----------
_default_client = SoulseekClient()  # singleton, lazily configured

def search(query: str) -> Dict[str, Any]:
    """Functional wrapper used by legacy code and unit-tests."""
    sid = _default_client.search(query)
    if not sid:
        return {}
    logger.info(f"Searching Soulseek P2P network for '{query}' (intelligent polling enabled)...")
    res = _default_client.wait_for_search_completion(sid, max_wait_time=120, check_interval=3)
    return {"search_id": sid, "responses": res}

def queue_search(query: str) -> str:
    """Queue a search and return search ID."""
    return _default_client.search(query)

def is_enabled() -> bool:
    """Check if Soulseek is enabled and configured."""
    return _default_client.enabled

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
