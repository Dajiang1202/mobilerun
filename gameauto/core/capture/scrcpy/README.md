# Scrcpy 子模块

基于 HOScrcpy SDK (华为鸿蒙投屏工具) 的低延迟截屏与触控模块。通过 gRPC H.264 视频流替代 hdc shell `snapshot_display` 子进程调用，将截图延迟从 **~500ms 降至 ~17ms** (约 30 倍提升)。

## 快速开始

```bash
# 独立预览
python example.py --demo 3

# 基础截图
python example.py --demo 1
```

```python
from gameauto.core.capture.scrcpy import ScrcpyCapture

capture = ScrcpyCapture("设备序列号", "gameauto/resource/hosScrcpy-1.0.15-beta.jar")
await capture.connect()
png = await capture.screenshot()  # ~17ms
```

完整可配置示例见 [example.py](example.py)。

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        Python 层                                  │
│                                                                    │
│  ┌──────────────────────┐  ┌──────────────────────┐              │
│  │  ScrcpyCapture        │  │  ScrcpyInput          │              │
│  │  (BaseCapture)        │  │  (BaseInput)           │              │
│  │  screenshot() → PNG   │  │  tap() / swipe()       │              │
│  └──────────┬───────────┘  └───────────┬────────────┘              │
│             │                          │                            │
│  ┌──────────▼──────────────────────────▼────────────┐              │
│  │  bridge.py  (JPype 桥接层)                        │              │
│  │                                                    │              │
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────┐ │              │
│  │  │ JVM 管理     │  │ 视频流解码    │  │ 触控注入  │ │              │
│  │  │ (jpype)     │  │ (PyAV+OpenCV) │  │ (SDK API) │ │              │
│  │  └──────┬──────┘  └──────┬───────┘  └────┬─────┘ │              │
│  └─────────┼────────────────┼───────────────┼───────┘              │
│            │                │               │                       │
└────────────┼────────────────┼───────────────┼──────────────────────┘
             │                │               │
      ┌──────▼──────┐  ┌──────▼──────┐ ┌──────▼──────┐
      │  HOScrcpy   │  │  gRPC 视频流 │ │ uitest 触控  │
      │  SDK JAR    │  │  H.264 NAL  │ │  socket      │
      │  (Java)     │  │  proto 格式  │ │  二进制协议   │
      └──────┬──────┘  └──────┬──────┘ └──────┬──────┘
             │                │               │
      ┌──────▼────────────────▼───────────────▼──────┐
      │            hdc port forward                    │
      │  fport tcp:X localabstract:scrcpy_grpc_socket │
      │  fport tcp:Y localabstract:uitest_socket      │
      └──────────────────────┬────────────────────────┘
                             │ USB / WiFi
      ┌──────────────────────▼────────────────────────┐
      │              鸿蒙设备                           │
      │  ┌──────────────┐  ┌────────────────────────┐ │
      │  │ uitest daemon │  │ scrcpy agent (.so)      │ │
      │  │ (系统预装)     │  │ (SDK 自动推送)           │ │
      │  │ 触控注入       │  │ H.264 编码 + gRPC 推流   │ │
      │  └──────────────┘  └────────────────────────┘ │
      └────────────────────────────────────────────────┘
```

## 数据流

### 截图路径

```
设备屏幕
  → scrcpy agent H.264 硬件编码
  → gRPC Server Streaming (ReplyMessage.data)
  → hdc fport → PC TCP socket
  → JPype JProxy.onData(ByteBuffer)
  → PyAV codec.parse() → codec.decode()
  → PIL/OpenCV YUV→BGR 转换
  → FrameQueue(2) 自动丢弃旧帧
  → screenshot() 返回最新帧 PNG ~17ms
```

### 触控路径

```
tap(x, y)
  → bridge.touch(px, py)
  → JPype → _device.onTouchDown/Up(x, y)
  → uitest_socket 二进制协议
  → 设备 uitest daemon → 系统触控注入
  ~0.01ms
