#!/usr/bin/env python3
"""交互式模板标注工具 — 左侧显示参考模板，右侧显示手机截图。

对照原版 DouZero 模板，在手机截图上找到对应区域并框选保存。

用法:
    python gameauto/tools/label_templates.py

操作:
    鼠标拖拽    = 在右侧截图上框选区域，松开后输入名称保存
    ← →         = 切换参考模板（上一张/下一张）
    ↑ ↓         = 切换手机截图
    c/b/u/o     = 切换保存分类（cards/buttons/ui/others）
    s           = 导出 ROI 配置
    q           = 退出
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

PANEL_W = 600  # each panel width

# State
ref_imgs = []       # list of (name, bgr_array)
screen_imgs = []    # list of (name, bgr_array)
ref_idx = 0
scr_idx = 0

drawing = False
start_x, start_y = -1, -1
current_roi = None  # [x1,y1,x2,y2] in ORIGINAL screenshot coords
rois = []           # [(x1,y1,x2,y2, name), ...] in original coords

category = "buttons"
scale = 1.0

# Display images
left_panel = None
right_panel = None
right_orig = None   # original screenshot for cropping


def load_refs():
    """Load reference templates (original DouZero pics)."""
    global ref_imgs
    for f in sorted(REF_DIR.glob("*.png")):
        try:
            arr = np.array(Image.open(str(f)).convert("RGB"))
            arr_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            ref_imgs.append((f.name, arr_bgr))
        except Exception as e:
            print(f"  跳过参考图 {f.name}: {e}")
    print(f"参考模板: {len(ref_imgs)} 张")
    return len(ref_imgs) > 0


def load_screens():
    """Load screenshots using PIL (cv2 can't handle Chinese paths)."""
    global screen_imgs
    for f in sorted(SCREENSHOT_DIR.glob("*.jpeg")):
        try:
            arr = np.array(Image.open(str(f)).convert("RGB"))
            arr_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            screen_imgs.append((f.name, arr_bgr))
        except Exception as e:
            print(f"  跳过截图 {f.name}: {e}")
    print(f"手机截图: {len(screen_imgs)} 张")
    return len(screen_imgs) > 0


def make_left_panel():
    """Render reference template to fit PANEL_W."""
    global left_panel
    _, ref = ref_imgs[ref_idx]
    h, w = ref.shape[:2]
    scale_ref = PANEL_W / max(w, 1)
    new_w = PANEL_W
    new_h = max(1, int(h * scale_ref))
    resized = cv2.resize(ref, (new_w, new_h))
    # Pad to match right panel height
    rh = right_panel.shape[0] if right_panel is not None else 400
    if new_h < rh:
        pad = np.zeros((rh - new_h, PANEL_W, 3), dtype=np.uint8)
        resized = np.vstack([resized, pad])
    left_panel = resized


def make_right_panel():
    """Render screenshot to fit PANEL_W, with ROI overlays."""
    global right_panel, scale, right_orig
    _, img = screen_imgs[scr_idx]
    right_orig = img
    h, w = img.shape[:2]
    scale = w / PANEL_W
    new_w = PANEL_W
    new_h = max(1, int(h / scale))
    display = cv2.resize(img, (new_w, new_h))

    # Draw saved ROIs
    for rx1, ry1, rx2, ry2, rname in rois:
        dx1, dy1 = int(rx1 / scale), int(ry1 / scale)
        dx2, dy2 = int(rx2 / scale), int(ry2 / scale)
        cv2.rectangle(display, (dx1, dy1), (dx2, dy2), (0, 255, 0), 2)
        cv2.putText(display, rname, (dx1, dy1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

    # Draw current selection
    if drawing and current_roi:
        cx1, cy1, cx2, cy2 = current_roi
        dx1, dy1 = int(cx1 / scale), int(cy1 / scale)
        dx2, dy2 = int(cx2 / scale), int(cy2 / scale)
        cv2.rectangle(display, (dx1, dy1), (dx2, dy2), (0, 0, 255), 2)

    right_panel = display


def make_display():
    """Combine left + right into one image."""
    global left_panel, right_panel
    make_left_panel()
    make_right_panel()
    # Match heights
    lh = left_panel.shape[0]
    rh = right_panel.shape[0]
    max_h = max(lh, rh)
    if lh < max_h:
        pad = np.zeros((max_h - lh, PANEL_W, 3), dtype=np.uint8)
        left_panel = np.vstack([left_panel, pad])
    if rh < max_h:
        pad = np.zeros((max_h - rh, PANEL_W, 3), dtype=np.uint8)
        right_panel = np.vstack([right_panel, pad])
    # Separator line
    sep = np.ones((max_h, 2, 3), dtype=np.uint8) * 100
    return np.hstack([left_panel, sep, right_panel])


def mouse_callback(event, x, y, _flags, _param):
    global drawing, start_x, start_y, current_roi, rois

    # Only respond to clicks on the RIGHT panel (x > PANEL_W + 2)
    if x < PANEL_W + 2:
        return

    # Adjust x to right-panel-local
    rx = x - PANEL_W - 2
    ry = y

    # Scale to original screenshot coords
    ox, oy = int(rx * scale), int(ry * scale)

    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        start_x, start_y = ox, oy
        current_roi = [ox, oy, ox, oy]

    elif event == cv2.EVENT_MOUSEMOVE and drawing:
        current_roi[2] = ox
        current_roi[3] = oy

    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        x1, y1 = min(start_x, ox), min(start_y, oy)
        x2, y2 = max(start_x, ox), max(start_y, oy)

        if x2 - x1 < 8 or y2 - y1 < 8:
            current_roi = None
            return

        # Clamp
        h, w = right_orig.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        ref_name = ref_imgs[ref_idx][0]
        print(f"\n参考: {ref_name}")
        print(f"框选: ({x1},{y1})→({x2},{y2})  {x2-x1}x{y2-y1}px")
        name = input("模板名称 (回车跳过): ").strip()
        if name:
            rois.append((x1, y1, x2, y2, name))
            (TEMPLATE_DIR / category).mkdir(parents=True, exist_ok=True)
            crop = right_orig[y1:y2, x1:x2]
            cv2.imwrite(str(TEMPLATE_DIR / category / f"{name}.png"), crop)
            print(f"  → {category}/{name}.png")

        current_roi = None


def show_status():
    ref_name = ref_imgs[ref_idx][0]
    scr_name = screen_imgs[scr_idx][0]
    print(f"\n参考: [{ref_idx+1}/{len(ref_imgs)}] {ref_name}")
    print(f"截图: [{scr_idx+1}/{len(screen_imgs)}] {scr_name}")
    print(f"分类: {category}/  | 已标: {len(rois)} 个")
    print(f"  ←→ 换参考  ↑↓ 换截图  c/b/u/o 分类  s 导出  q 退出")


def main():
    global ref_idx, scr_idx, rois, category

    if not load_refs():
        print(f"错误: 参考模板目录为空 {REF_DIR}")
        sys.exit(1)
    if not load_screens():
        print(f"错误: 截图目录为空 {SCREENSHOT_DIR}")
        sys.exit(1)

    for d in ["cards", "buttons", "ui", "others"]:
        (TEMPLATE_DIR / d).mkdir(parents=True, exist_ok=True)

    window_name = "左:参考模板  |  右:手机截图  |  ←→换参考 ↑↓换图 c/b/u/o分类"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, PANEL_W * 2 + 20, 600)
    cv2.setMouseCallback(window_name, mouse_callback)

    show_status()

    while True:
        display = make_display()
        cv2.imshow(window_name, display)
        key = cv2.waitKey(50) & 0xFF

        if key == ord('q'):
            break
        elif key == 81:  # left arrow
            ref_idx = (ref_idx - 1) % len(ref_imgs)
            show_status()
        elif key == 83:  # right arrow
            ref_idx = (ref_idx + 1) % len(ref_imgs)
            show_status()
        elif key == 82:  # up arrow
            scr_idx = (scr_idx - 1) % len(screen_imgs)
            rois = []
            show_status()
        elif key == 84:  # down arrow
            scr_idx = (scr_idx + 1) % len(screen_imgs)
            rois = []
            show_status()
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
        elif key == ord('s'):
            h, w = screen_imgs[scr_idx][1].shape[:2]
            config = {"resolution": [w, h], "rois": {}}
            for rx1, ry1, rx2, ry2, rname in rois:
                config["rois"][rname] = [
                    round(rx1 / w, 4), round(ry1 / h, 4),
                    round(rx2 / w, 4), round(ry2 / h, 4),
                ]
            ROI_CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False))
            print(f"\nROI 配置已导出: {ROI_CONFIG_PATH}")
        elif key == ord('r'):
            rois.clear()
            print("  → 已清除当前截图标记")

    cv2.destroyAllWindows()
    print(f"\n模板: {TEMPLATE_DIR}")


if __name__ == "__main__":
    main()
