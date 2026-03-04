from __future__ import annotations

import json

from app.clients.llm_client import AzureOpenAILlmClient


class DummyResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"ok": True})
                    }
                }
            ]
        }


def test_azure_openai_client_uses_deployment_path(monkeypatch):
    captured = {}

    def fake_post(url, headers, params, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        return DummyResponse()

    monkeypatch.setattr("app.clients.llm_client.httpx.post", fake_post)

    client = AzureOpenAILlmClient(
        base_url="https://example-resource.openai.azure.com/",
        api_key="test-key",
        deployment="gpt-5",
        api_version="2024-10-21",
        timeout_seconds=30,
    )
    payload = client.generate_json(system_prompt="system", user_prompt="user")

    assert payload == {"ok": True}
    assert captured["url"] == "https://example-resource.openai.azure.com/openai/deployments/gpt-5/chat/completions"
    assert captured["headers"]["api-key"] == "test-key"
    assert captured["params"] == {"api-version": "2024-10-21"}
