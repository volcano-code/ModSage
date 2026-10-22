"""Bounded provider transport with safe diagnostics and durable quota hooks."""

import asyncio
import json
import re
import time

import httpx


class ProviderRequestError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__("Provider request failed")


def reject_json_constant(_):
    raise ValueError("Invalid JSON constant")


def safe_metadata(value, settings, *, limit=200):
    """Provider-controlled labels must not become an alternate error/secret channel."""
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) or "://" in value:
        return None
    secrets = (
        settings.llm_api_key,
        settings.embedding_api_key,
        settings.steam_api_key,
        settings.admin_token,
    )
    if any(secret and secret.strip() in value for secret in secrets):
        return None
    return value


def usage_metadata(payload: dict) -> dict:
    result = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    if "usage" not in payload or payload["usage"] is None:
        return result
    usage = payload["usage"]
    if not isinstance(usage, dict):
        raise ProviderRequestError("invalid_usage")
    for target, aliases in {
        "input_tokens": ("prompt_tokens", "input_tokens"),
        "output_tokens": ("completion_tokens", "output_tokens"),
        "total_tokens": ("total_tokens",),
    }.items():
        values = [usage[key] for key in aliases if key in usage]
        if any(type(value) is not int or not 0 <= value <= 2**31 - 1 for value in values):
            raise ProviderRequestError("invalid_usage")
        if values and any(value != values[0] for value in values):
            raise ProviderRequestError("invalid_usage")
        result[target] = values[0] if values else None
    total = result["total_tokens"]
    known_components = sum(result[key] or 0 for key in ("input_tokens", "output_tokens"))
    if total is not None and total < known_components:
        raise ProviderRequestError("invalid_usage")
    return result


class ProviderCall:
    def __init__(self, settings, provider: str, operation: str, usage_store=None):
        self.settings = settings
        self.provider = provider
        self.operation = operation
        self.usage_store = usage_store
        self.started = time.monotonic()
        self.attempted = False
        self.finished = False
        self.metadata = {
            "call_id": None,
            "provider": provider,
            "operation": operation,
            "status": "blocked",
            "error_code": None,
            "latency_ms": 0,
            "http_status": None,
            "returned_model": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "request_id": None,
            "finish_reason": None,
        }

    async def request(self, *, client, url, body, timeout, response_limit):
        from modsage.provider_usage import ProviderLimitExceeded

        if not self.settings.provider_calls_enabled:
            raise ProviderRequestError("provider_calls_disabled")
        if self.settings.provider_daily_call_limit == 0:
            raise ProviderRequestError("daily_call_limit")
        if self.settings.provider_minute_call_limit == 0:
            raise ProviderRequestError("minute_call_limit")
        # Product callers must wire durable admission. Explicit HTTP injection is
        # reserved for controlled transport tests; a new production call site
        # cannot silently make an unmetered paid request.
        if self.usage_store is None and client is None:
            raise ProviderRequestError("usage_unavailable")
        if self.usage_store is not None:
            try:
                self.metadata["call_id"] = await asyncio.to_thread(
                    self.usage_store.reserve, provider=self.provider, operation=self.operation
                )
            except ProviderLimitExceeded as exc:
                raise ProviderRequestError(exc.code) from None
        own = client is None
        client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        key = getattr(self.settings, f"{self.provider}_api_key").strip()
        self.attempted = True
        self.metadata["status"] = "started"
        try:
            async with asyncio.timeout(timeout):
                async with client.stream(
                    "POST",
                    url,
                    json=body,
                    headers={"Authorization": "Bearer " + key},
                    follow_redirects=False,
                ) as response:
                    self.metadata["http_status"] = response.status_code
                    self.metadata["request_id"] = safe_metadata(
                        response.headers.get("x-request-id"), self.settings, limit=128
                    )
                    if response.status_code in {401, 403}:
                        raise ProviderRequestError("unauthorized")
                    if response.status_code == 429:
                        raise ProviderRequestError("rate_limited")
                    if not 200 <= response.status_code < 300:
                        raise ProviderRequestError("http_error")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > response_limit:
                            raise ProviderRequestError("response_too_large")
                        content.extend(chunk)
                try:
                    payload = json.loads(content, parse_constant=reject_json_constant)
                except (ValueError, UnicodeError, RecursionError):
                    raise ProviderRequestError("invalid_json") from None
                if not isinstance(payload, dict):
                    raise ProviderRequestError("invalid_response")
                self.metadata["returned_model"] = safe_metadata(payload.get("model"), self.settings)
                choices = payload.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    reason = choices[0].get("finish_reason")
                    if isinstance(reason, str) and reason in {
                        "stop",
                        "length",
                        "content_filter",
                        "tool_calls",
                        "function_call",
                        "error",
                    }:
                        self.metadata["finish_reason"] = reason
                self.metadata.update(usage_metadata(payload))
                return payload
        except (httpx.TimeoutException, TimeoutError):
            raise ProviderRequestError("timeout") from None
        except httpx.InvalidURL:
            raise ProviderRequestError("configuration_invalid") from None
        except httpx.RequestError:
            raise ProviderRequestError("connection_error") from None
        finally:
            if own:
                await client.aclose()

    async def finish(self, status: str, error_code=None) -> str | None:
        """Failure to persist is fail-closed; a consumed quota reservation is never refunded."""
        from modsage.provider_usage import ProviderLimitExceeded

        if self.finished:
            return self.metadata["error_code"]
        self.finished = True
        self.metadata.update(
            status=status if self.attempted else "blocked",
            error_code=error_code,
            latency_ms=max(0, round((time.monotonic() - self.started) * 1000)),
        )
        call_id = self.metadata["call_id"]
        if call_id is not None and self.usage_store is not None:
            fields = {
                key: value
                for key, value in self.metadata.items()
                if key not in {"call_id", "provider", "operation"}
            }
            # A successful reservation cancelled before the HTTP request is conservatively consumed.
            if fields["status"] == "blocked":
                fields["status"] = "cancelled" if status == "cancelled" else "failed"
            try:
                recorded = await asyncio.to_thread(self.usage_store.finish, call_id, **fields)
                if recorded is False:
                    raise ProviderLimitExceeded("usage_unavailable")
            except ProviderLimitExceeded:
                self.metadata.update(status="failed", error_code="usage_unavailable")
                return "usage_unavailable"
        return error_code
