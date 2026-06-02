"""Generic OpenAI-compatible VLM client — zero game dependency."""

from __future__ import annotations

import base64
import logging

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger("gameauto.perception.vlm")


class VlmClient:
    """Thin wrapper around OpenAI-compatible Vision API.

    Low temperature (0.2) for deterministic board recognition.
    No reasoning_effort — keeps the model in fast, non-thinking mode.

    Args:
        model: Model name (e.g. "gpt-4o").
        base_url: API base URL.
        api_key: API key.
        temperature: Sampling temperature (default 0.2, same as mobilerun fast_game_agent).
    """

    def __init__(self, model: str, base_url: str, api_key: str, temperature: float = 0.2) -> None:
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.AsyncClient(proxy=None),  # bypass system proxy
        )
        logger.info("VlmClient: model=%s base_url=%s api_key=%s...", model, base_url, api_key[:12] if api_key else "(empty)")

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        image: bytes,
        timeout: float = 120.0,
    ) -> str:
        """Send an image + prompts to the VLM and return the text response.

        Temperature is fixed at 0.2 for deterministic output.
        No reasoning_effort is set — the model runs in fast non-thinking mode.

        Args:
            system_prompt: System message.
            user_prompt: User text message.
            image: PNG/JPEG image bytes.
            timeout: Request timeout in seconds.

        Returns:
            Raw text response from the model.
        """
        data_url = self._image_to_data_url(image)
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            timeout=timeout,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _image_to_data_url(image: bytes) -> str:
        b64 = base64.b64encode(image).decode("ascii")
        return f"data:image/png;base64,{b64}"
