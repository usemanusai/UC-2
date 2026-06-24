"""
engine/core/discovery_schema.py
================================
Pydantic v2 schema for CrewAI / LLM discovery output.

Acts as the strict type boundary between the non-deterministic LLM output
and the deterministic GUI update logic in validator_pro_v2.py.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Try Pydantic v2 first, fall back to v1 or dict-based stub
_PYDANTIC_V2 = False
_PYDANTIC_AVAILABLE = False
try:
    from pydantic import BaseModel, field_validator, model_validator, ValidationError
    _PYDANTIC_V2 = True
    _PYDANTIC_AVAILABLE = True
except ImportError:
    try:
        from pydantic import BaseModel, validator, ValidationError
        _PYDANTIC_AVAILABLE = True
    except ImportError:
        pass

# ── Validation helpers ───────────────────────────────────────────────────────

_CSS_SAFE_RE = re.compile(r'^[^\x00-\x08\x0a-\x1f\x7f]{1,512}$')

_HALLUCINATION_PREFIXES = (
    "http://", "https://", "```", "//", "import ", "def ", "class ",
    "Traceback", "File \"", "{\"", "Error:", "None", "null",
)


def _validate_css_selector(value: Any) -> Optional[str]:
    """
    Normalise and validate a single CSS selector string.

    Returns the stripped selector on success, or raises ``ValueError``
    with a human-readable explanation.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"CSS selector must be a str, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 512:
        raise ValueError(f"CSS selector too long ({len(cleaned)} chars > 512): {cleaned[:60]}...")
    if not _CSS_SAFE_RE.match(cleaned):
        raise ValueError(f"CSS selector contains illegal characters: {cleaned[:60]}...")
    lower = cleaned.lower()
    for prefix in _HALLUCINATION_PREFIXES:
        if lower.startswith(prefix.lower()):
            raise ValueError(f"CSS selector looks like a hallucination (starts with {prefix!r}): {cleaned[:60]}...")
    return cleaned