```

## 为什么比 hdc 截图快

| 维度 | hdc shell 方案 | scrcpy 方案 |
|------|---------------|-------------|
| **通信方式** | 每次新建子进程 `hdc shell snapshot_display` | 持久 TCP 连接 |
| **进程开销** | Python→hdc→daemon→shell→uitest (4 跳) | Python→SDK→Agent (2 跳) |
| **数据格式** | JPEG 文件 (全量编码→传输→解码) | H.264 视频流 (增量 P/B 帧) |
| **编码** | 每次全量 JPEG 编码 (~200ms) | 硬件 H.264 编码 (~5ms) |
| **传输** | hdc file recv 拉取文件 (~100ms) | gRPC 流式推送 |
| **解码** | PIL JPEG 解码 (~50ms) | PyAV H.264 解码 (~10ms) |
| **子进程启动** | ~100ms/次 | 0 (一次启动) |

## 性能实测

测试设备: HarmonyOS NEXT, uitest 6.0.2.1, 1276×2848

| 操作 | hdc 方案 | scrcpy 方案 | 提升 |
|------|---------|------------|------|
| 截图 (720p) | ~509ms | **~17ms** | **30x** |
| 截图 (360p) | ~509ms | **~14ms** | **36x** |
| 截图 (原分辨率) | ~509ms | **~19ms** | **27x** |
| 点击 | ~517ms | **~0.01ms** | **50,000x** |
| 滑动 (200ms) | ~517ms+ | ~203ms | 2.5x |
| 视频流帧率 | 不支持 | **58 FPS** | ∞ |

## 文件结构

```
core/capture/scrcpy/
├── __init__.py       # 公开 API (ScrcpyCapture, ScrcpyConfig)
├── bridge.py         # JPype 桥接层 (JVM + 视频流 + 触控)
├── capture.py        # ScrcpyCapture(BaseCapture)
├── config.py         # ScrcpyConfig 数据类 + 分辨率预设
├── decoder.py        # H264Decoder (独立可用)
├── exceptions.py     # 异常层次
├── example.py        # 快速调用示例 (可直接运行)
└── README.md         # 本文档

core/input/
└── scrcpy.py         # ScrcpyInput(BaseInput)

resource/
└── hosScrcpy-1.0.15-beta.jar  # SDK JAR (上库备份)
```

## SDK JAR 更新适配

HOScrcpy SDK 是华为发布的闭源 JAR，托管在 [GitCode/OpenHarmonyToolkitsPlaza/HOScrcpy](https://gitcode.com/OpenHarmonyToolkitsPlaza/HOScrcpy)。

### 更新步骤

1. **下载新版 JAR**
   ```bash
   # 从 GitCode 下载最新 release
   ```

2. **替换 JAR 文件**
   ```bash
   cp hosScrcpy-2.x.x-beta.jar gameauto/resource/hosScrcpy-2.x.x-beta.jar
   ```

3. **更新配置** (`~/.gameauto/settings.yaml` 或 `default.yaml`):
   ```yaml
   device:
     scrcpy:
       sdk_jar: "gameauto/resource/hosScrcpy-2.x.x-beta.jar"
   ```

4. **验证 API 兼容性**
   - `jpype.startJVM(classpath=[新jar])` 启动成功
   - `HosRemoteDevice(HosRemoteConfig(serial))` 创建正常
   - `startCaptureScreen(callback)` 回调收到 H.264 数据
   - `onTouchDown/Up/Move` 注入正常

### 可能的 Breaking Changes

| 变更 | 影响 | 修复位置 |
|------|------|---------|
| 类名/包名变化 | import 失败 | [bridge.py](bridge.py) `_start_device()` |
| gRPC proto 字段变化 | 视频流解码异常 | 通常不影响，SDK 封装了 proto |
| config 参数变化 | bitrate/scale 不生效 | [bridge.py](bridge.py) `_start_device()` |
| callback 接口变化 | Proxy 创建失败 | [bridge.py](bridge.py) `_start_stream()` |

### 版本兼容记录

| SDK 版本 | uitest 最低版本 | 状态 | 备注 |
|----------|---------------|------|------|
| 1.0.15-beta | 6.0.2.1 | ✅ 测试通过 | 当前版本 |
| 1.0.14-beta | 5.1.1.3 | 未测试 | 旧版 |

## 依赖

```
jpype>=1.5                # Java-Python 桥接
av>=12                    # PyAV (FFmpeg H.264 解码)
opencv-python>=4.8        # 图像处理和预览
numpy>=1.24               # 数组计算
```

安装: `pip install jpype1 av opencv-python numpy`

## 故障排查

| 症状 | 原因 | 解决 |
|------|------|------|
| `JVM not found` | 未安装 JDK/JRE | 安装 DevEco Studio 或 OpenJDK |
| `Stream not ready` | 设备 uitest 版本过低 | 升级鸿蒙系统 |
| `UNAVAILABLE: Network closed` | gRPC 通道异常 | 重启设备 uitest daemon |
| `No frame available` | 视频流未启动 | 检查 `init()` 是否调用了 `startCaptureScreen` |
| `No module named 'jpype'` | 未安装依赖 | `pip install jpype1 av opencv-python numpy` |
