"""Sarvam chat adapter for the post-consent conversational intake."""

import json

import httpx

from app.core.config import Settings
from app.workflows.conversation import ConversationReply, ConversationUnavailable

SARVAM_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"


class SarvamConversationProvider:
    def __init__(self, settings: Settings) -> None:
        if not settings.sarvam_api_key:
            raise ConversationUnavailable("Sarvam is not configured")
        self._api_key = settings.sarvam_api_key
        self._model = settings.sarvam_chat_model
        self._timeout = settings.sarvam_chat_timeout_seconds

    async def reply(self, messages: list[dict[str, str]]) -> ConversationReply:
        content = await self.complete_json(
            messages, "intake_reply", ConversationReply.model_json_schema(), 400
        )
        try:
            return ConversationReply.model_validate(content)
        except ValueError as error:
            raise ConversationUnavailable("Sarvam did not return a usable response") from error

    async def complete_json(
        self, messages: list[dict[str, str]], name: str, schema: dict, max_tokens: int
    ) -> dict:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.3 if name == "intake_reply" else 0,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    SARVAM_CHAT_URL,
                    headers={"api-subscription-key": self._api_key},
                    json=payload,
                )
                response.raise_for_status()
                choice = response.json()["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("Incomplete model response")
                return json.loads(choice["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ConversationUnavailable("Sarvam did not return a usable response") from error
