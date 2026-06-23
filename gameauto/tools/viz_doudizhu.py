#!/usr/bin/env python3
"""斗地主模板匹配可视化 —— 分区匹配 + ROI 可视化 + JSON 输出。

分区匹配解决互串 + 提速; 每张图同步:
  - 画出各搜索 ROI(灰色框, 全图搜的不画)便于核对区域
  - 画出命中框(按类别着色) + 模板名
  - 输出 {stem}_result.json(latency / summary / 每条命中明细 / ROI 像素)

像素 ROI(本机截图, 运行时转归一化):
  手牌     x[350,2600]  y[770,970]
  底牌     x[1000,2100] y[0,130]
  上家出牌 x[700,1424]  y[200,600]
  下家出牌 x[1425,2100] y[200,600]
  按钮     x[500,2200]  y[600,800]
  地主标   全图搜索(roi=None)
  不出     同上家/下家出牌区

用法
  python gameauto/tools/viz_doudizhu.py             # 全部
  python gameauto/tools/viz_doudizhu.py --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.skills.doudizhu_douzero.perception import _strip_color

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "skills" / "doudizhu_douzero" / "assets" / "templates"
SCREEN_DIR = Path("D:/screenshots")
OUT_DIR = Path(__file__).resolve().parent.parent / "test_vlm_output" / "doudizhu_viz"

# 像素 ROI: (x1, y1, x2, y2)
PX = {
    "hand":      (350, 770, 2600, 970),
    "landlord3": (1000, 0, 2100, 130),
    "play_up":   (700, 200, 1424, 600),
    "play_down": (1425, 200, 2100, 600),
    "buttons":   (500, 600, 2200, 800),
}

# (标签, 模板目录, ROI键|None, scales, 框色RGB, 阈值, filter_names|None)
TASKS = [
    ("手牌",   "cards",   "hand",      [1.0],  (0, 200, 0),   0.88, None),
    ("上家",   "others",  "play_up",   [1.0],  (0, 130, 255), 0.85, None),
    ("下家",   "others",  "play_down", [1.0],  (0, 130, 255), 0.85, None),
    ("底牌",   "others",  "landlord3", [0.65], (0, 220, 255), 0.80, None),
    ("按钮",   "buttons", "buttons",   [1.0],  (0, 0, 255),   0.88,
     ["叫地主", "不叫", "抢地主", "加倍", "不加倍", "出牌", "不出", "要不起"]),
    ("继续游戏", "buttons", None,      [1.0],  (0, 0, 255),   0.85, ["继续"]),
    ("地主标", "ui",      None,        [1.0],  (255, 200, 0), 0.72, ["landlord_words"]),
    ("不出上", "ui",      "play_up",   [1.0],  (255, 200, 0), 0.85, ["pass"]),
    ("不出下", "ui",      "play_down", [1.0],  (255, 200, 0), 0.85, ["pass"]),
]


def load_font(size: int = 26):
    for p in ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
              "C:/Windows/Fonts/simhei.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def load_matchers() -> dict:
    dirs = {t[1] for t in TASKS}
    return {d: TemplateMatchTask(str(TEMPLATE_ROOT / d)) for d in dirs}


def _is_red(region_bgr: np.ndarray) -> bool:
    if region_bgr is None or region_bgr.size == 0:
        return False
    b = region_bgr[:, :, 0].astype(int)
    g = region_bgr[:, :, 1].astype(int)
    r = region_bgr[:, :, 2].astype(int)
    return ((r - g > 25) & (r - b > 25)).mean() > 0.05


def _color_matches(template: str, region_bgr: np.ndarray) -> bool:
    if len(template) >= 2 and template[0] in "mo" and template[1] in "br":
        return _is_red(region_bgr) == (template[1] == "r")
    return True


async def annotate(img_path: Path, matchers: dict, thr_override: float):
    img_bytes = img_path.read_bytes()
    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    font = load_font(22)
    font_sm = load_font(16)

    # 画 ROI 框(唯一区域, 灰色; 全图搜的不画)
    drawn: set[str] = set()
    for _label, _cat, rk, *_rest in TASKS:
        if rk and rk not in drawn:
            drawn.add(rk)
            x1, y1, x2, y2 = PX[rk]
            draw.rectangle([x1, y1, x2, y2], outline=(170, 170, 170), width=1)
            draw.text((x1 + 3, y1 + 3), f"ROI:{rk}", fill=(170, 170, 170), font=font_sm)

    def norm(rk):
        if rk is None:
            return None
        x1, y1, x2, y2 = PX[rk]
        return (x1 / w, y1 / h, x2 / w, y2 / h)

    results = await asyncio.gather(*[
        matchers[cat].run(
            img_bytes, roi=norm(rk),
            config={"threshold": (thr_override if thr_override else thr),
                    "scales": scales, "nms_iou": 0.3,
                    **({"filter_names": fn} if fn else {})})
        for _label, cat, rk, scales, _color, thr, fn in TASKS
    ])

    summary: dict[str, Counter] = {d: Counter() for d in matchers}
    detail: dict[str, list] = {}
    for (label, cat, _rk, _sc, color, _thr, _fn), res in zip(TASKS, results):
        dets = []
        for m in res.get("matches", []):
            x, y, bw, bh = m["x"], m["y"], m["w"], m["h"]
            region = img[y:y + bh, x:x + bw]
            if not _color_matches(m["template"], region):
                continue
            summary[cat][m["template"]] += 1
            dets.append({"template": m["template"], "score": round(float(m["score"]), 3),
                         "x": int(x), "y": int(y), "w": int(bw), "h": int(bh),
                         "scale": float(m["scale"])})
            draw.rectangle([x, y, x + bw, y + bh], outline=color, width=3)
            rank = _strip_color(m["template"])
            tag = rank if rank else m["template"]   # 牌显点数, 按钮显原名
            draw.text((x + 2, max(0, y - 26)), tag, fill=color, font=font,
                      stroke_width=2, stroke_fill=(0, 0, 0))
        detail[label] = dets

    pil.save(str(OUT_DIR / (img_path.stem + "_viz.png")))
    return summary, detail


async def main():
    ap = argparse.ArgumentParser(description="斗地主模板匹配可视化(分区+ROI+JSON)")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 张(0=全部)")
    ap.add_argument("--threshold", type=float, default=0.0, help="覆盖所有任务阈值(0=用各自默认)")
    ap.add_argument("--screens", default=str(SCREEN_DIR))
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    matchers = load_matchers()
    print(f"模板: {TEMPLATE_ROOT}")
    for d, m in matchers.items():
        print(f"  {d}: {len(m.template_names)} templates")

    shots = sorted(Path(args.screens).glob("*.jpeg"))
    if args.limit:
        shots = shots[: args.limit]
    print(f"待处理 {len(shots)} 张 -> {OUT_DIR}\n")

    times: list[float] = []
    t_all = time.perf_counter()
    for i, p in enumerate(shots, 1):
        t0 = time.perf_counter()
        summary, detail = await annotate(p, matchers, args.threshold)
        dt = time.perf_counter() - t0
        times.append(dt)

        # 写 JSON
        result = {
            "screenshot": p.name,
            "latency_ms": round(dt * 1000),
            "roi_pixels": {k: list(v) for k, v in PX.items()},
            "summary": {cat: dict(cnt) for cat, cnt in summary.items()},
            "matches": detail,
        }
        (OUT_DIR / (p.stem + "_result.json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        parts = []
        for cat in matchers:
            d = summary[cat]
            total = sum(d.values())
            detail_s = ",".join(f"{t}x{c}" for t, c in d.most_common(6))
            parts.append(f"{cat}={total}" + (f"({detail_s})" if detail_s else ""))
        print(f"[{i}/{len(shots)}] {p.name} ({dt:.2f}s) " + " | ".join(parts))
    total = time.perf_counter() - t_all
    print(f"\n=== {len(shots)} 张 | 总 {total:.1f}s | 平均 {total/len(shots):.2f}s/张"
          f" | 最快 {min(times):.2f}s | 最慢 {max(times):.2f}s ===")


if __name__ == "__main__":
    asyncio.run(main())
