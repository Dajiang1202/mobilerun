#!/usr/bin/env python3
"""TFT 感知 + 决策批量测试工具。

输入: 一个目录下的几十张游戏截图（PNG/JPEG）
输出: 每张截图的标注可视化 + HTML 汇总报告

用法:
    # 基础用法（需要 PaddleOCR）
    python tools/test_tft_perception.py --input screenshots/tft/

    # 不调 OCR（仅测试状态检测 + ROI 标注）
    python tools/test_tft_perception.py --input screenshots/tft/ --no-ocr

    # 指定输出目录
    python tools/test_tft_perception.py --input screenshots/tft/ --output results/tft_test/

    # 单张图片测试
    python tools/test_tft_perception.py --image screenshot.png

输出:
    results/
    ├── index.html              ← 汇总报告（浏览器打开）
    ├── img_001_annotated.png   ← 标注后的截图
    ├── img_001_perception.json ← 感知原始结果
    ├── img_002_annotated.png
    └── ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.skills.tft.decision import TftDecision, TftRules
from gameauto.skills.tft.perception import TftPerception
from gameauto.skills.tft.states import TftState

# ── PIL / visualization ────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL = True
except ImportError:
    _PIL = False

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("test_tft")


# ── Config ─────────────────────────────────────────────────────────────

@dataclass
class TestConfig:
    input_dir: Path | None = None
    output_dir: Path = Path("results/tft_test")
    single_image: Path | None = None
    no_ocr: bool = False
    no_decision: bool = False


# ── Colors ─────────────────────────────────────────────────────────────

CLR = {
    "state":       (0, 255, 255),      # Cyan — state label
    "roi":         (255, 200, 50, 120), # Orange — ROI rect
    "roi_label":   (255, 220, 100),     # Yellow — ROI label
    "ocr_text":    (255, 255, 255),     # White — OCR result text
    "ocr_bg":      (0, 0, 0, 160),      # Black bg — OCR text background
    "buy":         (0, 255, 0),         # Green — BUY
    "skip":        (255, 60, 60),       # Red — SKIP
    "action":      (255, 50, 200),      # Pink — action arrow
    "info":        (200, 200, 200),     # Gray — info text
}


# ── Test result data ───────────────────────────────────────────────────

@dataclass
class FrameResult:
    filename: str
    state_detected: str = "unknown"
    state_confidences: dict[str, bool] = field(default_factory=dict)
    gold: int | None = None
    level: int | None = None
    hp: int | None = None
    timer: int | None = None
    shop_units: list[str | None] = field(default_factory=list)
    shop_decisions: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    perception_latency_ms: float = 0
    error: str | None = None


# ── Main ───────────────────────────────────────────────────────────────

async def main():
    args = _parse_args()
    cfg = TestConfig(
        input_dir=Path(args.input) if args.input else None,
        output_dir=Path(args.output),
        single_image=Path(args.image) if args.image else None,
        no_ocr=args.no_ocr,
        no_decision=args.no_decision,
    )

    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    # Collect images
    images: list[Path] = []
    if cfg.single_image:
        images = [cfg.single_image]
    elif cfg.input_dir:
        images = sorted(
            p for p in cfg.input_dir.iterdir()
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp")
        )
    else:
        print("ERROR: need --input <dir> or --image <file>")
        sys.exit(1)

    if not images:
        print(f"No images found in {cfg.input_dir}")
        sys.exit(1)

    print(f"Processing {len(images)} images...")
    print(f"  OCR: {'OFF' if cfg.no_ocr else 'ON (PaddleOCR)'}")
    print(f"  Decision: {'OFF' if cfg.no_decision else 'ON'}")
    print(f"  Output: {cfg.output_dir.resolve()}")
    print()

    # ── Setup ──────────────────────────────────────────────────────────
    ocr_task = None
    if not cfg.no_ocr:
        try:
            from gameauto.core.perception.cv.ocr import OcrTask
            ocr_task = OcrTask(lang="ch", gpu=False, warmup=True)
            print("PaddleOCR initialized")
        except ImportError as e:
            print(f"PaddleOCR not available: {e}")
            print("Falling back to --no-ocr mode (state detection + ROI only)")
            cfg.no_ocr = True

    tm_task = _load_templates()
    perception = TftPerception(
        ocr_task=ocr_task if ocr_task else _FakeOcrTask(),
        tm_task=tm_task,
    )

    # Load config for decision rules
    try:
        from gameauto.config.loader import load_game_config
        game_cfg = load_game_config("tft")
        strategy_cfg = game_cfg.get("strategy", {})
    except Exception:
        strategy_cfg = _default_strategy()

    rules = TftRules(
        core_champions=strategy_cfg.get("core_champions", []),
        champion_roles=strategy_cfg.get("champion_roles", {}),
        config=strategy_cfg,
    )
    decision = TftDecision(rules) if not cfg.no_decision else None

    print(f"Strategy: {strategy_cfg.get('type', 'unknown')}")
    print(f"Core champions: {[c['name'] for c in strategy_cfg.get('core_champions', [])]}")
    print(f"Templates loaded: {tm_task.template_names or '(none — state detection uses fallback)'}")
    print()

    # ── Process each image ─────────────────────────────────────────────
    results: list[FrameResult] = []

    for idx, img_path in enumerate(images):
        print(f"[{idx+1}/{len(images)}] {img_path.name} ...", end=" ", flush=True)
        t0 = time.time()

        img_bytes = img_path.read_bytes()
        fr = FrameResult(filename=img_path.name)

        try:
            # 1. State Detection
            state_detected, state_conf = _detect_state(img_bytes, perception, tm_task)
            fr.state_detected = state_detected
            fr.state_confidences = state_conf

            # 2. Perception (only if PLANNING or always if --no-ocr mode for ROI viz)
            perception_data: dict[str, Any] = {}
            if state_detected == TftState.PLANNING or cfg.no_ocr:
                if not cfg.no_ocr and ocr_task:
                    result = await perception.recognize(img_bytes)
                    perception_data = result.parsed
                    fr.perception_latency_ms = result.latency_ms
                    fr.gold = perception_data.get("gold")
                    fr.level = perception_data.get("level")
                    fr.hp = perception_data.get("hp")
                    fr.timer = perception_data.get("timer_seconds")
                    fr.shop_units = perception_data.get("shop_units", [])

                # 3. Decision
                if decision and perception_data:
                    from gameauto.core.orchestration.context import GameContext
                    dummy_ctx = GameContext(
                        state=state_detected,
                        session_dir=cfg.output_dir,
                        capture_resolution=(1440, 3200),
                        input_resolution=(1440, 3200),
                        max_rounds=1,
                    )
                    actions = decision.decide(perception_data, dummy_ctx)
                    fr.actions = [{
                        "type": a.type,
                        "x1": a.x1, "y1": a.y1,
                        "x2": getattr(a, "x2", 0), "y2": getattr(a, "y2", 0),
                        "desc": a.description,
                    } for a in actions]

                    # Determine buy/skip per shop slot
                    bought_slots = {
                        a["x1"] for a in fr.actions if a["type"] == "tap" and a.get("desc", "").startswith("Buy")
                    }
                    import math
                    SHOP_SLOT_X = [160, 320, 500, 680, 840]
                    for i, unit in enumerate(fr.shop_units):
                        slot_x = SHOP_SLOT_X[i]
                        fr.shop_decisions.append({
                            "slot": i,
                            "unit": unit,
                            "decision": "BUY" if any(
                                abs(a["x1"] - slot_x) < 30 for a in fr.actions if a["type"] == "tap"
                            ) else "SKIP",
                        })

            # 4. Generate annotated image
            annotated = _annotate_frame(
                img_bytes, fr, perception,
                show_ocr=not cfg.no_ocr,
                show_decision=not cfg.no_decision,
            )
            out_path = cfg.output_dir / f"{img_path.stem}_annotated.png"
            annotated.save(str(out_path))

            # Save perception JSON
            json_path = cfg.output_dir / f"{img_path.stem}_perception.json"
            json_path.write_text(json.dumps({
                "filename": fr.filename,
                "state": fr.state_detected,
                "state_confidences": fr.state_confidences,
                "gold": fr.gold,
                "level": fr.level,
                "hp": fr.hp,
                "timer": fr.timer,
                "shop_units": fr.shop_units,
                "shop_decisions": fr.shop_decisions,
                "actions": fr.actions,
                "latency_ms": fr.perception_latency_ms,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            results.append(fr)
            dt = (time.time() - t0) * 1000
            status = _status_icon(fr)
            print(f"{status} {fr.state_detected} | gold={fr.gold} lv={fr.level} hp={fr.hp} | {dt:.0f}ms")

        except Exception as e:
            fr.error = str(e)
            results.append(fr)
            print(f"✗ ERROR: {e}")

    # ── Generate HTML report ───────────────────────────────────────────
    html_path = cfg.output_dir / "index.html"
    _generate_html(results, cfg, html_path)
    print(f"\nDone! {len(results)} images processed.")
    print(f"  Report: {html_path.resolve()}")
    print(f"  Open in browser to review all results.")


# ── State Detection ────────────────────────────────────────────────────

def _detect_state(
    image: bytes,
    perception: TftPerception,
    tm_task: TemplateMatchTask,
) -> tuple[str, dict[str, bool]]:
    """Run all 4 state detectors and return best match."""
    states = {
        TftState.LOBBY: ("lobby_play_btn", 0.70),
        TftState.PLANNING: ("planning_timer", 0.65),
        TftState.COMBAT: ("combat_indicator", 0.70),
    }

    results: dict[str, bool] = {}

    for state, (template_key, threshold) in states.items():
        roi = perception.get_state_detection_roi(template_key)
        try:
            results[state] = tm_task._match_inline(
                image, roi, template_key, threshold=threshold,
            )
        except Exception:
            results[state] = False

    # Loading detection: NOT lobby AND NOT planning AND NOT combat
    is_loading = not any(results.values())
    results[TftState.QUEUE_LOADING] = is_loading

    if results.get(TftState.PLANNING):
        return TftState.PLANNING, results
    if results.get(TftState.LOBBY):
        return TftState.LOBBY, results
    if results.get(TftState.COMBAT):
        return TftState.COMBAT, results
    return TftState.QUEUE_LOADING, results


# ── Annotated image generation ─────────────────────────────────────────

def _annotate_frame(
    image: bytes,
    fr: FrameResult,
    perception: TftPerception,
    show_ocr: bool = True,
    show_decision: bool = True,
) -> Image.Image:
    """Draw all annotations on the screenshot."""
    if not _PIL:
        return Image.new("RGB", (100, 100))

    img = Image.open(BytesIO(image)).convert("RGBA")
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    try:
        font_sm = ImageFont.truetype("simhei.ttf", 14)
        font_md = ImageFont.truetype("simhei.ttf", 20)
        font_lg = ImageFont.truetype("simhei.ttf", 28)
    except Exception:
        font_sm = font_md = font_lg = ImageFont.load_default()

    # ── 1. State banner (top) ──────────────────────────────────────────
    state_color = {
        TftState.LOBBY: (255, 200, 50),
        TftState.PLANNING: (50, 255, 100),
        TftState.COMBAT: (255, 80, 80),
        TftState.QUEUE_LOADING: (150, 150, 255),
    }.get(fr.state_detected, (200, 200, 200))

    banner_h = 50
    draw.rectangle([0, 0, w, banner_h], fill=(0, 0, 0, 200))
    draw.text((16, 10), f"State: {fr.state_detected}", fill=state_color, font=font_lg)

    # State confidence indicators
    x_off = 350
    for s, detected in fr.state_confidences.items():
        icon = "✓" if detected else "✗"
        clr = (100, 255, 100) if detected else (255, 100, 100)
        short = s.replace("tft_", "")
        draw.text((x_off, 15), f"{icon} {short}", fill=clr, font=font_sm)
        x_off += 130

    # Error banner
    if fr.error:
        draw.rectangle([0, banner_h, w, banner_h + 30], fill=(200, 0, 0, 200))
        draw.text((16, banner_h + 5), f"ERROR: {fr.error}", fill=(255, 255, 255), font=font_sm)

    # ── 2. Info overlay (top-right) ────────────────────────────────────
    info_y = banner_h + 10
    info_lines = [
        f"Gold: {fr.gold or '?'}",
        f"Level: {fr.level or '?'}",
        f"HP: {fr.hp or '?'}",
        f"Timer: {fr.timer or '?'}s",
        f"Latency: {fr.perception_latency_ms:.0f}ms",
    ]
    for line in info_lines:
        bbox = draw.textbbox((0, 0), line, font=font_md)
        tw = bbox[2] - bbox[0]
        draw.rectangle(
            [w - tw - 30, info_y - 4, w - 10, info_y + 22],
            fill=(0, 0, 0, 160),
        )
        draw.text((w - tw - 26, info_y), line, fill=CLR["ocr_text"], font=font_md)
        info_y += 28

    # ── 3. ROI rectangles ──────────────────────────────────────────────
    rois_to_draw = [
        ("gold", perception.get_state_detection_roi("lobby_play_btn")),
        ("level", perception.get_state_detection_roi("lobby_play_btn")),
        ("hp", perception.get_state_detection_roi("lobby_play_btn")),
        ("timer", perception.get_state_detection_roi("planning_timer")),
    ]

    # Draw info ROIs
    info_rois = _get_info_rois(perception)
    for label, roi in info_rois.items():
        left = int(roi[0] * w)
        top = int(roi[1] * h)
        right = int(roi[2] * w)
        bottom = int(roi[3] * h)
        draw.rectangle([left, top, right, bottom], outline=CLR["roi"], width=2)
        draw.text((left + 2, top - 18), label, fill=CLR["roi_label"], font=font_sm)

    # ── 4. Shop slots ──────────────────────────────────────────────────
    SHOP_SLOT_ROIS = [
        (0.06, 0.65, 0.22, 0.96),
        (0.24, 0.65, 0.40, 0.96),
        (0.42, 0.65, 0.58, 0.96),
        (0.60, 0.65, 0.76, 0.96),
        (0.78, 0.65, 0.94, 0.96),
    ]

    for i, roi in enumerate(SHOP_SLOT_ROIS):
        left = int(roi[0] * w)
        top = int(roi[1] * h)
        right = int(roi[2] * w)
        bottom = int(roi[3] * h)

        # Decision color
        decision = "?"
        if i < len(fr.shop_decisions):
            decision = fr.shop_decisions[i]["decision"]

        if decision == "BUY":
            color = CLR["buy"]
            width = 3
        elif decision == "SKIP":
            color = CLR["skip"]
            width = 1
        else:
            color = (150, 150, 150)
            width = 1

        draw.rectangle([left, top, right, bottom], outline=color, width=width)

        # Slot label
        unit_name = fr.shop_units[i] if i < len(fr.shop_units) else "?"
        label = f"Slot {i}: {unit_name or '---'} [{decision}]"
        label_y = bottom + 4 if bottom + 22 < h else top - 22
        draw.rectangle(
            [left, label_y, right, label_y + 20],
            fill=(0, 0, 0, 180),
        )
        draw.text((left + 2, label_y + 2), label, fill=color, font=font_sm)

    # ── 5. Action arrows ───────────────────────────────────────────────
    for action in fr.actions:
        px = int(action["x1"] * w / 1000)
        py = int(action["y1"] * h / 1000)
        r = 10

        if action["type"] == "tap":
            draw.ellipse([px - r, py - r, px + r, py + r], outline=CLR["action"], width=2)
            draw.text((px + r + 4, py - 8), action.get("desc", "")[:20],
                      fill=CLR["action"], font=font_sm)
        elif action["type"] in ("swipe", "drag"):
            px2 = int(action["x2"] * w / 1000)
            py2 = int(action["y2"] * h / 1000)
            draw.line([px, py, px2, py2], fill=CLR["action"], width=3)

    # ── 6. Footer ──────────────────────────────────────────────────────
    footer_y = h - 24
    draw.rectangle([0, footer_y, w, h], fill=(0, 0, 0, 180))
    draw.text((16, footer_y + 4), fr.filename, fill=CLR["info"], font=font_sm)

    img = Image.alpha_composite(img, overlay)
    return img.convert("RGB")


def _get_info_rois(perception: TftPerception) -> dict[str, tuple]:
    """Get info ROIs from perception instance."""
    rois = {}
    try:
        for key in ("gold", "level", "hp", "round_timer"):
            full_key = {
                "gold": "gold", "level": "level", "hp": "hp", "round_timer": "round_timer"
            }[key]
            # Use default ROIs since we can't easily access _rois
            pass
    except Exception:
        pass

    # Hardcoded fallback ROIs for visualization
    return {
        "gold": (0.02, 0.89, 0.10, 0.95),
        "level": (0.02, 0.02, 0.08, 0.07),
        "hp": (0.86, 0.02, 0.98, 0.07),
        "timer": (0.44, 0.00, 0.56, 0.06),
    }


# ── HTML Report ────────────────────────────────────────────────────────

def _generate_html(results: list[FrameResult], cfg: TestConfig, html_path: Path) -> None:
    """Generate an HTML summary page with all annotated images."""
    img_dir = cfg.output_dir

    rows_html = ""
    for i, fr in enumerate(results):
        img_name = f"{Path(fr.filename).stem}_annotated.png"
        shop_html = ""
        for sd in fr.shop_decisions:
            clr = "#4caf50" if sd["decision"] == "BUY" else "#f44336"
            shop_html += (
                f'<span style="color:{clr};margin:0 4px;padding:2px 6px;'
                f'border:1px solid {clr};border-radius:3px;font-size:11px">'
                f'Slot{sd["slot"]}: {sd["unit"] or "---"} [{sd["decision"]}]</span>'
            )

        actions_html = "<br>".join(
            f'<span style="color:#ff32c8">{a["type"]}</span> {a.get("desc", "")}'
            for a in fr.actions
        ) or "—"

        state_clr = {
            TftState.LOBBY: "#ffc832",
            TftState.PLANNING: "#32ff64",
            TftState.COMBAT: "#ff5050",
            TftState.QUEUE_LOADING: "#9696ff",
        }.get(fr.state_detected, "#ccc")

        rows_html += f"""
        <tr>
            <td>{i+1}</td>
            <td>{fr.filename}</td>
            <td style="color:{state_clr};font-weight:bold">{fr.state_detected}</td>
            <td>{fr.gold or '?'}</td>
            <td>{fr.level or '?'}</td>
            <td>{fr.hp or '?'}</td>
            <td>{fr.timer or '?'}s</td>
            <td>{shop_html}</td>
            <td>{actions_html}</td>
            <td>{fr.perception_latency_ms:.0f}ms</td>
            <td><a href="{img_name}" target="_blank">📷 View</a></td>
        </tr>"""

    states_count = {}
    for fr in results:
        s = fr.state_detected
        states_count[s] = states_count.get(s, 0) + 1
    state_summary = " | ".join(f"{s}: {c}" for s, c in states_count.items())

    buy_count = sum(
        1 for fr in results
        for sd in fr.shop_decisions if sd["decision"] == "BUY"
    )
    total_slots = sum(len(fr.shop_decisions) for fr in results)

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>TFT Perception Test — {datetime.now().strftime('%Y-%m-%d %H:%M')}</title>
<style>
  body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #1a1a2e; color: #eee; }}
  h1 {{ color: #ffc832; }}
  h2 {{ color: #aaa; font-size: 14px; font-weight: normal; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th {{ background: #16213e; padding: 8px 6px; text-align: left; position: sticky; top: 0; }}
  td {{ padding: 6px; border-bottom: 1px solid #333; vertical-align: top; }}
  tr:hover {{ background: #16213e55; }}
  .summary {{ background: #16213e; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
  .summary span {{ margin-right: 20px; }}
  a {{ color: #64b5f6; }}
  .error {{ color: #ff5050; }}
</style>
</head>
<body>
<h1>🎮 TFT Perception Test Report</h1>
<h2>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {len(results)} images | OCR: {'ON' if not cfg.no_ocr else 'OFF'} | Decision: {'ON' if not cfg.no_decision else 'OFF'}</h2>

<div class="summary">
  <strong>State Distribution:</strong> <span>{state_summary}</span><br>
  <strong>Buy Rate:</strong> <span>{buy_count}/{total_slots} slots ({buy_count/max(total_slots,1)*100:.0f}%)</span><br>
  <strong>Output:</strong> <span>{cfg.output_dir.resolve()}</span>
</div>

<table>
<thead>
<tr>
  <th>#</th><th>File</th><th>State</th><th>Gold</th><th>Lv</th><th>HP</th><th>Timer</th>
  <th>Shop Decisions</th><th>Actions</th><th>Latency</th><th>Image</th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>"""

    html_path.write_text(html, encoding="utf-8")


