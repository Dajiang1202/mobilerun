"""Generic OpenAI-compatible VLM client — zero game dependency."""

from __future__ import annotations

import base64
import logging

import httpx
from openai import AsyncOpenAI

from gameauto.utils.images import resize_image_to_max_side

logger = logging.getLogger("gameauto.perception.vlm")


class VlmClient:
    """Thin wrapper around OpenAI-compatible Vision API.

    Low temperature (0.2) for deterministic board recognition.
    No reasoning_effort — keeps the model in fast, non-thinking mode.

    Image preprocessing:
      - Resizes to ``max_image_side`` (default 1024) before encoding
      - Supports OpenAI ``detail`` param: "low" (85 tokens) / "high" / "auto"

    Args:
        model: Model name (e.g. "gpt-4o").
        base_url: API base URL.
        api_key: API key.
        temperature: Sampling temperature (default 0.2, same as mobilerun fast_game_agent).
        max_image_side: Resize so longest edge ≤ this (default 1024).
        image_detail: OpenAI image detail level — "low" / "high" / "auto" (default "auto").
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.2,
        enable_thinking: bool = False,
        max_image_side: int = 1024,
        image_detail: str = "auto",
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self._enable_thinking = enable_thinking
        self._max_image_side = max_image_side
        self._image_detail = image_detail
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.AsyncClient(proxy=None),  # bypass system proxy
        )
        logger.info(
            "VlmClient: model=%s base_url=%s thinking=%s max_side=%d detail=%s api_key=%s...",
            model, base_url, enable_thinking,
            max_image_side, image_detail,
            api_key[:12] if api_key else "(empty)",
        )

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

        The image is automatically resized to ``max_image_side`` (default 1024px)
        before encoding.  Pass ``max_image_side=0`` to skip resize.

        Args:
            system_prompt: System message.
            user_prompt: User text message.
            image: PNG/JPEG image bytes (raw, will be resized).
            timeout: Request timeout in seconds.

        Returns:
            Raw text response from the model.
        """
        # Resize to cap token cost (no-op if already within limit, or if max_image_side=0)
        if self._max_image_side > 0:
            image = resize_image_to_max_side(image, max_side=self._max_image_side)

        data_url, mime_type = self._image_to_data_url(image)
        image_url = {"url": data_url}
        if self._image_detail != "auto":
            image_url["detail"] = self._image_detail

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": image_url},
                ],
            },
        ]

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            timeout=timeout,
            extra_body={"enable_thinking": self._enable_thinking},
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _image_to_data_url(image: bytes) -> tuple[str, str]:
        """Return ``(data_url, mime_type)`` for image bytes.

        Detects PNG vs JPEG from magic bytes so the data URL uses the
        correct MIME type.
        """
        b64 = base64.b64encode(image).decode("ascii")
        if image.startswith(b"\x89PNG"):
            return f"data:image/png;base64,{b64}", "image/png"
        if image.startswith(b"\xff\xd8"):
            return f"data:image/jpeg;base64,{b64}", "image/jpeg"
        # Fallback — assume PNG (most common for screenshots)
        return f"data:image/png;base64,{b64}", "image/png"
