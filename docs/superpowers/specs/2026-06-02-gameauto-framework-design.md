# GameAuto Framework — 移动端游戏自动化框架设计

> 日期: 2026-06-02 | 语言: Python 3.11+ | 参考: BetterGI 架构 + 金铲铲Bot + mobilerun driver

---

## 1. 概述

### 1.1 目标

构建一个可扩展的纯视觉移动端游戏自动化框架，核心闭环：**画面捕获 → 视觉感知 → 决策 → 输入模拟 → 数据录制**。

### 1.2 核心原则

- **纯视觉方案**：游戏不可暂停/复现 → 必须录制每帧数据，支持离线回灌调试
- **并行感知管线**：多CV任务并行执行，单帧总延迟 = max(子任务延迟)，非 sum
- **组件化**：每个模块可独立开发、独立测试、独立调试

### 1.3 首批游戏

- **消消乐 (Match-3)**: M1 完整实现，VLM 感知 + Python 求解器
- 后续: 象棋、金铲铲、三角洲、QQ飞车、王者荣耀、斗地主、炉石传说

### 1.4 技术约束

- VLM (大模型视觉) 优先；OCR/YOLO/模板匹配/颜色检测 接口预留
- 鸿蒙 HDC 首批输入源；scrcpy/camera 接口预留
- 归一化坐标 [0-1000] 或 [0.0-1.0]

---

## 2. 架构分层

```
┌─────────────────────────────────────────────────────────────────┐
│                    skills/ (游戏插件层)                           │
│  match3/   tft/   chess/   poker/   fps/   racing/   moba/      │
│  每个游戏一个子目录, 实现 ISkill 接口                             │
├─────────────────────────────────────────────────────────────────┤
│                     core/ (核心引擎层)                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐│
│  │ capture  │ │perception│ │  input   │ │orchestra │ │recorder││
│  │ 画面捕获  │ │ 视觉感知  │ │ 输入模拟  │ │ 任务调度  │ │数据录制││
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘│
├─────────────────────────────────────────────────────────────────┤
│                     tools/ (调试工具链)                           │
│   test_capture   test_perception   debug_viewer   roi_calibrator│
├─────────────────────────────────────────────────────────────────┤
│                    utils/ (通用工具)                              │
│   coordinate   images   logging   config                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 目录结构

```
gameauto/
├── core/                             # 零游戏依赖
│   ├── capture/
│   │   ├── __init__.py
│   │   ├── base.py                   # BaseCapture 抽象类
│   │   └── hdc.py                    # HdcCapture 鸿蒙实现
│   │
│   ├── perception/
│   │   ├── __init__.py
│   │   ├── base.py                   # BasePerception + PerceptionResult + PerceptionTask
│   │   ├── pipeline.py               # PerceptionPipeline — 并行调度多个感知任务
│   │   ├── vlm_client.py             # VlmClient — 通用 OpenAI-compatible 调用器
│   │   └── cv/                       # CV 模块（接口+M1预留）
│   │       ├── __init__.py
│   │       ├── base.py               # BaseCVTask 抽象
│   │       ├── ocr.py                # PaddleOCR 封装（M1骨架）
│   │       ├── template_match.py     # OpenCV matchTemplate（M1骨架）
│   │       └── yolo.py               # YOLO ONNX 推理（M1骨架）
│   │
│   ├── input/
│   │   ├── __init__.py
│   │   ├── base.py                   # BaseInput 抽象类
│   │   └── hdc.py                    # HdcInput 鸿蒙实现 (swipe/tap)
│   │
│   ├── orchestration/
│   │   ├── __init__.py
│   │   ├── base.py                   # GameState 枚举, Action 模型
│   │   ├── state_machine.py          # StateMachine 通用引擎
│   │   ├── context.py                # GameContext 运行时上下文
│   │   └── loop.py                   # GameLoop 主循环
│   │
│   └── recorder/                     # 数据录制与回灌
│       ├── __init__.py
│       ├── session.py                # SessionManager — 会话目录/文件管理
│       ├── data_recorder.py          # DataRecorder — 逐帧录制
│       └── replay_simulator.py       # ReplaySimulator — 回灌模拟
│
├── skills/                           # 游戏插件层
│   └── match3/
│       ├── __init__.py
│       ├── skill.py                  # Match3Skill (ISkill)
│       ├── states.py                 # 状态注册 (detector+handler)
│       ├── perception.py             # Match3Perception (prompt+VlmClient+JSON解析)
│       ├── solver.py                 # 三消贪婪求解器 + solve_board_multi
│       ├── visualizer.py             # 棋盘/swipe 可视化
│       ├── config.py                 # 消消乐配置 (ROI/阈值/棋子类型等)
│       └── prompts/
│           └── generic.jinja2        # VLM 感知提示词
│
├── tools/                            # 调试工具链
│   ├── test_capture.py               # HDC连接+截屏测试
│   ├── test_perception.py            # 视觉管线独立测试 (单图/回灌)
│   ├── test_decision.py              # 决策引擎回灌测试
│   ├── debug_viewer.py               # 可视化调试 (截图叠加识别结果)
│   └── roi_calibrator.py             # ROI 校准工具
│
├── utils/
│   ├── __init__.py
│   ├── coordinate.py                 # to_absolute / to_normalized
│   ├── images.py                     # resize + grid overlay
│   └── logging.py                    # 多层日志 (DEBUG/INFO/GAME/ERROR)
│
├── config/
│   ├── __init__.py
│   ├── loader.py                     # YAML + ENV 配置加载
│   └── default.yaml                  # 默认配置
│
├── run_match3.py                     # 消消乐入口脚本
└── logs/                             # gitignore — 运行时产物
```

---

## 4. 核心接口

### 4.1 Capture

```python
class BaseCapture(ABC):
    @abstractmethod
    async def screenshot(self) -> bytes: ...
    @abstractmethod
    async def connect(self) -> None: ...
    @abstractmethod
    async def disconnect(self) -> None: ...
    @property
    @abstractmethod
    def native_resolution(self) -> tuple[int, int]: ...
