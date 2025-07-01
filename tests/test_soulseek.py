import os
import sys
import pytest
from unittest.mock import patch, Mock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from plex_playlist_sync.utils import soulseek


def make_response(status_code=200, data=None):
    resp = Mock(status_code=status_code)
    resp.json.return_value = data or {}
    def raise_for_status():
        if status_code >= 400:
            raise Exception("http error")
    resp.raise_for_status = raise_for_status
    return resp


def test_successful_search():
    resp = make_response(data={"results": [1, 2]})
    with patch("requests.get", return_value=resp) as mock_get:
        result = soulseek.search("test")
        assert result == {"results": [1, 2]}
        mock_get.assert_called_once()


def test_queue_download():
    resp = make_response(data={"download_id": "abc"})
    with patch("requests.post", return_value=resp) as mock_post:
        download_id = soulseek.queue_download("file1")
        assert download_id == "abc"
        mock_post.assert_called_once()


def test_download_with_retry_success():
    queued = make_response(data={"status": "queued"})
    complete = make_response(data={"status": "complete"})
    with patch("requests.post", return_value=make_response(data={"download_id": "x"})) as mock_post, \
         patch("requests.get", side_effect=[queued, complete]) as mock_get:
        result = soulseek.download_with_retry("file1", retries=2, delay=0)
        assert result is True
        assert mock_get.call_count == 2
        mock_post.assert_called_once()


def test_download_with_retry_failure():
    queued = make_response(data={"status": "queued"})
    failed = make_response(data={"status": "failed"})
    with patch("requests.post", return_value=make_response(data={"download_id": "x"})) as mock_post, \
         patch("requests.get", side_effect=[queued, failed]) as mock_get:
        result = soulseek.download_with_retry("file1", retries=2, delay=0)
        assert result is False
        assert mock_get.call_count == 2
        mock_post.assert_called_once()
