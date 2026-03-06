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


def test_azure_openai_client_sends_image_url_content(monkeypatch):
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
        deployment="gpt-5-mini",
        api_version="2024-10-21",
        timeout_seconds=30,
    )
    payload = client.generate_json_with_images(
        system_prompt="system",
        user_prompt="user",
        image_data_urls=["data:image/jpeg;base64,abc123"],
    )

    assert payload == {"ok": True}
    user_message = captured["json"]["messages"][1]
    assert isinstance(user_message["content"], list)
    assert user_message["content"][0]["type"] == "text"
    assert user_message["content"][1]["type"] == "image_url"
    assert user_message["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,abc123"