```

### 4.2 Input

```python
class BaseInput(ABC):
    @abstractmethod
    async def swipe(self, x1, y1, x2, y2, duration_ms=1000): ...
    @abstractmethod
    async def tap(self, x, y, duration_ms=100): ...
    @abstractmethod
    async def connect(self) -> None: ...
    @property
    @abstractmethod
    def input_resolution(self) -> tuple[int, int]: ...
```

### 4.3 Perception — 并行感知管线

core 提供**感知管线引擎**和**VlmClient**；游戏特定的 prompt/ROI/解析逻辑在 skill 中。

```python
# core/perception/base.py
class PerceptionTask(BaseModel):
    """单个感知子任务定义"""
    name: str                                    # e.g. "vlm_board", "ocr_gold", "template_phase"
    task_type: Literal["vlm", "ocr", "template_match", "yolo", "color_detect", "custom"]
    prompt: str | None = None                    # VLM prompt
    roi: tuple[float, float, float, float] | None # (left, top, right, bottom) 归一化 [0-1]
    config: dict = {}                            # 任务特定参数 (阈值/模型名等)

class PerceptionResult(BaseModel):
    """单次感知的完整输出"""
    raw_response: str | None = None              # 原始文本 (VLM)
    parsed: dict = {}                            # 结构化数据
    tasks_output: dict[str, Any] = {}            # 各子任务的原始输出 {task_name: output}
    latency_ms: float = 0

# core/perception/pipeline.py
class PerceptionPipeline:
    """并行调度多个感知子任务，总延迟 = max(各任务延迟)"""
    def __init__(self, vlm_client: VlmClient | None, cv_tasks: list[BaseCVTask] | None): ...
    def add_task(self, task: PerceptionTask): ...
    async def run(self, image: bytes) -> PerceptionResult: ...
```

```python
# core/perception/vlm_client.py
class VlmClient:
    """通用 OpenAI-compatible VLM 调用器 — 零游戏依赖"""
    def __init__(self, model: str, base_url: str, api_key: str): ...
    async def chat(self, system_prompt: str, user_prompt: str, image: bytes) -> str: ...

# core/perception/cv/base.py
class BaseCVTask(ABC):
    """CV 任务抽象 — OCR / 模板匹配 / YOLO 各自的基类"""
    @abstractmethod
    async def run(self, image: bytes, roi: tuple | None, config: dict) -> Any: ...
```

```python
# skills/match3/perception.py  (游戏特定)
class Match3Perception:
    """消消乐感知 — 组装 PerceptionPipeline，运行 VLM 识别"""
    def __init__(self, vlm: VlmClient, prompt_template: str): ...
    def build_pipeline(self) -> PerceptionPipeline: ...
    async def recognize(self, image: bytes) -> PerceptionResult: ...
```

**M1 的并行管线只跑1个VLM任务**（消消乐不需要CV），但 `PerceptionPipeline` 引擎本身就支持多任务并行调度——为金铲铲的5任务并行做好了准备。

### 4.4 Skill 插件

```python
class Action(BaseModel):
    type: Literal["swipe", "tap", "wait", "drag"]
    x1, y1, x2, y2: float | None          # 归一化 [0-1000]
    duration_ms: int = 1000

class ISkill(ABC):
    @abstractmethod
    async def perceive(self, image: bytes) -> PerceptionResult: ...
    @abstractmethod
    async def decide(self, result: PerceptionResult, state: GameState) -> list[Action]: ...
    @abstractmethod
    def register_states(self, sm: StateMachine) -> None: ...
```

### 4.5 状态机

```python
class GameState(StrEnum):
    UNKNOWN = "unknown"
    DESKTOP = "desktop"
    GAME_MENU = "game_menu"
    IN_GAME = "in_game"
    SETTLEMENT = "settlement"
    PAUSED = "paused"

class StateMachine:
    def register(self, state: GameState, detector: Callable, handler: Callable): ...
    async def step(self, image: bytes, context: GameContext) -> list[Action]: ...
