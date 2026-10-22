import asyncio
import json
import re

from modsage.provider_config import provider_configuration
from modsage.provider_http import ProviderCall, ProviderRequestError, reject_json_constant

MAX_RESPONSE_BYTES = 256 * 1024
MAX_INPUT_BYTES = 128 * 1024


class ModelUnavailable(RuntimeError):
    def __init__(self, message="Model service is unavailable", *, code="model_unavailable"):
        self.code = code
        super().__init__(message)


class ModelInvalidOutput(RuntimeError):
    def __init__(self, message="Invalid model output", *, code="invalid_response"):
        self.code = code
        super().__init__(message)


class ModelClient:
    """One bounded request; never follow source URLs or execute supplied code."""

    def __init__(self, settings, client=None, *, usage_store=None, operation="understand"):
        self.settings = settings
        self.client = client
        self.usage_store = usage_store
        self.operation = operation
        self.last_call = None

    async def json_response(self, system: str, data: dict) -> dict:
        call = ProviderCall(self.settings, "llm", self.operation, self.usage_store)
        self.last_call = call.metadata
        try:
            configuration = provider_configuration(self.settings, "llm")
            if not configuration["configured"]:
                raise ProviderRequestError(
                    "configuration_invalid" if configuration["issues"] else "not_configured"
                )
            if not isinstance(system, str) or not isinstance(data, dict):
                raise ProviderRequestError("invalid_input")
            try:
                encoded_data = json.dumps(data, ensure_ascii=False, allow_nan=False)
                if len(system.encode()) + len(encoded_data.encode()) > MAX_INPUT_BYTES:
                    raise ProviderRequestError("input_too_large")
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise ProviderRequestError("invalid_input") from None
            body = {
                "model": self.settings.llm_model.strip(),
                self.settings.llm_token_parameter: self.settings.llm_max_output_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": encoded_data},
                ],
            }
            if self.settings.llm_temperature is not None:
                body["temperature"] = self.settings.llm_temperature
            payload = await call.request(
                client=self.client,
                url=self.settings.llm_base_url.strip().rstrip("/") + "/chat/completions",
                body=body,
                timeout=self.settings.llm_timeout_seconds,
                response_limit=MAX_RESPONSE_BYTES,
            )
            if call.metadata["finish_reason"] == "length":
                raise ProviderRequestError("output_truncated")
            if call.metadata["finish_reason"] == "content_filter":
                raise ProviderRequestError("content_filtered")
            try:
                choice = payload["choices"][0]
                if not isinstance(choice, dict):
                    raise ProviderRequestError("invalid_response")
                if choice.get("finish_reason") not in {None, "stop"}:
                    raise ProviderRequestError("invalid_response")
                content = choice["message"]["content"]
                if not isinstance(content, str) or len(content) > 64000:
                    raise ProviderRequestError("invalid_response")
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
                result = json.loads(content, parse_constant=reject_json_constant)
            except (KeyError, IndexError, TypeError):
                raise ProviderRequestError("invalid_response") from None
            except (ValueError, RecursionError):
                raise ProviderRequestError("invalid_json") from None
            if not isinstance(result, dict):
                raise ProviderRequestError("invalid_response")
            try:
                # Valid JSON escapes can still decode to lone surrogate code
                # points, which cannot be stored or returned as UTF-8 JSON.
                json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
            except (ValueError, UnicodeError, RecursionError):
                raise ProviderRequestError("invalid_response") from None
            error = await call.finish("succeeded")
            if error:
                raise ProviderRequestError(error)
            return result
        except asyncio.CancelledError:
            await call.finish("cancelled", "cancelled")
            raise
        except ProviderRequestError as exc:
            code = await call.finish("failed", exc.code)
            if code in {
                "invalid_response",
                "output_truncated",
                "content_filtered",
                "invalid_usage",
            }:
                raise ModelInvalidOutput("Model returned an unusable response", code=code) from None
            message = (
                "Invalid model endpoint or configuration"
                if code == "configuration_invalid"
                else "Model request failed or returned invalid JSON"
            )
            raise ModelUnavailable(message, code=code) from None
