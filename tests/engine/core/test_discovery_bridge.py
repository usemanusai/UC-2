import json
import pytest
from unittest.mock import patch, MagicMock

from engine.core.discovery_bridge import _ask_ai

def test_ask_ai_claude_proxy_success():
    """Test that _ask_ai uses Claude proxy when enabled and it succeeds."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {"message": {"content": '{"success": true, "source": "claude"}'}}
        ]
    }

    with patch("requests.post", return_value=mock_response) as mock_post:
        result = _ask_ai(
            prompt="test prompt",
            api_keys=["openrouter-key"],
            claude_proxy_url="http://proxy",
            claude_proxy_enabled=True,
        )

        assert result == {"success": True, "source": "claude"}
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://proxy/v1/chat/completions"

def test_ask_ai_claude_proxy_fallback_to_openrouter():
    """Test that _ask_ai falls back to OpenRouter if Claude proxy fails."""
    # First response for Claude proxy (fails), second for OpenRouter (succeeds)
    mock_claude_response = MagicMock()
    mock_claude_response.status_code = 500
    # or we can simulate exception on post

    mock_openrouter_response = MagicMock()
    mock_openrouter_response.status_code = 200
    mock_openrouter_response.json.return_value = {
        "choices": [
            {"message": {"content": '{"success": true, "source": "openrouter"}'}}
        ]
    }

    with patch("requests.post", side_effect=[Exception("Claude fail"), mock_openrouter_response]) as mock_post:
        result = _ask_ai(
            prompt="test prompt",
            api_keys=["openrouter-key"],
            claude_proxy_url="http://proxy",
            claude_proxy_enabled=True,
        )

        assert result == {"success": True, "source": "openrouter"}
        assert mock_post.call_count == 2

def test_ask_ai_openrouter_success():
    """Test that _ask_ai uses OpenRouter successfully when proxy is disabled."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {"message": {"content": '{"success": true, "source": "openrouter"}'}}
        ]
    }

    with patch("requests.post", return_value=mock_response) as mock_post:
        result = _ask_ai(
            prompt="test prompt",
            api_keys=["openrouter-key"],
            claude_proxy_enabled=False,
        )

        assert result == {"success": True, "source": "openrouter"}
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://openrouter.ai/api/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer openrouter-key"

def test_ask_ai_json_block_extraction():
    """Test that _ask_ai correctly extracts JSON wrapped in markdown blocks."""
    mock_response_1 = MagicMock()
    mock_response_1.status_code = 200
    mock_response_1.json.return_value = {
        "choices": [
            {"message": {"content": '```json\n{"success": true, "block": "json"}\n```'}}
        ]
    }

    mock_response_2 = MagicMock()
    mock_response_2.status_code = 200
    mock_response_2.json.return_value = {
        "choices": [
            {"message": {"content": '```\n{"success": true, "block": "plain"}\n```'}}
        ]
    }

    with patch("requests.post", side_effect=[mock_response_1, mock_response_2]):
        # Test ```json
        result1 = _ask_ai(
            prompt="test prompt",
            api_keys=["openrouter-key"],
            claude_proxy_enabled=False,
        )
        assert result1 == {"success": True, "block": "json"}

        # Test ```
        result2 = _ask_ai(
            prompt="test prompt",
            api_keys=["openrouter-key"],
            claude_proxy_enabled=False,
        )
        assert result2 == {"success": True, "block": "plain"}

def test_ask_ai_all_fail_raises_runtime_error():
    """Test that _ask_ai raises RuntimeError if all API attempts fail."""
    with patch("requests.post", side_effect=Exception("Network error")):
        with pytest.raises(RuntimeError) as exc_info:
            _ask_ai(
                prompt="test prompt",
                api_keys=["key1", "key2"],
                claude_proxy_url="http://proxy",
                claude_proxy_enabled=True,
            )

        assert "AI query failed" in str(exc_info.value)
        assert "Network error" in str(exc_info.value)
