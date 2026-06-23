# GameAuto 部署指南 — 从 0 到 1

> 移动端游戏自动化框架 · 纯视觉方案 · 鸿蒙设备优先

---

## 一、项目概述

GameAuto 是一个**纯视觉**的移动端游戏自动化框架，核心思路：

```
截图 → 视觉感知(OCR/模板匹配/VLM) → 决策(规则/AI) → 模拟点击 → 数据录制 → 循环
```

**特点：**
- 零游戏内存依赖，纯截图分析
- 面向鸿蒙 (HarmonyOS) 设备设计，兼容 Android
- 双后端：scrcpy 低延迟推流 (~17ms) 或 HDC 直连 (~850ms)
- 组件化架构，新游戏只需实现 Skill 接口
- 全程录制：每帧截图 + 感知结果 + 决策日志，方便回灌调试

### 已适配游戏

| 游戏 | 感知方式 | 决策方式 | 入口脚本 | 状态 |
|------|---------|---------|---------|------|
| 开心消消乐 | VLM (GPT-4o) | 贪心求解 | `run_match3.py` | ✅ 可用 |
| 斗地主 (基础) | VLM | 规则 | `run_doudizhu.py` | ✅ 可用 |
| 斗地主 (DouZero) | CV 模板匹配 | DouZero 深度 AI | `run_doudizhu_douzero.py` | ✅ 可用 |
| 金铲铲之战 | PaddleOCR + 模板匹配 | L1 规则 (slow-roll) | `run_tft_scrcpy.py` | 🔧 开发中 |
| 天天象棋 | CV 模板 + VLM | Pikafish 引擎 | `run_xiangqi.py` | ✅ 可用 |

---

## 二、目录结构

```
gameauto/
├── core/                           # 零游戏依赖的引擎层 (不要改)
│   ├── capture/                    #   截图: HDC / scrcpy
│   ├── perception/                 #   感知: VLM / OCR / 模板匹配 / YOLO
│   ├── input/                      #   点击: HDC / scrcpy
│   ├── orchestration/              #   状态机 + 主循环
│   └── recorder/                   #   逐帧录制 + 回放
│
├── skills/                         # 游戏插件 (每个游戏一个目录)
│   ├── match3/                     #   消消乐
│   ├── doudizhu/                   #   斗地主 (VLM)
│   ├── doudizhu_douzero/           #   斗地主 (DouZero AI)
│   ├── tft/                        #   金铲铲之战
│   └── xiangqi/                    #   天天象棋
│
├── config/                         # 配置体系
│   ├── default.yaml                #   框架默认值
│   └── loader.py                   #   三层配置加载器
│
├── tools/                          # 调试工具 (22 个)
├── utils/                          # 通用工具 (坐标/图像/日志)
├── resource/                       # 二进制资源 (HOScrcpy JAR)
├── logs/                           # 运行时产物 (自动生成)
│
├── run_match3.py                   # 消消乐入口 (HDC)
├── run_match3_scrcpy.py            # 消消乐入口 (scrcpy)
├── run_doudizhu.py                 # 斗地主入口 (VLM)
├── run_doudizhu_douzero.py         # 斗地主入口 (DouZero)
├── run_tft_scrcpy.py               # 金铲铲入口
├── run_xiangqi.py                  # 象棋入口
├── requirements.txt                # Python 依赖
└── DEPLOY.md                       # 本文档
```

---

## 三、环境要求

| 项目 | 要求 |
|------|------|
| **Python** | 3.10 ~ 3.12 (PaddleOCR 不支持 3.13+) |
| **操作系统** | Windows 10/11 (已验证), Linux/macOS 理论兼容 |
| **鸿蒙设备** | 已开启 USB 调试 + HDC 可连接 |
| **HDC** | 鸿蒙设备连接器 (鸿蒙 SDK 自带) |
| **JVM** | scrcpy 后端需要 (JetBrains Runtime 推荐) |
| **GPU** | 可选, PaddleOCR GPU 加速需要 CUDA |
| **磁盘** | ≥ 5GB (含 PaddleOCR 模型 + PyTorch) |

