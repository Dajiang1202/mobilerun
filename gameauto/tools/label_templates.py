#!/usr/bin/env python3
"""交互式模板适配工具 — 双窗口对照标注。

窗口 "Template"：原版模板
窗口 "Screenshot"：手机截图
工作流：← → 切模板，↑ ↓ 切截图，在截图窗口鼠标框选 → 自动同名保存。

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
scr_orig = None     # current screenshot (original size)
scr_display = None  # current screenshot (for display)
scale = 1.0


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


def update_scr_display():
    """Rebuild screenshot display image with ROI overlay."""
    global scr_display, scale
    if scr_orig is None:
        return
    scr_display = scr_orig.copy()
    # Get actual window size to compute scale
    rect = cv2.getWindowImageRect("Screenshot")
    if rect and rect[2] > 0:
        win_w = rect[2]
    else:
        win_w = scr_orig.shape[1]
    h, w = scr_orig.shape[:2]
    scale = w / win_w if win_w > 0 else 1.0

    if roi:
        x1, y1, x2, y2 = roi
        cv2.rectangle(scr_display, (x1, y1), (x2, y2), (0, 0, 255), 2)

    # Also show existing saved ROIs for this screenshot
    status_text = f"{screen_imgs[scr_idx][0]}  |  [{scr_idx+1}/{len(screen_imgs)}]"
    cv2.putText(scr_display, status_text, (5, h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)


def mouse(event, x, y, _f, _p):
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
        if scr_orig is None or x2 - x1 < 10 or y2 - y1 < 10:
            roi = None; update_scr_display(); return
        h_img, w_img = scr_orig.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w_img, x2), min(h_img, y2)
        roi = [x1, y1, x2, y2]

        # Auto-save with reference template name
        ref_name = Path(ref_imgs[ref_idx][0]).stem
        fname = f"{ref_name}.png"
        out_dir = TEMPLATE_DIR / category
        out_dir.mkdir(parents=True, exist_ok=True)
        crop = scr_orig[y1:y2, x1:x2]
        cv2.imwrite(str(out_dir / fname), crop)
        print(f"\n  {category}/{fname}  ({x2-x1}x{y2-y1})  ← {ref_name}")

        # Export ROI
        if not ROI_CONFIG_PATH.exists():
            ROI_CONFIG_PATH.write_text("{}")
        cfg = json.loads(ROI_CONFIG_PATH.read_text(encoding="utf-8"))
        cfg.setdefault("rois", {})[ref_name] = [
            round(x1 / w_img, 4), round(y1 / h_img, 4),
            round(x2 / w_img, 4), round(y2 / h_img, 4),
        ]
        cfg["resolution"] = [w_img, h_img]
        cfg["category"] = category
        ROI_CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

    update_scr_display()


def load_scr():
    """Switch to a different screenshot."""
    global scr_orig, scr_display, roi
    roi = None
    _, scr_orig = screen_imgs[scr_idx]
    update_scr_display()
    _print_status()


def _print_status():
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

    # Template window — small, shows one ref at 3x
    cv2.namedWindow("Template", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Template", 200, 200)

    # Screenshot window — shows at native resolution (user can resize)
    cv2.namedWindow("Screenshot", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("Screenshot", mouse)

    # Skip bg.png
    for i, (n, _) in enumerate(ref_imgs):
        if not n.startswith("bg"):
            ref_idx = i; break

    load_scr()
    _print_status()

    while True:
        # Update template window
        _, ref = ref_imgs[ref_idx]
        ref_show = cv2.resize(ref, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        cv2.imshow("Template", ref_show)
        # Header on template
        cv2.setWindowTitle("Template",
            f"Template [{ref_idx+1}/{len(ref_imgs)}] {ref_imgs[ref_idx][0]}")

        # Update screenshot window
        if scr_display is not None:
            cv2.imshow("Screenshot", scr_display)
            cv2.setWindowTitle("Screenshot",
                f"Screenshot [{scr_idx+1}/{len(screen_imgs)}]  {category}/")

        key = cv2.waitKey(50) & 0xFF

        if key == ord('q'):
            break
        elif key == 83:  # →
            ref_idx = (ref_idx + 1) % len(ref_imgs)
            roi = None
            _print_status()
        elif key == 81:  # ←
            ref_idx = (ref_idx - 1) % len(ref_imgs)
            roi = None
            _print_status()
        elif key == 84:  # ↓
            scr_idx = (scr_idx + 1) % len(screen_imgs)
            load_scr()
        elif key == 82:  # ↑
            scr_idx = (scr_idx - 1) % len(screen_imgs)
            load_scr()
        elif key == ord('c'):
            category = "cards"; _print_status()
        elif key == ord('b'):
            category = "buttons"; _print_status()
        elif key == ord('u'):
            category = "ui"; _print_status()
        elif key == ord('o'):
            category = "others"; _print_status()

    cv2.destroyAllWindows()
    print(f"\n模板: {TEMPLATE_DIR}")


if __name__ == "__main__":
    main()
