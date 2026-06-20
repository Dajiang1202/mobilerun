#!/usr/bin/env python3
"""天天象棋棋盘标定 —— 从 rois.json[board_rect] 生成 board.json。

裁剪器(crop_template.py --skill xiangqi)框选 board_rect 时会把归一化框
写入 assets/rois.json["board_rect"] = {"src": <截图名>, "box": [x1,y1,x2,y2](归一化[0-1])}。
本脚本据该框 + 源截图分辨率, 生成 assets/board.json:
    {"resolution": [w, h], "board_rect_px": [x1, y1, x2, y2]}
board_rect_px = [col1的x, row10(黑方顶)的y, col9的x, row1(红方底)的y]。

用法
    python gameauto/tools/xiangqi_finalize_board.py
    python gameauto/tools/xiangqi_finalize_board.py --assets <skill assets dir> --screens D:/screenshots
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

DEFAULT_ASSETS = (Path(__file__).resolve().parent.parent
                  / "skills" / "xiangqi" / "assets")
DEFAULT_SCREENS = Path("D:/screenshots")


def _find_resolution(src_name: str, screens_dir: Path) -> tuple[int, int] | None:
    """在 screens_dir 找到 src 截图, 返回 (w, h)。"""
    cand = list(screens_dir.glob(src_name))
    if not cand:  # 容忍子目录 / 扩展名差异
        cand = list(screens_dir.rglob(Path(src_name).stem + "*"))
    if not cand:
        return None
    with Image.open(str(cand[0])) as im:
        return im.size  # (width, height)


def main() -> int:
    ap = argparse.ArgumentParser(description="由 rois.json[board_rect] 生成 board.json")
    ap.add_argument("--assets", default=str(DEFAULT_ASSETS), help="skill assets 目录")
    ap.add_argument("--screens", default=str(DEFAULT_SCREENS), help="截图目录(用于读取 board_rect.src 分辨率)")
    ap.add_argument("--resolution", default=None, help="手动指定分辨率 WxH(跳过从截图读取)")
    ap.add_argument("--box", default=None, help="手动指定 board_rect 归一化框 x1,y1,x2,y2 (跳过 rois.json)")
    args = ap.parse_args()

    assets = Path(args.assets)
    # rois.json 由裁剪器写到 templates/ 目录; 兼容历史(assets/ 根)。
    rois_path = assets / "templates" / "rois.json"
    if not rois_path.exists():
        rois_path = assets / "rois.json"
    board_path = assets / "board.json"

    # ── 取归一化框 ──────────────────────────────────────────────
    if args.box:
        box = [float(v) for v in args.box.split(",")]
        src = "<manual>"
    else:
        if not rois_path.exists():
            print(f"错误: 找不到 {rois_path}。先运行 crop_template.py --skill xiangqi 标定 board_rect, 或用 --box 手填。")
            return 1
        rois = json.loads(rois_path.read_text(encoding="utf-8"))
        entry = rois.get("board_rect")
        if not entry or "box" not in entry:
            print(f"错误: {rois_path} 中没有 board_rect 条目。先在裁剪器里框选 board_rect。")
            return 1
        box = entry["box"]
        src = entry.get("src", "")

    if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        print(f"错误: box 非法 {box} (应为 x1,y1,x2,y2, 归一化[0-1] 且 x1<x2,y1<y2)")
        return 1

    # ── 取分辨率 ────────────────────────────────────────────────
    if args.resolution:
        w, h = (int(v) for v in args.resolution.lower().split("x"))
    else:
        res = _find_resolution(src, Path(args.screens))
        if res is None:
            print(f"错误: 在 {args.screens} 找不到截图 '{src}' 来读取分辨率。用 --resolution WxH 手动指定。")
            return 1
        w, h = res

    x1, y1, x2, y2 = box
    rect_px = [round(x1 * w), round(y1 * h), round(x2 * w), round(y2 * h)]
    board = {
        "resolution": [w, h],
        "board_rect_px": rect_px,
        "note": (f"由 rois.json[board_rect] 生成 (src={src})。"
                 f"board_rect_px=[col1_x, row10_y, col9_x, row1_y]。"),
    }
    board_path.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 已写入 {board_path}")
    print(f"  resolution     = {w}x{h}")
    print(f"  board_rect_px  = {rect_px}")
    cell_w = (rect_px[2] - rect_px[0]) / 8
    cell_h = (rect_px[3] - rect_px[1]) / 9
    print(f"  cell_w={cell_w:.1f}px  cell_h={cell_h:.1f}px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
