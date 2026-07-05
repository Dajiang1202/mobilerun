#!/usr/bin/env python3
"""工具 B —— TFT 模板裁剪 (仿 crop_template.py, TFT 专用)。

按 TFT 模板清单, 从游戏截图框选 → 无损 PNG 保存到 skills/tft/assets/templates/,
并把比例 ROI 写进 rois.yaml 的 templates: section。

模板存成扁平文件 <file>.png 直接放在 templates/ 下 —— 因为
TemplateMatchTask.load_templates 用非递归 glob("*.png"), 子目录里的不会被加载。
要分子类就在下面清单的 cat 上分, 文件名仍保持唯一扁平。

用法:
    python gameauto/tools/crop_tft_template.py --screens D:/screenshots
    (或改下面 SCREENS_DIR / OUT_DIR 默认值)

操作: 鼠标框选=保存并下一项 | ← →切项 | ↑ ↓切截图 | x 跳过 | u 撤销 | q 退出
ROI 存成比例 {left,top,right,bottom}∈[0,1]。

新增模板 = 在下面 build_checklist() 里加一项。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from gameauto.tools.roi_annotator import (
    load_screenshots, ruamel_load, ruamel_dump, set_roi,
    MAX_DISP_W, MAX_DISP_H, ZOOM_PIX, ZOOM_RADIUS,
    KEY_RIGHT, KEY_LEFT, KEY_DOWN, KEY_UP, ROIS_YAML,
)

# ═══════════════════════════════════════════════════════════════════════
#  配置区
# ═══════════════════════════════════════════════════════════════════════

SCREENS_DIR = r"D:\screenshots"
OUT_DIR = (Path(__file__).resolve().parent.parent
           / "skills" / "tft" / "assets" / "templates")


def build_checklist() -> list[dict]:
    """TFT 模板清单。每项: file(文件名stem, 唯一) / cat(分类, 仅展示) / desc(中文)。"""
    items: list[dict] = []
    # 状态检测模板
    for nm, desc in [
        ("lobby_play_btn", "大厅-开始游戏按钮"),
        ("planning_timer", "备战阶段-计时器"),
        ("combat_indicator", "战斗中-标识"),
        ("result_rank", "结算-排名"),
        ("carousel_banner", "选秀-横幅"),
        ("augment_frame", "海克斯-强化框"),
        ("pve_indicator", "野怪关-标识"),
        ("shop_toggle", "商店开关按钮 (开/关商店)"),
    ]:
        items.append({"file": nm, "cat": "state", "desc": f"状态-{desc}"})
    # 按钮模板
    for nm, desc in [
        ("refresh_btn", "刷新商店按钮"),
        ("buy_xp_btn", "购买经验按钮"),
    ]:
        items.append({"file": nm, "cat": "buttons", "desc": f"按钮-{desc}"})
    # 掉落物(问号)模板 — 三种颜色, 模板匹配识别 (替代不稳的全图OCR)
    for nm, desc in [
        ("drop_blue", "蓝色问号掉落物"),
        ("drop_white", "白色问号掉落物"),
        ("drop_gold", "金色问号掉落物"),
    ]:
        items.append({"file": nm, "cat": "drops", "desc": f"掉落-{desc}"})
    return items


# ═══════════════════════════════════════════════════════════════════════


def save_crop(crop_bgr: np.ndarray, out_dir: Path, file: str) -> Path:
    """无损保存 (PIL 兼容中文路径)。扁平存放在 out_dir/<file>.png。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{file}.png"
    Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)).save(str(path))
    return path


