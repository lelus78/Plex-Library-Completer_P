import os
import time
import hashlib
import logging
import requests

USE_SOULSEEK = os.getenv("USE_SOULSEEK", "0") == "1"
SLSKD_URL = os.getenv("SLSKD_URL", "").rstrip("/")
SLSKD_TOKEN = os.getenv("SLSKD_TOKEN", "")

logger = logging.getLogger(__name__)

class SoulseekClient:
    def __init__(self):
        self.enabled = USE_SOULSEEK and bool(SLSKD_URL)
        self.base_url = SLSKD_URL
        self.session = requests.Session()
        if SLSKD_TOKEN:
            self.session.headers.update({"Authorization": f"Bearer {SLSKD_TOKEN}"})

    def _post(self, path: str, data: dict | list):
        url = f"{self.base_url}{path}"
        r = self.session.post(url, json=data, timeout=10)
        r.raise_for_status()
        return r

    def _get(self, path: str):
        url = f"{self.base_url}{path}"
        r = self.session.get(url, timeout=10)
        r.raise_for_status()
        return r

    def search(self, query: str) -> str | None:
        try:
            resp = self._post("/api/v0/searches", {"searchText": query})
            search_id = resp.json().get("id")
            return search_id
        except Exception as e:
            logger.error(f"Soulseek search failed for '{query}': {e}")
            return None

    def get_search_responses(self, search_id: str):
        try:
            resp = self._get(f"/api/v0/searches/{search_id}/responses")
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to get search results {search_id}: {e}")
            return []

    def queue_download(self, username: str, filename: str, size: int) -> bool:
        try:
            self._post(f"/api/v0/transfers/downloads/{username}", [{"Filename": filename, "Size": size}])
            return True
        except Exception as e:
            logger.error(f"Failed to queue {filename} from {username}: {e}")
            return False

    def get_queue_position(self, username: str, filename: str) -> int | None:
        download_id = hashlib.sha1(filename.encode("utf-8")).hexdigest()
        try:
            resp = self._get(f"/api/v0/transfers/downloads/{username}/{download_id}/position")
            return resp.json()
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise
        except Exception as e:
            logger.error(f"Failed to get queue position for {filename}: {e}")
            return None

    def search_and_download(self, artist: str, title: str) -> bool:
        if not self.enabled:
            return False
        query = f"{artist} - {title}"
        search_id = self.search(query)
        if not search_id:
            return False
        time.sleep(2)
        responses = self.get_search_responses(search_id)
        if not responses:
            logger.info(f"No Soulseek results for '{query}'")
            return False
        # prefer users with free slot
        best = None
        for resp in responses:
            files = resp.get("files", [])
            if not files:
                continue
            if best is None or (resp.get("hasFreeUploadSlot") and resp.get("queueLength", 0) < best.get("queueLength", 9999)):
                best = resp
        if not best:
            logger.info(f"No downloadable files found for '{query}'")
            return False
        file = best["files"][0]
        username = best["username"]
        if not self.queue_download(username, file["filename"], file.get("size", 0)):
            return False
        wait = 10
        for _ in range(5):
            pos = self.get_queue_position(username, file["filename"])
            if pos is None or pos == 0:
                logger.info(f"Download of '{file['filename']}' from {username} started")
                return True
            logger.info(f"Queued at position {pos} for {username}, retrying in {wait}s")
            time.sleep(wait)
            wait *= 2
        logger.warning(f"Download of '{file['filename']}' from {username} still queued")
        return True
