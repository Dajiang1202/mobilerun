#!/usr/bin/env python3
"""斗地主(DouZero)模板截取工具 —— 清单驱动 + 实时放大镜 + 无损裁剪。

背景
----
VLM 能识别「是什么」但说不出「在哪」,坐标对齐困难,故斗地主/象棋切到纯模板匹配。
本工具按 DouZero_For_HappyDouDiZhu 原版的模板命名规范,内置 67 项模板清单,
逐项引导用户从手机游戏截图里框选、自动命名、无损保存到对应子目录。

模板命名规范(与框架 RealCard2EnvCard 无缝对接,见 douzero/env/game.py)
  - 手牌角标  cards/m{b|r}{点数}   mb3 mrA ... (b黑/r红, 28张)
  - 对手角标  others/o{b|r}{点数}  ob3 orD ... (28张, 兼用底牌)
  - 地主/不出/白块 ui/             landlord_words pass white
  - 按钮      buttons/             叫地主 不叫 ... (中文文件名)
  点数表: 3 4 5 6 7 8 9 T J Q K A 2 X(小王) D(大王)
  模板是牌「左上角角标」小图(参考原版 mb3=34x46),不是整张牌。

工作流
  1. 准备若干游戏阶段截图放 --screens 目录(出牌/叫牌/结算/底牌 各阶段都要)
  2. 运行本工具: 左窗显示当前模板名+原版参考缩略图, 右窗显示截图
  3. ← →切清单项, ↑ ↓切截图; 鼠标在右窗框选 → 松开自动以模板名无损保存
  4. 截不到的项按 x 跳过; u 撤销上次保存; q 退出
  显示图会缩放, 但裁剪始终用原始分辨率坐标 → 无损。

用法
  python gameauto/tools/crop_template.py
  python gameauto/tools/crop_template.py --screens D:/screenshots --out <dir> --ref <原版pics>
  python gameauto/tools/crop_template.py --export-checklist checklist.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

DEFAULT_OUT = Path("D:/screenshots/doudizhu_templates")  # 标注工作区(含 progress.json)
DEFAULT_REF = (
    Path(__file__).resolve().parent.parent.parent
    / "tmp"
    / "DouZero_For_HappyDouDiZhu-2.0"
    / "pics"
)
DEFAULT_SCREENS = Path("D:/screenshots")

SCR_H = 820           # 右窗截图显示高度(宽度按宽高比);越大越便于框小角标
ZOOM_PIX = 170        # 放大镜边长(显示像素)
ZOOM_RADIUS = 26      # 放大镜在原图采样的半径(原图像素),≈ 看清 34x46 角标

# 方向键 keycode: 不同 OpenCV 后端返回值不同, 用集合兼容 + 字母键备用
# Windows(WIN32): 左2424832 上2490368 右2555904 下2621440
# Linux/Qt:       左65361   上65362   右65363   下65364
KEY_RIGHT = {2555904, 65363, 83, ord("d")}
KEY_LEFT = {2424832, 65361, 81, ord("a")}
KEY_DOWN = {2621440, 65364, 84, ord("s")}
KEY_UP = {2490368, 65362, 82, ord("w")}

# 点数: 黑色牌含小王X, 红色牌含大王D(小王偏黑、大王偏红, 与原版 AllCards 一致)
B_RANKS = list("3456789TJQKA2") + ["X"]   # 14
R_RANKS = list("3456789TJQKA2") + ["D"]   # 14

RANK_CN = {"3": "3", "4": "4", "5": "5", "6": "6", "7": "7", "8": "8", "9": "9",
           "T": "10", "J": "J", "Q": "Q", "K": "K", "A": "A", "2": "2",
           "X": "小王", "D": "大王"}


def build_checklist() -> list[dict]:
    """生成 67 项模板清单。每项: file(文件名stem) / cat(子目录) / desc(中文) / rw,rh(参考尺寸)。"""
    items: list[dict] = []
    for r in B_RANKS:
        items.append({"file": "mb" + r, "cat": "cards",
                      "desc": f"我的手牌-黑-{RANK_CN[r]}", "rw": 34, "rh": 46})
    for r in R_RANKS:
        items.append({"file": "mr" + r, "cat": "cards",
                      "desc": f"我的手牌-红-{RANK_CN[r]}", "rw": 34, "rh": 46})
    for r in B_RANKS:
        items.append({"file": "ob" + r, "cat": "others",
                      "desc": f"对手出牌/底牌-黑-{RANK_CN[r]}", "rw": 23, "rh": 33})
    for r in R_RANKS:
        items.append({"file": "or" + r, "cat": "others",
                      "desc": f"对手出牌/底牌-红-{RANK_CN[r]}", "rw": 23, "rh": 33})
    items.append({"file": "landlord_words", "cat": "ui", "desc": "地主标志(地主二字)", "rw": 34, "rh": 55})
    items.append({"file": "pass", "cat": "ui", "desc": "不要/不出 气泡", "rw": 90, "rh": 50})
    items.append({"file": "white", "cat": "ui", "desc": "出牌区清空白块", "rw": 20, "rh": 31})
    for nm in ["叫地主", "不叫", "抢地主", "加倍", "不加倍", "出牌", "不出", "要不起", "继续", "开始游戏"]:
        items.append({"file": nm, "cat": "buttons", "desc": f"按钮-{nm}", "rw": 0, "rh": 0})
    return items


def _ascii_label(item: dict, idx: int, total: int) -> str:
    """窗口内文字(OpenCV putText 不支持中文, 用 ASCII)。"""
    f = item["file"]
    if f.isascii():
        return f"[{idx + 1}/{total}] {item['cat']}/{f}  ({item['rw']}x{item['rh']})"
    return f"[{idx + 1}/{total}] {item['cat']}/<{item['rw']}x{item['rh']}>"


def load_screenshots(d: Path) -> list[tuple[str, np.ndarray]]:
    out = []
    for f in sorted(d.glob("*")):
        if f.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        try:
            arr = np.array(Image.open(str(f)).convert("RGB"))
            out.append((f.name, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)))
        except Exception as e:  # noqa: BLE001
            print(f"  跳过 {f.name}: {e}")
    return out


def save_crop(crop_bgr: np.ndarray, out_dir: Path, cat: str, file: str) -> Path:
    """无损保存(用 PIL 以兼容中文文件名/路径)。"""
    d = out_dir / cat
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{file}.png"
    Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)).save(str(path))
    return path


class Cropper:
    def __init__(self, screens: list, checklist: list[dict], out_dir: Path,
                 ref_dir: Path, roi_path: Path):
        self.screens = screens
        self.checklist = checklist
        self.out_dir = out_dir
        self.ref_dir = ref_dir
        self.roi_path = roi_path

        self.scr_idx = 0
        # 进度持久化: 已完成 = 扫描输出目录实际存在的 png; 跳过 = progress.json
        self.completed: set[str] = self._scan_completed()
        self.skipped: set[str] = set(self._load_progress().get("skipped", []))
        # 启动时跳到第一个未完成项, 方便接着标
        self.idx = 0
        for i, it in enumerate(self.checklist):
            if it["file"] not in self.completed and it["file"] not in self.skipped:
                self.idx = i
                break

        # 显示与裁剪
        self.scr_orig: np.ndarray | None = None
        self.scr_disp: np.ndarray | None = None
        self.disp_w = self.disp_h = 0
        self.scale = 1.0

        # 鼠标 / 框选(disp 坐标)
        self.drawing = False
        self.start = (0, 0)
        self.roi: list[int] | None = None       # [x1,y1,x2,y2] disp
        self.mx = self.my = -1                  # 当前鼠标 disp 坐标
        self.last_saved: Path | None = None

        self.rois = self._load_rois()

    # ── helpers ──────────────────────────────────────────────
    def _scan_completed(self) -> set[str]:
        """扫描输出目录所有 .png 的文件名(stem)作为已完成集合。
        以实际文件为准 —— 关掉再开也能恢复进度, 不依赖单独的状态文件。"""
        done: set[str] = set()
        if not self.out_dir.exists():
            return done
        for png in self.out_dir.rglob("*.png"):
            done.add(png.stem)
        return done

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

    def _load_rois(self) -> dict:
        if self.roi_path.exists():
            try:
                return json.loads(self.roi_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
        return {}

    def _save_rois(self):
        self.roi_path.write_text(json.dumps(self.rois, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    def _set_screenshot(self):
        self.scr_orig = self.screens[self.scr_idx][1]
        h, w = self.scr_orig.shape[:2]
        self.scale = SCR_H / h
        self.disp_h = SCR_H
        self.disp_w = int(w * self.scale)
        self.scr_disp = cv2.resize(self.scr_orig, (self.disp_w, self.disp_h),
                                   interpolation=cv2.INTER_AREA)
        self.roi = None

    def _cur(self) -> dict:
        return self.checklist[self.idx]

    # ── 鼠标回调 ──────────────────────────────────────────────
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
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(self.disp_w, x2), min(self.disp_h, y2)
            self._commit(x1, y1, x2, y2)

    def _commit(self, x1, y1, x2, y2):
        """框选松开 → 反算原图坐标 → 无损裁剪保存。"""
        ox1, oy1 = int(x1 / self.scale), int(y1 / self.scale)
        ox2, oy2 = int(x2 / self.scale), int(y2 / self.scale)
        crop = self.scr_orig[oy1:oy2, ox1:ox2]
        if crop.size == 0:
            return
        item = self._cur()
        path = save_crop(crop, self.out_dir, item["cat"], item["file"])
        self.last_saved = path
        self.completed.add(item["file"])
        self.skipped.discard(item["file"])
        # 记录归一化 ROI + 来源截图, 便于后续标定手牌/出牌区
        h, w = self.scr_orig.shape[:2]
        self.rois[item["file"]] = {
            "src": self.screens[self.scr_idx][0],
            "box": [round(ox1 / w, 4), round(oy1 / h, 4),
                    round(ox2 / w, 4), round(oy2 / h, 4)],
            "size": [ox2 - ox1, oy2 - oy1],
        }
        self._save_rois()
        self._save_progress()
        print(f"  ✓ {item['cat']}/{item['file']}.png  ({ox2-ox1}x{oy2-oy1})  ← {self.screens[self.scr_idx][0]}  [{item['desc']}]")
        self.roi = None
        self._next_item()

    # ── 导航 ─────────────────────────────────────────────────
    def _next_item(self):
        self.idx = (self.idx + 1) % len(self.checklist)

    def _prev_item(self):
        self.idx = (self.idx - 1) % len(self.checklist)

    def _next_scr(self):
        self.scr_idx = (self.scr_idx + 1) % len(self.screens)
        self._set_screenshot()

    def _prev_scr(self):
        self.scr_idx = (self.scr_idx - 1) % len(self.screens)
        self._set_screenshot()

    def _undo(self):
        item = self._cur()
        path = self.out_dir / item["cat"] / f"{item['file']}.png"
        if path.exists():
            path.unlink()
            print(f"  ✗ 撤销 {item['file']}")
        self.completed.discard(item["file"])
        self._save_progress()

    def _skip(self):
        item = self._cur()
        self.skipped.add(item["file"])
        self._save_progress()
        print(f"  · 跳过 {item['file']}  [{item['desc']}]")
        self._next_item()

    # ── 渲染 ─────────────────────────────────────────────────
    def _draw_zoom(self, disp):
        """右上角放大镜: 取鼠标周围原图区域放大, 带十字与坐标。"""
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
        # 贴右上角
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
        # 顶部信息条
        cv2.rectangle(disp, (0, 0), (self.disp_w, 26), (40, 40, 40), -1)
        label = _ascii_label(self._cur(), self.idx, len(self.checklist))
        done = sum(1 for it in self.checklist if it["file"] in self.completed)
        cv2.putText(disp, f"{label}  done={done}/{len(self.checklist)}",
                    (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 255, 200), 1)
        # 截图名(底部)
        cv2.putText(disp, f"{self.screens[self.scr_idx][0]}  [{self.scr_idx+1}/{len(self.screens)}]",
                    (6, self.disp_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        # 框选矩形
        if self.roi:
            x1, y1, x2, y2 = self.roi
            cv2.rectangle(disp, (x1, y1), (x2, y2), (0, 0, 255), 2)
        # 放大镜
        self._draw_zoom(disp)
        cv2.imshow("crop - screenshot", disp)

    def _draw_reference(self):
        """左窗: 当前项的原版参考模板(若存在), 3x 放大 + 完成标记。"""
        item = self._cur()
        panel = np.full((320, 320, 3), 30, np.uint8)
        ref_path = self.ref_dir / f"{item['file']}.png"
        if ref_path.exists():
            arr = np.array(Image.open(str(ref_path)).convert("RGB"))
            ref = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            ref = cv2.resize(ref, (ref.shape[1] * 3, ref.shape[0] * 3),
                             interpolation=cv2.INTER_NEAREST)
            hh, ww = ref.shape[:2]
            panel[20:20 + hh, :ww] = ref[:min(hh, 300), :min(ww, 320)]
        status = "DONE" if item["file"] in self.completed else (
            "SKIP" if item["file"] in self.skipped else "TODO")
        color = (0, 200, 0) if status == "DONE" else (
            (0, 140, 255) if status == "TODO" else (120, 120, 120))
        cv2.putText(panel, status, (10, 305), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.putText(panel, f"ref {item['rw']}x{item['rh']}", (110, 305),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.imshow("crop - reference", panel)

    def _print_progress(self):
        todo = [it for it in self.checklist
                if it["file"] not in self.completed and it["file"] not in self.skipped]
        print(f"\n进度: 已完成 {len(self.completed)}/{len(self.checklist)},"
              f" 跳过 {len(self.skipped)}, 待标 {len(todo)}")
        if todo:
            line = ", ".join(it["file"] for it in todo[:20])
            print("待标: " + line + (" ..." if len(todo) > 20 else ""))

    def _print_status(self):
        item = self._cur()
        flag = "[done]" if item["file"] in self.completed else (
            "[skip]" if item["file"] in self.skipped else "[todo]")
        print(f"\r {flag} [{self.idx+1}/{len(self.checklist)}] {item['cat']}/{item['file']}"
              f"  {item['desc']}      a/d=切项 w/s=切截图 x=跳过 u=撤销 q=退出",
              flush=True)

    # ── 主循环 ────────────────────────────────────────────────
    def run(self):
        cv2.namedWindow("crop - screenshot", cv2.WINDOW_NORMAL)
        cv2.setMouseCallback("crop - screenshot", self.mouse)
        cv2.namedWindow("crop - reference", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("crop - reference", 320, 320)

        self._set_screenshot()
        self._print_progress()
        print('提示: 按键前先点一下 "crop - screenshot" 窗口让它获得焦点')
        self._print_status()
        while True:
            self._draw()
            self._draw_reference()
            k = cv2.waitKeyEx(30)            # waitKeyEx 才能拿到方向键完整 keycode
            if k == -1:
                continue
            if k in (ord("q"), 27):          # q / ESC
                break
            elif k in KEY_RIGHT:             # → / d
                self._next_item(); self._print_status()
            elif k in KEY_LEFT:              # ← / a
                self._prev_item(); self._print_status()
            elif k in KEY_DOWN:              # ↓ / s
                self._next_scr(); self._print_status()
            elif k in KEY_UP:                # ↑ / w
                self._prev_scr(); self._print_status()
            elif k == ord("x"):
                self._skip(); self._print_status()
            elif k == ord("u"):
                self._undo(); self._print_status()
        cv2.destroyAllWindows()
        self._summary()

    def _summary(self):
        todo = [it["file"] for it in self.checklist
                if it["file"] not in self.completed and it["file"] not in self.skipped]
        print(f"\n=== 完成 {len(self.completed)}/{len(self.checklist)},"
              f" 跳过 {len(self.skipped)}, 未做 {len(todo)} ===")
        if todo:
            print("未做: " + ", ".join(todo[:30]) + (" ..." if len(todo) > 30 else ""))
        print(f"输出: {self.out_dir}")
        print(f"ROI : {self.roi_path}")


def main():
    ap = argparse.ArgumentParser(description="斗地主模板截取工具(清单驱动+放大镜+无损)")
    ap.add_argument("--screens", default=str(DEFAULT_SCREENS), help="游戏截图目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="模板输出目录")
    ap.add_argument("--ref", default=str(DEFAULT_REF), help="原版参考模板目录(左窗示意)")
    ap.add_argument("--roi", default=None, help="ROI 记录 json(默认 out/rois.json)")
    ap.add_argument("--export-checklist", default=None, help="导出清单为 json 后退出")
    args = ap.parse_args()

    checklist = build_checklist()

    if args.export_checklist:
        Path(args.export_checklist).write_text(
            json.dumps(checklist, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已导出 {len(checklist)} 项清单 -> {args.export_checklist}")
        return

    screens = load_screenshots(Path(args.screens))
    if not screens:
        print(f"错误: 在 {args.screens} 找不到截图(png/jpg)。用 --screens 指定。")
        sys.exit(1)

    out_dir = Path(args.out)
    roi_path = Path(args.roi) if args.roi else out_dir / "rois.json"
    ref_dir = Path(args.ref)
    print(f"截图 {len(screens)} 张 | 清单 {len(checklist)} 项 | 输出 {out_dir}")
    print(f"参考 {ref_dir} ({'存在' if ref_dir.exists() else '不存在, 左窗不显示参考图'})")
    print("操作: 鼠标框选=保存并下一项 | ←→ 切项 | ↑↓ 切截图 | x 跳过 | u 撤销 | q 退出\n")

    Cropper(screens, checklist, out_dir, ref_dir, roi_path).run()


if __name__ == "__main__":
    main()
