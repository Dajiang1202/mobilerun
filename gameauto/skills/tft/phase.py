"""TFT 阶段判断 (备战/战斗) —— 倒计时计数法, 不用模板。

判据(和用户对齐):
  - 同一 stage X-Y 内, 第1次倒计时 = 备战, 第2次 = 战斗
  - timer 值「跳回大数」(小→大, 如 3→30) = 新一轮倒计时 → 计数+1
  - stage 变了 → 计数归零
  - 结算(第X名) 由调用方检测后覆盖, 不在本类

PhaseTracker 只做阶段逻辑, 输入 stage 字符串 + timer 整数(可None), 输出阶段。
OCR stage/timer 由调用方(get_phase_ocr)做, 解耦便于测试。
"""

from __future__ import annotations


class PhaseTracker:
    """记 stage + 倒计时计数, 派生 备战/战斗/未知。"""

    def __init__(self, jump_threshold: int = 15, win: int = 3) -> None:
        self.stage: str | None = None
        self.ordinal = 0           # 当前 stage 第几轮倒计时 (1=备战, 2=战斗)
        self.last_timer: int | None = None      # 平滑后的 timer
        self.acted_this_planning = False   # 本轮备战是否已动作 (防每帧重复执行)
        self.phase = "未知"
        self._jump_thr = jump_threshold
        self._win = win
        self._hist: list[int] = []           # 最近 N 帧原始 timer (中值滤波用)

    def update(self, stage: str | None, timer: int | None) -> str:
        """喂一帧的 stage(如'2-1') 和 timer(秒数int或None), 返回当前阶段。

        timer 走中值滤波 (最近 win 帧), 抑制单帧 OCR 抖动
        (如 40 被读成 3 又读成 37, 中值=37, 不会被 3→37 误判成跳回)。
        """
        # stage 变了 → 归零
        if stage and stage != self.stage:
            self.stage = stage
            self.ordinal = 0
            self.acted_this_planning = False
            self.last_timer = None
            self._hist = []

        # timer 入窗 (剔除明显不合理值), 中值滤波后做跳变检测
        if timer is not None and 0 <= timer <= 70:
            self._hist.append(timer)
            if len(self._hist) > self._win:
                self._hist.pop(0)
            smooth = sorted(self._hist)[len(self._hist) // 2]   # 中位数

            if self.last_timer is None:
                if self.ordinal == 0:
                    self.ordinal = 1          # 首次见到 timer, 当备战
            elif smooth > self.last_timer + self._jump_thr and self.ordinal < 2:
                self.ordinal += 1
                if self.ordinal == 1:
                    self.acted_this_planning = False
            self.last_timer = smooth

        # 派生阶段
        if self.ordinal == 1:
            self.phase = "备战"
        elif self.ordinal >= 2:
            self.phase = "战斗"
        else:
            self.phase = "未知"
        return self.phase

    def mark_acted(self) -> None:
        """标记本轮备战已动作过, 防止同一备战阶段重复执行。"""
        self.acted_this_planning = True
