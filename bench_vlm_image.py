#!/usr/bin/env python3
"""Benchmark: old (raw) vs new (resize 1024px RGB) image preprocessing for VLM."""

from __future__ import annotations

import asyncio
import base64
import time
import sys
from io import BytesIO
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from PIL import Image

from gameauto.config.loader import load_global_config
from gameauto.utils.images import (
    resize_image_to_max_side,
    image_dimensions,
)

# ── Config ──────────────────────────────────────────────────────────────
IMAGE_PATH = Path("game_logs/20260528_214520_177/round_002/step0_original.png")
MATCH3_PROMPT = Path("gameauto/skills/match3/prompts/generic.jinja2").read_text("utf-8")
USER_PROMPT = "Output the board as JSON."

cfg = load_global_config()
vlm_cfg = cfg.get("vlm", {})
MODEL = vlm_cfg.get("model", "gpt-4o")
BASE_URL = vlm_cfg.get("base_url", "")
API_KEY = vlm_cfg.get("api_key", "")


# ── Helpers ─────────────────────────────────────────────────────────────

def prep_old(image_bytes: bytes) -> tuple[bytes, str]:
    """Current behavior: no resize, RGBA PNG → base64 data URL."""
    with Image.open(BytesIO(image_bytes)) as img:
        img = img.convert("RGBA")
        buf = BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
    b64 = base64.b64encode(raw).decode("ascii")
    return raw, f"data:image/png;base64,{b64}"


def prep_new(image_bytes: bytes) -> tuple[bytes, str]:
    """New behavior: resize to 1024px max, RGB, → base64 data URL."""
    raw = resize_image_to_max_side(image_bytes, max_side=1024)
    b64 = base64.b64encode(raw).decode("ascii")
    return raw, f"data:image/png;base64,{b64}"


