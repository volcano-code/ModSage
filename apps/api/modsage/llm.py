import json
import re

import httpx


class ModelUnavailable(RuntimeError):
    pass


class ModelInvalidOutput(RuntimeError):
    pass


class ModelClient:
    """One bounded request; never follow source URLs or execute supplied code."""

    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client

    async def json_response(self, system: str, data: dict) -> dict:
        if not self.settings.llm_configured:
            raise ModelUnavailable("Model credentials and model name are required")
        base = self.settings.llm_base_url.rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ModelUnavailable("Invalid model endpoint")
        body = {
            "model": self.settings.llm_model,
            "temperature": 0,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ],
        }
        own = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.settings.llm_timeout_seconds, follow_redirects=False
        )
        try:
            response = await client.post(
                base + "/chat/completions",
                headers={"Authorization": "Bearer " + self.settings.llm_api_key},
                json=body,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or len(content) > 16000:
                raise ModelInvalidOutput("Invalid model message")
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ModelInvalidOutput("Expected JSON object")
            return result
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            # No raw response text/URLs: providers sometimes echo credentials or prompts.
            raise ModelUnavailable("Model request failed or returned invalid JSON") from None
        finally:
            if own:
                await client.aclose()
