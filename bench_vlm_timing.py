#!/usr/bin/env python3
"""Timing benchmark: OLD vs NEW preprocessing — both with enable_thinking=False."""

import asyncio, base64, time
from io import BytesIO
from pathlib import Path
import httpx
from openai import AsyncOpenAI
from PIL import Image
from gameauto.config.loader import load_global_config
from gameauto.utils.images import resize_image_to_max_side, image_dimensions

IMAGE_PATH = Path("game_logs/20260528_214520_177/round_002/step0_original.png")
PROMPT_PATH = Path("gameauto/skills/match3/prompts/generic.jinja2")
USER_PROMPT = "Output the board as JSON."

cfg = load_global_config()
vlm = cfg.get("vlm", {})
MODEL = vlm.get("model", "gpt-4o")
BASE_URL = vlm.get("base_url", "")
API_KEY = vlm.get("api_key", "")

SYSTEM_TEXT = PROMPT_PATH.read_text("utf-8")


def prep_old(img_bytes):
    with Image.open(BytesIO(img_bytes)) as img:
        img = img.convert("RGBA")
        buf = BytesIO(); img.save(buf, format="PNG")
        raw = buf.getvalue()
    return raw, f"data:image/png;base64,{base64.b64encode(raw).decode()}"


def prep_new(img_bytes):
    raw = resize_image_to_max_side(img_bytes, max_side=1024)
    return raw, f"data:image/png;base64,{base64.b64encode(raw).decode()}"


async def call(label, data_url, client):
    print(f"\n  [{label}] sending... ({len(data_url):,} chars)")
    t0 = time.perf_counter()
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_TEXT},
                {"role": "user", "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
            temperature=0.2, timeout=300.0,
            extra_body={"enable_thinking": False},
        )
        elapsed = time.perf_counter() - t0
        u = resp.usage
        content = resp.choices[0].message.content or ""
        print(f"  [{label}] done: {elapsed:.1f}s | tokens: prompt={u.prompt_tokens} completion={u.completion_tokens} total={u.total_tokens}")
        return elapsed, u.total_tokens, len(content), ""
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"  [{label}] FAIL: {elapsed:.1f}s | {type(e).__name__}")
        return elapsed, 0, 0, str(e)[:80]


async def main():
    raw = IMAGE_PATH.read_bytes()
    ow, oh = image_dimensions(raw)

    old_bytes, old_url = prep_old(raw)
    new_bytes, new_url = prep_new(raw)
    ow2, oh2 = image_dimensions(old_bytes)
    nw, nh = image_dimensions(new_bytes)

    old_tiles = ((ow2+511)//512) * ((oh2+511)//512)
    new_tiles = ((nw+511)//512) * ((nh+511)//512)

    print(f"Image: {IMAGE_PATH.name}  original={ow}x{oh}  {len(raw):,}B")
    print(f"Model: {MODEL}  (enable_thinking=False)")
    print()
    print(f"{'':<16} {'res':<12} {'PNG':<10} {'base64':<10} {'tiles':<8} {'est.img_tok':<12}")
    print(f"{'OLD (raw)':<16} {ow2}x{oh2:<6} {len(old_bytes):<10,} {len(old_url):<10,} {old_tiles:<8} ~{old_tiles*170}")
    print(f"{'NEW (1024)':<16} {nw}x{nh:<6} {len(new_bytes):<10,} {len(new_url):<10,} {new_tiles:<8} ~{new_tiles*170}")

    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, http_client=httpx.AsyncClient(proxy=None))

    new_t, new_tok, new_out, new_err = await call("NEW (1024px)", new_url, client)
    old_t, old_tok, old_out, old_err = await call("OLD (raw)", old_url, client)

    await client.close()

    print(f"\n{'='*65}")
    print(f"  RESULTS  (enable_thinking=False)")
    print(f"{'='*65}")
    print(f"{'':<16} {'status':<10} {'wall time':<12} {'total_tok':<12} {'output':<10}")
    print(f"{'OLD (raw)':<16} {'FAIL' if old_err else 'OK':<10} {old_t:<12.1f}s {old_tok:<12} {old_out:<10}")
    print(f"{'NEW (1024)':<16} {'FAIL' if new_err else 'OK':<10} {new_t:<12.1f}s {new_tok:<12} {new_out:<10}")

    if not old_err and not new_err:
        dt = old_t - new_t
        dtok = old_tok - new_tok
        print(f"\n  Time:  {old_t:.0f}s -> {new_t:.0f}s  ({dt:+.0f}s, {old_t/new_t:.1f}x faster)")
        print(f"  Token: {old_tok} -> {new_tok}  ({dtok:+d}, {dtok/old_tok*100:.0f}% saved)")
    elif new_err:
        print(f"\n  Both failed or NEW failed")
    else:
        print(f"\n  OLD failed after {old_t:.0f}s ({old_err})")
        print(f"  NEW: {new_t:.0f}s, {new_tok} tokens, {new_out} chars output")

asyncio.run(main())
