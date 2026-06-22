# ohscrcpy

> HarmonyOS (鸿蒙) 设备的低延迟截屏与触控 —— 基于 HOScrcpy SDK。

通过 gRPC H.264 视频流替代 `hdc shell snapshot_display` 子进程调用，把截图延迟从 **~500ms 降到 ~17ms** (约 30 倍)，点击延迟 **~0.01ms**。从 `gameauto` 框架里独立出来的轻量包，零业务依赖，开箱即用。

## 快速开始

```bash
pip install -r requirements.txt
```

改 `quickstart.py` 顶部配置区的 `DEVICE_SERIAL` 和 `SDK_JAR`，然后：

```bash
python quickstart.py            # 跑截屏/点击/连点/滑动全部示例
python quickstart.py preview    # 实时预览 (按 q 退出, 按 s 截图)
```

最小代码：

```python
from ohscrcpy import Device

dev = Device(serial="YOUR_SN", sdk_jar="hosScrcpy-1.0.15-beta.jar")
dev.connect()

w, h = dev.resolution               # 原生分辨率
dev.click(w // 2, h // 2)            # 单击屏幕中心
dev.multi_click(w // 2, h // 2, times=5, interval=0.1)  # 连点 5 次
dev.swipe(w // 2, int(h*0.8), w // 2, int(h*0.2), duration_ms=400)  # 上滑
dev.save_screenshot("shot.png")     # 截图存盘

dev.close()
```

上下文管理器写法 (自动 close)：

```python
with Device(serial="YOUR_SN", sdk_jar="hosScrcpy-1.0.15-beta.jar") as dev:
    dev.click(500, 1000)
```

## 坐标约定

**全部坐标使用原生像素空间** (`dev.resolution` 返回值)。例如设备原生 1276×2848，那么 `(638, 1424)` 就是屏幕中心。

- `scale` 只影响**截图输出尺寸**，不影响点击/滑动坐标。
- 想要点击坐标带轻微随机抖动 (模拟人手)，构造时传 `jitter=3`。

## Device API

| 方法 | 说明 |
|------|------|
| `connect()` / `close()` | 连接设备 / 停止流并关 JVM (支持 `with`) |
| `resolution` | 原生分辨率 `(w, h)` —— 触控坐标空间 |
| `output_resolution` | 缩放后输出分辨率 —— 截图帧尺寸 |
| `screenshot(wait_new=True)` | 最新帧 PNG bytes |
| `screenshot_bgr()` | 最新帧 BGR numpy 数组 (OpenCV 友好) |
| `save_screenshot(path)` | 截图存盘 |
| `click(x, y, duration_ms=50)` | 单击 |
| `long_press(x, y, duration_ms=1000)` | 长按 |
| `multi_click(x, y, times=5, interval=0.1)` | 连点 |
| `swipe(x1,y1,x2,y2,duration_ms=500)` | 滑动 |

低层桥接函数 (`init / shutdown / screenshot / touch / swipe …`) 也在 `ohscrcpy` 包顶层导出，见 `ohscrcpy.bridge`。

## 部署依赖

### Python 包 (`pip install -r requirements.txt`)

| 包 | 作用 |
|----|------|
| `jpype1>=1.5` | Java-Python 桥接，启动 JVM 加载 SDK JAR |
| `av>=12` | PyAV / FFmpeg H.264 视频流解码 |
| `opencv-python>=4.8` | RGBA→BGR 转换 + 预览窗口 |
| `numpy>=1.24` | 像素数组 |

### 运行环境 (无法 pip 安装)

1. **JDK / JRE** —— 需包含 `jvm.dll`
   - 推荐 DevEco Studio 自带 JBR，或任意 OpenJDK 11+
   - 设置 `JAVA_HOME` 环境变量，或传 `Device(java_home="...")`
2. **HOScrcpy SDK JAR** —— 华为鸿蒙投屏闭源 SDK
   - 下载地址: https://gitcode.com/OpenHarmonyToolkitsPlaza/HOScrcpy
   - 传 `Device(sdk_jar="路径/hosScrcpy-1.0.15-beta.jar")`
3. **hdc 工具** —— 鸿蒙设备调试桥
   - 查看设备序列号: `hdc list targets`
   - 设备需开启 USB 调试并已 `hdc` 连上

## 性能

测试设备: HarmonyOS NEXT, uitest 6.0.2.1, 原生 1276×2848

| 操作 | hdc 方案 | ohscrcpy | 提升 |
|------|---------|----------|------|
| 截图 (scale=2) | ~509ms | **~17ms** | ~30× |
| 点击 | ~517ms | **~0.01ms** | ~50,000× |
| 滑动 (200ms) | ~517ms+ | ~203ms | ~2.5× |
| 视频流帧率 | 不支持 | **58 FPS** | ∞ |

## 工作原理

```
设备屏幕
  → scrcpy agent 硬件 H.264 编码
  → gRPC Server Streaming
  → hdc fport → PC TCP
  → JPype JProxy.onData
  → PyAV decode → OpenCV RGBA→BGR
  → screenshot() 返回 PNG (~17ms)

点击 / 滑动
  → JPype → SDK onTouchDown/Move/Up
  → uitest_socket → 设备触控注入 (~0.01ms)
```

## 项目结构

```
ohscrcpy/
├── README.md           # 本文档
├── requirements.txt    # Python 依赖
├── quickstart.py       # 主脚本: 配置 sn + 截图/点击/连点/滑动 示例
└── ohscrcpy/
    ├── __init__.py     # 公开 API
    ├── device.py       # Device 高层封装
    ├── bridge.py       # JPype 桥接层 (JVM + 视频流 + 触控)
    └── exceptions.py   # 异常层次
```

## 故障排查

| 症状 | 原因 | 解决 |
|------|------|------|
| `JVM (jvm.dll) not found` | 未安装/未配置 JDK | 安装 DevEco Studio 或 OpenJDK，设 `JAVA_HOME` |
| `Stream onReady not fired` | 设备 uitest 版本过低 | 升级鸿蒙系统 |
| `UNAVAILABLE: Network closed` | gRPC 通道异常 | 重启设备 uitest daemon，重新插拔 |
| `Video stream failed to deliver frames` | 设备未连 / SDK 未就绪 | 检查 `hdc list targets`、SDK JAR 路径 |
| `No module named 'jpype'` | 未装依赖 | `pip install -r requirements.txt` |
| 点击后截图读到旧帧 | 视频流缓存 | `screenshot(wait_new=True)` (默认已开) |

## 版本兼容

| SDK 版本 | uitest 最低 | 状态 |
|----------|------------|------|
| 1.0.15-beta | 6.0.2.1 | ✅ 当前测试通过 |

SDK 升级后若类名/包名变化，改 `ohscrcpy/bridge.py` 的 `_start_device()`；callback 接口变化改 `_start_stream()`。
