"""Offline provider configuration checks; never return credentials or endpoint text."""

import hashlib
import ipaddress
import json
import re
from urllib.parse import unquote, urlsplit


def valid_endpoint(value: str) -> bool:
    """Require an API base URL, not a completed operation URL or credential carrier."""
    if not isinstance(value, str) or not value or len(value) > 2000:
        return False
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return False
    if any(character in value for character in "\\?#"):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or port == 0
            or parsed.netloc.endswith(":")
        ):
            return False
        if ":" in host:
            ipaddress.IPv6Address(host)
        else:
            ascii_host = host.encode("idna").decode("ascii").rstrip(".")
            if len(ascii_host) > 253 or any(
                not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in ascii_host.split(".")
            ):
                return False
        path = unquote(parsed.path).rstrip("/").lower()
        if any(ord(character) < 32 or ord(character) == 127 for character in path):
            return False
        if path.endswith(("/chat/completions", "/embeddings", "/responses")):
            return False
    except (ValueError, UnicodeError):
        return False
    return True


def _valid_model_name(value: str) -> bool:
    try:
        value.encode()
    except UnicodeError:
        return False
    return len(value) <= 200 and not any(
        ord(character) < 32 or ord(character) == 127 for character in value
    )


def provider_configuration(settings, provider: str) -> dict:
    if provider not in {"llm", "embedding"}:
        raise ValueError("Unknown provider type")
    fields = {
        field: str(getattr(settings, f"{provider}_{field}", "")).strip()
        for field in ("api_key", "base_url", "model")
    }
    missing = [field for field, value in fields.items() if not value]
    issues = []
    if fields["base_url"] and not valid_endpoint(fields["base_url"]):
        issues.append("invalid_base_url")
    if fields["api_key"] and any(
        ord(character) < 33 or ord(character) > 126 for character in fields["api_key"]
    ):
        issues.append("invalid_api_key_format")
    if fields["model"] and not _valid_model_name(fields["model"]):
        issues.append("invalid_model_name")
    if provider == "embedding":
        response_model = settings.embedding_response_model.strip()
        if not _valid_model_name(response_model):
            issues.append("invalid_response_model_name")
    # The built-in base URL alone must not make an untouched installation look configured.
    untouched = (
        not fields["api_key"]
        and not fields["model"]
        and (
            not fields["base_url"]
            or provider == "llm"
            and fields["base_url"] == "https://api.openai.com/v1"
        )
    )
    state = (
        "invalid"
        if issues
        else "not_configured"
        if untouched
        else "incomplete"
        if missing
        else "configured"
    )
    return {
        "state": state,
        "configured": state == "configured",
        "missing_fields": missing,
        "issues": issues,
    }


def provider_fingerprint(settings, provider: str) -> str:
    """Rotate verification identity with credentials, without storing their plaintext."""
    if provider not in {"llm", "embedding"}:
        raise ValueError("Unknown provider type")
    identity = {
        "provider": provider,
        "endpoint": getattr(settings, f"{provider}_base_url").strip().rstrip("/"),
        "model": getattr(settings, f"{provider}_model").strip(),
        "credential_hash": hashlib.sha256(
            getattr(settings, f"{provider}_api_key").strip().encode()
        ).hexdigest(),
    }
    if provider == "llm":
        identity.update(
            token_parameter=settings.llm_token_parameter,
            max_output_tokens=settings.llm_max_output_tokens,
            temperature=settings.llm_temperature,
        )
    else:
        identity.update(
            dimensions=settings.embedding_dimensions,
            response_model=settings.embedding_response_model,
        )
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
