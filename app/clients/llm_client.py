from __future__ import annotations

import json
from typing import Protocol

import httpx

from app.core.config import get_settings


class LlmClient(Protocol):
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        ...


class DisabledLlmClient:
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        raise RuntimeError("LLM provider is disabled.")


def _raise_with_response_details(response: httpx.Response, context: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text.strip()
        if len(body) > 2000:
            body = body[:2000] + "..."
        raise RuntimeError(f"{context} failed with HTTP {response.status_code}: {body}") from exc


class OpenAICompatibleLlmClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=httpx.Timeout(self.timeout_seconds, connect=min(self.timeout_seconds, 10.0)),
        )
        _raise_with_response_details(response, "OpenAI-compatible chat completion")
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise RuntimeError("LLM response content is not a JSON string.")
        return json.loads(content)


class AzureOpenAILlmClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        deployment: str,
        api_version: str,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.deployment = deployment
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        response = httpx.post(
            f"{self.base_url}/openai/deployments/{self.deployment}/chat/completions",
            headers={
                "api-key": self.api_key,
                "Content-Type": "application/json",
            },
            params={"api-version": self.api_version},
            json={
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=httpx.Timeout(self.timeout_seconds, connect=min(self.timeout_seconds, 10.0)),
        )
        _raise_with_response_details(response, "Azure OpenAI chat completion")
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise RuntimeError("LLM response content is not a JSON string.")
        return json.loads(content)


def build_llm_client() -> LlmClient:
    settings = get_settings()
    provider = settings.llm_provider.lower().strip()
    if provider in {"", "disabled", "none"}:
        return DisabledLlmClient()
    if provider == "azure_openai":
        if not settings.llm_base_url or not settings.llm_api_key:
            raise RuntimeError("Azure OpenAI is configured but LLM_BASE_URL or LLM_API_KEY is missing.")
        deployment = settings.llm_deployment or settings.llm_model
        if not deployment:
            raise RuntimeError("Azure OpenAI requires LLM_DEPLOYMENT or LLM_MODEL.")
        return AzureOpenAILlmClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            deployment=deployment,
            api_version=settings.llm_api_version,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if provider == "openai_compatible":
        if not settings.llm_base_url or not settings.llm_api_key:
            raise RuntimeError("LLM provider is configured but LLM_BASE_URL or LLM_API_KEY is missing.")
        if "openai.azure.com" in settings.llm_base_url:
            deployment = settings.llm_deployment or settings.llm_model
            if not deployment:
                raise RuntimeError("Azure-style endpoint requires LLM_DEPLOYMENT or LLM_MODEL.")
            return AzureOpenAILlmClient(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                deployment=deployment,
                api_version=settings.llm_api_version,
                timeout_seconds=settings.llm_timeout_seconds,
            )
        return OpenAICompatibleLlmClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")
