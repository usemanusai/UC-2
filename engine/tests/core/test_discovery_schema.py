import pytest
from pydantic import ValidationError
from engine.core.discovery_schema import (
    DiscoveryResult,
    parse_result,
    DiscoveryValidationError,
    _validate_css_selector,
    _validate_url
)

def test_validate_css_selector_valid():
    assert _validate_css_selector(".my-class") == ".my-class"
    assert _validate_css_selector("#my-id") == "#my-id"
    assert _validate_css_selector("input[type='text']") == "input[type='text']"

def test_validate_css_selector_invalid():
    with pytest.raises(ValueError, match="must be a str"):
        _validate_css_selector(123)

    with pytest.raises(ValueError, match="too long"):
        _validate_css_selector("a" * 513)

    with pytest.raises(ValueError, match="contains illegal characters"):
        _validate_css_selector("class\x00")

def test_validate_css_selector_hallucination():
    hallucinations = ["http://example.com", "```css\n.class\n```", "// comment", "import os", "def foo():", "class Bar:", "Traceback (most recent call last):", "Error: failed"]
    for h in hallucinations:
        with pytest.raises(ValueError, match="(looks like a hallucination|contains illegal characters)"):
            _validate_css_selector(h)

def test_validate_url_valid():
    assert _validate_url("http://example.com") == "http://example.com"
    assert _validate_url("https://example.com") == "https://example.com"
    assert _validate_url(None) is None
    assert _validate_url("") is None
    assert _validate_url(123) is None

def test_validate_url_invalid():
    with pytest.raises(ValueError, match="must start with http:// or https://"):
        _validate_url("ftp://example.com")

    with pytest.raises(ValueError, match="too long"):
        _validate_url("http://" + "a" * 2048)

def test_discovery_result_valid():
    data = {
        "email_field": "#email",
        "password_field": "#password",
        "submit_button": "#submit",
        "auth_pattern": 1,
        "redirect_url": "https://example.com/home"
    }
    result = DiscoveryResult(**data)
    assert result.email_field == "#email"
    assert result.password_field == "#password"
    assert result.submit_button == "#submit"
    assert result.auth_pattern == 1
    assert result.redirect_url == "https://example.com/home"
    assert result.found_any() is True

def test_discovery_result_missing_primary_selector():
    data = {
        "password_field": "#password",
        "auth_pattern": 1
    }
    with pytest.raises(ValidationError, match="must contain at least one login selector"):
        DiscoveryResult(**data)

def test_discovery_result_invalid_auth_pattern():
    data = {
        "email_field": "#email",
        "auth_pattern": 9
    }
    with pytest.raises(ValidationError, match="auth_pattern must be 1–8"):
        DiscoveryResult(**data)

def test_parse_result_valid():
    raw = {
        "email_field": "#email",
        "extra_field": "ignore me"
    }
    result = parse_result(raw)
    assert result.email_field == "#email"
    assert not hasattr(result, "extra_field")

def test_parse_result_invalid_type():
    with pytest.raises(DiscoveryValidationError, match="Expected dict"):
        parse_result("not a dict")

def test_parse_result_validation_error():
    raw = {
        "email_field": "http://hallucination.com"
    }
    with pytest.raises(DiscoveryValidationError, match="LLM discovery output failed schema validation: Value error, CSS selector looks like a hallucination"):
        parse_result(raw)
