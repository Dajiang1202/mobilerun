#!/usr/bin/env python3
"""本地 VLM 模型快速测试 — 消消乐棋盘识别 + 可视化。

用法:
    # 默认截图
    python tools/test_local_vlm.py

    # 指定截图
    python tools/test_local_vlm.py --image D:/AI/qw/screenshot.png

    # 自定义模型地址
    python tools/test_local_vlm.py --base-url http://localhost:8000/v1 \\
        --model /mnt/d/AI/qw/models/Qwen2-VL-7B-Instruct-AWQ

    # 也用 TFT 感知 prompt 测试（金铲铲阵容决策）
    python tools/test_local_vlm.py --mode tft

输出:
    - 控制台: 模型原始输出 + 解析后的 JSON + 延迟
    - board_annotated.png: 棋盘网格可视化
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.core.perception.vlm_client import VlmClient
from gameauto.utils.images import image_dimensions


def load_prompt(mode: str = "match3") -> str:
    """Load jinja2 prompt template."""
    base = Path(__file__).parent.parent
    if mode == "match3":
        p = base / "skills" / "match3" / "prompts" / "generic.jinja2"
    else:
        # TFT strategic prompt (Phase 2 preview)
        p = base / "skills" / "tft" / "prompts" / "strategy.jinja2" if (base / "skills" / "tft" / "prompts").exists() else None
        if not p or not p.exists():
            # Fallback: simple TFT prompt
            return _TFT_PROMPT

    if not p.exists():
        print(f"Prompt not found: {p}")
        sys.exit(1)
    return p.read_text(encoding="utf-8")


_TFT_PROMPT = """你是金铲铲之战的策略顾问。根据当前游戏截图，分析:
1. 当前金币数、等级、血量
2. 场上棋子和备战席棋子
3. 建议本回合操作（买什么、是否刷新、是否升级）
输出严格 JSON，不要其他文字。
{
  "gold": <number>,
  "level": <number>,
  "hp": <number>,
  "stage": "<stage>",
  "shop_units": [<list of champion names>],
  "recommendation": "<buy/roll/level/pass>",
  "reason": "<brief reason>"
}"""


def extract_json(text: str) -> dict | None:
    """Extract JSON from VLM response (handles markdown code blocks)."""
    # Try ```json ... ``` block first
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # Try raw JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON object in text
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


async def main():
    parser = argparse.ArgumentParser(description="Test local VLM perception")
    parser.add_argument("--image", default="D:/AI/qw/screenshot.png", help="Screenshot path")
    parser.add_argument("--mode", default="match3", choices=["match3", "tft"],
                        help="Perception mode (match3 board or TFT strategy)")
    parser.add_argument("--base-url", default="http://localhost:8000/v1", help="vLLM API base URL")
    parser.add_argument("--model", default="/mnt/d/AI/qw/models/Qwen2-VL-7B-Instruct-AWQ",
                        help="Model name/path")
    parser.add_argument("--timeout", type=float, default=120.0, help="Request timeout (s)")
    parser.add_argument("--output", default="board_annotated.png", help="Output annotated image")
    args = parser.parse_args()

    # ── Load image ────────────────────────────────────────────────────
    img_path = Path(args.image)
    if not img_path.exists():
        print(f"Image not found: {img_path}")

        # Try alternate paths
        for alt in ["D:/screenshots/screenshot.png", "D:/screenshots/latest.png"]:
            if Path(alt).exists():
                img_path = Path(alt)
                break
        else:
            print("No screenshot found. Specify with --image")
            sys.exit(1)

    img_bytes = img_path.read_bytes()
    w, h = image_dimensions(img_bytes)
    print(f"Image:  {img_path.name}  ({w}x{h}, {len(img_bytes)/1024:.0f}KB)")
    print(f"Model:  {args.model.rsplit('/', 1)[-1]}")
    print(f"API:    {args.base_url}")
    print(f"Mode:   {args.mode}")
    print()

    # ── Load prompt ────────────────────────────────────────────────────
    prompt = load_prompt(args.mode)
    user_msg = "Output the board as JSON." if args.mode == "match3" else "分析截图，输出JSON。"

    print(f"Prompt: {len(prompt)} chars  |  User: {user_msg}")

    # ── Create VlmClient ──────────────────────────────────────────────
    vlm = VlmClient(
        model=args.model,
        base_url=args.base_url,
        api_key="local",  # local vLLM doesn't need a real key
        temperature=0.2,
    )

    # ── Run perception ────────────────────────────────────────────────
    print("Calling VLM...", end=" ", flush=True)
    t0 = time.perf_counter()

    try:
        raw_response = await vlm.chat(
            system_prompt=prompt,
            user_prompt=user_msg,
            image=img_bytes,
            timeout=args.timeout,
        )
    except Exception as e:
        print(f"\nERROR: {e}")
        print("\nTroubleshooting:")
        print("  1. Check vLLM server is running: curl", args.base_url + "/models")
        print("  2. Check model path is correct")
        print("  3. Try with smaller image or shorter timeout")
        sys.exit(1)

    latency = (time.perf_counter() - t0) * 1000
    tokens = len(raw_response)
    tps = tokens / (latency / 1000) if latency > 0 else 0
    print(f"done")
    print()

    # ── Stats ──────────────────────────────────────────────────────────
    print("=" * 60)
    print(f"Latency:   {latency:.0f}ms")
    print(f"Tokens:    {tokens} output")
    print(f"Speed:     {tps:.0f} tok/s")
    print("=" * 60)
    print()

    # ── Print raw response ────────────────────────────────────────────
    print("RAW VLM RESPONSE:")
    print("-" * 60)
    print(raw_response[:3000])
    if len(raw_response) > 3000:
        print(f"\n... ({len(raw_response)} chars total, truncated)")
    print("-" * 60)
    print()

    # ── Parse JSON ────────────────────────────────────────────────────
    parsed = extract_json(raw_response)
    if not parsed:
        print("⚠ Could not parse JSON from response. Raw text above may still be useful.")
        print("  Common issues: markdown wrapping, extra text before/after JSON.")
        print("  Try: --mode match3 (uses stricter JSON prompt)")
    else:
        print("PARSED JSON:")
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        print()

        # ── Mode-specific summary ─────────────────────────────────────
        if args.mode == "match3":
            print(f"Board:   {parsed.get('rows', '?')}×{parsed.get('cols', '?')}")
            board = (parsed.get('board_left'), parsed.get('board_top'),
                     parsed.get('board_right'), parsed.get('board_bottom'))
            print(f"Bounds:  {board}")
            tiles = parsed.get("tiles", [])
            if tiles:
                n_empty = sum(1 for r in tiles for t in r if t == "empty")
                n_blocked = sum(1 for r in tiles for t in r if t == "blocked")
                n_normal = sum(1 for r in tiles for t in r if t not in ("empty", "blocked"))
                print(f"Tiles:   {n_normal} normal, {n_empty} empty, {n_blocked} blocked")
        else:
            print(f"Gold:    {parsed.get('gold', '?')}  |  Level: {parsed.get('level', '?')}  |  HP: {parsed.get('hp', '?')}")
            print(f"Stage:   {parsed.get('stage', '?')}")
            print(f"Action:  {parsed.get('recommendation', '?')} — {parsed.get('reason', '?')}")

    # ── Generate visualization ────────────────────────────────────────
    if args.mode == "match3" and parsed and "tiles" in parsed:
        print()
        print(f"Generating board visualization → {args.output} ...", end=" ", flush=True)
        try:
            from gameauto.skills.match3.visualizer import annotate_board
            annotated = annotate_board(img_bytes, parsed)
            Path(args.output).write_bytes(annotated)
            print(f"done ({len(annotated)/1024:.0f}KB)")
        except Exception as e:
            print(f"ERROR: {e}")

    print()
    print("=" * 60)
    model_short = args.model.rsplit('/', 1)[-1]
    status = "✓" if parsed else "⚠ (no JSON)"
    print(f"{status}  {model_short}  |  {latency:.0f}ms  |  {tokens}tok  |  {tps:.0f} tok/s")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
