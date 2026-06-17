#!/usr/bin/env python3
"""交互式模板适配工具 — 原版模板 → 手机截图。

左侧：原版 DouZero 模板（一次展示一个）
右侧：手机截图（6 张中选）
工作流：← → 切模板，↑ ↓ 切截图，鼠标框选 → 自动同名保存。

用法:
    python gameauto/tools/label_templates.py
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# Paths
SCREENSHOT_DIR = Path("D:/screenshots")
REF_DIR = Path(__file__).parent.parent.parent / "tmp" / "ref_templates"
SKILL_DIR = Path(__file__).parent.parent / "skills" / "doudizhu_douzero"
TEMPLATE_DIR = SKILL_DIR / "assets" / "templates"
ROI_CONFIG_PATH = SKILL_DIR / "assets" / "rois.json"

PANEL_W = 600

# Data
ref_imgs = []       # [(name, bgr)]
screen_imgs = []    # [(name, bgr)]
ref_idx = 0
scr_idx = 0
category = "buttons"

# Drawing
drawing = False
start_x, start_y = -1, -1
roi = None  # [x1,y1,x2,y2] in original screenshot coords
scale = 1.0
right_orig = None   # current screenshot original


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


def render():
    """Build side-by-side display."""
    global scale, right_orig

    # Left: reference template
    _, ref = ref_imgs[ref_idx]
    rh, rw = ref.shape[:2]
    s = PANEL_W / max(rw, 1)
    left = cv2.resize(ref, (PANEL_W, max(1, int(rh * s))))

    # Right: screenshot with ROI overlay
    _, scr = screen_imgs[scr_idx]
    right_orig = scr
    h, w = scr.shape[:2]
    scale = w / PANEL_W
    right = cv2.resize(scr, (PANEL_W, max(1, int(h / scale))))

    # Overlay selection
    if roi:
        x1, y1, x2, y2 = roi
        dx1, dy1 = int(x1 / scale), int(y1 / scale)
        dx2, dy2 = int(x2 / scale), int(y2 / scale)
        cv2.rectangle(right, (dx1, dy1), (dx2, dy2), (0, 0, 255), 2)

    # Match heights
    lh, rh2 = left.shape[0], right.shape[0]
    max_h = max(lh, rh2)
    if lh < max_h:
        left = np.vstack([left, np.zeros((max_h - lh, PANEL_W, 3), dtype=np.uint8)])
    if rh2 < max_h:
        right = np.vstack([right, np.zeros((max_h - rh2, PANEL_W, 3), dtype=np.uint8)])

    sep = np.ones((max_h, 2, 3), dtype=np.uint8) * 100
    return np.hstack([left, sep, right])


def mouse(event, x, y, _f, _p):
    global drawing, start_x, start_y, roi
    if x < PANEL_W + 2:
        return
    rx, ry = x - PANEL_W - 2, y
    ox, oy = int(rx * scale), int(ry * scale)
    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        start_x, start_y = ox, oy
        roi = [ox, oy, ox, oy]
    elif event == cv2.EVENT_MOUSEMOVE and drawing:
        roi[2], roi[3] = ox, oy
    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        x1, y1 = min(start_x, ox), min(start_y, oy)
        x2, y2 = max(start_x, ox), max(start_y, oy)
        if x2 - x1 < 10 or y2 - y1 < 10:
            roi = None
            return
        h_img, w_img = right_orig.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w_img, x2), min(h_img, y2)
        roi = [x1, y1, x2, y2]
        # Auto-save with reference template name
        ref_name = Path(ref_imgs[ref_idx][0]).stem
        fname = f"{ref_name}.png"
        out_dir = TEMPLATE_DIR / category
        out_dir.mkdir(parents=True, exist_ok=True)
        crop = right_orig[y1:y2, x1:x2]
        cv2.imwrite(str(out_dir / fname), crop)
        print(f"  {category}/{fname}  ({x2-x1}x{y2-y1})  ← {ref_name}")
        # Export ROI
        _export_roi()


def _export_roi():
    if not roi:
        return
    h, w = right_orig.shape[:2]
    ref_name = Path(ref_imgs[ref_idx][0]).stem
    x1, y1, x2, y2 = roi
    cfg = {}
    if ROI_CONFIG_PATH.exists():
        cfg = json.loads(ROI_CONFIG_PATH.read_text(encoding="utf-8"))
    if "rois" not in cfg:
        cfg["rois"] = {}
    cfg["rois"][ref_name] = [
        round(x1 / w, 4), round(y1 / h, 4),
        round(x2 / w, 4), round(y2 / h, 4),
    ]
    cfg["resolution"] = [w, h]
    cfg["category"] = category
    ROI_CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def status():
    ref_n = ref_imgs[ref_idx][0]
    scr_n = screen_imgs[scr_idx][0]
    print(f"\r  [{ref_idx+1}/{len(ref_imgs)}] {ref_n:20s}  |  [{scr_idx+1}/{len(screen_imgs)}] {scr_n:30s}  |  {category}/  ←→↑↓ c/b/u/o  q",
          end="", flush=True)


def main():
    global ref_imgs, screen_imgs, ref_idx, scr_idx, category, roi

    ref_imgs = load_images(REF_DIR, {".png"}, "参考模板")
    screen_imgs = load_images(SCREENSHOT_DIR, {".jpeg", ".jpg", ".png"}, "手机截图")
    if not ref_imgs or not screen_imgs:
        print("错误: 缺少图片"); sys.exit(1)

    for d in ["cards", "buttons", "ui", "others"]:
        (TEMPLATE_DIR / d).mkdir(parents=True, exist_ok=True)

    wname = "左:模板 | 右:截图 | ←→切模板 ↑↓切截图 框选=保存"
    cv2.namedWindow(wname, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(wname, PANEL_W * 2 + 20, 600)
    cv2.setMouseCallback(wname, mouse)

    # Start from non-bg template
    for i, (n, _) in enumerate(ref_imgs):
        if not n.startswith("bg"):
            ref_idx = i; break

    status()

    while True:
        cv2.imshow(wname, render())
        key = cv2.waitKey(50) & 0xFF

        if key == ord('q'):
            break
        elif key == 83:  # →
            ref_idx = (ref_idx + 1) % len(ref_imgs)
            roi = None
            status()
        elif key == 81:  # ←
            ref_idx = (ref_idx - 1) % len(ref_imgs)
            roi = None
            status()
        elif key == 84:  # ↓
            scr_idx = (scr_idx + 1) % len(screen_imgs)
            roi = None
            status()
        elif key == 82:  # ↑
            scr_idx = (scr_idx - 1) % len(screen_imgs)
            roi = None
            status()
        elif key == ord('c'):
            category = "cards"; status()
        elif key == ord('b'):
            category = "buttons"; status()
        elif key == ord('u'):
            category = "ui"; status()
        elif key == ord('o'):
            category = "others"; status()

    cv2.destroyAllWindows()
    print(f"\n模板: {TEMPLATE_DIR}")
    print(f"ROI:  {ROI_CONFIG_PATH}")


if __name__ == "__main__":
    main()