```

### 4.6 数据录制与回灌

```python
# core/recorder/data_recorder.py
class DataRecorder:
    """逐帧录制: 截图 + 感知结果 + 决策 + 操作 — 零侵入"""
    def __init__(self, session_dir: Path): ...
    def record_frame(self, frame_id: int, screenshot: bytes,
                     perception: PerceptionResult, decisions: list, actions: list[Action]): ...
    def record_event(self, event_type: str, data: dict): ...
    def record_summary(self, summary: dict): ...

# core/recorder/replay_simulator.py
class ReplaySimulator:
    """用录制数据驱动任意模块，离线复现和调试"""
    def __init__(self, replay_dir: Path): ...
    def replay_perception(self, perception_pipeline, frame_range=None) -> list[dict]: ...
    def replay_decisions(self, skill, frame_range=None) -> list[dict]: ...
    def replay_full_pipeline(self, skill) -> Generator: ...
```

#### 录制数据目录结构

```
logs/<session_id>/
├── metadata.json              # 对局元信息 (时间/游戏/设备/分辨率)
├── summary.json               # 对局总结 (轮数/成功数/延迟/错误)
├── events/                    # 离散事件
│   ├── 000042_state_change.json
│   └── 000156_error.json
└── frames/                    # 逐帧数据
    ├── 000000/
    │   ├── screenshot.png      # 原始截图
    │   ├── perception.json     # 感知管线输出
    │   ├── decisions.json      # 决策引擎输出
    │   └── actions.json        # 操作序列
    ├── 000001/
    │   └── ...
    └── ...
```

### 4.7 调试工具

```python
# tools/test_capture.py — HDC 连接+截屏独立测试
# python tools/test_capture.py --device hdc --serial xxx --test screenshot

# tools/test_perception.py — 视觉管线独立测试
# python tools/test_perception.py --image test.png
# python tools/test_perception.py --replay logs/session_001/

# tools/test_decision.py — 决策引擎回灌测试
# python tools/test_decision.py --replay logs/session_001/

# tools/debug_viewer.py — 截图上叠加识别结果和操作箭头
# python tools/debug_viewer.py --replay logs/session_001/

# tools/roi_calibrator.py — 可视化调整归一化ROI坐标
# python tools/roi_calibrator.py --device hdc
```

---

## 5. 消消乐 Skill 实现

### 5.1 闭环流程（含录制）

```
LOOP:
  HdcCapture.screenshot()
      → DataRecorder 保存原始截图
      → PerceptionPipeline.run(image)    # M1: 仅1个VLM任务
          → VlmClient.chat(prompt, image)
          → 解析棋盘 JSON
      → DataRecorder 保存感知结果
      → StateMachine.step(image, context)
          → Match3Skill.decide(board, IN_GAME)
              → solve_board_multi(board, max_steps=2)
              → [Action(swipe, A→B), Action(swipe, C→D)]
      → DataRecorder 保存决策和操作
      → for each action:
          HdcInput.execute(action)
      → visualizer.draw() → 保存可视化截图
```

### 5.2 状态注册 (M1: 仅 IN_GAME)

```python
def register_states(self, sm: StateMachine):
    sm.register(
        GameState.IN_GAME,
        detector=lambda img: True,       # M1: 默认游戏中
        handler=self._handle_in_game,
    )
```

### 5.3 配置

```yaml
# config/default.yaml
game: match3
max_steps_per_round: 2
rounds: 10
vlm:
  model: "gpt-4o"
  base_url: "..."
  api_key: "${API_KEY}"
device:
  type: hdc
  serial: "..."
logging:
  session_dir: "logs"
  save_screenshots: true
  save_frames: true          # 是否逐帧录制
```

---

## 6. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 坐标系统 | 归一化 [0-1000] 和 [0.0-1.0] 双支持 | VLM用[0-1000]，CV用[0-1] |
| 感知并行 | PerceptionPipeline 统一调度 | 单帧总延迟 = max(子任务延迟) |
| 数据录制 | 逐帧全量录制 | 纯视觉方案无法暂停 → 必须可回灌 |
| 调试工具 | 每个模块独立CLI | 不连接设备也能调试识别/决策 |
| VLM API | OpenAI-compatible | 通用，支持多种模型 |
| 状态机 | 属性驱动 detector+handler | 新增状态只需注册 |

---

## 7. 里程碑

**M1 — 消消乐跑通（本次）**:
- core 框架完整（capture/perception/pipeline/input/orchestration/recorder）
- match3 skill（VLM感知 + 多步求解 + IN_GAME状态）
- HDC driver 完整
- tools/ 5个调试工具（test_capture/test_perception/test_decision/debug_viewer/roi_calibrator）
- CV模块接口骨架（ocr/template_match/yolo base class，M1不实现）
- 逐帧录制 + 回灌

**M2 — CV 模块实现**:
- PaddleOCR / 模板匹配 / YOLO 真实实现
- 消消乐 CV fallback

**M3 — 第二个游戏（金铲铲）**:
- 验证多CV并行管线 + 复杂状态机 + 三层决策