async def call_vlm(client: AsyncOpenAI, label: str, data_url: str) -> tuple[str, float, dict, str]:
    """Send one VLM request, return (response_text, elapsed, usage_dict, error)."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  data_url length: {len(data_url):,} chars")

    t0 = time.perf_counter()
    error = ""
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": MATCH3_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": USER_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0.2,
            timeout=120.0,
        )
        elapsed = time.perf_counter() - t0

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            if hasattr(response.usage, "image_tokens"):
                usage["image_tokens"] = response.usage.image_tokens

        content = response.choices[0].message.content or ""
        return content, elapsed, usage, error

    except Exception as e:
        elapsed = time.perf_counter() - t0
        error = f"{type(e).__name__}: {e}"
        return "", elapsed, {}, error


# ── Main ────────────────────────────────────────────────────────────────

async def main():
    if not IMAGE_PATH.exists():
        print(f"Image not found: {IMAGE_PATH}")
        sys.exit(1)

    original_bytes = IMAGE_PATH.read_bytes()
    ow, oh = image_dimensions(original_bytes)
    print(f"Input: {IMAGE_PATH}")
    print(f"  dimensions: {ow}×{oh}")
    print(f"  file size:  {len(original_bytes):,} bytes ({len(original_bytes)/1024:.0f} KB)")
    print(f"  VLM model:  {MODEL}")
    print(f"  Base URL:   {BASE_URL}")

    # ── Preprocess ──────────────────────────────────────────────────
    # OLD
    t0 = time.perf_counter()
    old_bytes, old_url = prep_old(original_bytes)
    old_prep_ms = (time.perf_counter() - t0) * 1000
    old_w, old_h = image_dimensions(old_bytes)

    # NEW
    t0 = time.perf_counter()
    new_bytes, new_url = prep_new(original_bytes)
    new_prep_ms = (time.perf_counter() - t0) * 1000
    new_w, new_h = image_dimensions(new_bytes)

    # ── Preprocess summary ─────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  PREPROCESS COMPARISON")
    print(f"{'─'*70}")
    print(f"{'Method':<14} {'Dims':<14} {'PNG bytes':<12} {'Base64 len':<14} {'Prep time':<10}")
    print(f"{'─'*70}")
    print(f"{'OLD (raw)':<14} {old_w}×{old_h:<8} {len(old_bytes):<12,} {len(old_url):<14,} {old_prep_ms:.1f}ms")
    print(f"{'NEW (1024)':<14} {new_w}×{new_h:<8} {len(new_bytes):<12,} {len(new_url):<14,} {new_prep_ms:.1f}ms")
    ratio = len(new_bytes) / len(old_bytes) * 100
    print(f"{'─'*70}")
    print(f"  PNG size: {ratio:.0f}% of original ({len(old_bytes)-len(new_bytes):,} bytes saved)")
    print(f"  Base64:   {len(new_url)/len(old_url)*100:.0f}% of original ({len(old_url)-len(new_url):,} chars saved)")

    # Tile estimate
    old_tiles = ((old_w + 511) // 512) * ((old_h + 511) // 512)
    new_tiles = ((new_w + 511) // 512) * ((new_h + 511) // 512)
    print(f"\n  [Tile] OpenAI-style tile estimate (512×512 tiles × 170 tokens):")
    print(f"    OLD: {old_tiles} tiles → ~{old_tiles * 170} image tokens")
    print(f"    NEW: {new_tiles} tiles → ~{new_tiles * 170} image tokens")
    print(f"    [Money] Estimated savings: {old_tiles * 170 - new_tiles * 170} tokens ({(1-new_tiles/old_tiles)*100:.0f}%)")

    # ── VLM calls ──────────────────────────────────────────────────
    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
        http_client=httpx.AsyncClient(proxy=None),
    )

    # Run NEW first (more likely to succeed)
    new_text, new_elapsed, new_usage, new_err = await call_vlm(
        client, "NEW — 1024px max (optimized)", new_url
    )

    old_text, old_elapsed, old_usage, old_err = await call_vlm(
        client, "OLD — raw 1344×2772 (current)", old_url
    )

    await client.close()

    # ── Final report ───────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print(f"  FINAL REPORT")
    print(f"{'='*70}")
    print(f"{'Method':<16} {'Dims':<12} {'PNG bytes':<10} {'Result':<12} {'Wall time':<10}")
    print(f"{'─'*70}")

    old_status = f"FAIL: {old_err[:60]}" if old_err else f"OK: {len(old_text)} chars"
    new_status = f"FAIL: {new_err[:60]}" if new_err else f"OK: {len(new_text)} chars"
    print(f"{'OLD (raw)':<16} {old_w}×{old_h:<8} {len(old_bytes):<10,} {old_status:<12} {old_elapsed:.1f}s")
    print(f"{'NEW (1024)':<16} {new_w}×{new_h:<8} {len(new_bytes):<10,} {new_status:<12} {new_elapsed:.1f}s")

    # Token usage
    print(f"\n{'─'*70}")
    print(f"  TOKEN USAGE (API reported)")
    print(f"{'─'*70}")
    for label, usage in [("OLD", old_usage), ("NEW", new_usage)]:
        if usage:
            print(f"  {label}:")
            for k, v in usage.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {label}: (no usage data)")

    # Bottom line
    print(f"\n{'='*70}")
    if old_err and not new_err:
        print(f"  !! OLD method FAILED - payload too large for API!")
        print(f"  OK NEW method SUCCEEDED - resize makes VLM call possible")
    elif not old_err and not new_err:
        if old_usage and new_usage:
            saved = old_usage.get("total_tokens", 0) - new_usage.get("total_tokens", 0)
            pct = saved / max(old_usage.get("total_tokens", 1), 1) * 100
            print(f"  [Money] Token savings: {saved} tokens ({pct:.1f}%)")
            time_delta = old_elapsed - new_elapsed
            print(f"  Time: OLD={old_elapsed:.1f}s  NEW={new_elapsed:.1f}s  delta={time_delta:+.1f}s")
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