class Cropper:
    def __init__(self, screens, checklist, out_dir: Path,
                 yaml_path: Path, win_name: str = "crop - TFT template"):
        self.screens = screens
        self.checklist = checklist
        self.out_dir = out_dir
        self.yaml_path = yaml_path
        self.win_name = win_name

        self.scr_idx = 0
        self.completed: set[str] = self._scan_completed()
        self.skipped: set[str] = set(self._load_progress().get("skipped", []))
        self.idx = 0
        for i, it in enumerate(self.checklist):
            if it["file"] not in self.completed and it["file"] not in self.skipped:
                self.idx = i
                break

        self.scr_orig = self.scr_disp = None
        self.disp_w = self.disp_h = 0
        self.scale = 1.0

        self.drawing = False
        self.start = (0, 0)
        self.roi: list[int] | None = None
        self.mx = self.my = -1

    # ── 进度 ──────────────────────────────────────────────────────────
    def _scan_completed(self) -> set[str]:
        if not self.out_dir.exists():
            return set()
        return {p.stem for p in self.out_dir.glob("*.png")}

    def _progress_path(self) -> Path:
        return self.out_dir / "progress.json"

    def _load_progress(self) -> dict:
        p = self._progress_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
        return {}

    def _save_progress(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._progress_path().write_text(
            json.dumps({"skipped": sorted(self.skipped),
                        "completed": sorted(self.completed)},
                       ensure_ascii=False, indent=2), encoding="utf-8")

    def _cur(self) -> dict:
        return self.checklist[self.idx]

    def _set_screenshot(self):
        self.scr_orig = self.screens[self.scr_idx][1]
        h, w = self.scr_orig.shape[:2]
        self.scale = min(MAX_DISP_W / w, MAX_DISP_H / h)
        self.disp_w = int(w * self.scale)
        self.disp_h = int(h * self.scale)
        self.scr_disp = cv2.resize(self.scr_orig, (self.disp_w, self.disp_h),
                                   interpolation=cv2.INTER_AREA)
        try:
            cv2.resizeWindow(self.win_name, self.disp_w, self.disp_h)
        except Exception:  # noqa: BLE001
            pass
        self.roi = None

    # ── 鼠标 ──────────────────────────────────────────────────────────
    def mouse(self, event, x, y, _f, _p):
        self.mx, self.my = x, y
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.start = (x, y)
            self.roi = [x, y, x, y]
        elif event == cv2.EVENT_MOUSEMOVE and self.drawing:
            self.roi[2], self.roi[3] = x, y
        elif event == cv2.EVENT_LBUTTONUP:
            self.drawing = False
            x1, y1 = min(self.start[0], x), min(self.start[1], y)
            x2, y2 = max(self.start[0], x), max(self.start[1], y)
            if x2 - x1 < 3 or y2 - y1 < 3:
                self.roi = None
                return
            self._commit(max(0, x1), max(0, y1),
                         min(self.disp_w, x2), min(self.disp_h, y2))

    def _commit(self, x1, y1, x2, y2):
        ox1, oy1 = int(x1 / self.scale), int(y1 / self.scale)
        ox2, oy2 = int(x2 / self.scale), int(y2 / self.scale)
        crop = self.scr_orig[oy1:oy2, ox1:ox2]
        if crop.size == 0:
            return
        item = self._cur()
        path = save_crop(crop, self.out_dir, item["file"])
        self.completed.add(item["file"])
        self.skipped.discard(item["file"])
        # 比例 ROI 写进 rois.yaml 的 templates: section
        h, w = self.scr_orig.shape[:2]
        data = ruamel_load(self.yaml_path)
        data = set_roi(data, "templates", item["file"],
                       (ox1 / w, oy1 / h, ox2 / w, oy2 / h))
        ruamel_dump(self.yaml_path, data)
        self._save_progress()
        print(f"  ✓ {item['file']}.png  ({ox2-ox1}x{oy2-oy1})  "
              f"← {self.screens[self.scr_idx][0]}  [{item['desc']}]")
        self.roi = None
        self.idx = (self.idx + 1) % len(self.checklist)

    def _undo(self):
        item = self._cur()
        path = self.out_dir / f"{item['file']}.png"
        if path.exists():
            path.unlink()
            print(f"  ✗ 撤销 {item['file']}")
        self.completed.discard(item["file"])
        data = ruamel_load(self.yaml_path)
        if data and data.get("templates") and item["file"] in data["templates"]:
            del data["templates"][item["file"]]
            ruamel_dump(self.yaml_path, data)
        self._save_progress()

    def _skip(self):
        item = self._cur()
        self.skipped.add(item["file"])
        self._save_progress()
        print(f"  · 跳过 {item['file']}  [{item['desc']}]")
        self.idx = (self.idx + 1) % len(self.checklist)

    # ── 渲染 ──────────────────────────────────────────────────────────
    def _draw_zoom(self, disp):
        if self.mx < 0 or self.scr_orig is None:
            return
        ox = int(self.mx / self.scale)
        oy = int(self.my / self.scale)
        h, w = self.scr_orig.shape[:2]
        x1, x2 = max(0, ox - ZOOM_RADIUS), min(w, ox + ZOOM_RADIUS)
        y1, y2 = max(0, oy - ZOOM_RADIUS), min(h, oy + ZOOM_RADIUS)
        if x2 <= x1 or y2 <= y1:
            return
        patch = self.scr_orig[y1:y2, x1:x2]
        zoom = cv2.resize(patch, (ZOOM_PIX, ZOOM_PIX), interpolation=cv2.INTER_NEAREST)
        bx, by = self.disp_w - ZOOM_PIX - 12, 12
        disp[by:by + ZOOM_PIX, bx:bx + ZOOM_PIX] = zoom
        cv2.rectangle(disp, (bx, by), (bx + ZOOM_PIX, by + ZOOM_PIX), (0, 255, 255), 1)
        cx, cy = bx + ZOOM_PIX // 2, by + ZOOM_PIX // 2
        cv2.line(disp, (cx - 8, cy), (cx + 8, cy), (0, 255, 0), 1)
        cv2.line(disp, (cx, cy - 8), (cx, cy + 8), (0, 255, 0), 1)
        cv2.putText(disp, f"({ox},{oy})", (bx, by + ZOOM_PIX + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

    def _draw(self):
        from gameauto.tools.cv_text import overlay_text

        disp = self.scr_disp.copy()
        item = self._cur()
        done = sum(1 for it in self.checklist if it["file"] in self.completed)
        # desc 含中文, 半透明底不全宽遮挡
        disp = overlay_text(
            disp,
            f"[{self.idx+1}/{len(self.checklist)}] {item['cat']}/{item['file']}  "
            f"{item['desc']}  done={done}/{len(self.checklist)}",
            (6, 6), color_bgr=(200, 255, 200), px=20,
        )
        cv2.putText(disp, self.screens[self.scr_idx][0],
                    (6, self.disp_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        if self.roi:
            x1, y1, x2, y2 = self.roi
            cv2.rectangle(disp, (x1, y1), (x2, y2), (0, 0, 255), 2)
        self._draw_zoom(disp)
        cv2.imshow(self.win_name, disp)

    def _status(self):
        item = self._cur()
        flag = "[done]" if item["file"] in self.completed else (
            "[skip]" if item["file"] in self.skipped else "[todo]")
        print(f"\r {flag} [{self.idx+1}/{len(self.checklist)}] {item['cat']}/{item['file']}"
              f"  {item['desc']}      a/d=切项 w/s=切截图 x=跳过 u=删除当前项 q=退出", flush=True)

    def run(self):
        cv2.namedWindow(self.win_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.win_name, self.mouse)
        self._set_screenshot()
        todo = sum(1 for it in self.checklist
                   if it["file"] not in self.completed and it["file"] not in self.skipped)
        print(f"截图 {len(self.screens)} | 清单 {len(self.checklist)} 项 | 待标 {todo} | 输出 {self.out_dir}")
        print(f"提示: 按键前先点一下 \"{self.win_name}\" 窗口让它获得焦点")
        self._status()
        while True:
            self._draw()
            k = cv2.waitKeyEx(30)
            if k == -1:
                continue
            if k in (ord("q"), 27):
                break
            elif k in KEY_RIGHT:
                self.idx = (self.idx + 1) % len(self.checklist); self._status()
            elif k in KEY_LEFT:
                self.idx = (self.idx - 1) % len(self.checklist); self._status()
            elif k in KEY_DOWN:
                self.scr_idx = (self.scr_idx + 1) % len(self.screens); self._set_screenshot()
            elif k in KEY_UP:
                self.scr_idx = (self.scr_idx - 1) % len(self.screens); self._set_screenshot()
            elif k == ord("x"):
                self._skip(); self._status()
            elif k == ord("u"):
                self._undo(); self._status()
        cv2.destroyAllWindows()
        print(f"\n=== 完成 {len(self.completed)}/{len(self.checklist)},"
              f" 跳过 {len(self.skipped)} ===\n输出: {self.out_dir}")


def main():
    screens = load_screenshots(Path(SCREENS_DIR))
    if not screens:
        print(f"错误: 在 {SCREENS_DIR} 找不到截图 (png/jpg)。改脚本顶部 SCREENS_DIR。")
        sys.exit(1)

    Cropper(screens, build_checklist(), OUT_DIR, ROIS_YAML).run()


if __name__ == "__main__":
    main()