def _validate_url(value: Any) -> Optional[str]:
    """Accept only valid http/https URLs or None."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if not cleaned.startswith("http://") and not cleaned.startswith("https://"):
        raise ValueError(f"redirect_url must start with http:// or https://: {cleaned[:60]}")
    if len(cleaned) > 2048:
        raise ValueError(f"redirect_url too long ({len(cleaned)} chars)")
    return cleaned


# ── Schema ───────────────────────────────────────────────────────────────────

if _PYDANTIC_AVAILABLE:
    class DiscoveryResult(BaseModel):
        """
        Strictly typed schema for the selectors discovered by the CrewAI squad.

        All CSS selector fields are validated against a set of heuristics that
        reject LLM hallucinations (URLs, tracebacks, JSON blobs, etc.).
        """
        auth_pattern: Optional[int] = None
        login_url: Optional[str] = None
        email_field: Optional[str] = None
        password_field: Optional[str] = None
        submit_button: Optional[str] = None
        next_button: Optional[str] = None
        trigger_button: Optional[str] = None
        sso_provider: Optional[str] = None
        sso_button_selector: Optional[str] = None
        redirect_url: Optional[str] = None
        mfa_otp_field: Optional[str] = None
        mfa_submit_button: Optional[str] = None
        # Error detection fields
        invalid_error_selector: Optional[str] = None
        invalid_inner_html: Optional[str] = None
        invalid_outer_html: Optional[str] = None
        captcha_error_selector: Optional[str] = None
        captcha_inner_html: Optional[str] = None
        captcha_outer_html: Optional[str] = None

        if _PYDANTIC_V2:
            @field_validator(
                "email_field", "password_field", "submit_button", "next_button",
                "trigger_button", "sso_button_selector", "mfa_otp_field",
                "mfa_submit_button", "invalid_error_selector",
                "captcha_error_selector",
                mode="before",
            )
            @classmethod
            def _check_selector(cls, v: Any) -> Optional[str]:
                return _validate_css_selector(v)

            @field_validator("redirect_url", "login_url", mode="before")
            @classmethod
            def _check_url(cls, v: Any) -> Optional[str]:
                return _validate_url(v)

            @field_validator("auth_pattern", mode="before")
            @classmethod
            def _check_auth_pattern(cls, v: Any) -> Optional[int]:
                if v is None:
                    return None
                n = int(v)
                if not (1 <= n <= 8):
                    raise ValueError(f"auth_pattern must be 1–8, got {n}")
                return n

            @model_validator(mode="after")
            def _require_at_least_one(self) -> "DiscoveryResult":
                has_primary = any([
                    self.email_field, self.submit_button,
                    self.sso_button_selector, self.mfa_otp_field,
                ])
                if not has_primary:
                    raise ValueError(
                        "DiscoveryResult must contain at least one login selector "
                        "(email_field, submit_button, sso_button_selector, or mfa_otp_field)."
                    )
                return self
        else:
            # Pydantic v1 validators
            @validator(
                "email_field", "password_field", "submit_button", "next_button",
                "trigger_button", "sso_button_selector", "mfa_otp_field",
                "mfa_submit_button", "invalid_error_selector",
                "captcha_error_selector",
                pre=True, always=True,
            )
            @classmethod
            def _check_selector_v1(cls, v: Any) -> Optional[str]:
                return _validate_css_selector(v)

            @validator("redirect_url", "login_url", pre=True, always=True)
            @classmethod
            def _check_url_v1(cls, v: Any) -> Optional[str]:
                return _validate_url(v)

        def to_gui_dict(self) -> Dict[str, str]:
            """
            Return a dict containing only non-None validated fields.

            Safe to pass directly to the GUI's ``update_selectors()`` function
            without further validation — all values have already passed through
            the model validators.
            """
            return {
                k: v for k, v in (self.model_dump() if hasattr(self, "model_dump") else self.dict()).items()
                if v is not None and v != ""
            }

        def found_any(self) -> bool:
            """Return True if at least one primary login selector was discovered."""
            return bool(self.email_field or self.password_field or
                        self.submit_button or self.sso_button_selector or
                        self.mfa_otp_field)

        def missing_fields(self) -> List[str]:
            """
            Return names of login selector fields that are still None,
            taking auth_pattern into account (password not required for P5/6/7).
            """
            required = ["email_field", "submit_button"]
            if self.auth_pattern not in (5, 6, 7):
                required.append("password_field")
            return [f for f in required if getattr(self, f, None) is None]

        class Config:
            # Allow extra fields from LLM output without crashing
            extra = "ignore"

else:
    # ── Fallback: No Pydantic available ──────────────────────────────────────
    class DiscoveryResult:
        """Dict-based fallback when Pydantic is not installed."""

        _SELECTOR_FIELDS = (
            "email_field", "password_field", "submit_button", "next_button",
            "trigger_button", "sso_button_selector", "mfa_otp_field",
            "mfa_submit_button", "invalid_error_selector",
            "captcha_error_selector",
        )
        _URL_FIELDS = ("redirect_url", "login_url")
        _TEXT_FIELDS = (
            "invalid_inner_html", "invalid_outer_html",
            "captcha_inner_html", "captcha_outer_html",
            "sso_provider",
        )
        _ALL_FIELDS = _SELECTOR_FIELDS + _URL_FIELDS + _TEXT_FIELDS + ("auth_pattern",)

        def __init__(self, **kwargs: Any):
            for f in self._SELECTOR_FIELDS:
                val = kwargs.get(f)
                try:
                    val = _validate_css_selector(val)
                except ValueError:
                    val = None
                setattr(self, f, val)
            for f in self._URL_FIELDS:
                val = kwargs.get(f)
                try:
                    val = _validate_url(val)
                except ValueError:
                    val = None
                setattr(self, f, val)
            for f in self._TEXT_FIELDS:
                raw = kwargs.get(f)
                setattr(self, f, raw.strip() if isinstance(raw, str) and raw.strip() else None)
            ap = kwargs.get("auth_pattern")
            self.auth_pattern = int(ap) if ap is not None and 1 <= int(ap) <= 8 else None

        def to_gui_dict(self) -> Dict[str, str]:
            return {f: getattr(self, f) for f in self._ALL_FIELDS if getattr(self, f, None) is not None}

        def found_any(self) -> bool:
            return bool(self.email_field or self.password_field or
                        self.submit_button or self.sso_button_selector or
                        self.mfa_otp_field)

        def missing_fields(self) -> List[str]:
            required = ["email_field", "submit_button"]
            if self.auth_pattern not in (5, 6, 7):
                required.append("password_field")
            return [f for f in required if getattr(self, f, None) is None]

        def model_dump(self) -> Dict[str, Any]:
            return {f: getattr(self, f, None) for f in self._ALL_FIELDS}


# ── Exception ────────────────────────────────────────────────────────────────

class DiscoveryValidationError(Exception):
    """
    Raised when the LLM output fails DiscoveryResult validation.

    Carries both the original raw dict and the Pydantic error list so
    callers can log structured diagnostics without crashing.
    """

    def __init__(self, raw: Dict[str, Any], errors: List[Dict[str, Any]]):
        self.raw = raw
        self.errors = errors
        error_summary = "; ".join(e.get("msg", "?") for e in errors[:5])
        super().__init__(f"LLM discovery output failed schema validation: {error_summary}")


# ── Parser ───────────────────────────────────────────────────────────────────

def parse_result(raw: Any) -> DiscoveryResult:
    """
    Parse and validate a raw LLM-output dict into a :class:`DiscoveryResult`.

    Parameters
    ----------
    raw:
        The unvalidated dict returned by ``run_discovery()`` or read from
        the JSON result file.

    Returns
    -------
    DiscoveryResult
        A fully validated result object.

    Raises
    ------
    DiscoveryValidationError
        If the raw dict fails validation.
    """
    if not isinstance(raw, dict):
        raise DiscoveryValidationError(
            raw={"_input": str(raw)[:200]},
            errors=[{"msg": f"Expected dict, got {type(raw).__name__}"}],
        )

    # Filter to only known fields to prevent injection
    known_fields = set(DiscoveryResult._ALL_FIELDS if hasattr(DiscoveryResult, "_ALL_FIELDS") else
                       DiscoveryResult.model_fields.keys() if hasattr(DiscoveryResult, "model_fields") else
                       [])
    if known_fields:
        filtered = {k: v for k, v in raw.items() if k in known_fields}
    else:
        filtered = raw

    try:
        if _PYDANTIC_AVAILABLE:
            return DiscoveryResult(**filtered)
        else:
            result = DiscoveryResult(**filtered)
            if not result.found_any():
                raise ValueError(
                    "DiscoveryResult must contain at least one login selector "
                    "(email_field, submit_button, sso_button_selector, or mfa_otp_field)."
                )
            return result
    except Exception as exc:
        if isinstance(exc, DiscoveryValidationError):
            raise
        # Wrap validation errors
        if _PYDANTIC_AVAILABLE and isinstance(exc, ValidationError):
            errors = exc.errors() if hasattr(exc, "errors") else [{"msg": str(exc)}]
        else:
            errors = [{"msg": str(exc)}]
        raise DiscoveryValidationError(raw=raw, errors=errors) from exc
