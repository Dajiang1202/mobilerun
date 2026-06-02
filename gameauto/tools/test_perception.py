#!/usr/bin/env python3
"""Test match-3 perception on a single screenshot or replay session.

Usage:
    python tools/test_perception.py --image screenshot.png
    python tools/test_perception.py --replay logs/session_001/
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.config.loader import load_config
from gameauto.core.perception.vlm_client import VlmClient
from gameauto.skills.match3.perception import Match3Perception


async def main():
    parser = argparse.ArgumentParser(description="Test match-3 perception")
    parser.add_argument("--image", type=str, help="Single screenshot to test")
    parser.add_argument("--replay", type=str, help="Replay session directory")
    parser.add_argument("--config", type=str, help="Config file path")
    args = parser.parse_args()

    config = load_config(args.config)
    vlm_cfg = config.get("vlm", {})
    vlm = VlmClient(
        model=vlm_cfg.get("model", "gpt-4o"),
        base_url=vlm_cfg.get("base_url", ""),
        api_key=vlm_cfg.get("api_key", ""),
    )

    prompt_path = config.get("perception", {}).get("prompt_path", "")
    if not Path(prompt_path).exists():
        prompt_path = Path(__file__).parent.parent / prompt_path

    perception = Match3Perception.from_prompt_file(vlm, prompt_path)
    print(f"VLM: {vlm_cfg.get('model')} | Prompt: {prompt_path}")

    if args.image:
        img = Path(args.image).read_bytes()
        result = await perception.recognize(img)
        print(f"\nRaw response:\n{result.raw_response[:500]}...")
        print(f"\nParsed board: {result.parsed.get('rows', '?')}x{result.parsed.get('cols', '?')}")
        tiles = result.parsed.get("tiles", [])
        for i, row in enumerate(tiles[:5]):
            print(f"  row{i}: {row[:8]}...")
    elif args.replay:
        print(f"Replay mode not yet implemented for: {args.replay}")
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
