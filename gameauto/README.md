# GameAuto Framework

移动端游戏自动化框架 — 纯视觉方案，组件化架构，支持多游戏扩展。

## 架构

```
截图 → 视觉感知(VLM/CV) → 决策(规则/LLM) → 输入模拟 → 录制
```

### 目录结构

```
gameauto/
├── core/                          # 零游戏依赖的引擎层
│   ├── capture/                   # 画面捕获 (HDC/scrcpy/camera)
│   ├── perception/                # 视觉感知 (VLM/OCR/YOLO/模板匹配)
│   ├── input/                     # 输入模拟 (tap/swipe/drag)
│   ├── orchestration/             # 任务调度 (状态机 + 主循环)
│   └── recorder/                  # 数据录制 + 回灌调试
│
├── skills/                        # 游戏插件 (每个游戏一个目录)
│   ├── match3/                    # 消消乐
│   └── doudizhu/                  # 斗地主
│
├── config/                        # 配置体系 (三层: 全局/默认/用户)
├── tools/                         # 调试工具 (单模块可测试)
├── utils/                         # 通用工具 (坐标/图像/日志)
├── logs/                          # 运行时产物 (gitignore)
│
├── run_match3.py                  # 消消乐入口
└── run_doudizhu.py                # 斗地主入口
```

### 核心抽象

| 层 | 接口 | 职责 |
|----|------|------|
| Capture | `BaseCapture.screenshot() → bytes` | 截取屏幕 |
| Perception | `BasePerception.recognize(image) → PerceptionResult` | 视觉识别 |
| Input | `BaseInput.swipe/tap(x,y)` | 设备操作 |
| Skill | `register_states(sm)` | 注入游戏状态 |
| StateMachine | `register(state, detector, handler)` | 状态路由 |
| GameLoop | `run()` | 驱动循环 |

### 配置体系

```
~/.gameauto/settings.yaml             ← 全局 (device + VLM, 跨游戏)
skills/<game>/config.yaml             ← 技能默认参数
~/.gameauto/games/<game>.yaml         ← 用户覆盖 (可选, gitignore)
```

## 适配新游戏

### 最少需要创建 6 个文件

```
skills/<your_game>/
├── config.yaml               # 游戏参数 (rounds 等)
├── prompts/<your_game>.jinja2 # VLM 感知提示词
├── perception.py              # 组装 prompt → 调 VLM → 解析 JSON
├── decision.py                # 决策引擎 (规则/LLM)
├── visualizer.py              # 可视化标注 (可选)
├── states.py                  # 状态注册 (detector + handler)
└── skill.py                   # Skill 门面

run_<your_game>.py             # 入口脚本 (复制 run_match3.py 改游戏名)
```

### 适配步骤

1. 写 VLM prompt (`prompts/*.jinja2`)
   - 告诉 VLM 输出什么 JSON (phase, buttons, hand_cards 等)
   - 定义坐标系统 [0-1000]
   - 关键识别要点 (按钮状态、游戏阶段)

2. 实现 perception (`perception.py`)
   - 继承/参考 `Match3Perception` 或 `DouDiZhuPerception`
   - `recognize(image) → PerceptionResult`

3. 实现决策 (`decision.py`)
   - 纯 Python 函数: `decide(state) → list[Action]`
   - 可以从简单规则开始，逐步加 LLM

4. 注册状态 (`states.py`)
   - 每个游戏阶段一个 handler
   - handler 流程: 感知 → 决策 → Action[]

5. 实现可视化 (`visualizer.py`)
   - 参考 match3/visualizer.py 的 annotate_board/annotate_swipe
   - 在截图上标注识别结果，方便调试

6. 创建 Skill 类 (`skill.py`)
   - 委托给 StateRegistrar

7. 创建入口脚本 (`run_<game>.py`)
   - 复制 `run_match3.py`，改 3 处: prompt 路径、Skill 类、max_steps

### 决策复杂度分层

| 层 | 延迟 | 适用场景 | 示例 |
|----|------|---------|------|
| L1 规则 | <1ms | 高频简单决策 | 消消乐贪心求解、斗地主按钮点击 |
| L2 查表 | <100ms | 策略查询 | 装备合成表、阵容推荐 |
| L3 LLM | 2-5s | 关键战略决策 | 海克斯选择、转型判断 |

### 感知方式扩展

当前支持: VLM (OpenAI-compatible API)
预留接口: OCR (PaddleOCR)、模板匹配 (OpenCV)、YOLO (ONNX)

新感知方式只需实现 `BaseCVTask` 接口，注册到 `PerceptionPipeline` 即可并行调度。

### 调试

```bash
# 单个模块测试
python tools/test_capture.py --serial xxx
python tools/test_perception.py --image screenshot.png

# 可视化调试（回放录制数据）
python tools/debug_viewer.py --replay logs/session_001/
```
