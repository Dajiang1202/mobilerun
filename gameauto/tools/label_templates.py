#!/usr/bin/env python3
"""交互式模板标注工具 — 鼠标框选 ROI，自动裁剪保存。

用法:
    python gameauto/tools/label_templates.py

操作:
    - 鼠标拖拽框选区域（左键按下 → 拖拽 → 松开）
    - 松开后弹出命名窗口，输入模板名（如 "出牌", "叫地主", "A_spade"）
    - 模板自动保存到 templates/ 对应子目录
    - 按 'c' 切到 cards 目录  按 'b' 切到 buttons 目录
    - 按 'u' 切到 ui 目录      按 'o' 切到 others 目录
    - 按 'n' 切换截图         按 'q' 退出
    - 按 'r' 重置当前截图的所有框选
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

# State
drawing = False
start_x, start_y = -1, -1
current_roi = None
rois = []  # list of (x1,y1,x2,y2, name)
category = "buttons"  # cards, buttons, ui, others
img_idx = 0
images = []
current_img = None
display_img = None
scale = 1.0
DISPLAY_W = 1200


def load_images():
    global images
    for f in sorted(SCREENSHOT_DIR.glob("*.jpeg")):
        img = cv2.imread(str(f))
        if img is not None:
            images.append((f.name, img))
    if not images:
        # Try PIL fallback
        for f in sorted(SCREENSHOT_DIR.glob("*.jpeg")):
            pil = Image.open(str(f)).convert("RGB")
            arr = np.array(pil)
            arr_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            images.append((f.name, arr_bgr))
    return len(images) > 0


def redraw():
    global display_img
    display_img = current_img.copy()
    h, w = display_img.shape[:2]

    # Draw existing ROIs
    for rx1, ry1, rx2, ry2, rname in rois:
        sx1 = int(rx1 / scale)
        sy1 = int(ry1 / scale)
        sx2 = int(rx2 / scale)
        sy2 = int(ry2 / scale)
        cv2.rectangle(display_img, (sx1, sy1), (sx2, sy2), (0, 255, 0), 2)
        cv2.putText(display_img, rname, (sx1, sy1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)

    # Draw current selection
    if drawing and current_roi:
        cx1, cy1, cx2, cy2 = current_roi
        sx1 = int(cx1 / scale)
        sy1 = int(cy1 / scale)
        sx2 = int(cx2 / scale)
        sy2 = int(cy2 / scale)
        cv2.rectangle(display_img, (sx1, sy1), (sx2, sy2), (0, 0, 255), 2)


def mouse_callback(event, x, y, flags, param):
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

        if x2 - x1 < 10 or y2 - y1 < 10:
            current_roi = None
            redraw()
            return

        # Ask for name
        print(f"\n框选区域: ({x1},{y1}) → ({x2},{y2})  size={x2-x1}x{y2-y1}")
        name = input("模板名称 (回车跳过): ").strip()
        if name:
            rois.append((x1, y1, x2, y2, name))
            # Save immediately
            (TEMPLATE_DIR / category).mkdir(parents=True, exist_ok=True)
            crop = current_img[y1:y2, x1:x2]
            fname = f"{name}.png"
            cv2.imwrite(str(TEMPLATE_DIR / category / fname), crop)
            print(f"  → 已保存: {category}/{fname}")

        current_roi = None
        redraw()


def export_config():
    """Export all ROIs to JSON config for perception module."""
    config = {"resolution": [current_img.shape[1], current_img.shape[0]], "rois": {}}
    for rx1, ry1, rx2, ry2, rname in rois:
        h, w = current_img.shape[:2]
        config["rois"][rname] = [rx1 / w, ry1 / h, rx2 / w, ry2 / h]

    # Load existing config if any
    if ROI_CONFIG_PATH.exists():
        existing = json.loads(ROI_CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        existing = {}
    existing.update(config)

    ROI_CONFIG_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    print(f"\nROI 配置已更新: {ROI_CONFIG_PATH}")


def show_help():
    print("""
╔══════════════════════════════════════════════════════╗
║           模板标注工具 — 操作说明                     ║
╠══════════════════════════════════════════════════════╣
║  鼠标拖拽  = 框选区域，松开后输入名称保存              ║
║  c         = 切换到 cards/（手牌模板）                ║
║  b         = 切换到 buttons/（按钮模板）              ║
║  u         = 切换到 ui/（标记模板）                   ║
║  o         = 切换到 others/（对手出牌）               ║
║  n         = 下一张截图                               ║
║  p         = 上一张截图                               ║
║  r         = 删除当前截图所有框选                      ║
║  s         = 导出 ROI 配置                            ║
║  h         = 显示帮助                                 ║
║  q         = 退出                                     ║
╚══════════════════════════════════════════════════════╝
""")


def main():
    global current_img, display_img, scale, img_idx, rois, category

    if not load_images():
        print(f"错误: {SCREENSHOT_DIR} 下没有 JPEG 截图")
        sys.exit(1)

    for d in ["cards", "buttons", "ui", "others"]:
        (TEMPLATE_DIR / d).mkdir(parents=True, exist_ok=True)

    cv2.namedWindow("Template Labeler", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Template Labeler", DISPLAY_W, 600)
    cv2.setMouseCallback("Template Labeler", mouse_callback)

    show_help()

    def load_image():
        global current_img, display_img, scale, rois
        name, img = images[img_idx]
        current_img = img
        h, w = img.shape[:2]
        scale = w / DISPLAY_W
        rois = []
        redraw()
        print(f"\n当前: [{img_idx+1}/{len(images)}] {name}  |  {w}x{h}  |  [b]uttons [c]ards [u]i [o]thers")

    load_image()

    while True:
        cv2.imshow("Template Labeler", display_img)
        key = cv2.waitKey(50) & 0xFF

        if key == ord('q'):
            break
        elif key == ord('b'):
            category = "buttons"
            print(f"  → 分类: buttons/")
        elif key == ord('c'):
            category = "cards"
            print(f"  → 分类: cards/")
        elif key == ord('u'):
            category = "ui"
            print(f"  → 分类: ui/")
        elif key == ord('o'):
            category = "others"
            print(f"  → 分类: others/")
        elif key == ord('n'):
            img_idx = (img_idx + 1) % len(images)
            load_image()
        elif key == ord('p'):
            img_idx = (img_idx - 1) % len(images)
            load_image()
        elif key == ord('r'):
            rois.clear()
            redraw()
            print("  → 已清除所有框选")
        elif key == ord('s'):
            export_config()
        elif key == ord('h'):
            show_help()

    cv2.destroyAllWindows()
    print(f"\n模板保存位置: {TEMPLATE_DIR}")
    print(f"ROI 配置: {ROI_CONFIG_PATH}")


if __name__ == "__main__":
    main()
