#!/usr/bin/env python3
"""Test HDC capture: connection + screenshot.

Usage:
    python tools/test_capture.py
    python tools/test_capture.py --serial xxx --output test.png
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.core.capture.hdc import HdcCapture


async def main():
    parser = argparse.ArgumentParser(description="Test HDC capture")
    parser.add_argument("--serial", type=str, help="Device serial")
    parser.add_argument("--hdc-path", type=str, default="hdc", help="Path to hdc binary")
    parser.add_argument("--method", type=str, default="auto", choices=["auto", "snapshot", "screenCap"])
    parser.add_argument("--output", type=str, default="test_screenshot.png", help="Output file")
    args = parser.parse_args()

    capture = HdcCapture(serial=args.serial, hdc_path=args.hdc_path, screenshot_method=args.method)
    print(f"Connecting to HDC device...")
    await capture.connect()
    print(f"Connected! Resolution: {capture.native_resolution}")

    img = await capture.screenshot()
    Path(args.output).write_bytes(img)
    print(f"Screenshot saved to {args.output} ({len(img)} bytes)")

    await capture.disconnect()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
