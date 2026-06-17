#!/usr/bin/env python3
"""交互式模板适配工具 — 双窗口对照标注。

左窗口 "Template"：原版模板（3x 放大）
右窗口 "Screenshot"：手机截图（等比缩放至 700px 高度）
工作流：← → 切模板，↑ ↓ 切截图，截图窗口鼠标框选 → 自动同名保存。

用法:
    python gameauto/tools/label_templates.py
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SCREENSHOT_DIR = Path("D:/screenshots")
REF_DIR = Path(__file__).parent.parent.parent / "tmp" / "ref_templates"
SKILL_DIR = Path(__file__).parent.parent / "skills" / "doudizhu_douzero"
TEMPLATE_DIR = SKILL_DIR / "assets" / "templates"
ROI_CONFIG_PATH = SKILL_DIR / "assets" / "rois.json"

SCR_H = 700  # screenshot display height (width auto from aspect ratio)

# Data
ref_imgs = []
screen_imgs = []
ref_idx = 0
scr_idx = 0
category = "buttons"

# Drawing
drawing = False
start_x, start_y = -1, -1
roi = None           # [x1,y1,x2,y2] in DISPLAY coords
scr_orig = None      # original screenshot (full resolution)
scr_disp = None      # resized screenshot for display
disp_w = disp_h = 0  # display dimensions
scale_x = scale_y = 1.0  # orig → display scale


def load_images(path, exts, label):
    imgs = []
    for f in sorted(path.glob("*")):
        if f.suffix.lower() not in exts:
            continue
        try:
            arr = np.array(Image.open(str(f)).convert("RGB"))
            imgs.append((f.name, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)))
        except Exception:
            pass
    print(f"{label}: {len(imgs)}")
    return imgs


def build_display():
    """Resize screenshot to SCR_H height, preserving aspect ratio."""
    global scr_disp, disp_w, disp_h, scale_x, scale_y, roi
    if scr_orig is None:
        return
    h, w = scr_orig.shape[:2]
    scale_y = SCR_H / h
    scale_x = scale_y  # preserve aspect ratio
    disp_w = int(w * scale_x)
    disp_h = SCR_H
    scr_disp = cv2.resize(scr_orig, (disp_w, disp_h))

    # Draw ROI overlay
    if roi:
        x1, y1, x2, y2 = roi
        cv2.rectangle(scr_disp, (x1, y1), (x2, y2), (0, 0, 255), 2)

    # Status bar
    name = screen_imgs[scr_idx][0]
    cv2.putText(scr_disp, f"{name}  [{scr_idx+1}/{len(screen_imgs)}]",
                (5, disp_h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)


def mouse(event, x, y, _f, _p):
    """Mouse coords are in DISPLAY space."""
    global drawing, start_x, start_y, roi

    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        start_x, start_y = x, y
        roi = [x, y, x, y]

    elif event == cv2.EVENT_MOUSEMOVE and drawing:
        roi[2], roi[3] = x, y

    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        x1, y1 = min(start_x, x), min(start_y, y)
        x2, y2 = max(start_x, x), max(start_y, y)

        if x2 - x1 < 10 or y2 - y1 < 10:
            roi = None; build_display(); return

        # Clamp to display bounds
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(disp_w, x2), min(disp_h, y2)
        roi = [x1, y1, x2, y2]

        # Convert to original coords for cropping
        ox1, oy1 = int(x1 / scale_x), int(y1 / scale_y)
        ox2, oy2 = int(x2 / scale_x), int(y2 / scale_y)

        # Auto-save with reference template name
        ref_name = Path(ref_imgs[ref_idx][0]).stem
        fname = f"{ref_name}.png"
        out_dir = TEMPLATE_DIR / category
        out_dir.mkdir(parents=True, exist_ok=True)
        crop = scr_orig[oy1:oy2, ox1:ox2]
        cv2.imwrite(str(out_dir / fname), crop)
        print(f"\n  {category}/{fname}  ({ox2-ox1}x{oy2-oy1})  ← {ref_name}")

        # Export ROI config (normalized [0-1])
        oh, ow = scr_orig.shape[:2]
        if not ROI_CONFIG_PATH.exists():
            ROI_CONFIG_PATH.write_text("{}")
        cfg = json.loads(ROI_CONFIG_PATH.read_text(encoding="utf-8"))
        cfg.setdefault("rois", {})[ref_name] = [
            round(ox1 / ow, 4), round(oy1 / oh, 4),
            round(ox2 / ow, 4), round(oy2 / oh, 4),
        ]
        cfg["resolution"] = [ow, oh]
        cfg["category"] = category
        ROI_CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

    build_display()


def load_scr():
    global roi
    roi = None
    build_display()
    _status()


def _status():
    ref_n = ref_imgs[ref_idx][0]
    scr_n = screen_imgs[scr_idx][0]
    print(f"\r [{ref_idx+1}/{len(ref_imgs)}] {ref_n:20s} | [{scr_idx+1}/{len(screen_imgs)}] {scr_n:30s} | {category}/ ←→↑↓ c/b/u/o q",
          end="", flush=True)


def main():
    global ref_imgs, screen_imgs, ref_idx, scr_idx, category, roi, scr_orig

    ref_imgs = load_images(REF_DIR, {".png"}, "参考模板")
    screen_imgs = load_images(SCREENSHOT_DIR, {".jpeg", ".jpg", ".png"}, "手机截图")
    if not ref_imgs or not screen_imgs:
        print("错误: 缺少图片"); sys.exit(1)

    for d in ["cards", "buttons", "ui", "others"]:
        (TEMPLATE_DIR / d).mkdir(parents=True, exist_ok=True)

    cv2.namedWindow("Template", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Template", 250, 250)
    cv2.moveWindow("Template", 0, 0)

    cv2.namedWindow("Screenshot", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("Screenshot", mouse)
    cv2.moveWindow("Screenshot", 270, 0)

    # Skip bg.png
    for i, (n, _) in enumerate(ref_imgs):
        if not n.startswith("bg"):
            ref_idx = i; break

    _, scr_orig = screen_imgs[scr_idx]
    load_scr()
    _status()

    while True:
        # Template window
        _, ref = ref_imgs[ref_idx]
        ref_show = cv2.resize(ref, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        cv2.imshow("Template", ref_show)
        cv2.setWindowTitle("Template",
            f"T [{ref_idx+1}/{len(ref_imgs)}] {ref_imgs[ref_idx][0]}")

        # Screenshot window
        if scr_disp is not None:
            cv2.imshow("Screenshot", scr_disp)
            cv2.setWindowTitle("Screenshot",
                f"S [{scr_idx+1}/{len(screen_imgs)}] {category}/")

        key = cv2.waitKey(50) & 0xFF

        if key == ord('q'):
            break
        elif key == 83:  # →
            ref_idx = (ref_idx + 1) % len(ref_imgs); _status()
        elif key == 81:  # ←
            ref_idx = (ref_idx - 1) % len(ref_imgs); _status()
        elif key == 84:  # ↓
            scr_idx = (scr_idx + 1) % len(screen_imgs)
            _, scr_orig = screen_imgs[scr_idx]; load_scr()
        elif key == 82:  # ↑
            scr_idx = (scr_idx - 1) % len(screen_imgs)
            _, scr_orig = screen_imgs[scr_idx]; load_scr()
        elif key == ord('c'):
            category = "cards"; _status()
        elif key == ord('b'):
            category = "buttons"; _status()
        elif key == ord('u'):
            category = "ui"; _status()
        elif key == ord('o'):
            category = "others"; _status()

    cv2.destroyAllWindows()
    print(f"\n模板: {TEMPLATE_DIR}")


if __name__ == "__main__":
    main()
