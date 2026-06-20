"""天天象棋感知 — 模板匹配棋盘识别（无 VLM 依赖）。

替代受阻的 VLM 路径，复用 core/perception/cv/template_match 的 TemplateMatchTask，
产出与 XiangqiPerception (VLM) 完全相同的 parsed dict 结构：
    {screen_type, board:{left,top,right,bottom}, pieces[], buttons[]}
因此 decision.py / engine.py / visualizer.py / states.py 零改动。

设计要点:
  - 棋盘定位: 优先静态标定 board.json (resolution + board_rect_px)；
    当标定缺失/分辨率漂移/棋子偏离交叉点 >0.4 格时, 聚类兜底自动重建 board rect。
  - 棋子模板按 {r|b}_{字形} 拆分红黑: 馬/車/炮 红黑字形相同,
    TM_CCOEFF_NORMED 减均值会丢失颜色致互串, 命中后做颜色校验区分。
  - 同一网格 cell 多模板命中时保留最高分, 解决 r_車/b_車 重叠。
  - screen_type: 开始游戏→menu; 再来一局/game_over_marker→game_over;
    playing_marker 或 棋子数>=20→playing; 否则 unknown。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from gameauto.core.perception.base import PerceptionResult
from gameauto.core.perception.cv.template_match import TemplateMatchTask

logger = logging.getLogger("gameauto.xiangqi.perception_template")

# 模板名 → (引擎字形, 阵营); 模板名格式 {r|b}_{字形}
_PIECE_TEMPLATES: dict[str, tuple[str, str]] = {
    "r_帥": ("帥", "red"), "r_仕": ("仕", "red"), "r_相": ("相", "red"),
    "r_馬": ("馬", "red"), "r_車": ("車", "red"), "r_炮": ("炮", "red"), "r_兵": ("兵", "red"),
    "b_将": ("将", "black"), "b_士": ("士", "black"), "b_象": ("象", "black"),
    "b_馬": ("馬", "black"), "b_車": ("車", "black"), "b_炮": ("炮", "black"), "b_卒": ("卒", "black"),
}

# 按钮 / UI 标志模板名
_BUTTON_TEMPLATES = ["开始游戏", "再来一局", "返回大厅"]
_GAMEOVER_MARKER = "game_over_marker"
_PLAYING_MARKER = "playing_marker"

# 网格列/行数
_N_COLS = 9   # col 1..9
_N_ROWS = 10  # row 1..10 (row1=红方底, row10=黑方顶)


class XiangqiPerceptionLike(Protocol):
    """VLM / template 等感知后端的统一接口。"""

    async def recognize(self, image: bytes) -> PerceptionResult: ...


def _is_red(region_bgr: np.ndarray) -> bool:
    """判断棋子是否红方: 取中心 60% 区域最暗 25% 像素(=字形笔画墨色),
    红墨 R 明显高于 G(dRG>40); 黑墨 R≈G(dRG<10)。整体颜色被棋盘木色背景主导,
    故只在字形墨色上判色(实测红棋 dRG≈75, 黑棋 dRG≈5, 分界清晰)。
    """
    if region_bgr is None or region_bgr.size == 0:
        return False
    h, w = region_bgr.shape[:2]
    c = region_bgr[h // 5:4 * h // 5, w // 5:4 * w // 5]
    b = c[:, :, 0].astype(int)
    g = c[:, :, 1].astype(int)
    r = c[:, :, 2].astype(int)
    lum = (b + g + r) // 3
    thr = np.percentile(lum, 25)
    mask = lum <= thr
    if mask.sum() == 0:
        return False
    return float((r[mask] - g[mask]).mean()) > 40


def _color_matches(template_stem: str, region_bgr: np.ndarray) -> bool:
    """模板名前缀 r/b 与命中区域颜色是否一致; 仅校验 {r|b}_ 前缀的棋子模板。"""
    if len(template_stem) >= 3 and template_stem[0] in "rb" and template_stem[1] == "_":
        return _is_red(region_bgr) == (template_stem[0] == "r")
    return True


class XiangqiTemplatePerception:
    """基于模板匹配的天天象棋棋盘识别。"""

    def __init__(
        self,
        template_dir: str,
        board_json: str | None = None,
        piece_confidence: float = 0.90,
        button_confidence: float = 0.85,
        marker_confidence: float = 0.80,
    ) -> None:
        self._dir = Path(template_dir)
        self._piece_matcher = self._load_matcher(self._dir / "pieces")
        self._button_matcher = self._load_matcher(self._dir / "buttons")
        self._ui_matcher = self._load_matcher(self._dir / "ui")
        self._piece_thr = piece_confidence
        self._button_thr = button_confidence
        self._marker_thr = marker_confidence

        # 静态标定
        self._calib_res: tuple[int, int] | None = None
        self._board_rect_px: tuple[int, int, int, int] | None = None
        if board_json and Path(board_json).is_file():
            self._load_board_json(board_json)
        # 运行期锁定的棋盘 rect: 开局(棋子全)聚类最准, 锁定后整局复用(棋盘不动),
        # 中后期棋子被吃也不会漂移。优于每帧重聚类。
        self._locked_board_px: tuple[int, int, int, int] | None = None

        logger.info(
            "Template perception: pieces=%d buttons=%d ui=%d | board=%s",
            len(self._piece_matcher.template_names),
            len(self._button_matcher.template_names),
            len(self._ui_matcher.template_names),
            self._board_rect_px,
        )

    @classmethod
    def from_skill_dir(cls, skill_dir: str, **kw: Any) -> "XiangqiTemplatePerception":
        sd = Path(skill_dir)
        return cls(
            template_dir=str(sd / "assets" / "templates"),
            board_json=str(sd / "assets" / "board.json"),
            **kw,
        )

    # ── 公共接口 ──────────────────────────────────────────────────
    async def recognize(self, image: bytes) -> PerceptionResult:
        t0 = time.perf_counter()
        img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("template: failed to decode image")
            return PerceptionResult(raw_response="", parsed={"screen_type": "unknown"},
                                    tasks_output={}, latency_ms=0)
        h, w = img.shape[:2]

        # ── 棋盘 rect: 静态标定 (像素) ────────────────────────────
        board_px = self._resolve_board_px(w, h, None)
        resolution_mismatch = (self._calib_res is not None
                               and (w, h) != self._calib_res)
        if resolution_mismatch:
            logger.warning("Capture %dx%d != calibrated %s — will prefer cluster fallback",
                           w, h, self._calib_res)

        board_roi: tuple[float, float, float, float] | None = None
        if board_px is not None:
            bl, bt, br, bb = board_px
            # roi 向外扩一个棋子半径(~80px): 锁定的 board rect = 棋子中心 min/max,
            # 底排/边列棋子中心在 rect 边缘, 若不扩 margin 其本体一半会被 roi 裁掉
            # 导致 matchTemplate 匹配不到(实测锁定 roi 只命中 10、全图命中 32)。
            margin = 80
            board_roi = (
                max(0, bl - margin) / w,
                max(0, bt - margin) / h,
                min(w, br + margin) / w,
                min(h, bb + margin) / h,
            )

        # ── 并发匹配 ──────────────────────────────────────────────
        piece_task = self._piece_matcher.run(
            image, roi=board_roi,
            config={"threshold": self._piece_thr, "scales": [1.0], "nms_iou": 0.3})
        button_task = self._button_matcher.run(
            image, roi=None,
            config={"threshold": self._button_thr, "scales": [1.0], "nms_iou": 0.3,
                    "filter_names": _BUTTON_TEMPLATES})
        marker_task = self._ui_matcher.run(
            image, roi=None,
            config={"threshold": self._marker_thr, "scales": [0.9, 1.0, 1.1], "nms_iou": 0.3})

        pieces_res, buttons_res, markers_res = await asyncio.gather(
            piece_task, button_task, marker_task)

        # ── 棋子: 颜色校验 + 中心 ─────────────────────────────────
        centers: list[tuple[float, float]] = []
        raw_hits: list[dict] = []
        for m in pieces_res.get("matches", []):
            region = img[m["y"]:m["y"] + m["h"], m["x"]:m["x"] + m["w"]]
            if not _color_matches(m["template"], region):
                continue
            glyph, side = _PIECE_TEMPLATES.get(m["template"], ("?", "?"))
            cx = m["x"] + m["w"] / 2.0
            cy = m["y"] + m["h"] / 2.0
            centers.append((cx, cy))
            raw_hits.append({"piece": glyph, "side": side, "cx": cx, "cy": cy,
                             "score": m["score"]})

        # ── 棋盘 rect: 若静态标定缺失/漂移, 聚类兜底 ───────────────
        board_px = self._resolve_board_px(w, h, centers)
        if board_px is None:
            logger.warning("No board rect (no calibration & cluster failed) — pieces unmapped")
            pieces: list[dict] = []
        else:
            pieces = self._assemble_pieces(raw_hits, board_px, w, h)

        # ── 按钮 ──────────────────────────────────────────────────
        buttons = [{
            "text": m["template"],
            "x": round((m["x"] + m["w"] / 2.0) / w * 1000, 1),
            "y": round((m["y"] + m["h"] / 2.0) / h * 1000, 1),
        } for m in buttons_res.get("matches", [])]

        # ── screen_type ───────────────────────────────────────────
        screen_type = self._detect_screen_type(
            markers_res.get("matches", []), buttons, pieces)

        # ── board rect (归一化 [0-1000]) ──────────────────────────
        board = self._board_dict(board_px, w, h)

        parsed = {
            "screen_type": screen_type,
            "board": board,
            "pieces": pieces,
            "buttons": buttons,
        }
        latency = (time.perf_counter() - t0) * 1000
        logger.info("Template: %.0fms screen=%s pieces=%d buttons=%d",
                    latency, screen_type, len(pieces), len(buttons))
        return PerceptionResult(raw_response="", parsed=parsed,
                                tasks_output={}, latency_ms=round(latency))

    # ── 内部辅助 ──────────────────────────────────────────────────
    @staticmethod
    def _load_matcher(d: Path) -> TemplateMatchTask:
        """加载子目录模板; 目录缺失则返回空 matcher。"""
        m = TemplateMatchTask()
        if d.is_dir():
            try:
                m.load_templates(str(d))
            except Exception:
                logger.exception("Failed to load templates from %s", d)
        return m

    def _load_board_json(self, board_json: str) -> None:
        try:
            bj = json.loads(Path(board_json).read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read %s", board_json)
            return
        res = bj.get("resolution") or [0, 0]
        rect = bj.get("board_rect_px") or [0, 0, 0, 0]
        if res and res[0] > 0 and res[1] > 0:
            self._calib_res = (int(res[0]), int(res[1]))
        if rect and rect[2] > rect[0] and rect[3] > rect[1]:
            self._board_rect_px = (int(rect[0]), int(rect[1]),
                                   int(rect[2]), int(rect[3]))

    def _resolve_board_px(
        self, w: int, h: int,
        centers: list[tuple[float, float]] | None,
    ) -> tuple[int, int, int, int] | None:
        """决定棋盘像素 rect。

        优先级:
          1. 静态标定 board.json (分辨率匹配且不漂移) — 最可靠, 需手动标定一次。
          2. 运行期锁定的开局 rect — 开局棋子全时聚类最准, 锁定后整局复用。
          3. 本帧聚类推断 — 兜底(中后期会漂移, 仅在未锁定时用)。
        开局(检测到 >=28 子)聚类成功时自动锁定, 后续帧不再漂移。
        """
        static = self._board_rect_px
        # 1. 静态标定(分辨率匹配 + 不漂移)
        if static is not None and (w, h) == self._calib_res:
            if not (centers and self._centers_drift(centers, static)):
                return static

        # 2. 已锁定的开局 rect
        if self._locked_board_px is not None:
            return self._locked_board_px

        # 3. 本帧聚类
        if centers and len(centers) >= 4:
            clustered = self._cluster_board(centers)
            if clustered is not None:
                # 开局(棋子近全)时锁定, 整局复用 —— 棋盘不随棋子移动而变。
                if len(centers) >= 28 and self._locked_board_px is None:
                    self._locked_board_px = clustered
                    logger.info("Board rect locked from opening (%d pieces): %s",
                                len(centers), clustered)
                else:
                    logger.info("Board rect from cluster (unlocked, may drift): %s", clustered)
                return clustered
        return static

    @staticmethod
    def _centers_drift(centers: list[tuple[float, float]],
                       board_px: tuple[int, int, int, int]) -> bool:
        """棋子中心是否平均偏离最近交叉点 >0.4 格 → 标定漂移。"""
        bl, bt, br, bb = board_px
        cell_w = (br - bl) / 8.0
        cell_h = (bb - bt) / 9.0
        if cell_w <= 0 or cell_h <= 0:
            return True
        total = 0.0
        for cx, cy in centers:
            dx = abs((cx - bl) / cell_w - round((cx - bl) / cell_w))
            dy = abs((bb - cy) / cell_h - round((bb - cy) / cell_h))
            total += dx + dy
        avg = total / len(centers)
        return avg > 0.4

    @staticmethod
    def _cluster_board(
        centers: list[tuple[float, float]],
    ) -> tuple[int, int, int, int] | None:
        """由棋子中心推断棋盘四角交叉点。

        象棋开局四角(col1/col9, row1/row10)必有車, 且棋子永远落在交叉点上,
        故最外圈棋子的 min/max 即棋盘网格四角(不要求所有行列都有棋子——
        开局仅 6 行有棋, 逐列/行聚类凑不齐 9/10, 故用 min/max 包围盒)。
        用 5/95 百分位而非绝对 min/max, 抗个别离群误检。
        """
        if len(centers) < 4:
            return None
        xs = np.array([c[0] for c in centers])
        ys = np.array([c[1] for c in centers])
        left = int(round(np.percentile(xs, 5)))
        right = int(round(np.percentile(xs, 95)))
        top = int(round(np.percentile(ys, 5)))      # row10(黑方顶) y 最小
        bottom = int(round(np.percentile(ys, 95)))  # row1(红方底) y 最大
        if right - left < 100 or bottom - top < 100:
            return None
        return (left, top, right, bottom)

    def _assemble_pieces(
        self, raw_hits: list[dict],
        board_px: tuple[int, int, int, int], w: int, h: int,
    ) -> list[dict]:
        """棋子命中 → 按网格 cell 去重(同格保留最高分) → pieces dict。"""
        bl, bt, br, bb = board_px
        cell_w = (br - bl) / 8.0
        cell_h = (bb - bt) / 9.0
        occupied: dict[tuple[int, int], dict] = {}
        for hit in raw_hits:
            col, row = self._center_to_grid(hit["cx"], hit["cy"], bl, bt, br, bb,
                                            cell_w, cell_h)
            key = (col, row)
            cur = occupied.get(key)
            if cur is None or hit["score"] > cur["_score"]:
                occupied[key] = {
                    "piece": hit["piece"], "side": hit["side"],
                    "board_pos": {"col": col, "row": row},
                    "pixel_pos": {"x": round(hit["cx"] / w * 1000, 1),
                                  "y": round(hit["cy"] / h * 1000, 1)},
                    "_score": hit["score"],
                }
        return [{k: v for k, v in d.items() if k != "_score"} for d in occupied.values()]

    @staticmethod
    def _center_to_grid(cx: float, cy: float,
                        bl: int, bt: int, br: int, bb: int,
                        cell_w: float, cell_h: float) -> tuple[int, int]:
        col = max(1, min(_N_COLS, int(round((cx - bl) / cell_w)) + 1))
        # cy=bb → row1(红方底); cy=bt → row10(黑方顶)
        row = max(1, min(_N_ROWS, int(round((bb - cy) / cell_h)) + 1))
        return col, row

    @staticmethod
    def _detect_screen_type(marker_matches: list[dict],
                            buttons: list[dict], pieces: list[dict]) -> str:
        names = {m["template"] for m in marker_matches}
        btn_texts = {b["text"] for b in buttons}
        if "开始游戏" in btn_texts:
            return "menu"
        if "再来一局" in btn_texts or _GAMEOVER_MARKER in names:
            return "game_over"
        # 对局判定: 双方将帅都在 = 真实进行中的棋盘(残局棋子少也成立)。
        # 不再用 n_pieces>=20 —— 残局常 <20 子会被误判 unknown 而不决策。
        glyphs = {p.get("piece") for p in pieces}
        has_red_king = glyphs & {"帥", "帅"}
        has_black_king = glyphs & {"将", "將"}
        if _PLAYING_MARKER in names:
            return "playing"
        if has_red_king and has_black_king:
            return "playing"
        if len(pieces) >= 20:  # 兜底: 未识别到将/帅但棋子很多
            return "playing"
        return "unknown"

    @staticmethod
    def _board_dict(board_px: tuple[int, int, int, int] | None,
                    w: int, h: int) -> dict:
        if board_px is None:
            return {}
        bl, bt, br, bb = board_px
        return {
            "left": round(bl / w * 1000, 1),
            "top": round(bt / h * 1000, 1),
            "right": round(br / w * 1000, 1),
            "bottom": round(bb / h * 1000, 1),
        }