---

## 四、安装步骤

### Step 1: 克隆项目 & 安装 Python 依赖

```bash
# 进入项目根目录
cd d:\gameauto\mobilerun

# (推荐) 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 安装 GameAuto 依赖
pip install -r gameauto/requirements.txt
```

> **关于 PyTorch (仅斗地主 DouZero 需要)：**
> ```bash
> # 如果玩斗地主 DouZero, 额外安装 PyTorch
> pip install torch>=2.0.0
> # CUDA 版本参考: https://pytorch.org/get-started/locally/
> ```

### Step 2: 安装 HDC (鸿蒙设备连接器)

HDC 是鸿蒙 SDK 的命令行工具，用于截图和模拟点击。

```bash
# 方式一: 从 DevEco Studio 获取
# DevEco Studio → SDK → Toolchains → hdc.exe

# 方式二: 从鸿蒙 SDK 命令行工具获取
# https://developer.huawei.com/consumer/cn/deveco-studio/

# 验证连接
hdc list targets
# 应显示你的设备 serial

# (可选) 加入 PATH, 或在 settings.yaml 中指定路径
```

### Step 3: 安装 JVM (scrcpy 后端需要)

scrcpy 低延迟推流需要 JVM 来运行 HOScrcpy SDK。

```bash
# 下载 JetBrains Runtime (推荐)
# https://github.com/JetBrains/JetBrainsRuntime/releases
# 下载 jbr-21.x.x-windows-x64 解压到 D:\resource\jbr

# 验证
D:\resource\jbr\bin\java.exe -version
```

> **不使用 scrcpy？** 如果只用 HDC 后端 (截图 ~850ms, 较慢但稳定), 可以跳过 JVM 安装。

### Step 4: 准备模板图片 (按游戏)

模板匹配是核心感知方式之一，需要预先裁剪游戏界面元素：

```bash
# 金铲铲 — 放入模板图片
gameauto/skills/tft/assets/templates/
# 需要: play_btn.png, refresh_btn.png, level_up_btn.png 等

# 斗地主 DouZero — 扑克牌模板
gameauto/skills/doudizhu_douzero/assets/templates/
# 需要: 54 张扑克牌 + 按钮模板

# 裁剪工具 (从截图中裁剪模板)
python gameauto/tools/crop_template.py --image screenshot.png --output templates/
```

### Step 5: 准备外部资源 (按游戏)

| 游戏 | 资源路径 | 说明 |
|------|---------|------|
| 斗地主 DouZero | `D:\resource\douzero\` | DouZero 预训练模型 |
| 天天象棋 | `D:\resource\pikafish\` | Pikafish UCI 引擎 |
| HOScrcpy SDK | `gameauto/resource/hosScrcpy-1.0.15-beta.jar` | 已内置 |

---

## 五、配置

### 配置体系 (三层覆盖)

```
优先级从低到高:
① gameauto/config/default.yaml    ← 框架默认值 (不要改)
② gameauto/skills/<game>/config.yaml ← 游戏默认参数
③ ~/.gameauto/games/<game>.yaml   ← 用户覆盖 (可选, git 不追踪)
```

### 首次运行 — 全局配置

首次运行任何游戏会自动创建 `~/.gameauto/settings.yaml`：

```yaml
# ~/.gameauto/settings.yaml — 编辑此文件!

device:
  serial: "your_device_serial"    # ← 必填! hdc list targets 查看
  hdc_path: hdc                    # HDC 路径 (加入 PATH 则无需改)

vlm:
  model: gpt-4o                    # VLM 模型 (match3/xiangqi 需要)
  base_url: "https://api.openai.com/v1"
  api_key: "sk-..."                # ← VLM 游戏必填

logging:
  console_level: INFO              # DEBUG / INFO / WARNING
