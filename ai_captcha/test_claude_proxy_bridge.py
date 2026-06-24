import pytest
from unittest.mock import patch, MagicMock

from ai_captcha.claude_proxy_bridge import is_proxy_healthy

def test_is_proxy_healthy_httpx_success():
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        assert is_proxy_healthy() is True
        mock_get.assert_called_once()

def test_is_proxy_healthy_httpx_non_200():
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        assert is_proxy_healthy() is False
        mock_get.assert_called_once()

def test_is_proxy_healthy_httpx_fails_requests_success():
    with patch("httpx.get") as mock_httpx_get, \
         patch("requests.get") as mock_requests_get:
        mock_httpx_get.side_effect = Exception("httpx failed")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        assert is_proxy_healthy() is True
        mock_httpx_get.assert_called_once()
        mock_requests_get.assert_called_once()

def test_is_proxy_healthy_httpx_fails_requests_non_200():
    with patch("httpx.get") as mock_httpx_get, \
         patch("requests.get") as mock_requests_get:
        mock_httpx_get.side_effect = Exception("httpx failed")

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_requests_get.return_value = mock_resp

        assert is_proxy_healthy() is False
        mock_httpx_get.assert_called_once()
        mock_requests_get.assert_called_once()

def test_is_proxy_healthy_both_fail():
    with patch("httpx.get") as mock_httpx_get, \
         patch("requests.get") as mock_requests_get:
        mock_httpx_get.side_effect = Exception("httpx failed")
        mock_requests_get.side_effect = Exception("requests failed")

        assert is_proxy_healthy() is False
        mock_httpx_get.assert_called_once()
        mock_requests_get.assert_called_once()
