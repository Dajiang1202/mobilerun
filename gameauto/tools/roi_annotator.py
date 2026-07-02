#!/usr/bin/env python3
"""ROI 标注共享核心 —— 鼠标框选截图 → 比例 ROI → 保留注释写回 rois.yaml。

被 annotate_tft_ocr_rois.py / annotate_tft_tm_rois.py 复用; crop_tft_template.py
复用其中的 load_screenshots / ruamel 读写 / 常量。

UX 抄自 crop_template.py(已验证): 显示坐标↔原图坐标分离, 右上角放大镜,
← →切清单项, ↑ ↓切截图, 鼠标框选=保存, x 跳过, u 撤销, q 退出。
ROI 一律存成比例 {left,top,right,bottom}∈[0,1], 分辨率无关。

ruamel.yaml 做注释保留往返编辑 —— 手写的 rois.yaml 注释/格式不丢。
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

try:
    from ruamel.yaml import YAML
except ImportError:  # pragma: no cover
    sys.exit("缺少 ruamel.yaml, 请: pip install ruamel.yaml")

# ── 显示 / 交互常量 (与 crop_template.py 一致) ────────────────────────
MAX_DISP_W = 1200
MAX_DISP_H = 980
ZOOM_PIX = 170
ZOOM_RADIUS = 26

# 方向键 keycode (Windows WIN32 / Linux Qt 两套 + 字母备用)
KEY_RIGHT = {2555904, 65363, 83, ord("d")}
KEY_LEFT = {2424832, 65361, 81, ord("a")}
KEY_DOWN = {2621440, 65364, 84, ord("s")}
KEY_UP = {2490368, 65362, 82, ord("w")}

ROIS_YAML = (Path(__file__).resolve().parent.parent / "skills" / "tft" / "config" / "rois.yaml")


# ── 通用辅助 ───────────────────────────────────────────────────────────

def load_screenshots(d: Path) -> list[tuple[str, np.ndarray]]:
    """加载目录下所有 png/jpg, 返回 [(文件名, BGR)]。用 PIL 兼容中文路径。"""
    out: list[tuple[str, np.ndarray]] = []
    if not d.exists():
        return out
    for f in sorted(d.glob("*")):
        if f.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        try:
            arr = np.array(Image.open(str(f)).convert("RGB"))
            out.append((f.name, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)))
        except Exception as e:  # noqa: BLE001
            print(f"  跳过 {f.name}: {e}")
    return out


def ruamel_load(path: Path):
    """注释保留地读取 yaml, 返回 ruamel CommentedMap。"""
    yaml = YAML()
    yaml.preserve_quotes = True
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.load(f)


def ruamel_dump(path: Path, data) -> None:
    """注释保留地写回 yaml。"""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = None  # 让 {left: ..} 内联块按原文件风格; None=自动
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def set_roi(data, section: str, key: str, box_ratios: tuple[float, float, float, float]):
    """把比例 ROI 写进 data[section][key] = {left,top,right,bottom}。section 不存在则建。"""
    from ruamel.yaml import CommentedMap
    if data is None:
        data = CommentedMap()
    if section not in data or data[section] is None:
        data[section] = CommentedMap()
    sec = data[section]
    l, t, r, b = box_ratios
    sec[key] = {
        "left": round(l, 4), "top": round(t, 4),
        "right": round(r, 4), "bottom": round(b, 4),
    }
    return data


def get_roi(data, section: str, key: str) -> tuple[float, float, float, float] | None:
    """读 data[section][key] 的比例 ROI, 没有/不合法返回 None。"""
    if not data:
        return None
    sec = data.get(section)
    if not sec:
        return None
    box = sec.get(key)
    if not box:
        return None
    try:
        return (float(box["left"]), float(box["top"]),
                float(box["right"]), float(box["bottom"]))
    except Exception:  # noqa: BLE001
        return None


def section_keys(data, section: str) -> list[str]:
    """某 section 下所有 key (用于叠加显示已标注 ROI)。"""
    if not data:
        return []
    sec = data.get(section)
    return list(sec.keys()) if sec else []


# ── RoiAnnotator: 纯 ROI 标注 (工具 A/C 用) ───────────────────────────

class RoiAnnotator:
    """清单驱动的 ROI 标注器, 写进 rois.yaml 的指定 section。

    Args:
        screens: load_screenshots() 的结果 [(name, bgr)]。
        checklist: [{"key": str, "desc": str}] —— 逐项标注。
        yaml_path: rois.yaml 路径。
        section: 写进的顶层 section 名 (如 "ocr" / "template_match")。
    """

    def __init__(self, screens, checklist, yaml_path: Path, section: str,
                 win_title: str = "ROI Annotator"):
        self.screens = screens
        self.checklist = checklist
        self.yaml_path = Path(yaml_path)
        self.section = section
        self.win_title = win_title

        self.scr_idx = 0
        self.idx = 0

        self.scr_orig: np.ndarray | None = None
        self.scr_disp: np.ndarray | None = None
        self.disp_w = self.disp_h = 0
        self.scale = 1.0

        self.drawing = False
        self.start = (0, 0)
        self.roi: list[int] | None = None
        self.mx = self.my = -1

    def _cur(self) -> dict:
        return self.checklist[self.idx]

    def _data(self):
        return ruamel_load(self.yaml_path)

    def _set_screenshot(self):
        self.scr_orig = self.screens[self.scr_idx][1]
        h, w = self.scr_orig.shape[:2]
        self.scale = min(MAX_DISP_W / w, MAX_DISP_H / h)
        self.disp_w = int(w * self.scale)
        self.disp_h = int(h * self.scale)
        self.scr_disp = cv2.resize(self.scr_orig, (self.disp_w, self.disp_h),
                                   interpolation=cv2.INTER_AREA)
        try:
            cv2.resizeWindow(self.win_title, self.disp_w, self.disp_h)
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
        """框选松开 → 比例 ROI → 写回 yaml[section][key]。"""
        h, w = self.scr_orig.shape[:2]
        box = (x1 / self.scale / w, y1 / self.scale / h,
               x2 / self.scale / w, y2 / self.scale / h)
        data = self._data()
        data = set_roi(data, self.section, self._cur()["key"], box)
        ruamel_dump(self.yaml_path, data)
        print(f"  ✓ {self.section}/{self._cur()['key']} = "
              f"[{box[0]:.3f},{box[1]:.3f},{box[2]:.3f},{box[3]:.3f}]  "
              f"← {self.screens[self.scr_idx][0]}  [{self._cur()['desc']}]")
        self.roi = None
        self.idx = (self.idx + 1) % len(self.checklist)

    def _undo(self):
        """删掉当前项已存的 ROI。"""
        data = self._data()
        if data and data.get(self.section) and self._cur()["key"] in data[self.section]:
            del data[self.section][self._cur()["key"]]
            ruamel_dump(self.yaml_path, data)
            print(f"  ✗ 撤销 {self.section}/{self._cur()['key']}")

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
        disp = self.scr_disp.copy()
        # 叠加显示已标注的全部 ROI (本 section)
        data = self._data()
        for k in section_keys(data, self.section):
            box = get_roi(data, self.section, k)
            if not box:
                continue
            l = int(box[0] * self.disp_w); t = int(box[1] * self.disp_h)
            r = int(box[2] * self.disp_w); b = int(box[3] * self.disp_h)
            cur = (k == self._cur()["key"])
            color = (0, 0, 255) if cur else (0, 200, 0)
            cv2.rectangle(disp, (l, t), (r, b), color, 1 if not cur else 2)
            cv2.putText(disp, k, (l, max(0, t - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        # 顶部信息条 (半透明底, 不全宽遮挡 stage/timer)
        item = self._cur()
        from gameauto.tools.cv_text import overlay_text
        disp = overlay_text(
            disp,
            f"[{self.idx + 1}/{len(self.checklist)}] {self.section}/{item['key']}  {item['desc']}",
            (6, 6), color_bgr=(200, 255, 200), px=20,
        )
        cv2.putText(disp, self.screens[self.scr_idx][0],
                    (6, self.disp_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        if self.roi:
            x1, y1, x2, y2 = self.roi
            cv2.rectangle(disp, (x1, y1), (x2, y2), (0, 0, 255), 2)
        self._draw_zoom(disp)
        cv2.imshow(self.win_title, disp)

    def _status(self):
        item = self._cur()
        print(f"\r [{self.idx + 1}/{len(self.checklist)}] {self.section}/{item['key']}"
              f"  {item['desc']}      a/d=切项 w/s=切截图 框选=保存 u=删除当前项 q=退出", flush=True)

    def run(self):
        if not self.screens:
            print("错误: 没有截图。用 --screens 指定截图目录。")
            return
        cv2.namedWindow(self.win_title, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.win_title, self.mouse)
        self._set_screenshot()
        print(f"section: {self.section}  截图: {len(self.screens)}  清单: {len(self.checklist)} 项")
        print(f"提示: 按键前先点一下 \"{self.win_title}\" 窗口让它获得焦点")
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
                self.scr_idx = (self.scr_idx + 1) % len(self.screens)
                self._set_screenshot()
            elif k in KEY_UP:
                self.scr_idx = (self.scr_idx - 1) % len(self.screens)
                self._set_screenshot()
            elif k == ord("u"):
                self._undo(); self._status()
        cv2.destroyAllWindows()
        print(f"\n完成。ROI 已写回 {self.yaml_path} 的 {self.section}: section")
