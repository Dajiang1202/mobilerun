# DouZero 斗地主集成设计文档

**日期**: 2026-06-17
**分支**: feat/doudizhu-douzero
**目标**: 将 DouZero AI 决策引擎集成到 gameauto 框架，新建独立 skill 实例，不动原有 `skills/doudizhu/`

---

## 1. 动机

- 原有 `doudizhu` skill 使用 VLM 感知 + 硬编码规则决策，决策质量有限
- DouZero 是快手开源的深度蒙特卡洛斗地主 AI，在斗地主任务上达到 SOTA 水平
- 集成 DouZero 的 `DeepAgent` 预训练模型替代规则决策，同时保留 CV 模板匹配做快速感知
- 新建独立 skill，两个斗地主方案共存，互不干扰

## 2. 方案选型

选择**方案 A：搬运引擎，轻量适配**。

将 DouZero 的 `env/`（游戏引擎）整体搬入 skill，保持内部状态机不变。感知层用 CV 模板匹配，决策层用 DouZero DeepAgent，执行层用 HDC Input。

与其他方案对比：
- 方案 B（纯模型决策，自写引擎）需重写大量游戏逻辑，重复造轮子
- 方案 C（最小化嵌入，不维护引擎状态）缺乏游戏状态校验，容错差，浪费 LSTM 历史序列建模

## 3. 目录结构

```
skills/doudizhu_douzero/
├── config.yaml                    # 模型路径、置信度阈值、等待时间
├── skill.py                       # DouDiZhuDouzeroSkill facade
├── states.py                      # 状态注册（detector + handler）
├── perception.py                  # CV 模板匹配感知
├── decision.py                    # 封装 GameEnv + DeepAgent 决策接口
├── visualizer.py                  # 调试标注
├── assets/
│   └── templates/                 # 手机端卡牌/按钮模板图片
│       ├── cards/                 # 自己的手牌模板
│       ├── others/                # 对手的出牌模板
│       └── ui/                    # 按钮/标记模板
├── douzero/                       # 从 DouZero 项目搬运，尽量不改
│   ├── env/
│   │   ├── game.py               # GameEnv 游戏引擎
│   │   ├── env.py                # Env + InfoSet + 观测编码
│   │   ├── move_detector.py      # 牌型识别
│   │   ├── move_generator.py     # 合法出牌生成
│   │   ├── move_selector.py      # 过滤能大过对方的出牌
│   │   └── utils.py              # 牌型常量
│   ├── models.py                  # 神经网络定义
│   ├── deep_agent.py             # DeepAgent（加载模型 + act）
│   └── baselines/                 # 预训练权重
│       └── douzero_WP/
│           ├── landlord.ckpt
│           ├── landlord_up.ckpt
│           └── landlord_down.ckpt

gameauto/
└── run_doudizhu_douzero.py        # 入口脚本
```

`douzero/` 目录代码尽量原样搬运，只做最小适配（import 路径修正、PyTorch 版本兼容）。`perception.py`、`decision.py`、`states.py` 是 gameauto 框架胶水层。

## 4. 状态机设计

使用多状态模式（类似 TFT），每个状态有独立 detector（模板匹配）和 handler：

| 状态 | Detector | 感知目标 | Handler 行为 |
|------|----------|---------|-------------|
| `BIDDING` | 匹配"叫地主"等按钮 | 按钮类型 + 地主标记 + 底牌 | 点击对应按钮 |
| `PLAYING` | 匹配"出牌"按钮 | 手牌 + 对手出牌 + 可用按钮 | 同步 GameEnv → DeepAgent 决策 → 出牌 |
| `SETTLEMENT` | 匹配"继续"按钮 | 结算按钮 | 点击继续 |
| `WAITING` | 默认 fallback | 无 | 等待 1s |

## 5. 数据流

```
HDC screenshot
    │
    ▼
StateMachine.step()
    │
    ├── detector: 模板匹配判断当前阶段
    │
    └── handler:
         │
         ├── PerceptionPipeline (并行 CV 任务)
         │   ├── TemplateMatch: 手牌识别
         │   ├── TemplateMatch: 对手出牌识别
         │   ├── TemplateMatch: 按钮检测
         │   └── TemplateMatch: 地主/底牌检测
         │
         ├── DouzeroDecision.decide(perception_dict)
         │   ├── 非己方回合: 记录对手出牌 → env.step()
         │   ├── 己方回合: 构造 InfoSet → DeepAgent.act() → 最优出牌
         │   └── BIDDING/SETTLEMENT: 简单按钮决策
         │
         └── ActionBuilder: 卡牌列表 + 屏幕坐标 → Action[]
              (逐张 tap 出牌 + 点击"出牌"按钮)
    │
    ▼
HDC Input 执行 Action[]
```

## 6. 感知层