```

### 游戏参数配置

每个游戏有独立配置，以金铲铲为例：

```yaml
# gameauto/skills/tft/config.yaml

rounds: 500                        # 总对局数
max_steps_per_round: 6             # 每轮最大操作步数

strategy:
  type: slow_roll                  # 策略类型
  core_champions:                  # 核心棋子
    - name: "琴女"
      cost: 1
      target_stars: 3
    - name: "盖伦"
      cost: 1
      target_stars: 3
  level_up_schedule:               # 升级时间表
    - { round: 2, level: 5 }
    - { round: 5, level: 6 }
```

---

## 六、快速开始

### 🎮 消消乐 (最简单的入门)

需要：VLM API Key + HDC 连接

```bash
# 1. 确保 settings.yaml 已配置 serial 和 VLM api_key
# 2. 手机上打开消消乐游戏界面

# 3. 运行 (HDC 后端, 稳定)
python gameauto/run_match3.py

# 4. 或运行 (scrcpy 后端, 快)
python gameauto/run_match3_scrcpy.py

# 5. 控制运行轮数
ROUNDS=5 python gameauto/run_match3.py
```

### 🃏 斗地主 (DouZero AI)

需要：模板图片 + PyTorch + DouZero 模型

```bash
# 1. 安装 PyTorch
pip install torch>=2.0.0

# 2. 准备 DouZero 模型到 D:\resource\douzero\

# 3. 准备扑克牌模板图片 (用 crop_template.py 裁剪)

# 4. 手机上打开斗地主游戏界面, 运行
python gameauto/run_doudizhu_douzero.py
```

### ♟️ 金铲铲之战

需要：PaddleOCR + scrcpy + 模板图片

```bash
# 1. 确保 PaddleOCR 已安装 (requirements.txt 已包含)
# 2. 确保 JVM 已安装 (scrcpy 需要)
# 3. 准备模板图片放入 skills/tft/assets/templates/

# 4. 手机上打开金铲铲游戏界面, 运行
python gameauto/run_tft_scrcpy.py

# 5. 控制对局数
ROUNDS=100 python gameauto/run_tft_scrcpy.py
```

---

## 七、运行时日志

每次运行自动创建会话目录：

```
gameauto/logs/<session_timestamp>/
├── debug.log              ← 完整调试日志 (含时间戳)
├── game.log               ← 人类可读的决策日志
├── metadata.json          ← 会话元信息 (设备、参数)
├── summary.json           ← 运行总结
└── round_001/
    ├── screenshot.png     ← 原始截图
    ├── perception.json    ← 感知结果 (OCR/模板匹配)
    ├── actions.json       ← 执行的操作
    └── annotated.png      ← 标注可视化 (部分游戏)
```

**调试技巧：**
```bash
# 查看最新会话
ls gameauto/logs/ | tail -1

# 离线测试感知 (不连设备)
python gameauto/tools/test_tft_perception.py --image screenshot.png

# 回放录制数据
python gameauto/tools/debug_viewer.py --replay gameauto/logs/session_xxx/
```

---

## 八、后端选择

GameAuto 支持两种截图/点击后端，在入口脚本顶部切换：

| 后端 | 截图延迟 | 点击延迟 | 需要 JVM | 稳定性 |
|------|---------|---------|---------|--------|
| **scrcpy** | ~17-50ms | ~0.01ms | ✅ | 截图可能滞后 |
| **HDC** | ~850ms | ~100ms | ❌ | 非常稳定 |

```python
# 在每个 run_*.py 入口脚本顶部修改:
CAPTURE_BACKEND = "scrcpy"   # 截图: "scrcpy" (快) / "hdc" (准)
INPUT_BACKEND = "scrcpy"     # 点击: "scrcpy" (快) / "hdc" (准)
```

**推荐组合：**
- 速度优先：`scrcpy + scrcpy` (金铲铲、消消乐)
- 稳定优先：`hdc + scrcpy` (HDC 截图准 + scrcpy 点击快)
- 无 JVM：`hdc + hdc` (最慢但最稳)

---

## 九、适配新游戏

最少 6 个文件即可接入新游戏：

```
gameauto/skills/<your_game>/
├── config.yaml           # 游戏参数
├── perception.py         # 感知: 截图 → 结构化数据
├── decision.py           # 决策: 结构化数据 → Action[]
├── states.py             # 状态注册 (大厅/游戏中/结算...)
├── skill.py              # Skill 门面类
└── prompts/game.jinja2   # VLM 提示词 (如果用 VLM)

