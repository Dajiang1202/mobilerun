#!/usr/bin/env python3
"""
Minimal self-contained VLM board recognition demo.

No dependency on mobilerun config, jinja2 templates, or ConfigLoader.
Just one script: prompt, image, LLM call — everything inline.

Usage:
  python vlm_board_minimal.py screenshot.png
  python vlm_board_minimal.py screenshot.png --model gemini-3.1-flash-lite-preview
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time

# ── Config ──────────────────────────────────────────────────────────────
# Set these env vars for API keys (or edit the defaults below):
#   GOOGLE_API_KEY     — for GoogleGenAI
#   OPENAI_API_KEY     — for OpenAI / OpenAILike
#   DEEPSEEK_API_KEY   — for DeepSeek (OpenAILike)
#   OPENROUTER_API_KEY — for OpenRouter

DEFAULT_PROVIDER = "GoogleGenAI"
DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_API_BASE = ""  # only for OpenAILike providers
# ────────────────────────────────────────────────────────────────────────

JSON_RE = re.compile(r"\{[\s\S]*\}")

SYSTEM_PROMPT = """You are a game board perception module. Your ONLY job is to describe the current 开心消消乐 game board as a JSON object. Do NOT reason about swaps, matches, or strategy.

## Task

Look at the screenshot and output a JSON object describing the board layout and tile types.

## Output Format — STRICT JSON, nothing else

```json
{
  "rows": <number of rows>,
  "cols": <number of columns>,
  "board_left": <x coordinate of left edge of leftmost column in [0-1000]>,
  "board_top": <y coordinate of top edge of topmost row in [0-1000]>,
  "board_right": <x coordinate of right edge of rightmost column in [0-1000]>,
  "board_bottom": <y coordinate of bottom edge of bottommost row in [0-1000]>,
  "tiles": [
    ["tile_type", "tile_type", ...],
    ...
  ]
}
```

## Rules

1. **Board bounds**: Use the [0-1000] grid on the screenshot. board_left/board_top/board_right/board_bottom define the bounding box of the tile grid area.
2. **Rows and cols**: Count exactly how many rows and columns of tiles you see.
3. **Tiles**: Each tile should be identified by its type. Use consistent names:
   - **frog** (green)
   - **bear** (brown/orange)
   - **chick** (yellow)
   - **fox** (red/orange)
   - **hedgehog** (purple)
   - **cat** (blue)
   - **empty** (blank/empty cell)
   - **blocked** (ice, chain, or obstacle)
