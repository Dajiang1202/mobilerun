# TFT 视频回放工作台 — 设计文档

> 用一段 30fps 的 TFT 游戏视频模拟真机推流，驱动框架的感知/决策正常逻辑，
> 控制台打印识别结果与决策点，用于快速验证方法是否可用。
> 视频按墙上时钟异步播放、自动跳帧——算法慢时视频不会等，天然模拟实时性。

---

## 一、目标与范围

### 目标
- 输入一段 TFT 游戏视频（30fps），用视频流**模拟真机**（行为对标 scrcpy 推流）。
- 系统**走正常逻辑**：取帧 → 感知 → 决策 → 打印。
- **实时性模拟**：视频按墙上时钟播放，消费慢时自动丢帧；算法不会拖住视频。
- 控制台打印每 tick 的识别结果 + 决策动作 + 耗时，便于快速判断方法是否可用。

### 非目标（YAGNI）
- 不做轮次/状态机闭环（那是 GameLoop 的事，感知/决策还没成型，暂不需要）。
- 不接真机、不触控设备（视频回放纯离线）。
- 默认不接现有 TftPerception/TftDecision（模板缺失、OCR 慢，会一上来就跑不动）。
- 不做站位/装备/选秀等具体玩法逻辑。

### 起步姿态
**全插拔 + stub 起步**：感知/决策是约定签名的纯函数对象，默认用零依赖 stub 把视频闭环跑通，再逐步替换为真实方法。

---

## 二、架构

### 数据流

```
视频文件 (30fps)
   │  VideoCapture 后台线程 (墙上时钟对齐, 覆写最新帧, 消费慢则丢帧)
   ▼
 latest frame (PNG bytes) ◄── screenshot() 取"现在"这一帧
   │
   ▼
 ReplayDriver.tick():
   ├─ frame = capture.screenshot()
   ├─ state  = perceive(frame)        ← 可插拔 (默认 stub)
   ├─ actions = decide(state)         ← 可插拔 (默认 stub, 返回 [])
   ├─ print  (时间戳 / state / actions / 耗时 / 跳帧数)
   └─ 落盘   (tick_NNNN/{frame.png, perception.json, actions.json})
```

### 三层职责
| 层 | 职责 | 游戏相关？ |
|----|------|-----------|
| `VideoCapture` | 视频当 BaseCapture，墙上时钟播放 + 丢帧 | 否（通用） |
| `ReplayDriver` | tick 循环：取帧→感知→决策→print→落盘 | 否（通用，perceive/decide 是入参） |
| `run_tft_replay.py` | 装配 perceive/decide（默认 stub）+ CLI + 适配器示例 | 是（TFT 入口） |

---

## 三、模块设计

### 3.1 `VideoCapture`（`gameauto/core/capture/video.py`）

通用 `BaseCapture` 实现，行为对标 [bridge.py](../gameauto/core/capture/scrcpy/bridge.py) 的 scrcpy 推流。

**实时帧模型（核心）**
- 后台线程按**视频原生 fps（30）走墙上时钟**播放：`read()` 一帧后 `sleep` 对齐到 `1/fps` 秒，不是"解码越快越好"。
- 每读到一帧 → 转 PNG bytes → 加锁覆写 `_latest`。
- **消费慢时丢弃旧帧**，永远只保留最新——这是"视频不等算法、跳帧异步"的落点。
- `--speed` 倍速：线程时钟步进 = `(1/fps) / speed`。`2x`=视频比真实快一倍（快速过视频），`0.5x`=慢放观察。

**接口**
```python
class VideoCapture(BaseCapture):
    def __init__(self, video_path: str, speed: float = 1.0, loop: bool = False): ...
    async def connect(self) -> None            # 启动后台播放线程
    async def screenshot(self) -> bytes        # 当前最新帧 PNG (wait_new 语义对齐 scrcpy)
    async def disconnect(self) -> None
    @property
    def native_resolution(self) -> tuple[int, int]   # 视频原始宽高 (坐标映射基准)

    # driver 用的额外接口:
    def current_timestamp(self) -> float        # 当前帧在视频中的秒数
    def dropped_count(self) -> int              # 被丢弃的帧数 (实时性诊断)
    def is_finished(self) -> bool               # 视频是否播完 (非 loop)
```

