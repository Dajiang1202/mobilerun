#!/usr/bin/env python3
"""批量可视化感知结果 —— 对一个目录的所有截图跑感知后端, 画框+结果, 存 viz/。

用于改完 ROI/阈值后批量验收识别效果。改顶部配置区即可。

用法: python gameauto/tools/viz_perception.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import gameauto.run_tft_replay as m
from gameauto.tools.cv_text import overlay_text, put_text_zh

# ═══════════════════════════════════════════════════════════════════════
#  配置区
# ═══════════════════════════════════════════════════════════════════════

SRC_DIR = r"D:\gameauto\mobilerun\gameauto\core\capture\scrcpy\captured"
BACKEND = "full"   # "full" (血条+ROI-OCR) | "text" (全图OCR+掉落物) | "champions" (仅血条)

# ═══════════════════════════════════════════════════════════════════════


def main():
    src = Path(SRC_DIR)
    out = src / "viz"
    out.mkdir(exist_ok=True)
    shots = sorted(src.glob("*.png"))
    shots = [s for s in shots if s.parent.name != "viz"]
    if not shots:
        print(f"在 {src} 找不到 png")
        sys.exit(1)

    backend = m.PERCEIVE_BACKENDS[BACKEND]
    print(f"{len(shots)} 张 | 后端={BACKEND} -> {out}\n")
    print(f"{'图':<16}{'棋盘':>4}{'战备':>4}  结果摘要")
    print("-" * 80)

    for sp in shots:
        img = cv2.imdecode(np.fromfile(str(sp), dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            print(f"{sp.name}: 读取失败")
            continue
        st = backend(img)
        disp = img.copy()
        h, w = img.shape[:2]

        for ov in st.get("overlays", []):
            l, t, r, b = ov.get("box", (0, 0, 0, 0))
            cv2.rectangle(disp, (l, t), (r, b), (0, 255, 0), 2)
            if ov.get("label"):
                disp = put_text_zh(disp, ov["label"], (l, max(0, t - 22)),
                                   color_bgr=(0, 255, 0), px=18)

        ocr = st.get("ocr", {})
        summary = (f"棋盘{st.get('champion_count', 0)} 战备{st.get('bench_count', 0)} | "
                   f"gold={ocr.get('gold', '')} stage={ocr.get('stage', '')} | "
                   f"shop={[ocr.get(f'shop{i}', '') for i in range(5)]}")
        # 顶部汇总 (半透明底, 不遮挡 stage/timer/HP)
        disp = overlay_text(disp, summary, (10, 14), color_bgr=(255, 255, 255), px=22)
        cv2.imencode(".png", disp)[1].tofile(str(out / sp.name))

        print(f"{sp.name:<16}{st.get('champion_count', 0):>4}{st.get('bench_count', 0):>4}  "
              f"gold={ocr.get('gold', '')!s:<5} stage={ocr.get('stage', '')!s:<7} "
              f"shop={' '.join((ocr.get(f'shop{i}', '') or '·') for i in range(5))}")

    print(f"\n完成, viz 在 {out}")


if __name__ == "__main__":
    main()