gameauto/run_<your_game>.py  # 入口脚本 (复制 run_match3.py 改)
```

**关键设计原则：**
- 坐标使用 `[0-1000]` 归一化值，自动适配任意分辨率
- 状态机模式：每个游戏阶段注册 `detector` (判断状态) + `handler` (执行逻辑)
- 分层决策：L1 规则 (<1ms) → L2 查表 (<100ms) → L3 LLM (2-5s)

---

## 十、常见问题

### Q: `hdc list targets` 显示空？
```bash
# 1. 确认 USB 调试已开启
# 2. 确认数据线 (非充电线) 已连接
# 3. 手机上点击 "允许 USB 调试"
# 4. 重启 HDC: hdc kill && hdc start
```

### Q: scrcpy 启动失败 / JVM 报错？
```bash
# 1. 检查 JVM 路径是否正确
# 2. 确认 HOScrcpy JAR 存在: gameauto/resource/hosScrcpy-1.0.15-beta.jar
# 3. 回退到 HDC 后端: 修改入口脚本 CAPTURE_BACKEND = "hdc"
# 4. scrcpy 退出可能挂起, Ctrl+C 无效时用 os._exit(0) 已内置处理
```

### Q: PaddleOCR 安装失败？
```bash
# PaddleOCR 不支持 Python 3.13+, 请用 3.10-3.12
# GPU 版本需要 CUDA toolkit
# 最小安装: pip install paddlepaddle paddleocr

# 验证
python -c "from paddleocr import PaddleOCR; print('OK')"
```

### Q: 模板匹配找不到元素？
```bash
# 1. 确认模板图片存在且不为空
# 2. 模板必须从同分辨率截图裁剪
# 3. 用 crop_template.py 重新裁剪
# 4. 降低 confidence 阈值 (默认 0.85)
```

### Q: Ctrl+C 无法停止程序？
```bash
# scrcpy 模式下 JVM 会吞信号, 程序已内置 os._exit(0) 处理
# 如果仍然卡住: 另开终端 taskkill /F /PID <pid>
```

---

## 十一、性能参考

| 操作 | scrcpy | HDC |
|------|--------|-----|
| 截图 | ~17-50ms (scale=2) | ~850ms |
| 点击 | ~0.01ms | ~100ms |
| PaddleOCR | ~100ms (GPU) / ~300ms (CPU) | — |
| 模板匹配 (单目标) | ~5-30ms | — |
| VLM 调用 | ~2-5s | — |
| 完整循环 (TFT) | ~200ms/帧 | ~1.2s/帧 |

---

## 十二、一键部署脚本 (Windows)

```powershell
# deploy.ps1 — 从零部署 GameAuto

# 1. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 2. 安装依赖
pip install -r gameauto/requirements.txt

# 3. (可选) 安装 PyTorch (斗地主 DouZero)
# pip install torch>=2.0.0

# 4. 验证 HDC
hdc list targets

# 5. 初始化配置 (首次运行会自动创建)
python -c "from gameauto.config.loader import load_global_config; load_global_config()"

# 6. 编辑配置
notepad $HOME\.gameauto\settings.yaml
# 填入 device.serial 和 vlm.api_key

# 7. 测试运行 (消消乐 1 轮)
$env:ROUNDS="1"; python gameauto/run_match3.py
```

---

**文档版本：** 2026-06-23 · 基于 GameAuto M1 版本
