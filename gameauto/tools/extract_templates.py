#!/usr/bin/env python3
"""从截图中自动提取 DouDiZhu 卡牌和按钮模板。

输入: D:\screenshots\ 下的截图
输出: skills/doudizhu_douzero/assets/templates/{cards,others,buttons,ui}/
     + skills/doudizhu_douzero/assets/rois.json

用法:
    python gameauto/tools/extract_templates.py
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

SCREENSHOT_DIR = Path("D:/screenshots")
SKILL_DIR = Path(__file__).parent.parent / "skills" / "doudizhu_douzero"
TEMPLATE_DIR = SKILL_DIR / "assets" / "templates"


def main():
    for d in ["cards", "others", "buttons", "ui"]:
        (TEMPLATE_DIR / d).mkdir(parents=True, exist_ok=True)

    files = sorted(SCREENSHOT_DIR.glob("*.jpeg"))
    if not files:
        print("ERROR: No JPEG screenshots found in", SCREENSHOT_DIR)
        sys.exit(1)

    images = {}
    for f in files:
        try:
            images[f.name] = np.array(Image.open(str(f)).convert("RGB"))
            print(f"Loaded: {f.name}  {images[f.name].shape}")
        except Exception as e:
            print(f"SKIP {f.name}: {e}")

    # Identify screenshots by file order / size pattern
    names = list(images.keys())
    playing_key = names[0]  # first should be playing
    settlement_key = [n for n in names if "结算" in n][0] if any("结算" in n for n in names) else names[-1]

    playing = images[playing_key]
    h, w = playing.shape[:2]
    print(f"\nResolution: {w}x{h}")

    # ── 1. Hand Cards ──────────────────────────────────────────
    print("\n--- Hand Cards ---")
    hand = playing[int(h * 0.78):h, 0:w]
    hand_gray = np.mean(hand, axis=2)
    col_intensity = np.mean(hand_gray, axis=0)

    # Find dips (gaps between cards)
    from scipy.ndimage import gaussian_filter1d
    col_smooth = gaussian_filter1d(col_intensity, sigma=5)
    median_val = np.median(col_smooth)

    dips = []
    for i in range(3, len(col_smooth) - 3):
        if col_smooth[i] < col_smooth[i - 3] and col_smooth[i] < col_smooth[i + 3]:
            if col_smooth[i] < median_val * 0.85:
                dips.append(i)

    print(f"Card gaps: {len(dips)} at {dips}")

    # Extract cards from between gaps
    saved_fingerprints = set()
    card_count = 0

    def extract_card(x1, x2):
        nonlocal card_count
        if x2 - x1 < 25:
            return
        card = hand[8:hand.shape[0] - 8, x1:x2]
        card_resized = np.array(Image.fromarray(card).resize((card.shape[1], 100), Image.LANCZOS))
        fp = tuple((np.mean(card_resized[20:80, 5:-5], axis=(0, 1)) // 25).astype(int))
        if fp not in saved_fingerprints:
            saved_fingerprints.add(fp)
            fname = f"card_{card_count:02d}.png"
            Image.fromarray(card_resized).save(str(TEMPLATE_DIR / "cards" / fname))
            print(f"  {fname}  ({card_resized.shape[1]}x{card_resized.shape[0]})")
            card_count += 1

    if dips:
        extract_card(max(0, dips[0] - 80), min(dips[0] + 15, w))
        for i in range(len(dips) - 1):
            if dips[i + 1] - dips[i] > 60:
                extract_card(dips[i] + 5, dips[i + 1] - 5)
        extract_card(dips[-1] + 5, min(dips[-1] + 120, w))
    else:
        # Fallback: extract evenly spaced cards
        for x in range(50, w - 100, w // 18):
            extract_card(x, min(x + 120, w - 10))

    # ── 2. Buttons from Bidding/Double/Rob screens ─────────────
    print("\n--- Buttons ---")
    phase_names = {
        "叫地主": "bidding",
        "抢地主": "rob",
        "加倍": "double",
    }

    for fname, img in images.items():
        label = None
        for keyword, phase in phase_names.items():
            if keyword in fname:
                label = phase
                break
        if label is None:
            continue

        ih, iw = img.shape[:2]
        btn_area = img[int(ih * 0.48):int(ih * 0.85), int(iw * 0.25):int(iw * 0.75)]
        btn_gray = np.mean(btn_area, axis=2)

        # Find bright regions (button faces are lighter than dark background)
        bright = (btn_gray > np.percentile(btn_gray, 70)) & (btn_gray > 80)
        labeled, n = ndimage.label(bright)

        btn_idx = 0
        for lidx in range(1, n + 1):
            region = labeled == lidx
            ys, xs = np.where(region)
            if len(ys) < 200:
                continue
            y1, y2 = max(0, ys.min() - 10), min(btn_area.shape[0], ys.max() + 10)
            x1, x2 = max(0, xs.min() - 15), min(btn_area.shape[1], xs.max() + 15)
            if x2 - x1 > 80 and y2 - y1 > 40:
                btn = btn_area[y1:y2, x1:x2]
                fname_out = f"{label}_{btn_idx}.png"
                Image.fromarray(btn).save(str(TEMPLATE_DIR / "buttons" / fname_out))
                print(f"  {fname_out}  ({x2 - x1}x{y2 - y1})")
                btn_idx += 1

        # Also save full button area as reference
        Image.fromarray(btn_area).save(str(TEMPLATE_DIR / "buttons" / f"{label}_area.png"))

    # ── 3. Settlement Button ───────────────────────────────────
    print("\n--- Settlement ---")
    settle = images.get(settlement_key)
    if settle is not None:
        sh, sw = settle.shape[:2]
        settle_area = settle[int(sh * 0.48):int(sh * 0.85), int(sw * 0.25):int(sw * 0.75)]
        Image.fromarray(settle_area).save(str(TEMPLATE_DIR / "buttons" / "settle_area.png"))
        print(f"  settle_area.png  ({settle_area.shape[1]}x{settle_area.shape[0]})")

        # Try to find the specific button
        settle_gray = np.mean(settle_area, axis=2)
        bright = settle_gray > np.percentile(settle_gray, 75)
        labeled, n = ndimage.label(bright)
        for lidx in range(1, n + 1):
            region = labeled == lidx
            ys, xs = np.where(region)
            if len(ys) < 150:
                continue
            y1, y2 = max(0, ys.min() - 8), min(settle_area.shape[0], ys.max() + 8)
            x1, x2 = max(0, xs.min() - 15), min(settle_area.shape[1], xs.max() + 15)
            btn = settle_area[y1:y2, x1:x2]
            Image.fromarray(btn).save(str(TEMPLATE_DIR / "buttons" / f"settle_{lidx}.png"))
            print(f"  settle_{lidx}.png  ({x2 - x1}x{y2 - y1})")

    # ── 4. UI Markers ──────────────────────────────────────────
    print("\n--- UI Markers ---")
    # Top banner (may contain landlord icon, room info)
    banner = playing[0:int(h * 0.06), int(w * 0.2):int(w * 0.8)]
    Image.fromarray(banner).save(str(TEMPLATE_DIR / "ui" / "top_banner.png"))
    print(f"  top_banner.png  ({banner.shape[1]}x{banner.shape[0]})")

    # ── 5. Opponent Card Area ──────────────────────────────────
    print("\n--- Opponent Area ---")
    center = playing[int(h * 0.25):int(h * 0.65), int(w * 0.1):int(w * 0.9)]
    Image.fromarray(center).save(str(TEMPLATE_DIR / "others" / "play_area.png"))
    print(f"  play_area.png  ({center.shape[1]}x{center.shape[0]})")

    # ── 6. Save ROI Config ─────────────────────────────────────
    roi_config = {
        "resolution": [w, h],
        "hand_cards_roi": [0.0, 0.78, 1.0, 1.0],
        "opponent_play_roi": [0.1, 0.25, 0.9, 0.65],
        "button_roi": [0.50, 0.65, 1.0, 1.0],
        "bidding_button_roi": [0.25, 0.48, 0.75, 0.85],
        "settlement_roi": [0.25, 0.48, 0.75, 0.85],
        "landlord_icon_roi": [0.0, 0.0, 1.0, 0.15],
    }

    config_path = SKILL_DIR / "assets" / "rois.json"
    config_path.write_text(json.dumps(roi_config, indent=2, ensure_ascii=False))
    print(f"\nROI config saved: {config_path}")

    # Summary
    print("\n=== Template Extraction Complete ===")
    for d in ["cards", "buttons", "ui", "others"]:
        pngs = sorted((TEMPLATE_DIR / d).glob("*.png"))
        print(f"  {d}: {len(pngs)} templates")
        for p in pngs:
            print(f"    - {p.name}")


if __name__ == "__main__":
    main()