**结束行为**：视频读完且非 `--loop` → 标记 `is_finished()`，`screenshot()` 返回最后帧，driver 据此停止。
**中文路径**：用 `cv2.imdecode(np.fromfile())` 读帧（斗地主踩坑：`cv2.imread` 不支持中文路径）。

### 3.2 `ReplayDriver`（`gameauto/tools/replay_driver.py`）

通用调试 tick 循环，**不 import 任何 TFT 代码**。

```python
def perceive(frame_bgr: np.ndarray) -> dict: ...      # 帧 → 结构化状态 (约定签名)
def decide(state: dict) -> list[Action]: ...          # 状态 → 动作列表 (约定签名)

class ReplayDriver:
    def __init__(self, capture, perceive, decide,
                 tick_interval: float = 0.0,
                 record: bool = True,
                 session_dir: Path | None = None,
                 show: bool = False,
                 verbose: bool = False): ...

    async def run(self) -> None:
        # 循环: screenshot → perceive → decide → print → 落盘
        # 停止条件: capture.is_finished() 且无新帧, 或 Ctrl+C
```

**tick 间隔**：`tick_interval` 默认 `0`（尽可能快，由感知限速）——感知 800ms，视频 800ms 内走过 24 帧，下个 tick 拿到 24 帧之后的画面，与真机 bot 反应慢一拍完全一致。设 `0.5` 则每 tick 间至少 sleep 0.5s（只 throttle driver，不影响视频线程）。

**落盘**：复用框架 `SessionManager`，与真机日志同构。`tick_NNNN/{frame.png, perception.json, actions.json}`。**识别先于决策落盘**：哪怕 `decide` 抛异常，`perception.json` 已存（斗地主教训）。

### 3.3 入口 `run_tft_replay.py`（`gameauto/`）

装配 perceive/decide + CLI + 适配器示例。

**后端查表**（CLI 友好，字符串映射到函数）：
```python
PERCEIVE_BACKENDS = {
    "stub":    stub_perceive,      # 默认: 返回帧基本信息 (h/w/mean_bgr/ts), 零依赖
    "adapter": tft_adapter_perceive,  # 示例: 包 TftPerception.recognize() (注释/参考, 默认不接)
}
DECIDE_BACKENDS = {
    "stub":    stub_decide,        # 默认: 返回 [], 只打 state 摘要
    "adapter": tft_adapter_decide, # 示例: 包 TftDecision.decide()
}
```

**stub**：
- `stub_perceive(frame)` → `{"frame_h":, "frame_w":, "mean_bgr":, "ts":}`，验证"帧在动、跳帧正常"。
- `stub_decide(state)` → `[]`。

**适配器示例**（预留，默认不接）：注释演示如何把现有 `TftPerception.recognize()` / `TftDecision.decide()` 包成约定签名，等以后要接真模块时照改。

**Action**：复用 [orchestration/base.py](../gameauto/core/orchestration/base.py) 的 `Action`，stub→真决策无缝过渡。

---

## 四、输出（三种并行）

### 4.1 控制台 print（默认开，主力）
每 tick 一行紧凑摘要 + 详情：
```
[t=12.30s] frame#369 dropped=24  perceive=412ms  decide=1ms
  state: {"gold":42,"lvl":6,"shop":["凯尔","瑟提",...]}
  actions: [tap shop[2], tap shop[4]]
```
- `t` / `frame#`：视频时间戳/帧号（知道跳到哪了）
- `dropped`：本 tick 期间丢弃的帧数（实时性诊断，直接打在首行）
- 分项耗时：`perceive` / `decide`
- `state` 全量 JSON、`actions` 列表
- `--quiet`：只打 actions；`--verbose`：连原始中间文本都打

### 4.2 cv2 预览窗（`--show`，可选）
- 显示当前帧；若 `state` 约定字段（如 `state["overlays"]`）有 ROI/坐标，画框/标字叠加。
- 标题栏显示视频时间 + `dropped=N`。
- 调感知时肉眼对照帧。

