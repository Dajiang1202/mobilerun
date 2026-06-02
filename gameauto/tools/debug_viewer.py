#!/usr/bin/env python3
"""Visual debug viewer: overlay recognition results on screenshots.

Usage:
    python tools/debug_viewer.py --replay logs/session_001/
    python tools/debug_viewer.py --image screenshot.png --board board.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.skills.match3.visualizer import annotate_board, annotate_swipe, annotate_multi_swipe


def main():
    parser = argparse.ArgumentParser(description="Visual debug viewer")
    parser.add_argument("--replay", type=str, help="Replay session directory")
    parser.add_argument("--image", type=str, help="Single screenshot")
    parser.add_argument("--board", type=str, help="Board JSON file (for --image mode)")
    parser.add_argument("--actions", type=str, help="Actions JSON file (for --image mode)")
    parser.add_argument("--output", type=str, default="debug_output.png", help="Output file")
    args = parser.parse_args()

    if args.replay:
        replay_dir = Path(args.replay)
        frames_dir = replay_dir / "frames"
        if not frames_dir.exists():
            print(f"No frames found in {args.replay}")
            return

        frame_dirs = sorted(d for d in frames_dir.iterdir() if d.is_dir())
        output_dir = Path("debug_output")
        output_dir.mkdir(exist_ok=True)

        for frame_dir in frame_dirs:
            screenshot_path = frame_dir / "screenshot.png"
            perception_path = frame_dir / "perception.json"
            actions_path = frame_dir / "actions.json"

            if not screenshot_path.exists():
                continue

            img = screenshot_path.read_bytes()

            # Overlay board grid
            if perception_path.exists():
                perception = json.loads(perception_path.read_text(encoding="utf-8"))
                board = perception.get("parsed", {})
                if board and "tiles" in board:
                    img = annotate_board(img, board)

            # Overlay swipe arrows
            if actions_path.exists():
                actions_data = json.loads(actions_path.read_text(encoding="utf-8"))
                swipes = []
                for a in actions_data:
                    if a.get("type") == "swipe":
                        swipes.append((a["x1"], a["y1"], a["x2"], a["y2"]))
                if len(swipes) == 1:
                    img = annotate_swipe(img, *swipes[0])
                elif len(swipes) > 1:
                    img = annotate_multi_swipe(img, swipes)

            out_path = output_dir / f"{frame_dir.name}.png"
            out_path.write_bytes(img)
            print(f"  {out_path}")

        print(f"\n{len(frame_dirs)} frames written to {output_dir}/")

    elif args.image and args.board:
        img = Path(args.image).read_bytes()
        board = json.loads(Path(args.board).read_text(encoding="utf-8"))
        result = annotate_board(img, board)

        if args.actions:
            actions_data = json.loads(Path(args.actions).read_text(encoding="utf-8"))
            swipes = [(a["x1"], a["y1"], a["x2"], a["y2"]) for a in actions_data if a.get("type") == "swipe"]
            for s in swipes:
                result = annotate_swipe(result, *s)

        Path(args.output).write_bytes(result)
        print(f"Output: {args.output}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