# ── Helpers ────────────────────────────────────────────────────────────

def _load_templates() -> TemplateMatchTask:
    """Load templates from skills/tft/assets/templates/."""
    tm_dir = Path(__file__).parent.parent / "skills" / "tft" / "assets" / "templates"
    tm = TemplateMatchTask(str(tm_dir) if tm_dir.is_dir() else None)
    return tm


class _FakeOcrTask:
    """No-op OCR task for --no-ocr mode."""
    async def run(self, image, roi=None, config=None):
        return {"texts": [], "confidences": [], "combined_text": ""}


def _status_icon(fr: FrameResult) -> str:
    """Return status icon for console output."""
    if fr.error:
        return "✗"
    if fr.state_detected == TftState.PLANNING:
        has_buy = any(sd["decision"] == "BUY" for sd in fr.shop_decisions)
        return "🟢" if has_buy else "🟡"
    return "⏳"


def _default_strategy() -> dict:
    return {
        "type": "德玛西亚琴女",
        "core_champions": [
            {"name": "娑娜", "cost": 1, "target_stars": 3},
            {"name": "嘉文四世", "cost": 1, "target_stars": 3},
            {"name": "波比", "cost": 2, "target_stars": 3},
            {"name": "赵信", "cost": 2, "target_stars": 3},
            {"name": "盖伦", "cost": 4, "target_stars": 2},
            {"name": "拉克丝", "cost": 4, "target_stars": 2},
        ],
        "champion_roles": {
            "tank": ["嘉文四世", "波比", "赵信", "盖伦"],
            "carry": ["娑娜", "拉克丝"],
        },
    }


def _parse_args():
    p = argparse.ArgumentParser(description="TFT Perception Batch Test")
    p.add_argument("--input", help="Directory of screenshots")
    p.add_argument("--image", help="Single screenshot file")
    p.add_argument("--output", default="results/tft_test", help="Output directory (default: results/tft_test)")
    p.add_argument("--no-ocr", action="store_true", help="Skip OCR (state detection + ROI visualization only)")
    p.add_argument("--no-decision", action="store_true", help="Skip decision engine")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
