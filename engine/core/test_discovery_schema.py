import pytest
from engine.core.discovery_schema import parse_result, DiscoveryValidationError, DiscoveryResult, _PYDANTIC_AVAILABLE

def test_parse_result_valid_input():
    raw_data = {
        "email_field": "#email",
        "password_field": "#password",
        "submit_button": ".login-btn",
        "auth_pattern": 1,
        "login_url": "https://example.com/login"
    }
    result = parse_result(raw_data)
    assert result.email_field == "#email"
    assert result.password_field == "#password"
    assert result.submit_button == ".login-btn"
    assert result.auth_pattern == 1
    assert result.login_url == "https://example.com/login"

def test_parse_result_filters_unknown_fields():
    raw_data = {
        "email_field": "#email",
        "submit_button": ".login-btn",
        "unknown_field": "some_value",
        "another_extra": 123
    }
    result = parse_result(raw_data)
    assert not hasattr(result, "unknown_field")
    assert not hasattr(result, "another_extra")
    assert result.email_field == "#email"
    assert result.submit_button == ".login-btn"

def test_parse_result_invalid_type():
    with pytest.raises(DiscoveryValidationError) as exc:
        parse_result(["email_field", "#email"])
    assert "Expected dict, got list" in str(exc.value)

    with pytest.raises(DiscoveryValidationError) as exc:
        parse_result("just a string")
    assert "Expected dict, got str" in str(exc.value)

def test_parse_result_missing_primary_fields():
    raw_data = {
        "next_button": ".next-btn",
        "login_url": "https://example.com/login"
    }
    with pytest.raises(DiscoveryValidationError) as exc:
        parse_result(raw_data)
    assert "at least one login selector" in str(exc.value)

def test_parse_result_invalid_urls():
    raw_data = {
        "email_field": "#email",
        "login_url": "ftp://example.com/login"
    }
    if _PYDANTIC_AVAILABLE:
        with pytest.raises(DiscoveryValidationError) as exc:
            parse_result(raw_data)
        assert "http://" in str(exc.value) or "https://" in str(exc.value)
    else:
        # Fallback ignores invalid URL and sets to None
        result = parse_result(raw_data)
        assert result.login_url is None

def test_parse_result_invalid_selectors():
    raw_data = {
        "email_field": "#email",
        "next_button": "http://example.com"
    }
    if _PYDANTIC_AVAILABLE:
        with pytest.raises(DiscoveryValidationError) as exc:
            parse_result(raw_data)
        assert "hallucination" in str(exc.value).lower()
    else:
        # Fallback sets invalid selector to None
        result = parse_result(raw_data)
        assert result.next_button is None

def test_parse_result_selector_too_long():
    raw_data = {
        "email_field": "#email",
        "next_button": "a" * 513
    }
    if _PYDANTIC_AVAILABLE:
        with pytest.raises(DiscoveryValidationError) as exc:
            parse_result(raw_data)
        assert "too long" in str(exc.value).lower()
    else:
        result = parse_result(raw_data)
        assert result.next_button is None