### 4.3 落盘（`--record`，默认开）
- `logs/tft_replay_<session>/tick_NNNN/{frame.png, perception.json, actions.json}`
- 复用 `SessionManager`，与真机日志同构，便于以后对比。

---

## 五、CLI

```bash
python run_tft_replay.py path/to/tft.mp4                       # 默认: stub, 1x, 控制台+落盘
python run_tft_replay.py tft.mp4 --speed 2 --show              # 2x 快放 + 预览窗
python run_tft_replay.py tft.mp4 --perceive adapter --decide adapter  # 接现有 TFT 模块
python run_tft_replay.py tft.mp4 --tick-interval 0.5           # 每 tick 至少 0.5s
python run_tft_replay.py tft.mp4 --loop                        # 视频循环
python run_tft_replay.py tft.mp4 --no-record --quiet           # 只打 actions, 不落盘
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `video_path` | (必填) | 视频文件路径 |
| `--speed` | `1.0` | 播放倍速 |
| `--loop` | `False` | 视频循环 |
| `--tick-interval` | `0.0` | driver tick 最小间隔（s），0=尽可能快 |
| `--perceive` | `stub` | `stub` / `adapter` |
| `--decide` | `stub` | `stub` / `adapter` |
| `--show` | `False` | 显示 cv2 预览窗 |
| `--record` / `--no-record` | 开 | 是否落盘 |
| `--verbose` / `--quiet` | 普通 | 日志详略 |

---

## 六、验证里程碑

| 里程碑 | 验证方法 | 通过标准 |
|--------|---------|---------|
| M1 视频闭环 | stub 感知跑一段视频 | 控制台持续打印 t/frame#/dropped，视频正常推进，结束后自动停 |
| M2 实时性 | 故意写个 `time.sleep(0.8)` 的 perceive | dropped 数随感知耗时上升，视频不卡顿 |
| M3 插拔验证 | `--perceive adapter` 接现有 TftPerception | 能跑（即使识别不全），不崩 |
| M4 落盘对齐 | 检查 `tick_NNNN/perception.json` | 内容与控制台 state 一致，decide 崩时 perception.json 仍在 |

---

## 七、文件清单

| 文件 | 状态 | 说明 |
|------|------|------|
| `gameauto/core/capture/video.py` | 新建 | VideoCapture（通用 BaseCapture） |
| `gameauto/tools/replay_driver.py` | 新建 | ReplayDriver + stub perceive/decide |
| `gameauto/run_tft_replay.py` | 新建 | TFT 入口：装配 + CLI + 适配器示例 |

依赖的现有模块：`BaseCapture`（[core/capture/base.py](../gameauto/core/capture/base.py)）、`Action`（[orchestration/base.py](../gameauto/core/orchestration/base.py)）、`SessionManager`（[recorder/session.py](../gameauto/core/recorder/session.py)）。

---

## 八、设计决策记录

1. **为什么新建 ReplayDriver 而不复用 GameLoop？** GameLoop 是轮次+阶段模型，带真机调好的 sleep 且依赖能跑的状态机；感知/决策从零搭时这套结构碍事。工作台要的是连续 tick + 全控 print/落盘。
2. **为什么 VideoCapture 走墙上时钟而不是尽快解码？** 真机推流是墙上时钟的（30fps 实时），尽快解码会把视频几秒内灌完，失去实时模拟意义。墙上时钟 + 丢帧才能复现"bot 反应慢一拍"。
3. **为什么默认 stub 不接现有 TFT 模块？** 现有 TftPerception 模板缺失、OCR 慢，直接接会一上来跑不动，挡住"先把视频闭环验证通"这个第一步。stub 保证 day-1 可跑，adapter 预留接入口。
4. **为什么 perceive/decide 用约定签名函数对象？** 换方法零成本（改入口一行 import），工作台核心价值就是快速 A/B 不同感知/决策方法。
5. **为什么 `--perceive/--decide` 用字符串枚举而非传对象？** CLI 友好；可选后端在入口查表映射到函数。
