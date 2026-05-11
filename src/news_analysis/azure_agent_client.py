from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()


class AzureAgentError(RuntimeError):
    pass


class AzureAgentClient:
    """
    Lightweight Azure chat-completions client for the agent layer.

    Supported env names:
      - AZURE_OPENAI_ENDPOINT or AZURE_AI_ENDPOINT
      - AZURE_OPENAI_API_KEY or AZURE_AI_API_KEY
      - AZURE_OPENAI_DEPLOYMENT or AZURE_AI_DEPLOYMENT
      - AZURE_OPENAI_API_VERSION or AZURE_AI_API_VERSION

    The agent definitions, YAML config, and Python output schema are passed as
    context so the model can return JSON matching the local dataclass contract.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
        api_version: str | None = None,
        timeout_seconds: int = 90,
    ) -> None:
        self.endpoint = (endpoint or os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("AZURE_AI_ENDPOINT") or "").rstrip("/")
        self.api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_AI_API_KEY") or ""
        self.deployment = deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("AZURE_AI_DEPLOYMENT") or ""
        self.api_version = api_version or os.getenv("AZURE_OPENAI_API_VERSION") or os.getenv("AZURE_AI_API_VERSION") or "2024-02-15-preview"
        self.timeout_seconds = timeout_seconds

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key and self.deployment)

    def run_json(
        self,
        agent_name: str,
        agent_definition_path: Path,
        config_path: Path,
        output_schema_path: Path,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise AzureAgentError(
                "Azure agent is not configured. Set AZURE_OPENAI_ENDPOINT, "
                "AZURE_OPENAI_API_KEY, and AZURE_OPENAI_DEPLOYMENT."
            )

        messages = [
            {
                "role": "system",
                "content": "\n\n".join(
                    [
                        f"You are the {agent_name} agent.",
                        "Return only valid JSON. Do not wrap the JSON in markdown.",
                        "Do not use future market data or post-news price action.",
                        _read_text(agent_definition_path),
                        "Agent YAML config:\n" + _read_text(config_path),
                        "Python output schema contract:\n" + _read_text(output_schema_path),
                    ]
                ),
            },
            {
                "role": "user",
                "content": json.dumps(_jsonable(payload), default=str, ensure_ascii=False),
            },
        ]
        response = requests.post(
            self._chat_completions_url(),
            headers={"api-key": self.api_key, "Content-Type": "application/json"},
            params={"api-version": self.api_version},
            json={
                "messages": messages,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise AzureAgentError(f"Azure agent call failed: {response.status_code} {response.text}")

        body = response.json()
        content = body["choices"][0]["message"]["content"]
        return _parse_json_object(content)

    def _chat_completions_url(self) -> str:
        return f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise AzureAgentError("Azure agent did not return a JSON object.")
    return parsed