纯 CV，不使用 VLM。通过 `PerceptionPipeline` 并行执行多个 `TemplateMatchTask`。

### 各阶段感知任务

**BIDDING 阶段：**
- 检测当前按钮（叫地主/不叫/抢地主/加倍/不加倍）
- 检测地主标记
- 检测 3 张底牌

**PLAYING 阶段：**
- 识别自己手牌（逐张匹配 54 张牌模板）
- 识别上家/下家出牌区
- 检测"不出"标记
- 检测可用按钮（出牌/不出/提示）

**SETTLEMENT 阶段：**
- 检测"继续"按钮

### 感知输出格式

```python
{
    "phase": "playing",              # bidding / playing / settlement
    "my_hand": ["♠A", "♥K", ...],   # 手牌列表
    "last_play": ["♦3", "♦3"],      # 上一轮出牌
    "last_player": "landlord_up",   # 谁出的上一轮
    "landlord_cards": [...],         # 3张底牌
    "buttons": ["出牌", "不出"],
    "is_my_turn": true,
    "card_positions": {              # 每张牌的屏幕坐标 [0-1000]
        "♠A": [320, 850],
        ...
    }
}
```

### 模板图片来源

第一版用 HDC 截图从实际手机游戏截取裁剪，放 `assets/templates/`。后续可做半自动标注工具。

## 7. 决策层

### `DouzeroDecision` 类

包装 DouZero 的 `GameEnv` + `DeepAgent`：

```python
class DouzeroDecision:
    def __init__(self, model_dir: str):
        self.agent = DeepAgent(model_path_dict)
        self.env = GameEnv()
        self.round_initialized = False

    def init_round(self, landlord_pos, my_hand, landlord_cards, my_position):
        """每局开始，初始化 GameEnv"""
        ...

    def decide(self, perception: dict) -> list[Action]:
        """核心：感知结果 → GameEnv.step → DeepAgent.act → Action[]"""
        ...
```

- `DeepAgent.act()` 返回的 action 是牌型索引，通过 `GameEnv._get_action_cards()` 反查实际卡牌列表
- 非己方回合通过特征编码（对手出牌区的模板匹配结果）驱动 `env.step()`，维护状态同步
- BIDDING/SETTLEMENT 阶段不经过 DeepAgent，简单按钮点击决策

## 8. 执行层

- 使用 gameauto 现成 `HdcInput`（`core/input/hdc.py`）
- 坐标从感知阶段记录在 `card_positions` 中，`ActionBuilder` 转换为 tap 序列
- 出牌节奏：单牌/对子/三带 tap 间隔 80ms，顺子/连对 50ms，出牌后等 1.5s
- 按钮点击后根据阶段等待不同时长（BIDDING 后等 3s，结算后等 5s）

## 9. 入口脚本

`run_doudizhu_douzero.py`，遵循现有 8 步模板：

1. CLI 解析
2. 加载 config
3. 设置 logging + session
4. 连接 HDC 设备
5. 创建 PerceptionPipeline（CV，**无需 VLM**）
6. 创建 DouDiZhuDouzeroSkill + 注册状态
7. 创建 GameContext
8. 启动 GameLoop

### 与 `run_doudizhu.py` 对比

| | run_doudizhu.py | run_doudizhu_douzero.py |
|---|---|---|
| 感知 | VLM（Jinja2 prompt） | CV（PerceptionPipeline + template match） |
| 决策 | 规则（优先级按钮） | DouZero DeepAgent 模型 |
| VLM 依赖 | 必须 | 无 |
| 单帧延迟 | 2-5s（VLM） | <200ms（CV + 模型推理） |

## 10. 配置

```yaml
# 对局设置
max_rounds: 20

# 模型路径（相对于 skill 目录）
model_dir: douzero/baselines/douzero_WP

# 模板匹配
template_confidence: 0.90
card_confidence: 0.85
pass_confidence: 0.90

# 时间控制（秒）
wait_between_rounds: 3.0
wait_after_action: 1.5
tap_interval: 0.08
```

## 11. 风险与应对

| 风险 | 应对 |
|------|------|
| 手机端卡牌模板制作工作量大 | 先做最小集（54张手牌 + 不出/按钮等几个 UI 模板），后续补全 |
| 模板匹配置信度不够（不同手机分辨率/皮肤） | 支持多套模板 + 动态调整置信度阈值；必要时回退到 VLM |
| DouZero 代码 PyTorch 版本兼容 | 原项目用 torch 1.6，当前环境可能用新版本；做兼容适配 |
| 对手出牌识别困难（小图标、重叠） | 先检测"不出"文本；出牌区尝试定位 + 逐张匹配 |
| GameEnv 与感知结果状态不同步 | 出牌后验证（自己的手牌区少了几张 = 出牌成功） |
