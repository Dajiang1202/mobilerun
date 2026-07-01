"""ReplayDriver — 视频回放调试 tick 循环。

不 import 任何游戏代码。perceive / decide 是约定签名的可调用对象, 由入口装配:

    perceive(frame_bgr: np.ndarray) -> dict        帧 → 结构化状态
    decide(state: dict) -> list[Action]            状态 → 动作列表

每个 tick:
    1. screenshot_bgr() 取视频"现在"这一帧 (慢消费 → 中间帧已丢)
    2. perceive(frame) → state
    3. decide(state) → actions
    4. print: 时间戳 / 帧号 / 跳帧数 / state / actions / 分项耗时
    5. 落盘: tick_NNNN/{frame.png, perception.json, actions.json} (识别先于决策)

停止: 视频结束 (非 loop) 且无新帧, 或 Ctrl+C。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from gameauto.core.orchestration.base import Action
from gameauto.core.recorder.session import SessionManager

log = logging.getLogger("gameauto.replay")

PerceiveFn = Callable[[np.ndarray], dict]
DecideFn = Callable[[dict], list[Action]]


# ── 默认 stub (零依赖, 让视频闭环第一天就能跑通) ──────────────────────────

def stub_perceive(frame_bgr: np.ndarray) -> dict:
    """返回帧基本信息, 验证"帧在动、跳帧正常"。"""
    if frame_bgr is None:
        return {"frame_h": 0, "frame_w": 0, "mean_bgr": [0, 0, 0]}
    h, w = frame_bgr.shape[:2]
    mean = frame_bgr.reshape(-1, 3).mean(axis=0).tolist()
    return {"frame_h": int(h), "frame_w": int(w), "mean_bgr": [round(v, 1) for v in mean]}


def stub_decide(state: dict) -> list[Action]:
    """默认不决策, 只让 driver 打 state 摘要。"""
    return []


# ── ReplayDriver ───────────────────────────────────────────────────────

class ReplayDriver:
    """视频回放 tick 循环驱动器。

    Args:
        capture: VideoCapture (需暴露 screenshot_bgr / current_timestamp /
                 current_frame_index / frames_advanced_since_last_serve /
                 is_finished)。
        perceive: 帧 → state。
        decide: state → actions。
        tick_interval: driver 两次 tick 间最小间隔 (s)。0=尽可能快, 由感知限速。
        record: 是否落盘。
        session_dir: 落盘根目录 (None 则用 logs/tft_replay_<ts>)。
        show: 是否显示 cv2 预览窗。
        verbose: 打原始中间文本; quiet 只打 actions。
        quiet: 只打 actions。
    """

    def __init__(
        self,
        capture,
        perceive: PerceiveFn,
        decide: DecideFn,
        tick_interval: float = 0.0,
        record: bool = True,
        session_dir: Path | None = None,
        show: bool = False,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        self._cap = capture
        self._perceive = perceive
        self._decide = decide
        self._tick_interval = tick_interval
        self._show = show
        self._verbose = verbose
        self._quiet = quiet

        self._record = record
        if record:
            base = session_dir if session_dir is not None else Path("logs") / "tft_replay"
            self._session = SessionManager(base_dir=base)
            self._session_dir = self._session.setup({"game": "tft_replay"})
        else:
            self._session = None
            self._session_dir = None

        self._tick = 0

    async def run(self) -> None:
        """主 tick 循环。"""
        if self._show:
            cv2.namedWindow("TFT Replay", cv2.WINDOW_NORMAL)

        log.info("ReplayDriver 启动 | record=%s show=%s tick_interval=%.3f",
                 self._record, self._show, self._tick_interval)

        try:
            while True:
                stopped = await self._tick_once()
                if stopped:
                    break
                if self._tick_interval > 0:
                    await _async_sleep(self._tick_interval)
        except KeyboardInterrupt:
            log.info("ReplayDriver: 用户中断")
        finally:
            if self._show:
                cv2.destroyAllWindows()
            if self._session:
                self._session.write_summary({"ticks": self._tick})
            log.info("ReplayDriver 结束 | 共 %d ticks | 落盘: %s",
                     self._tick, self._session_dir)

    async def _tick_once(self) -> bool:
        """执行一次 tick。返回 True 表示应停止 (视频结束)。"""
        frame = self._cap.screenshot_bgr()
        ts = self._cap.current_timestamp()
        fidx = self._cap.current_frame_index()
        advanced = self._cap.frames_advanced_since_last_serve()
        self._tick += 1

        # 视频结束且尚无任何帧 → 停
        if frame is None:
            if self._cap.is_finished():
                return True
            await _async_sleep(0.01)
            return False

        # ── perceive ──
        t0 = time.perf_counter()
        try:
            state = self._perceive(frame)
        except Exception:
            log.exception("perceive 抛异常 (tick %d)", self._tick)
            state = {"_error": "perceive exception"}
        dt_perceive = (time.perf_counter() - t0) * 1000

        # 识别先于决策落盘 (决策崩也保留 perception)
        round_dir = self._save_frame(frame, state) if self._record else None

        # ── decide ──
        t0 = time.perf_counter()
        try:
            actions = self._decide(state)
        except Exception:
            log.exception("decide 抛异常 (tick %d)", self._tick)
            actions = []
        dt_decide = (time.perf_counter() - t0) * 1000

        if self._record and round_dir is not None:
            self._save_actions(round_dir, actions)

        self._print(ts, fidx, advanced, state, actions, dt_perceive, dt_decide)

        if self._show:
            self._preview(frame, ts, advanced, state)

        # 视频已结束且这一帧是最后一帧 → 再 tick 一次拿不到新帧, 停
        if self._cap.is_finished():
            return True
        return False

    # ── 输出 ────────────────────────────────────────────────────────────

    def _print(self, ts, fidx, advanced, state, actions, dt_p, dt_d) -> None:
        head = (f"[t={ts:6.2f}s] frame#{fidx:<5d} advanced={advanced:<3d} "
                f"perceive={dt_p:5.0f}ms decide={dt_d:3.0f}ms")
        if self._quiet:
            print(head + f"  actions={_actions_brief(actions)}")
            return
        print(head)
        if self._verbose:
            print(f"  state(full): {json.dumps(state, ensure_ascii=False)}")
        else:
            # overlays/details 是给预览/逐行打印用的, 不进单行 brief
            brief = {k: v for k, v in state.items() if k not in ("overlays", "details")}
            print(f"  state: {_state_brief(brief)}")
        # perceive 可返回 details: list[str], 每行一条 (如每个 ROI 的文本+耗时)
        for line in state.get("details", []):
            print(f"    {line}")
        print(f"  actions: {_actions_brief(actions) or '(无)'}")

    def _preview(self, frame: np.ndarray, ts: float, advanced: int, state: dict) -> None:
        disp = frame.copy()
        # perceive 可返回 overlays: [{"box":(l,t,r,b), "label":str}, ...]
        for ov in state.get("overlays", []):
            l, t, r, b = ov.get("box", (0, 0, 0, 0))
            cv2.rectangle(disp, (l, t), (r, b), (0, 255, 0), 2)
            label = ov.get("label", "")
            if label:
                cv2.putText(disp, label, (l, max(0, t - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.setWindowTitle(
            "TFT Replay",
            f"TFT Replay — t={ts:.2f}s dropped≈{advanced} (按 q 退出)",
        )
        cv2.imshow("TFT Replay", disp)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            raise KeyboardInterrupt

    # ── 落盘 ────────────────────────────────────────────────────────────

    def _save_frame(self, frame: np.ndarray, state: dict) -> Path | None:
        d = self._session.frame_dir(self._tick)
        # 中文路径安全: imencode + tofile
        ok, buf = cv2.imencode(".png", frame)
        if ok:
            (d / "frame.png").write_bytes(buf.tobytes())
        (d / "perception.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return d

    def _save_actions(self, round_dir: Path, actions: list[Action]) -> None:
        data = [a.model_dump() for a in actions]
        (round_dir / "actions.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
        )


# ── helpers ────────────────────────────────────────────────────────────

def _actions_brief(actions: list[Action]) -> str:
    if not actions:
        return ""
    parts = []
    for a in actions:
        desc = a.description or a.type
        parts.append(desc)
    return ", ".join(parts)


def _state_brief(state: dict) -> str:
    if not state:
        return "{}"
    # 取前几个键的紧凑表示, 避免长输出刷屏
    items = list(state.items())[:6]
    return "{" + ", ".join(f"{k}={v!r}" for k, v in items) + "}"


async def _async_sleep(s: float) -> None:
    import asyncio
    await asyncio.sleep(s)