4. tiles[row][col] — row 0 is top, col 0 is left.
5. Output ONLY the JSON. No markdown, no explanation, no backticks. Pure JSON."""


def _extract_json(text: str) -> str | None:
    text = text.strip()
    if text.startswith("```"):
        end = text.rfind("```")
        if end > 3:
            text = text[text.index("\n") + 1:end].strip()
    m = JSON_RE.search(text)
    return m.group(0) if m else None


async def acall_with_retries(llm, messages, retries=3, timeout=120, delay=1.0, stream=False):
    """Minimal inline retry wrapper around llama-index achat/astream_chat."""
    last_exception = None

    for attempt in range(1, retries + 1):
        try:
            if stream:
                # ── streaming path ──────────────────────────────────
                content = ""
                async for chunk in await llm.astream_chat(messages=messages):
                    delta = chunk.delta or ""
                    if delta:
                        sys.stdout.write(delta)
                        sys.stdout.flush()
                    content += delta
                sys.stdout.write("\n")
                sys.stdout.flush()
                # Build a response-like object
                from llama_index.core.base.llms.types import ChatMessage, ChatResponse
                response = ChatResponse(
                    message=ChatMessage(role="assistant", content=content),
                )
            else:
                response = await asyncio.wait_for(
                    llm.achat(messages=messages), timeout=timeout,
                )

            if (response is not None
                    and getattr(response, "message", None) is not None
                    and getattr(response.message, "content", None)):
                return response
            else:
                last_exception = ValueError("Empty response content")

        except asyncio.TimeoutError:
            print(f"  [attempt {attempt} timed out]", file=sys.stderr)
            last_exception = TimeoutError("Timed out")
        except Exception as e:
            print(f"  [attempt {attempt} failed: {e!r}]", file=sys.stderr)
            last_exception = e

        if attempt < retries:
            await asyncio.sleep(delay * attempt)

    if last_exception:
        raise last_exception
    raise ValueError("All attempts returned empty response content")


def load_llm_simple(provider, model, api_base=""):
    """Create a llama-index LLM instance — no mobilerun config needed."""
    if provider == "GoogleGenAI":
        from llama_index.llms.google_genai import GoogleGenAI
        api_key = os.environ.get("GOOGLE_API_KEY")
        return GoogleGenAI(model=model, api_key=api_key)

    elif provider in ("OpenAIResponses",):
        from llama_index.llms.openai.responses import OpenAIResponses
        api_key = os.environ.get("OPENAI_API_KEY")
        return OpenAIResponses(model=model, api_key=api_key)

    elif provider == "OpenAILike":
        from llama_index.llms.openai_like import OpenAILike
        api_key = os.environ.get("OPENAI_API_KEY")
        base = api_base or "http://localhost:11434/v1"
        return OpenAILike(model=model, api_key=api_key, api_base=base, is_chat_model=True)

    elif provider == "DeepSeek":
        from llama_index.llms.openai_like import OpenAILike
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        return OpenAILike(
            model=model, api_key=api_key,
            api_base="https://api.deepseek.com", is_chat_model=True,
        )

    elif provider == "OpenRouter":
        from llama_index.llms.openrouter import OpenRouter
        api_key = os.environ.get("OPENROUTER_API_KEY")
        return OpenRouter(model=model, api_key=api_key)

    elif provider == "Ollama":
        from llama_index.llms.ollama import Ollama
        base = api_base or "http://localhost:11434"
        return Ollama(model=model, base_url=base)

    elif provider == "Anthropic":
        from llama_index.llms.anthropic import Anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        return Anthropic(model=model, api_key=api_key)

    else:
        raise ValueError(f"Unsupported provider: {provider}. "
                         f"Choose: GoogleGenAI, OpenAIResponses, OpenAILike, DeepSeek, OpenRouter, Ollama, Anthropic")


async def main():
    parser = argparse.ArgumentParser(description="Minimal VLM board recognition")
    parser.add_argument("image", help="Path to screenshot image")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER,
                        help=f"LLM provider (default: {DEFAULT_PROVIDER})")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE,
                        help="API base URL (for OpenAILike)")
    parser.add_argument("--stream", action="store_true", help="Stream output")
    parser.add_argument("--no-resize", action="store_true", help="Skip image resize")
    parser.add_argument("--max-side", type=int, default=640,
                        help="Max side length for resize (default: 640)")
    args = parser.parse_args()

    # ── 1. Load image ─────────────────────────────────────────────
    image_path = args.image
    with open(image_path, "rb") as f:
        img_bytes = f.read()

    # ── 2. Resize (optional) ──────────────────────────────────────
    if not args.no_resize:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        max_side = max(w, h)
        if max_side > args.max_side:
            scale = args.max_side / max_side
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            print(f"Resized: {w}x{h} -> {new_w}x{new_h}")
        else:
            print(f"Original size: {w}x{h}")

    # ── 3. Build messages ─────────────────────────────────────────
    from llama_index.core.base.llms.types import ChatMessage, ImageBlock, TextBlock

    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            blocks=[
                TextBlock(text="Output the board as JSON."),
                ImageBlock(image=img_bytes),
            ],
        ),
    ]

    # ── 4. Create LLM ─────────────────────────────────────────────
    llm = load_llm_simple(args.provider, args.model, args.api_base)
    print(f"Provider: {args.provider}  Model: {args.model}")

    # ── 5. Call VLM ───────────────────────────────────────────────
    t0 = time.time()
    response = await acall_with_retries(llm, messages, stream=args.stream)
    content = response.message.content or ""
    elapsed = time.time() - t0
    print(f"\nVLM latency: {elapsed:.1f}s")

    # ── 6. Parse & display ────────────────────────────────────────
    json_str = _extract_json(content)
    if not json_str:
        print("ERROR: No JSON found in response.")
        print(f"Raw response:\n{content}")
        sys.exit(1)

    board = json.loads(json_str)
    print(f"\nBoard: {board.get('rows', '?')}x{board.get('cols', '?')}")
    print(f"Bounds: L={board.get('board_left')}, T={board.get('board_top')}, "
          f"R={board.get('board_right')}, B={board.get('board_bottom')}")
    print("Tiles:")
    for i, row in enumerate(board.get("tiles", [])):
        print(f"  row{i}: {row}")

    # Write cleaned JSON to stdout for piping
    print(f"\n--- JSON ---")
    print(json.dumps(board, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
