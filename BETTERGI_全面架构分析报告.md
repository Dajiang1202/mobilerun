# BetterGI (Better Genshin Impact) — 全面架构分析报告

> **版本**: 0.61.1-alpha.2 | **技术栈**: C# 12 / .NET 8.0 / WPF | **目标游戏**: 原神 (Genshin Impact)

---

## 目录

1. [项目总览](#1-项目总览)
2. [整体架构](#2-整体架构)
3. [模块全景图](#3-模块全景图)
4. [核心子系统深度分析](#4-核心子系统深度分析)
   - [4.1 画面捕获子系统 (GameCapture)](#41-画面捕获子系统-gamecapture)
   - [4.2 视觉识别子系统 (Recognition)](#42-视觉识别子系统-recognition)
   - [4.3 输入模拟子系统 (Input Simulation)](#43-输入模拟子系统-input-simulation)
   - [4.4 任务调度引擎 (Task Orchestration)](#44-任务调度引擎-task-orchestration)
   - [4.5 路径规划与导航 (Pathfinding)](#45-路径规划与导航-pathfinding)
   - [4.6 脚本引擎 (Script Engine)](#46-脚本引擎-script-engine)
   - [4.7 配置系统 (Configuration)](#47-配置系统-configuration)
   - [4.8 通知联动 (Notification)](#48-通知联动-notification)
5. [移动端移植可行性评估](#5-移动端移植可行性评估)
6. [跨游戏适配扩展性评估](#6-跨游戏适配扩展性评估)
7. [总结与建议](#7-总结与建议)

---

## 1. 项目总览

BetterGI 是一个基于 **WPF MVVM** 架构的 Windows 桌面游戏自动化工具，专为《原神》设计。它通过**画面捕获 → 视觉识别 → 输入模拟**的闭环实现游戏自动化。

### 核心技术栈

| 层次 | 技术 | 用途 |
|------|------|------|
| 框架 | .NET 8.0 + WPF | 桌面应用壳 |
| UI | WPF-UI 4.3.0 + Violeta 主题 | Fluent Design 界面 |
| MVVM | CommunityToolkit.Mvvm | 源码生成器版 MVVM |
| DI | Microsoft.Extensions.DependencyInjection | 通用 Host 模式的依赖注入 |
| 视觉 | OpenCvSharp4 + YoloSharp + ONNX Runtime (DirectML) | 模板匹配、YOLO 检测、OCR |
| 脚本 | ClearScript.V8 (Google V8) | JavaScript 用户脚本引擎 |
| 日志 | Serilog | 文件+控制台+RichtextBox 多通道日志 |
| 输入 | Vanara.PInvoke (SendInput/PostMessage) | 键鼠模拟 |
| AI 加速 | TensorRT / CUDA / DirectML / OpenVINO | GPU 推理加速 |

### 解决方案项目结构 (6 个项目)

```
BetterGenshinImpact.sln
├── BetterGenshinImpact       ← 主 WPF 应用 (核心)
├── Fischless.GameCapture     ← 游戏画面捕获库 (BitBlt/DX/WinRT)
├── Fischless.HotkeyCapture   ← 全局热键注册库
├── Fischless.WindowsInput    ← 键鼠输入模拟库 (SendInput)
├── BetterGenshinImpact.Test  ← 集成测试工程
└── BetterGenshinImpact.UnitTest ← 单元测试 (xUnit)
```

### 依赖关系图

```
BetterGenshinImpact (主应用)
  ├── Fischless.GameCapture   (画面捕获)
  ├── Fischless.HotkeyCapture (热键监听)
  └── Fischless.WindowsInput  (输入模拟)

BetterGenshinImpact.Test  ──→ BetterGenshinImpact
BetterGenshinImpact.UnitTest ──→ BetterGenshinImpact
```

---

## 2. 整体架构

### 架构分层图

```
┌─────────────────────────────────────────────────────────────────┐
│                     WPF View 层 (XAML)                          │
│   MainWindow / MaskWindow / Pages / Windows / Controls          │
├─────────────────────────────────────────────────────────────────┤
│                 MVVM ViewModel 层                                │
│   15+ 页面 ViewModel | MainWindowViewModel | NotifyIconViewModel │
├─────────────────────────────────────────────────────────────────┤
│                     Service 层                                   │
│   ConfigService / ScriptService / UpdateService                  │
│   NotificationService / MapApiServices                          │
├─────────────────────────────────────────────────────────────────┤
│                   GameTask 任务层 (核心)                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐│
│  │ TaskTrigger │  │  ISoloTask   │  │  AutoPathing (路径规划)   ││
│  │ Dispatcher  │  │  独立任务们   │  │  Navigation (地图定位)    ││
│  │ (50ms 轮询) │  │  (19+ 任务)  │  │  PathExecutor (路径执行)  ││
│  └─────────────┘  └──────────────┘  └──────────────────────────┘│
├─────────────────────────────────────────────────────────────────┤
│                    Core 核心层                                   │
│  ┌───────────┐ ┌──────────────┐ ┌───────────┐ ┌──────────────┐ │
│  │ BgiVision │ │ Recognition  │ │ Simulator │ │ Script       │ │
│  │ (游戏视觉) │ │ (OCR/YOLO/   │ │ (输入模拟) │ │ (V8 JS引擎)  │ │
│  │           │ │  Template)   │ │           │ │              │ │
│  └───────────┘ └──────────────┘ └───────────┘ └──────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                    Fischless 库层 (可复用)                       │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐│
│  │ Fischless.       │ │ Fischless.       │ │ Fischless.       ││
│  │ GameCapture      │ │ HotkeyCapture    │ │ WindowsInput     ││
│  └──────────────────┘ └──────────────────┘ └──────────────────┘│
├─────────────────────────────────────────────────────────────────┤
│                    外部 API / 硬件层                             │
│  Win32 API | DirectX | WinRT | ONNX Runtime | HTTP APIs        │
└─────────────────────────────────────────────────────────────────┘
```

### 核心闭环自动化流程

```
[50ms Timer Tick]
       │
       ▼
┌──────────────┐    ┌──────────────────┐    ┌─────────────────┐
│ GameCapture  │───→│ TaskTrigger      │───→│ ITaskTrigger[]  │
│ .Capture()   │    │ Dispatcher       │    │ OnCapture(      │
│              │    │ .Tick()          │    │  CaptureContent) │
│ BitBlt/DX/   │    │                  │    │                 │
│ WinRT 捕获    │    │ 优先级排序        │    │ 独占/后台/UI分类 │
└──────────────┘    └──────────────────┘    └─────────────────┘
                                                    │
                               ┌────────────────────┘
                               ▼
                    ┌──────────────────┐
                    │  视觉识别         │
                    │  - 模板匹配       │
                    │  - OCR 文字识别   │
                    │  - YOLO 目标检测  │
                    │  - 颜色范围检测    │
                    │  - 地图特征匹配    │
                    └──────────────────┘
                               │
                               ▼
                    ┌──────────────────┐
                    │  决策与状态机     │
                    │  - 当前 UI 判定   │
                    │  - 条件判断       │
                    │  - 状态转换       │
                    │  - 动作选择       │
                    └──────────────────┘
                               │
                               ▼
                    ┌──────────────────┐
                    │  输入模拟         │
                    │  - SendInput     │
                    │  - PostMessage   │
                    │  - MouseEvent    │
                    └──────────────────┘
```

---

## 3. 模块全景图

### 3.1 主应用目录树 (`BetterGenshinImpact/`)

```
BetterGenshinImpact/
├── App.xaml/cs                    ← 应用入口 + DI 配置 + 异常处理
├── Core/                          ← 核心基础设施
│   ├── BgiVision/                 ← 游戏视觉 API (BvImage, BvLocator, BvPage)
│   ├── Config/                    ← 25+ 个配置模型类
│   ├── Monitor/                   ← 键鼠监控 (全局 Hook)
│   ├── Recognition/               ← 视觉识别引擎 ★★★
│   │   ├── OCR/                   ←   PaddleOCR (Det + Rec ONNX)
│   │   ├── ONNX/                  ←   YOLO / SVTR / BgiOnnxFactory
│   │   └── OpenCv/                ←   模板匹配 / SIFT / 颜色检测
│   ├── Recorder/                  ← 宏录制与回放
│   ├── Script/                    ← V8 JS 脚本引擎
│   │   ├── Dependence/            ←   注入 JS 的 C# 对象
│   │   ├── Group/                 ←   脚本组调度
│   │   ├── Project/               ←   脚本项目管理
│   │   └── WebView/               ←   内嵌 WebView 地图编辑器
│   └── Simulator/                 ← 输入模拟门面
│       └── Extensions/            ←   GIActions 动作映射
├── GameTask/                      ← 所有自动化任务 ★★★
│   ├── TaskTriggerDispatcher.cs   ← 主循环调度器 (50ms)
│   ├── GameTaskManager.cs         ← 触发器注册管理
│   ├── TaskContext.cs             ← 运行时上下文 (单例)
│   ├── CaptureContent.cs          ← 捕获帧包装
│   ├── Common/                    ← 共享组件
│   │   ├── BgiVision/             ←   游戏 UI 状态检测
│   │   ├── Element/               ←   UI 元素检测
│   │   ├── Job/                   ←   17+ 可复用任务
│   │   ├── Map/                   ←   地图匹配系统
│   │   ├── StateMachine/          ←   通用有限状态机
│   │   └── TaskControl.cs         ←   静态捕获/延时辅助
│   ├── Model/                     ← 基础模型 (区域/坐标/UI)
│   │   └── Area/                  ←   坐标系统 (Desktop→Capture→Image→Region)
│   ├── AutoPick/                  ← 自动拾取
│   ├── AutoSkip/                  ← 自动跳过对话
│   ├── AutoFight/                 ← 自动战斗 (YOLO检测+战斗脚本)
│   ├── AutoDomain/                ← 自动秘境
│   ├── AutoFishing/               ← 自动钓鱼
│   ├── AutoWood/                  ← 自动伐木
│   ├── AutoCook/                  ← 自动烹饪
│   ├── AutoEat/                   ← 自动进食
│   ├── AutoGeniusInvokation/      ← 自动七圣召唤
│   ├── AutoMusicGame/             ← 自动音游
│   ├── AutoLeyLineOutcrop/        ← 自动地脉花
│   ├── AutoPathing/               ← 自动路径规划 (核心) ★★★
│   │   ├── Model/                 ←   Waypoint / PathingTask
│   │   ├── Handler/               ←   18 种动作处理器
│   │   ├── Navigation.cs          ←   地图定位门面
│   │   ├── PathExecutor.cs        ←   路径执行器 (1398行)
│   │   └── PathRecorder.cs        ←   路径录制
│   ├── AutoTrackPath/             ← 自动寻路 (旧版)
│   ├── AutoOpenChest/             ← 自动开箱
│   ├── AutoArtifactSalvage/       ← 自动圣遗物
│   ├── AutoStygianOnslaught/      ← 自动幽境危战
│   ├── QuickTeleport/             ← 快速传送
│   ├── QuickSereniteaPot/         ← 快速尘歌壶
│   ├── QuickBuy/                  ← 快速购买
│   ├── QuickForge/                ← 快速锻造
│   ├── GameLoading/               ← 自动进入游戏
│   ├── GetGridIcons/              ← 图标采集
│   ├── LogParse/                  ← 日志解析
│   ├── Macro/                     ← 宏回放
│   ├── MapMask/                   ← 地图蒙层
│   ├── SkillCd/                   ← 技能冷却显示
│   ├── UseRedeemCode/             ← 自动兑换码
│   ├── Shell/                     ← Shell 命令
│   ├── Placeholder/               ← 占位测试
│   ├── ChatUiHotkeyGuard/         ← 聊天热键保护
│   ├── TaskProgress/              ← 任务进度 UI
│   └── FarmingPlan/               ← 采集计划管理
├── Service/                       ← 应用服务层
│   ├── ConfigService.cs           ← 配置读写 (JSON)
│   ├── ApplicationHostService.cs  ← 应用生命周期
│   ├── UpdateService.cs           ← 版本更新检查
│   ├── ScriptService.cs           ← JS 脚本编排
│   ├── NotificationService.cs     ← 通知中心
│   └── Notifier/                  ← 15+ 通知渠道
├── Genshin/                       ← 原神特定集成
│   ├── Paths/                     ← 游戏路径检测
│   ├── Settings/                  ← 游戏设置读取 (注册表)
│   └── Settings2/                 ← 完整游戏设置模型
├── View/                          ← WPF 视图层
│   ├── MainWindow.xaml            ← 主窗口
│   ├── MaskWindow.xaml            ← 透明覆盖层
│   ├── HtmlMaskWindow.xaml        ← WebView2 覆盖层
│   ├── Pages/                     ← 15+ 页面
│   ├── Windows/                   ← 20+ 弹窗
│   └── Controls/                  ← 自定义控件
├── ViewModel/                     ← MVVM ViewModel
│   ├── Pages/                     ← 15+ 页面 VM
│   └── Windows/                   ← 弹窗 VM
├── Model/                         ← 领域模型
├── Helpers/                       ← 工具类集合
├── Hutao/                         ← 胡桃(第三方工具)集成
├── Wine/                          ← Wine/Linux 兼容
└── Resources/                     ← 静态资源
```

### 3.2 模块统计

| 类别 | 模块数 | 说明 |
|------|--------|------|
| 自动化任务 (GameTask) | 28 个 | 每种游戏功能一个子目录 |
| 触发器 (Trigger) | 9 个 | 每帧轮询的快速触发器 |
| 独立任务 (ISoloTask) | 19+ 个 | 长时间运行的有状态任务 |
| 可复用作业 (Job) | 17+ 个 | ReturnMainUi/SwitchParty/ClaimRewards 等 |
| 动作处理器 (ActionHandler) | 18 种 | 路径规划中的具体动作执行器 |
| 通知渠道 (Notifier) | 15+ 种 | 邮件/Bark/Telegram/Discord/钉钉等 |
| 配置模型 (Config) | 25+ 个 | JSON 持久化配置 |
| 地图区域 | 6 个 | Teyvat/Chasm/Enkanomiya 等 |
| ONNX 模型 | 11+ 个 | YOLO/PaddleOCR/SVTR/SileroVAD 等 |

---

## 4. 核心子系统深度分析

### 4.1 画面捕获子系统 (GameCapture)

#### 架构

```
IGameCapture (接口)
    ├── Start(hWnd, settings)
    ├── Capture() → Mat
    └── Stop()
           │
    ┌──────┼──────────────────────────┐
    ▼      ▼                          ▼
BitBltCapture           SharedSurfaceCapture       GraphicsCapture
(GDI BitBlt)            (DwmGetDxSharedSurface)    (WinRT GraphicsCapture)
                                                      │
                                              GraphicsCaptureHdr
                                              (HDR→SDR 转换)
```

#### 四种捕获模式

| 模式 | 技术 | 性能 | 适用场景 |
|------|------|------|----------|
| `BitBlt` | GDI `BitBlt` + DIB Section + 缓冲池 | 中等 | 兼容性最好，Win11 需注册表调整 |
| `WindowsGraphicsCapture` | WinRT `GraphicsCapture` API + D3D11 | 高 | Win10 1903+，标准选择 |
| `DwmGetDxSharedSurface` | DWM 共享 DX 表面 | 高 | 特殊场景 |
| `WindowsGraphicsCaptureHdr` | WinRT + HLSL Compute Shader HDR→SDR | 高 | HDR 显示器 |

#### 关键技术细节

- **BitBlt**: 使用 `CreateDIBSection` 分配 24-bit BGR 缓冲区，通过 `ConcurrentStack<IntPtr>` 实现缓冲池复用，避免频繁分配
- **GraphicsCapture**: 使用 `Direct3D11CaptureFramePool` + `FrameArrived` 事件，`ReaderWriterLockSlim` 保护帧缓存，限制 ~62 FPS
- **HDR 处理**: 自定义 HLSL Compute Shader (`HdrToSdrShader`) 将 R16G16B16A16Float 转换为 B8G8R8A8UIntNormalized
- **DPI 缩放**: `CaptureContent` 自动将捕获帧下降到 1080P 基准 (`DeriveTo1080P()`)

---

### 4.2 视觉识别子系统 (Recognition)

这是整个项目的**感知层核心**，包含三大类识别能力。

#### 4.2.1 识别类型体系

```csharp
enum RecognitionTypes {
    None,                   // 无
    TemplateMatch,          // 模板匹配 (OpenCV MatchTemplate)
    ColorMatch,             // 颜色匹配 (HSV/阈值范围)
    OcrMatch,               // OCR 文字匹配
    Ocr,                    // 纯 OCR
    ColorRangeAndOcr,       // 颜色范围 + OCR 组合
    Detect                  // YOLO 神经网络检测
}
```

#### 4.2.2 模板匹配 (Template Match)

**文件**: `Core/Recognition/OpenCv/MatchTemplateHelper.cs`

```
核心能力:
├── 单模板匹配:      一次匹配一个模板
├── 多模板匹配:      匹配多个不同模板 (字典驱动)
├── 单模板多实例:     找到一个后涂黑 → 继续找下一个
├── 子像素精化:      二次多项式拟合 3x3 邻域
├── 通道分离匹配:     分通道计算交叉相关 (用于小地图)
├── 掩码匹配:        支持 mask 的模板匹配
├── 匹配模式:        CCoeffNormed / SqDiff / CCoeff / CCorr
└── 稳定性检测:      降采样 320×180 + 连续 2 帧 > 0.98 NCC
```

**`RecognitionObject`** 配置模型支持:
- 模板图片路径 + 阈值
- 掩码模板
- 三通道/二值化匹配
- 彩色匹配 (颜色空间转换 + 上下界)
- OCR 匹配 (引擎选择 + 替换字典 + 正则)

#### 4.2.3 OCR 文字识别

**架构**:

```
IOcrService (接口)
    └── PaddleOcrService (实现)
            ├── Det.cs    ← ONNX 文本检测模型 (V4/V5)
            ├── Rec.cs    ← ONNX 文本识别模型 (多语言)
            └── OcrUtils  ← 张量预处理

ITextInference (接口)
    └── PickTextInference ← SVTR ONNX 模型 (拾取文本识别)
```

**PaddleOCR 流水线**:
1. `Det.Run(srcMat)` → 检测文本区域 (旋转矩形)
2. 裁剪每个区域
3. `Rec.Run(croppedMats)` → 识别文字内容
4. 组装 `OcrResult` (文本 + 位置 + 置信度)

**支持的语言**: 中文 / English / Latin / Eslav / Korean (通过 `FromCultureInfo()` 自动检测)

**SVTR 拾取文本**: 使用自定义训练的 `YapModelTraining` ONNX 模型，输入 (1,1,32,384) 张量 → argmax 解码 → 字典映射

#### 4.2.4 YOLO / ONNX 神经网络推理

**ONNX 运行时工厂** (`BgiOnnxFactory`):

```
提供者自动选择:
  GPU 模式: TensorRT (最快) → CUDA → DirectML → CPU
  专用模式: DirectML / OpenVINO / CPU
  
自动检测:
  - CUDA/cuDNN 路径 (注册表 + 环境变量)
  - TensorRT 引擎缓存
  - 优化模型缓存
```

**已注册的 ONNX 模型** (11+ 个):

| 模型 | 路径 | 用途 |
|------|------|------|
| `BgiFish` | `Assets/Model/Fish/` | 钓鱼检测 |
| `BgiTree` | `Assets/Model/Domain/` | 秘境树检测 |
| `BgiWorld` | `Assets/Model/World/` | 世界物体检测 |
| `BgiMine` | `Assets/Model/Mine/` | 矿物检测 |
| `BgiAvatarSide` | `Assets/Model/Common/` | 角色侧像分类 |
| `BgiQClassify` | `Assets/Model/Common/` | Q技能冷却分类 |
| `SileroVad` | `Assets/Model/Vad/` | 语音活动检测 |
| `PaddleOcrDetV4/V5` | `Assets/Model/PaddleOCR/Det/` | OCR 文本检测 |
| `PaddleOcrRecV4/V5+` | `Assets/Model/PaddleOCR/Rec/` | OCR 文字识别 |
| `YapModelTraining` | `Assets/Model/Yap/` | 拾取文本推理 |

#### 4.2.5 BgiVision 游戏视觉 API

分层设计:

```
Core/BgiVision/ (底层 API)
    ├── BvImage:    图片加载/保存
    ├── BvLocator:  UI 元素定位器 (FindAll/IsExist/Click/WaitFor)
    └── BvPage:     页面级操作 (Screenshot/Locator/GetByText/Ocr/Click)

GameTask/Common/BgiVision/ (游戏级 API)
    ├── BvImage.cs:      ImRead / WaitUntilFound / ClickUntilFound
    ├── BvOcr.cs:        FindFKeyText (F键交互文本)
    ├── BvStatus.cs:     WhichGameUi / IsInMainUi / IsInDomain / IsInBigMapUi
    ├── BvSimpleOperation.cs: FindAndClick / ClickConfirmButton / FindFAndPress
    ├── BvSkill.cs:      技能相关检测
    └── BvChatUi.cs:     聊天UI检测
```

**BvStatus 检测的游戏 UI 状态**:
- `Main` (主界面)
- `Talk` (对话界面)
- `BigMap` (大地图)
- `Domain` (秘境中)
- `PartyView` (队伍界面)
- `PromptDialog` (提示弹窗)
- `RevivePrompt` (复活提示)

#### 4.2.6 坐标系统 (区域层级)

```
DesktopRegion (绝对屏幕坐标)
    → GameCaptureRegion (游戏窗口像素坐标 + DPI缩放)
        → ImageRegion (图像数据 + 子区域裁剪)
            → Region (链式子区域)
```

所有区域支持:
- `Click()` / `Move()` / `Draw()` 操作
- 层级坐标转换 (父→子 / 子→父)
- `Find()` / `FindMulti()` 分派到模板匹配或 OCR

---

### 4.3 输入模拟子系统 (Input Simulation)

#### 三层架构

```
┌─────────────────────────────────────────────────────────┐
│            Simulation (静态门面)                         │
│   .SendInput       (全局 SendInput)                     │
│   .MouseEvent      (mouse_event API)                    │
│   .PostMessage(hWnd)  (后台 PostMessage)                │
│   .ReleaseAllKey()                                     │
└────────────────────┬────────────────────────────────────┘
                     │
    ┌────────────────┼────────────────────┐
    ▼                ▼                    ▼
InputSimulator    MouseEventSimulator   PostMessageSimulator
(前台 SendInput)  (旧版 mouse_event)    (后台 PostMessage)
    │
    ├── KeyboardSimulator
    │     └── InputBuilder → USER32.INPUT[] 构建
    ├── MouseSimulator
    └── WindowsInputDeviceStateAdaptor
          └── GetKeyState / GetAsyncKeyState
```

#### 关键特性

| 特性 | SendInput | PostMessage |
|------|-----------|-------------|
| 目标窗口 | 前台焦点窗口 | 指定 hWnd (后台) |
| 是否需要窗口激活 | 是 | 否 |
| 被反作弊检测风险 | 较低 | 较高 |
| 适用场景 | 普通操作 | 后台挂机 |

**动作映射系统** (`GIActions` → `KeyId`):

```csharp
// 60+ 游戏动作枚举
public enum GIActions {
    MoveForward, MoveBackward, MoveLeft, MoveRight,
    NormalAttack, ElementalSkill, ElementalBurst,
    SprintKeyboard, Jump, OpenMap, OpenInventory,
    OpenPartySetupScreen, SwitchMember1~5,
    QuickUseGadget, Interact, Drop, ...
}

// 通过 SimulateKeyHelper 映射到实际按键
SimulateKeyHelper.ToActionKey(GIActions.NormalAttack) → KeyId.LeftMouseButton
```

**键绑定配置** (`KeyBindingsConfig`): 将每个 `GIActions` 映射到物理按键 (`KeyId`)，支持从游戏注册表自动读取用户自定义按键。

**键鼠宏系统** (`Core/Recorder/`):
- `KeyMouseRecorder`: 录制键鼠事件为时间戳 `MacroEvent` 序列，支持鼠标移动数据压缩和摄像机朝向记录
- `KeyMouseMacroPlayer`: 回放录制的事件，支持分辨率/DPI 自适应和摄像机角度校正

#### 热键系统

**文件**: `Fischless.HotkeyCapture/HotkeyHook.cs`

使用 `RegisterHotKey` + WPF `NativeWindow` 的 `WM_HOTKEY` 消息循环实现全局热键。支持 35+ 个热键绑定:
- `F11` — 总开关
- `Ctrl+F1` — 自动寻路
- `Ctrl+F2` — 自动秘境
- `Ctrl+F3` — 自动战斗
- `Ctrl+F7` — 截图
- ...等

---

### 4.4 任务调度引擎 (Task Orchestration)

#### 双层任务模型

```
┌─────────────────────────────────────────────────────────┐
│               ITaskTrigger (帧级触发器)                   │
│  特点: 每帧快速执行, 无长时间阻塞, 优先级 + 独占          │
│  方法: Init() / OnCapture(CaptureContent)               │
│  属性: Priority / IsExclusive / IsBackgroundRunning      │
│        SupportedGameUiCategory                          │
│                                                         │
│  已注册触发器:                                           │
│    AutoPickTrigger, QuickTeleportTrigger,               │
│    AutoSkipTrigger, AutoFishingTrigger,                 │
│    AutoEatTrigger, MapMaskTrigger,                      │
│    SkillCdTrigger, GameLoadingTrigger, TestTrigger      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│               ISoloTask (独立任务)                        │
│  特点: 长时间运行, 有状态, CancellationToken 取消        │
│  方法: Start(CancellationToken)                         │
│                                                         │
│  已实现任务 (19+):                                       │
│    AutoFight, AutoDomain, AutoTrackPath, AutoMusicGame, │
│    AutoWood, AutoFishing, AutoGeniusInvokation,         │
│    AutoArtifactSalvage, AutoCook, AutoLeyLineOutcrop,   │
│    AutoOpenChest, AutoStygianOnslaught, OneKeyFight,    │
│    OneKeyExpedition, AutoAlbum, Shell, QuickBuy,        │
│    QuickForge, QuickSereniteaPot                        │
└─────────────────────────────────────────────────────────┘
```

#### TaskTriggerDispatcher 主循环

```
[Configurable Timer (default 50ms)]
           │
           ▼
    ┌──────────────┐
    │ 1. Capture() │  ← IGameCapture.Capture() → CaptureContent
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ 2. Sort()    │  ← 按优先级排序, 处理独占
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ 3. Dispatch()│  ← 遍历触发器, 检查:
    │              │     - IsEnabled?
    │              │     - IsExclusive? (阻塞其他)
    │              │     - IsBackgroundRunning?
    │              │     - GameUiCategory 匹配?
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ 4. OnCapture │  ← 每个触发器处理当前帧
    └──────────────┘
```

#### 有限状态机

**文件**: `GameTask/Common/StateMachine/StateMachineBase<TState, TContext>`

基于属性的通用 FSM:
```csharp
[StateDetector]  // 状态检测器 → 接收 ImageRegion, 返回 bool
bool DetectMainUi() { ... }

[StateHandler]   // 状态处理器 → 接收 TContext, 返回 StateHandlerResult
async Task<StateHandlerResult> HandleMainUi() { ... }

// 转换注册限制检测范围
RegisterStateTransition(from: StateA, toStates: [StateB, StateC]);
```

支持:
- 基于次数或超时的重试策略
- 属性自动扫描注册
- `Success` / `Wait` / `Retry` / `Fail` 结果

#### "一条龙" 编排系统

将多个脚本组/内置任务串联:
```
领取邮件 → 合成树脂 → 自动秘境 → 自动地脉花 → 领取奖励 → [完成后操作]
```

配置按星期几分队伍/国家/领域，支持完成后关机/休眠等操作。

---

### 4.5 路径规划与导航 (Pathfinding)

这是项目中**最复杂的子系统**之一。

#### 整体架构

```
┌──────────────────────────────────────────────────────────┐
│                      AutoPathing                          │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐   ┌────────────────────┐              │
│  │ Navigation   │   │ MapManager          │              │
│  │ (定位门面)   │   │ (地图注册中心)       │              │
│  │              │   │                     │              │
│  │ GetPosition()│   │ TeyvatMap           │              │
│  │ GetDistance()│   │ TheChasmMap          │              │
│  │ GetOrientation│  │ EnkanomiyaMap        │              │
│  └──────┬───────┘   │ AncientSacredMountain│              │
│         │           │ SeaOfBygoneEras      │              │
│         │           │ TempleOfSpace        │              │
│         │           └────────────────────┘              │
│         │                                               │
│  ┌──────┴───────────────────────────────────┐          │
│  │          PathExecutor (1398 行)           │          │
│  │                                           │          │
│  │  航点循环:                                │          │
│  │    1. 坐标转换 (图像↔游戏坐标)             │          │
│  │    2. 传送 (TpTask)                      │          │
│  │    3. 移动到目标 (W键 + 摄像机旋转)        │          │
│  │    4. 动作执行 (ActionFactory → Handler)  │          │
│  │    5. 异常处理 (陷阱/战斗/卡住/低血量)     │          │
│  └──────┬────────────────────────────────────┘          │
│         │                                               │
│  ┌──────┴───────────────────────────────────┐          │
│  │     ActionFactory (18 种动作处理器)        │          │
│  │                                           │          │
│  │  fight → AutoFightHandler                 │          │
│  │  combat_script → CombatScriptHandler      │          │
│  │  mining → MiningHandler                   │          │
│  │  fishing → FishingHandler                 │          │
│  │  nahida_collect → NahidaCollectHandler    │          │
│  │  pick_around → PickAroundHandler          │          │
│  │  normal_attack → NormalAttackHandler      │          │
│  │  elemental_skill → ElementalSkillHandler  │          │
│  │  hydro/electro/anemo/pyro_collect → ...   │          │
│  │  set_time → SetTimeHandler                │          │
│  │  exit_and_relogin → ExitAndReloginHandler │          │
│  │  ... (共 18 种)                           │          │
│  └───────────────────────────────────────────┘          │
└──────────────────────────────────────────────────────────┘
```

#### 地图定位系统

**定位方法**:
1. **小地图局部匹配**: 使用 `FastSqDiffMatcher` + `SubPixMatch` 在小地图区域定位玩家箭头
2. **大地图全局匹配**: 当局部匹配失败时回退到 SIFT 特征匹配 (`BigMapTeyvat256Layer`)
3. **坐标系统**: 每个地图区域有硬编码的游戏坐标网格 (Teyvat: 15行×22列, 2048px块)

**地图匹配方法**: 支持 `SIFT` 和 `TemplateMatch` 两种方式，通过 `{MapType}_{MatchingMethod}` 键注册。

#### 路径模型

```json
{
  "Info": {"Name": "璃月矿产路线", "Type": "Collect", "Map": "Teyvat", "Version": "5.0"},
  "Config": {"ContinuousMode": true},
  "Positions": [
    {"X": 1234.5, "Y": 5678.9, "Type": "Path", "MoveMode": "Walk"},
    {"X": 1250.0, "Y": 5700.0, "Type": "Target", "Action": "mining"},
    {"X": 1300.0, "Y": 5800.0, "Type": "Teleport", "Action": "teleport"}
  ]
}
```

航点类型: `Path` / `Target` / `Teleport` / `Orientation`
移动模式: `Walk` / `Run` / `Dash` / `Climb` / `Fly` / `Jump` / `Swim`
动作类型: 18 种 (采矿/钓鱼/采集/战斗/重登/设置时间...)

#### 异常处理 (PathExecutor)

- **陷阱逃脱**: 检测卡住 → `TrapEscaper` 执行跳跃/冲刺
- **低血量恢复**: 自动传送到七天神像
- **战斗**: 路径上遇到敌人 → 自动切换到战斗模式
- **飞行/攀爬/游泳**: 根据移动模式调整控制
- **摄像机校正**: `CameraRotateTask` 旋转摄像机朝向目标
- **失败重试**: 可配置的重试策略

---

### 4.6 脚本引擎 (Script Engine)

#### ClearScript V8 集成

```
┌────────────────────────────────────────────┐
│         V8ScriptEngine (ClearScript)       │
│                                            │
│  注入的 C# 宿主对象 (host objects):        │
│  ┌──────────────────────────────────────┐ │
│  │ genshin     ← 游戏控制 (战斗/移动)    │ │
│  │ key_mouse   ← 键鼠操作               │ │
│  │ log         ← 日志输出               │ │
│  │ http        ← HTTP 请求              │ │
│  │ dispatcher  ← 任务调度/截图/识别      │ │
│  │ notification ← 通知推送              │ │
│  │ autopathing ← 路径执行               │ │
│  │ postMessage ← 后台模拟输入            │ │
│  └──────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

#### 脚本仓库系统

**文件**: `ScriptRepoUpdater.cs` (2700 行)

支持多平台 Git 仓库 (GitHub / GitCode / CNB):
- 仓库克隆/更新/订阅
- 依赖解析
- 多频道支持
- 自动更新检查

#### 脚本组调度

```
ScriptGroup
  ├── ScriptGroupProject[]  (可执行项目列表)
  │     ├── type: "Javascript"     → V8 JS 脚本
  │     ├── type: "KeyMouse"       → 录制的宏回放
  │     ├── type: "Pathing"        → 地图路径追踪
  │     └── type: "Shell"          → Shell 命令
  ├── ScriptGroupConfig  (路径队伍/Shell 配置)
  └── 调度规则: 每日/每周 Cron 表达式
```

---

### 4.7 配置系统 (Configuration)

#### 配置架构

```
AllConfig (根配置, ObservableObject)
  ├── MaskWindowConfig       ← 覆盖层窗口
  ├── CommonConfig           ← 通用行为
  ├── GenshinStartConfig     ← 游戏启动
  ├── HardwareAccelerationConfig ← GPU 加速
  ├── HotKeyConfig           ← 35+ 快捷键
  ├── KeyBindingsConfig      ← 游戏按键映射
  ├── MacroConfig            ← 宏设置
  ├── OneDragonFlowConfig    ← 一条龙流程
  ├── ScriptConfig           ← 脚本仓库
  ├── PathingConditionConfig ← 队伍选择条件
  ├── PathingPartyConfig     ← 路径队伍
  ├── AutoPickConfig         ← 自动拾取
  ├── AutoSkipConfig         ← 自动跳过
  ├── AutoFishingConfig      ← 自动钓鱼
  ├── AutoFightConfig        ← 自动战斗
  ├── AutoDomainConfig       ← 自动秘境
  ├── AutoWoodConfig         ← 自动伐木
  ├── AutoCookConfig         ← 自动烹饪
  ├── AutoEatConfig          ← 自动进食
  ├── AutoMusicGameConfig    ← 自动音游
  ├── AutoLeyLineOutcropConfig ← 自动地脉花
  ├── AutoArtifactSalvageConfig ← 自动圣遗物
  ├── AutoStygianOnslaughtConfig ← 自动幽境危战
  ├── AutoRedeemCodeConfig   ← 自动兑换码
  ├── QuickTeleportConfig    ← 快速传送
  ├── TpConfig               ← 传送配置
  ├── MapMaskConfig          ← 地图蒙层
  ├── SkillCdConfig          ← 技能冷却
  ├── NotificationConfig     ← 通知配置
  ├── DevConfig              ← 开发者选项
  └── ...更多
```

#### 持久化

- **存储**: `User/config.json` (JSON 格式)
- **线程安全**: `ReaderWriterLockSlim` + `lock`
- **自动备份**: 损坏文件自动备份到 `User/backup/config_yyyyMMdd_HHmmss_fff.json.bak`
- **自定义序列化**: OpenCV `Rect`/`Point` 的 JSON 转换器
- **热重载**: 通过 `ObservableObject.PropertyChanged` 实时生效

#### 游戏设置同步

自动从 Windows 注册表读取原神设置:
```
HKEY_CURRENT_USER\Software\miHoYo\Genshin Impact\GENERAL_DATA
  → 分辨率 / 语言 / 按键绑定 / 画质 / HDR / 控制器映射 / ...
```

---

### 4.8 通知联动 (Notification)

#### 架构

```
INotifier (接口)
    └── SendAsync(BaseNotificationData) → Task

NotifierManager (门面)
    ├── EmailNotifier          (SMTP 邮件)
    ├── BarkNotifier           (iOS Bark)
    ├── TelegramNotifier       (Telegram Bot)
    ├── DiscordWebhookNotifier (Discord Webhook)
    ├── FeishuNotifier         (飞书)
    ├── WorkWeixinNotifier     (企业微信)
    ├── DingDingWebhook        (钉钉)
    ├── ServerChanNotifier     (Server酱)
    ├── WebhookNotifier        (通用 Webhook)
    ├── WebSocketNotifier      (WebSocket)
    ├── OneBotNotifier         (OneBot)
    ├── XxtuiNotifier          (XXTUI)
    ├── WindowsUwpNotifier     (Windows Toast)
    └── ...
```

---

## 5. 移动端移植可行性评估

### 5.1 用户描述的方案

```
移动设备 (游戏运行) ←→ 投屏/连接桥 ←→ PC (算力中心)

┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│ 手机/平板    │────→│ 采集卡 / scrcpy  │────→│  PC 视觉识别  │
│ (原神/游戏)  │     │ 画面传到 PC      │     │  决策        │
│             │←────│                  │←────│              │
│             │     │ HDC / ADB        │     │  输入指令    │
│             │     │ 机械手 / USB     │     │              │
└─────────────┘     └──────────────────┘     └──────────────┘
```

### 5.2 方案分层分析

#### 第一层: 画面输出 (可行性: ★★★★☆ 高)

| 方案 | 延迟 | 画面质量 | 稳定性 | 复杂度 |
|------|------|----------|--------|--------|
| **scrcpy** | ~35-70ms | 1080P H.264 | ★★★★ | 低 |
| **HDMI 采集卡** | <10ms | 4K 无损 | ★★★★★ | 中 |
| **USB 采集卡 (UVC)** | <20ms | 1080P MJPEG | ★★★★ | 低 |
| **无线投屏 (AirPlay/Miracast)** | 100-500ms | 不稳定 | ★★ | 极低 |
| **云游戏客户端** | 30-80ms | 依赖网络 | ★★★ | 极低 |

**推荐**: scrcpy (USB 连接) — 延迟可控，零成本，画面质量足够视觉识别。采集卡方案延迟最低，适合对实时性要求极高的场景 (如战斗)。

**BetterGI 适配点**: `IGameCapture` 接口已做抽象，只需新增一个 `ScrcpyCapture` / `VideoCaptureDevice` 实现即可。

#### 第二层: 视觉识别 (可行性: ★★★★☆ 高)

**优势**:
- BetterGI 的所有视觉识别都在 PC 端执行 (OpenCV/ONNX)，手机仅提供画面
- 识别管线与输入来源解耦 (`IGameCapture` → `CaptureContent` → `ImageRegion`)
- 1080P 基准分辨率已固定，`DeriveTo1080P()` 缩放逻辑现成
- YOLO/PaddleOCR 等模型与游戏平台无关

**挑战**:
- **UI 比例差异**: 移动端 UI 布局与 PC 不同 (按钮位置/大小/比例) → 需要重新制作模板图片资产
- **渲染差异**: 移动端使用不同的画质设置和光照模型 → 模板匹配阈值需重新标定
- **分辨率变化**: 手机分辨率多样 (720P/1080P/1440P, 不同宽高比) → 需要更鲁棒的缩放和匹配策略
- **编码伪影**: scrcpy 的 H.264 压缩引入伪影 → 可能需要增强图像预处理 (降噪/锐化)

**改造工作量**: 中等。需要:
1. 新增 `IGameCapture` 实现 (~200 行)
2. 重新制作全套模板图片资产 (数百张 PNG，耗时最大)
3. 调整匹配阈值和 ROI 区域配置

#### 第三层: 输入控制 (可行性: ★★★☆☆ 中等)

| 方案 | 延迟 | 可靠性 | 反检测 | 复杂度 |
|------|------|--------|--------|--------|
| **ADB `input tap/swipe`** | 50-200ms | ★★★ | 低 | 低 |
| **ADB `sendevent`** | 20-50ms | ★★★ | 中 | 中 |
| **scrcpy 反向控制** | ~50ms | ★★★★ | 中 | 低 |
| **机械手** | 100-500ms | ★★☆ | 极高 | 极高 |
| **USB HID 触摸模拟** | <5ms | ★★★★★ | 高 | 高 |
| **Android AccessibilityService** | <20ms | ★★★★ | 低 (易检测) | 中 |

**推荐**: scrcpy 反向控制 + ADB 命令。scrcpy 天然支持鼠标点击 → 手机触摸的转换。ADB 作为备选处理复杂手势 (多点触控/滑动)。

**BetterGI 适配点**: 需要在 `Simulation` 下新增移动端输入实现:
- `AndroidInputSimulator` (通过 ADB/scrcpy 发送触控事件)
- 触摸坐标映射 (PC 屏幕坐标 → 手机屏幕坐标，考虑 DPI 差异)

**改造工作量**: 中等偏高。核心是把 PC 的键盘/鼠标操作映射为移动端的触控/手势操作:
- `MoveForward` → 虚拟摇杆上推
- `NormalAttack` → 点击攻击按钮
- `ElementalSkill` → 点击技能按钮
- 摄像机旋转 → 滑动屏幕右半区
- 这些映射依赖游戏的具体 UI 布局

#### 第四层: 游戏特定适配 (可行性: ★★☆☆☆ 低-中)

**核心问题**: BetterGI **深度绑定**《原神》PC 版的 UI 布局和交互方式:

| 耦合点 | PC 版 | 移动版 | 适配难度 |
|--------|-------|--------|----------|
| 操作方式 | 键盘 + 鼠标 | 触摸屏 + 虚拟摇杆 | ★★★★ 高 |
| UI 布局 | 16:9 横屏, 固定位置 | 可变比例, 自适应布局 | ★★★ 中 |
| 按键映射 | 30+ 键位 (WASD/E/Q/F...) | 虚拟按钮 (位置不固定) | ★★★★ 高 |
| 菜单交互 | 键盘快捷键 | 触屏导航 | ★★★ 中 |
| 小地图 | 固定左上角 | 固定但比例不同 | ★★ 低 |
| 模板图片 | 数百张 PC 1080P 截图 | 需全部重新截取 | ★★★ 中 |
| 游戏进程检测 | 读注册表/Win32 API | 需改为 ADB 检测 | ★ 极低 |

**结论**: 对于《原神》本身，移动端移植是**可行但工作量大**的，因为你本质上是在做一个"更困难"的事情——移动端没有稳定的键鼠接口，一切依赖视觉识别定位虚拟按钮。

### 5.3 总体评估

| 维度 | 评分 (1-5) | 说明 |
|------|-----------|------|
| 画面捕获 | ★★★★☆ | scrcpy/采集卡方案成熟 |
| 视觉识别 | ★★★★☆ | 核心算法可复用，需重做模板 |
| 输入模拟 | ★★★☆☆ | HDC/ADB + 机械手方案可行，延迟和可靠性需优化 |
| 游戏适配 | ★★☆☆☆ | 原神移动端 UI 差异大，工作量大 |
| 整体可行性 | ★★★☆☆ | **技术上可行，工程量大** (~3-6 人月) |

### 5.4 推荐实施方案

```
Phase 1: 基础框架 (2-3 周)
  ├── 实现 ScrcpyCapture (IGameCapture)
  ├── 实现 AndroidTouchSimulator (触控模拟)
  ├── 坐标系统适配 (PC → Mobile DPI 映射)
  └── 基础模板重采集

Phase 2: 核心功能移植 (4-6 周)
  ├── 自动拾取 (重做模板 + 触控映射)
  ├── 自动战斗 (重做 YOLO 检测区域 + 技能触控位置)
  ├── 自动秘境 (重做 UI 检测逻辑)
  └── 自动跳过 (重做对话检测模板)

Phase 3: 高级功能 (3-4 周)
  ├── 自动路径规划 (重做小地图定位 + 触控移动)
  ├── 自动钓鱼 (重做钓鱼 UI 检测)
  └── 其他模块

总计: 10-14 周 (2.5-3.5 人月)
```

---

## 6. 跨游戏适配扩展性评估

### 6.1 BetterGI 的耦合分析

#### 高耦合层 (游戏特定)

```
GameTask/* (28 个模块)           ← 100% 原神特定
  └── 每个任务都依赖原神 UI 结构/游戏机制

Genshin/* (游戏设置读取)         ← 100% 原神特定
  └── 注册表路径/进程名/配置文件

BgiVision (游戏视觉 API)         ← 80% 通用, 20% 原神特定
  └── BvStatus 中检测的具体 UI 状态

Core/Recognition/ONNX/           ← 模型是游戏特定的
  └── BgiFish/BgiTree/BgiMine 等模型的训练数据

GameTask/Common/Map/             ← 地图坐标系统是原神特定的
  └── 6 个区域的坐标网格常量

Assets (模板图片)                ← 数百张原神 UI 截图

GIActions (动作枚举)            ← 原神按键体系
```

#### 中耦合层 (可参数化)

```
Core/Config/*                    ← 配置结构通用, 内容游戏特定
Core/Simulator/Extensions/       ← 动作映射可配置
KeyBindingsConfig                ← 按键绑定可配置
HotKeyConfig                     ← 热键配置通用
```

#### 低耦合层 (通用可复用)

```
Fischless.GameCapture            ← 通用 Windows 画面捕获
Fischless.WindowsInput           ← 通用键鼠模拟
Fischless.HotkeyCapture          ← 通用热键注册
Core/Recognition/                ← 通用视觉识别引擎
  ├── OpenCv/MatchTemplate       ← 通用模板匹配
  ├── OpenCv/Contours             ← 通用轮廓检测
  ├── OCR/PaddleOCR               ← 通用 OCR
  └── ONNX/BgiOnnxFactory        ← 通用 ONNX 运行时
Core/Recorder/                   ← 通用宏录制
Core/Script/                     ← 通用 JS 脚本引擎
StateMachine/*                   ← 通用有限状态机
Service/Notifier/*               ← 通用通知系统
```

### 6.2 重构为通用框架的方案

#### 理想架构

```
┌─────────────────────────────────────────────────────────┐
│               GameAuto Framework (通用层)                │
│                                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │Capture   │ │Recognition│ │Input     │ │Orchestration│ │
│  │Interface │ │Engine    │ │Simulator │ │Engine       │ │
│  │          │ │          │ │          │ │             │ │
│  │- BitBlt  │ │- Template│ │- Key/Mouse│ │- Trigger   │ │
│  │- DXGI    │ │- OCR     │ │- Touch   │ │- FSM       │ │
│  │- scrcpy  │ │- YOLO    │ │- Gamepad │ │- Script(V8)│ │
│  │- Video   │ │- Color   │ │- ADB     │ │- Macro     │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
│                                                         │
├─────────────────────────────────────────────────────────┤
│              游戏适配层 (Per-Game Plugin)                 │
│                                                         │
│  ┌────────────┐  ┌────────────┐  ┌───────────────────┐ │
│  │GenshinImpact│  │Delta Force │  │Any Other Game... │ │
│  │Adapter      │  │Adapter     │  │                   │ │
│  │             │  │            │  │                   │ │
│  │- UI Assets  │  │- UI Assets │  │                   │ │
│  │- Actions    │  │- Actions   │  │                   │ │
│  │- Tasks      │  │- Tasks     │  │                   │ │
│  │- Maps       │  │- Maps      │  │                   │ │
│  │- Models     │  │- Models    │  │                   │ │
│  └────────────┘  └────────────┘  └───────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

#### 通用化所需的重构

| 组件 | 当前状态 | 通用化改造 |
|------|----------|-----------|
| **IGameCapture** | 仅 Win32 窗口捕获 | + scrcpy + 采集卡 + 视频文件输入 |
| **Recognition** | 通用引擎 ✅ | 无需改动 (已是通用) |
| **Input Simulation** | 仅 SendInput/PostMessage | + ADB 触控 + 手柄模拟 + 虚拟驱动 |
| **Task System** | 与 GIActions 耦合 | 抽象 `IGameActions` 接口, 每个游戏实现自己的动作集 |
| **Config** | 与 Genshin Config 耦合 | 抽取基础配置, 游戏配置作为插件化扩展 |
| **BgiVision** | 混合通用+游戏特定 | 分离: 通用视觉 API + 游戏 UI 描述文件 |
| **Map System** | 硬编码原神地图 | 抽象 `IGameMap` 接口, 支持自定义坐标系统 |
| **模板资产** | Hard-coded paths | 插件化资源包, 按游戏组织 |
| **ONNX Models** | 混合通用+游戏特定 | 通用模型 (OCR) 保留, 游戏模型按插件加载 |

### 6.3 以三角洲行动 (Delta Force) 为例

#### 三角洲行动的特点

| 特性 | 原神 | 三角洲行动 | 差异 |
|------|------|-----------|------|
| 游戏类型 | 开放世界 ARPG | FPS 战术射击 | 根本不同 |
| 视角 | 第三人称 | 第一人称 | 视觉识别策略完全不同 |
| 操作 | 技能+普攻+切换 | 射击+瞄准+战术装备 | 动作映射差异大 |
| 自动化需求 | 采集/战斗/探索 | 瞄准辅助/物资搜索/撤离 | 需求不同 |
| UI 布局 | RPG 风格 HUD | 军事风格 HUD | 需全新 UI 模板 |
| 视觉特征 | 清晰 UI 按钮 | 低对比度战术界面 | 模板匹配难度更高 |
| 反作弊 | 中等 (内核级反作弊但容忍辅助) | 极高 (FPS 游戏零容忍) | **关键风险** |

#### 适配难度分析

| 维度 | 难度 | 说明 |
|------|------|------|
| 画面捕获 | ★☆☆☆☆ | 与平台无关，DXGI/WinRT 同样适用 |
| 通用视觉识别 | ★★☆☆☆ | 模板匹配/OCR/颜色检测同可用于三角洲 |
| YOLO 目标检测 | ★★★★☆ | 需重新训练模型 (敌人/物资/撤离点) |
| 输入模拟 | ★★★★☆ | FPS 需要更精确的鼠标移动控制 (压枪/跟枪/瞄准) |
| UI 识别 | ★★★☆☆ | 需重新制作模板和管理 ROI 配置 |
| 游戏逻辑 | ★★★★★ | 从 RPG 到 FPS，任务/状态/流程完全不同 |
| **反作弊** | **★★★★★** | **FPS 反作弊极度严格，高风险** |

#### 适配工作量估算

| 任务 | 工作量 | 说明 |
|------|--------|------|
| 通用框架抽取 | 4-6 周 | 将 BetterGI 重构为 GameAuto Framework |
| 三角洲 UI 模板制作 | 2-3 周 | 数百个 UI 元素的截图和标注 |
| 敌人物资检测模型 | 3-6 周 | YOLO 训练数据的收集/标注/训练/验证 |
| 射击辅助逻辑 | 4-8 周 | 瞄准/压枪/跟枪算法 (需考虑游戏物理引擎) |
| 任务编排 | 2-4 周 | 针对三角洲的特定任务流程 |
| **总计** | **15-27 周 (4-7 人月)** | |

### 6.4 跨游戏适配通用性总结

| 游戏类型 | 适配难度 | 主要挑战 | 需改造的模块 |
|----------|----------|----------|-------------|
| **开放世界 RPG** (类似原神) | ★★☆☆☆ | UI 布局差异 | UI 模板 + 动作映射 |
| **MMORPG** | ★★★☆☆ | 复杂 UI + 社交交互 | UI 模板 + 自定义任务 |
| **卡牌/回合制** | ★★☆☆☆ | 回合逻辑 | 状态检测 + OCR |
| **音游** | ★★★★☆ | 毫秒级时机检测 | 音频分析 + 低延迟输入 |
| **FPS (三角洲)** | ★★★★★ | 反作弊 + 精确操控 + 3D 视觉 | 几乎全部 |
| **MOBA** | ★★★★☆ | 复杂决策 + 实时性 | AI 决策 + 小地图分析 |
| **模拟经营** | ★★☆☆☆ | 资源管理逻辑 | UI 模板 + 流程编排 |

### 6.5 关键结论

**BetterGI 的架构对同类型 RPG 游戏有较好的扩展性**。核心的捕获-识别-模拟闭环是通用的，但在以下维度上对每个游戏都需要大量定制:

1. **UI 模板资产**: 每个游戏都是全新的 (~2-3 周)
2. **操作动作映射**: 从按键→游戏逻辑的映射不同 (~1-2 周)
3. **游戏特定逻辑**: 任务/状态/流程完全不同 (~2-4 周)
4. **AI 模型**: YOLO 等模型需要针对游戏重新训练 (~3-6 周)

**FPS 游戏 (如三角洲行动) 的适配是最困难的**，因为:
- 反作弊系统对输入注入极为敏感
- 需要亚像素级的精确鼠标控制
- 3D 环境的视觉识别比 2D UI 难得多
- 战术决策需要更复杂的 AI

---

## 7. 总结与建议

### 7.1 项目品质评估

| 维度 | 评分 | 评语 |
|------|------|------|
| 架构设计 | ★★★★★ | MVVM + DI + 接口抽象, 分层清晰 |
| 代码质量 | ★★★★☆ | CommunityToolkit 源码生成器, 良好的模式应用 |
| 视觉识别 | ★★★★★ | 多种识别方法, GPU 加速, 在线+离线模型 |
| 输入系统 | ★★★★★ | 三种模拟方式, 后台输入, 动作映射抽象 |
| 任务引擎 | ★★★★★ | 触发器/SoloTask 双层, FSM, 脚本编排 |
| 可扩展性 | ★★★☆☆ | 对原神深度优化, 跨游戏需重构 |
| 文档 | ★★☆☆☆ | 中文社区，缺少架构文档 |
| **综合** | **★★★★☆** | **优秀的桌面游戏自动化项目** |

### 7.2 核心优势

1. **完整闭环**: 捕获→识别→决策→模拟全链路
2. **GPU 加速推理**: TensorRT/DirectML/CUDA 多后端
3. **多种捕获方式**: GDI/DWM/WinRT 适配各种 Windows 环境
4. **脚本化扩展**: V8 JS 引擎 + Git 仓库系统
5. **丰富的通知**: 15+ 种通知渠道
6. **健壮的路径规划**: 多区域地图 + SIFT 匹配 + 异常恢复

### 7.3 可改进方向

1. **游戏解耦**: 将原神特定代码从通用引擎中分离
2. **插件化**: 将游戏适配作为独立 DLL/包加载
3. **模板资产管理**: 使用声明式配置文件描述 UI 元素 (替代硬编码)
4. **测试覆盖**: 增加视觉识别单元测试
5. **文档化**: 编写架构文档和贡献指南

### 7.4 移植/扩展建议

**短期 (1-2 个月)**: 保持原神专注，优化现有功能
**中期 (3-6 个月)**: 抽取通用框架，适配 1-2 个类似 RPG 游戏验证扩展性
**长期 (6-12 个月)**: 建立游戏适配 SDK，支持社区贡献游戏插件

---

> **报告生成时间**: 2026-06-01
> **分析方法**: 静态代码分析 + 架构推理
> **代码量**: 约 150,000+ 行 C# 代码，6 个项目
