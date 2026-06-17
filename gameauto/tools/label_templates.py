#!/usr/bin/env python3
"""交互式模板标注工具 — 鼠标框选 ROI，自动裁剪保存。

用法:
    python gameauto/tools/label_templates.py

操作:
    - 鼠标拖拽框选区域（左键按下 → 拖拽 → 松开）
    - 松开后终端输入模板名，自动保存
    - 按 'c' 切到 cards  按 'b' 切到 buttons
    - 按 'u' 切到 ui     按 'o' 切到 others
    - 按 'n'/'p' 切图   按 's' 导出配置   按 'q' 退出
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SCREENSHOT_DIR = Path("D:/screenshots")
SKILL_DIR = Path(__file__).parent.parent / "skills" / "doudizhu_douzero"
TEMPLATE_DIR = SKILL_DIR / "assets" / "templates"
ROI_CONFIG_PATH = SKILL_DIR / "assets" / "rois.json"

DISPLAY_W = 1200  # display width in pixels

# State
drawing = False
start_x, start_y = -1, -1
current_roi = None
rois = []  # list of (x1,y1,x2,y2, name) in ORIGINAL image coords
category = "buttons"
img_idx = 0
images = []  # list of (filename, original_numpy_bgr_array)
current_orig = None   # original image (full res)
current_display = None  # resized for display
scale = 1.0  # original_w / display_w
display_h = 0
display_w = 0


def load_images():
    """Load screenshots using PIL (cv2 can't handle Chinese paths)."""
    global images
    for f in sorted(SCREENSHOT_DIR.glob("*.jpeg")):
        try:
            pil = Image.open(str(f)).convert("RGB")
            arr = np.array(pil)
            arr_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            images.append((f.name, arr_bgr))
            print(f"  加载: {f.name}  {arr_bgr.shape[1]}x{arr_bgr.shape[0]}")
        except Exception as e:
            print(f"  跳过 {f.name}: {e}")
    return len(images) > 0


def redraw():
    global current_display
    # Resize original to display size
    current_display = cv2.resize(current_orig, (display_w, display_h))

    # Draw existing ROIs (scaled to display)
    for rx1, ry1, rx2, ry2, rname in rois:
        dx1 = int(rx1 / scale)
        dy1 = int(ry1 / scale)
        dx2 = int(rx2 / scale)
        dy2 = int(ry2 / scale)
        cv2.rectangle(current_display, (dx1, dy1), (dx2, dy2), (0, 255, 0), 2)
        cv2.putText(current_display, rname, (dx1, dy1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    # Draw current selection
    if drawing and current_roi:
        cx1, cy1, cx2, cy2 = current_roi
        dx1 = int(cx1 / scale)
        dy1 = int(cy1 / scale)
        dx2 = int(cx2 / scale)
        dy2 = int(cy2 / scale)
        cv2.rectangle(current_display, (dx1, dy1), (dx2, dy2), (0, 0, 255), 2)


def mouse_callback(event, x, y, flags, param):
    """Mouse coords are in DISPLAY size. Convert to original."""
    global drawing, start_x, start_y, current_roi, rois

    # Scale mouse coords to original image
    ox, oy = int(x * scale), int(y * scale)

    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        start_x, start_y = ox, oy
        current_roi = [ox, oy, ox, oy]

    elif event == cv2.EVENT_MOUSEMOVE and drawing:
        current_roi[2] = ox
        current_roi[3] = oy
        redraw()

    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        x1, y1 = min(start_x, ox), min(start_y, oy)
        x2, y2 = max(start_x, ox), max(start_y, oy)

        if x2 - x1 < 8 or y2 - y1 < 8:
            current_roi = None
            redraw()
            return

        # Clamp to image bounds
        h, w = current_orig.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        print(f"\n框选: ({x1},{y1})→({x2},{y2})  {x2-x1}x{y2-y1}px")
        name = input("模板名称 (回车跳过): ").strip()
        if name:
            rois.append((x1, y1, x2, y2, name))
            (TEMPLATE_DIR / category).mkdir(parents=True, exist_ok=True)
            crop = current_orig[y1:y2, x1:x2]
            fname = f"{name}.png"
            cv2.imwrite(str(TEMPLATE_DIR / category / fname), crop)
            print(f"  → 已保存: {category}/{fname}")

        current_roi = None
        redraw()


def export_config():
    """Export all ROIs as normalized [0-1] coordinates."""
    h, w = current_orig.shape[:2]
    config = {"resolution": [w, h], "rois": {}}
    for rx1, ry1, rx2, ry2, rname in rois:
        config["rois"][rname] = [round(rx1 / w, 4), round(ry1 / h, 4),
                                  round(rx2 / w, 4), round(ry2 / h, 4)]
    ROI_CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    print(f"\nROI 配置已更新: {ROI_CONFIG_PATH}")


def show_help():
    print("""
  [b]uttons   [c]ards   [u]i   [o]thers
  [n]ext  [p]rev  [r]eset  [s]ave config  [q]uit
  鼠标拖拽框选 → 松开 → 输入名称 → 自动保存
""")


def main():
    global current_orig, current_display, scale, img_idx, rois, category
    global display_h, display_w

    if not load_images():
        print(f"错误: {SCREENSHOT_DIR} 下没有 JPEG 截图")
        sys.exit(1)

    for d in ["cards", "buttons", "ui", "others"]:
        (TEMPLATE_DIR / d).mkdir(parents=True, exist_ok=True)

    cv2.namedWindow("Template Labeler", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Template Labeler", DISPLAY_W, 550)
    cv2.setMouseCallback("Template Labeler", mouse_callback)

    show_help()

    def load_image():
        global current_orig, current_display, scale, rois, display_h, display_w
        name, img = images[img_idx]
        current_orig = img
        h, w = img.shape[:2]
        scale = w / DISPLAY_W
        display_w = DISPLAY_W
        display_h = int(h / scale)
        rois = []
        redraw()
        print(f"\n[{img_idx+1}/{len(images)}] {name}  {w}x{h}  |  [b]uttons [c]ards [u]i [o]thers")

    load_image()

    while True:
        cv2.imshow("Template Labeler", current_display)
        key = cv2.waitKey(50) & 0xFF

        if key == ord('q'):
            break
        elif key == ord('b'):
            category = "buttons"
            print(f"  → buttons/")
        elif key == ord('c'):
            category = "cards"
            print(f"  → cards/")
        elif key == ord('u'):
            category = "ui"
            print(f"  → ui/")
        elif key == ord('o'):
            category = "others"
            print(f"  → others/")
        elif key == ord('n'):
            img_idx = (img_idx + 1) % len(images)
            load_image()
        elif key == ord('p'):
            img_idx = (img_idx - 1) % len(images)
            load_image()
        elif key == ord('r'):
            rois.clear()
            redraw()
            print("  → 已清除")
        elif key == ord('s'):
            export_config()
        elif key == ord('h'):
            show_help()

    cv2.destroyAllWindows()
    print(f"\n模板: {TEMPLATE_DIR}")
    print(f"配置: {ROI_CONFIG_PATH}")


if __name__ == "__main__":
    main()
