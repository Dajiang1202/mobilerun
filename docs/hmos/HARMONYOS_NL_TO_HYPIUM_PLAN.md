# 鸿蒙自然语言脚本转 hypium —— 技术方案

> **状态**:设计稿(v1)
> **分支**:`mobileohos`
> **工作路径**:`D:\AI\mobilerun_hmos`
> **最后更新**:2026-07-21

---

## 0. TL;DR(给忙碌的读者)

我们要做的事情:**把几千个人工写的自然语言测试脚本,自动转化成鸿蒙 hypium 自动化脚本**。

- **输入**:纯文本的人类可读脚本(类似"打开微信→找小美→发5张图")
- **执行**:用 mobilerun + LLM(qwen3-vl-flash)在真机上跑一遍,LLM 负责把人类语言映射到具体 UI 元素
- **输出**:固化后的 hypium 脚本,**不再依赖 LLM/VLM**,纯确定动作

**核心技术选型**:
- 底层驱动用开源 **hmdriver2**(MIT 协议),它通过 Hypium RPC 协议直连设备 uitest 引擎,已解决 UI 树感知 + 元素定位两大难题
- mobilerun 的 `HarmonyOSDriver` 改造成 hmdriver2 的 async 适配层
- LLM 决策走"截图 + UI 树双通道",执行轨迹全程记录,固化阶段从轨迹提取稳定定位器

---

## 1. 背景与动机

### 1.1 业务场景

团队积累了**几千个自然语言文字脚本**,描述各类 App 的操作流程。这些脚本是给人看的:

```
打开微信,打开名为小美的对话框执行以下操作
1. 每1.5s向上抛滑一次,抛滑距离1/4屏,共滑动15次
2. 发送五张图片,一次发一张
3. 每1.5秒输入一个英文字母"g",输入6次,点击发送,重复5次
4. 间隔1.5s发送5s语音,共发送3次
5. 发送3个5s视频...
6. 发送3个表情包...
```

人类执行时凭借**视觉能力 + 思考能力**补齐了脚本里的"缺陷":
- "发送五张图片"——具体点哪个+号?相册里选哪5张?发送键在哪?脚本没说,人能看出来
- "向上抛滑距离1/4屏"——人凭视觉判断,自动化需要精确坐标

### 1.2 核心矛盾

| 人类执行 | 自动化执行 |
|---|---|
| 有视觉,能看截图 | 需要 UI 树 + 截图识别 |
| 有思考,能理解"小美的对话框"=列表里第N项 | 需要 LLM 把语义映射到 UI 元素 |
| 能凭感觉判断时序(1.5s间隔、5s长按) | 需要脚本框架精确控制时序 |
| 模糊描述足够 | 自动化必须补齐成确定动作 |

### 1.3 为什么是 mobilerun + LLM

mobilerun 是一个用 LLM 控制移动设备的开源框架(v0.6.11),已有完整的 agent / tools / 工作流抽象。我们复用它:
- **Agent 层**(FastAgent):现成的 LLM 调用 + 工具注册 + 轨迹记录
- **Driver 抽象**:`DeviceDriver` 基类,加一个 `HarmonyOSDriver` 即可接入鸿蒙
- **vision-only 模式**:已支持(qwen3-vl-flash + normalized coordinates)

我们要做的**不是从零造轮子**,而是在 mobilerun 上加鸿蒙适配 + UI 树感知 + 固化流水线。

---

## 2. 已有基础(mobileohos 分支现状)

`mobileohos` 分支继承自 fork 的 3 个核心 commit:

| Commit | 内容 |
|---|---|
| `0da63ed` | vision_only + normalized [0-1000] coordinates + qwen XML 格式兼容 |
| `6d00500` | `HarmonyOSDriver`(裸 hdc 命令)+ `create_driver()` 工厂 + DeviceConfig 扩展 |
| `8e21e49` | HDC 截图修复(`snapshot_display` + `file recv` 拉回) |

### 2.1 当前数据流

```
LLM(qwen3-vl-flash)
  ↑ {screenshot, "没有 UI 树,用 [0-1000] 归一化坐标"}
  ↓ click_at(x, y) / swipe / type
HarmonyOSDriver (裸 hdc)
  ↓ hdc shell uitest uiInput click x y
鸿蒙真机
```

**关键限制**:驱动只支持 `{tap, swipe, screenshot}`,**看不到 UI 树**。LLM 完全靠视觉识别,坐标不稳定、慢、贵。

### 2.2 关键文件

| 文件 | 作用 |
|---|---|
| `mobilerun/tools/driver/harmonyos.py` (263行) | HDC 驱动主体 |
| `mobilerun/tools/driver/__init__.py` | `create_driver()` 工厂,按 platform 分发 |
| `mobilerun/tools/ui/screenshot_provider.py` | vision-only 状态提供者,返回 `elements=[]` |
| `mobilerun/config_manager/config_manager.py` | `DeviceConfig`(含 `hdc_path`, `screenshot_method`) |
| `mobilerun/agent/utils/signatures.py` | 工具注册表,用 `deps` 能力集做平台自适应 |

---

## 3. 目标架构

### 3.1 整体流水线

```
┌─────────────────────────────────────────────────────────────────────┐
│                    离线:文字脚本 → hypium 转译流水线                 │
│                                                                     │
│  ┌──────────┐   ┌─────────────────────┐   ┌──────────────────┐     │
│  │ 文字脚本 │ → │  mobilerun 执行期   │ → │   固化期         │     │
│  │ (纯文本) │   │  (LLM 补齐语义)     │   │ (提取稳定定位器) │     │
│  └──────────┘   └─────────────────────┘   └────────┬─────────┘     │
│                                                          │           │
│                                                          ▼           │
│                                                ┌─────────────────┐   │
│                                                │  hypium 脚本    │   │
│                                                │  (无 LLM 依赖)  │   │
│                                                └─────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 执行期架构(mobilerun 在线)

```
┌──────────────────────────────────────────────────────────────────┐
│                     mobilerun 执行期                             │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Step Orchestrator(脚本框架,管时序/循环/计时)          │    │
│  │   - 解析文字脚本的 Step 列表                            │    │
│  │   - 控制定时(1.5s 间隔、5s 长按、N 次循环)              │    │
│  │   - 每个"找按钮/输入"动作委托给 LLM 决策                │    │
│  └────────────────────────┬────────────────────────────────┘    │
│                           │ 每步:截图 + UI 树 + prompt          │
│  ┌────────────────────────▼────────────────────────────────┐    │
│  │  FastAgent(LLM 决策层,qwen3-vl-flash)                  │    │
│  │   输入:screenshot + UI tree(text/bounds) + 任务描述    │    │
│  │   输出:工具调用(click_element / type / swipe / ...)    │    │
│  │        ↑ 轨迹全程记录(供固化期消费)                    │    │
│  └────────────────────────┬────────────────────────────────┘    │
│                           │ async 调用                           │
│  ┌────────────────────────▼────────────────────────────────┐    │
│  │  HarmonyOSDriver(async,适配 hmdriver2)                 │    │
│  │   - screenshot() → bytes(vision pipeline 用)            │    │
│  │   - get_ui_tree() → 复用 hmdriver2.dump_hierarchy()     │    │
│  │   - find_element(text/id/...) → 复用 hmdriver2          │    │
│  │   - tap/swipe/input_text → 复用 hmdriver2               │    │
│  └────────────────────────┬────────────────────────────────┘    │
│                           │ asyncio.to_thread 包同步调用         │
│  ┌────────────────────────▼────────────────────────────────┐    │
│  │  hmdriver2(sync, Hypium RPC)                           │    │
│  │   - 通过设备 uitest 引擎(8012 端口)驱动                │    │
│  │   - dump_hierarchy / findComponent / Driver.click 等    │    │
│  └────────────────────────┬────────────────────────────────┘    │
│                           │ hdc fport tcp:N tcp:8012             │
└───────────────────────────┼─────────────────────────────────────┘
                            ▼
                    ┌────────────────┐
                    │  鸿蒙真机       │
                    │  (uitest 引擎)  │
                    └────────────────┘
```

### 3.3 固化期架构(离线)

```
┌──────────────────────────────────────────────────────────────────┐
│                      固化期(离线,无 LLM)                        │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐                    │
│  │ 执行期轨迹       │ → │  定位器提取器    │                    │
│  │ (screenshot +    │   │  对每个动作:     │                    │
│  │  UI tree +       │   │  1. 优先级降级   │                    │
│  │  action + 元素)  │   │  2. 稳定性评分   │                    │
│  └──────────────────┘   └────────┬─────────┘                    │
│                                  ▼                               │
│                         ┌──────────────────┐                    │
│                         │ hypium 代码生成器│                    │
│                         │ d(text="...").   │                    │
│                         │   click()       │                    │
│                         └──────────────────┘                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. 核心技术选型:hmdriver2

### 4.1 为什么选它

**开源项目**:`hmdriver2` ([github.com/codematrixer/hmdriver2](https://github.com/codematrixer/hmdriver2), MIT 协议, PyPI 可装)

它不是简单的 hdc 封装,而是**通过 Hypium RPC 协议直接驱动设备上的 uitest 引擎**(鸿蒙官方 hypium 测试框架同一条通道)。这解决了 mobilerun 当前最大的两个缺口:

| 能力 | mobilerun 现状 | hmdriver2 提供 |
|---|---|---|
| UI 树感知 | ❌ 没有 | ✅ `dump_hierarchy()` 返回完整 JSON 树 |
| 元素定位 | ❌ 只能点坐标 | ✅ `d(text/id/key/desc/xpath)` |
| 文本输入 | ❌ 没有 | ✅ `input_text` 到聚焦输入框 |
| 按键事件 | ❌ 没有 | ✅ `press_key`(~300 个 KeyCode)|
| App 管理 | ❌ 没有 | ✅ start/stop/install/list |
| 手势 | swipe 基础 | ✅ swipe / long_click / drag_to / pinch |
| 截图 | ✅ bytes(好用)| ✅ 文件路径(需适配)|

### 4.2 它的工作原理

```
PC 端                              鸿蒙设备
─────                              ────────
hmdriver2.Driver
  │
  ├─ 1. push agent.so ──────────→ /data/local/tmp/agent.so (ARM64 原生代理)
  │
  ├─ 2. uitest start-daemon ────→ uitest 引擎监听 8012 端口
  │
  ├─ 3. hdc fport tcp:N tcp:8012 (端口转发)
  │
  └─ 4. Hypium RPC over TCP ←──→ JSON 协议
       {"method":"callHypiumApi",
        "params":{"api":"Driver.click",...}}
```

**关键**:所有 UI 操作(点击/查找/dump)都通过这条 RPC 通道,由设备端的官方 uitest 引擎执行——**和我们最终固化的 hypium 脚本是同一套 API**。这意味着固化的脚本天然稳定,因为执行期和固化期走的是同一条路径。

### 4.3 元素定位能力(关键)

hmdriver2 支持三种定位方式:

| 方式 | API | 适用场景 |
|---|---|---|
| **By 选择器** | `d(text="登录")` / `d(id="btn_login")` / `d(type="Button", index=0)` | 最常用,匹配发生在设备端 |
| **XPath** | `d.xpath('//Button[@text="登录"]')` / `d.xpath('//Row[1]/Button[3]')` | 复杂结构定位 |
| **坐标** | `d.click(x, y)` / `d.click(0.5, 0.5)` (比例) | 兜底,分辨率敏感 |

**支持的 By 属性**:`id` / `key` / `text` / `type`(控件类) / `description`(content-desc) / `clickable` / `enabled` / `selected` / `checked` / `isBefore` / `isAfter`(相邻兄弟) / `index`(第N个匹配)

**不支持**:`contains` / 正则模糊匹配(README 标注 TODO)——这意味着如果文字部分匹配,需要 XPath 配合 `contains()` 函数。

### 4.4 集成成本

| 改动点 | 工作量 |
|---|---|
| async/sync 适配(`asyncio.to_thread` 包装) | 小,每个方法包一层 |
| screenshot 返回类型(bytes vs path) | 小,读文件返回 bytes |
| Driver 单例生命周期管理 | 小,在 `connect()` 里懒构造 |
| agent.so 首次部署 | 一次性,1-2 秒 |

**整体集成成本远小于从零实现 UI 树解析 + 定位逻辑**。

---

## 5. 详细设计

### 5.1 HarmonyOSDriver 改造(集成 hmdriver2)

**目标**:把现有基于裸 hdc 的 driver,改造成 hmdriver2 的 async 包装层,同时保持 mobilerun `DeviceDriver` 接口兼容。

#### 5.1.1 接口设计

```python
# mobilerun/tools/driver/harmonyos.py (改造后骨架)

from mobilerun.tools.driver.base import DeviceDriver

class HarmonyOSDriver(DeviceDriver):
    """HarmonyOS driver backed by hmdriver2 (Hypium RPC)."""

    platform = "harmonyos"
    # 关键:声明支持的能力集,框架据此启用对应工具
    supported = {
        "tap", "swipe", "input_text", "screenshot",
        "element_index",        # 启用基于元素的 click/type 工具
        "convert_point",        # 启用坐标转换
        "direct_text_input",    # 启用直接文本输入
        "start_app",            # 启用 App 启动
        "get_apps",             # 启用 App 列表
        "press_button",         # 启用系统按键
    }
    supported_buttons = {"back", "home", "enter"}

    def __init__(self, serial=None, ...):
        self._hm_driver = None  # hmdriver2.Driver 实例,懒构造

    async def connect(self):
        # 在线程里构造 hmdriver2.Driver(它会 push agent.so + 建连接)
        self._hm_driver = await asyncio.to_thread(hmdriver2.Driver, self._serial)

    # ── 基础动作(包装 hmdriver2)──
    async def tap(self, x, y):
        await asyncio.to_thread(self._hm_driver.click, x, y)

    async def swipe(self, x1, y1, x2, y2, duration_ms=1000):
        speed = self._duration_to_speed(x1, y1, x2, y2, duration_ms)
        await asyncio.to_thread(self._hm_driver.swipe, x1, y1, x2, y2, speed)

    async def input_text(self, text, clear=False):
        if clear:
            # 先全选删除
            await asyncio.to_thread(self._hm_driver.press_key, KeyCode.CtrlA)
            await asyncio.to_thread(self._hm_driver.press_key, KeyCode.Del)
        await asyncio.to_thread(self._hm_driver.input_text, text)

    async def press_button(self, button):
        key_map = {"back": KeyCode.BACK, "home": KeyCode.HOME, "enter": KeyCode.ENTER}
        await asyncio.to_thread(self._hm_driver.press_key, key_map[button])

    # ── 新增:UI 树感知 ──
    async def get_ui_tree(self) -> dict:
        """返回 mobilerun 标准 state dict {a11y_tree, phone_state, device_context}."""
        hierarchy = await asyncio.to_thread(self._hm_driver.dump_hierarchy)
        return self._convert_to_mobilerun_state(hierarchy)

    # ── 新增:元素查找(LLM 工具调用用)──
    async def find_element(self, **by) -> dict | None:
        """按 text/id/key/desc/type 查找,返回 element info."""
        ui_obj = self._hm_driver(**by)
        return await asyncio.to_thread(lambda: ui_obj.info)

    async def screenshot(self) -> bytes:
        # hmdriver2 截图返回 path,我们读文件返回 bytes
        path = await asyncio.to_thread(self._hm_driver.screenshot)
        return Path(path).read_bytes()
```

#### 5.1.2 hmdriver2 JSON 树 → mobilerun 元素 dict

hmdriver2 的 `dump_hierarchy()` 返回嵌套 JSON:
```json
{
  "attributes": {
    "type": "Button",
    "text": "登录",
    "id": "btn_login",
    "key": "",
    "bounds": "[100,200][300,250]",
    "clickable": "true",
    "enabled": "true"
  },
  "children": [...]
}
```

转成 mobilerun 的元素 schema(供 `IndexedFormatter` 编号后给 LLM):
```python
def _convert_to_mobilerun_state(self, hierarchy: dict) -> dict:
    elements = []
    self._flatten_hierarchy(hierarchy, elements, index_counter=[0])
    return {
        "a11y_tree": elements,  # list[dict]
        "phone_state": {"currentApp": ..., "packageName": ...},
        "device_context": {"screen_bounds": {"width": ..., "height": ...}},
    }

def _flatten_hierarchy(self, node, out, index_counter):
    attrs = node.get("attributes", {})
    if attrs.get("type"):  # 跳过纯容器节点
        out.append({
            "index": index_counter[0],
            "type": attrs.get("type", ""),
            "text": attrs.get("text", ""),
            "className": attrs.get("type", ""),
            "bounds": self._normalize_bounds(attrs.get("bounds", "")),
            "id": attrs.get("id", ""),
            "key": attrs.get("key", ""),
            "description": attrs.get("description", ""),
            "clickable": attrs.get("clickable") == "true",
        })
        index_counter[0] += 1
    for child in node.get("children", []):
        self._flatten_hierarchy(child, out, index_counter)
```

### 5.2 State Provider:截图 + UI 树双通道

**目标**:让 LLM 同时看到截图和 UI 树,综合决策。

新增 `HarmonyStateProvider`(或者改造 `ScreenshotOnlyStateProvider` 加个开关):

```python
# mobilerun/tools/ui/harmony_provider.py (新文件)

class HarmonyStateProvider(StateProvider):
    """截图 + UI 树双通道,综合决策。"""

    supported = {"convert_point", "element_index", "direct_text_input"}
    requires_coordinate_tools = True

    def __init__(self, driver, use_normalized=False, include_tree=True):
        super().__init__(driver)
        self.use_normalized = use_normalized
        self.include_tree = include_tree

    async def get_state(self) -> UIState:
        # 1. 截图(给 vision 模型)
        screenshot = await self.driver.screenshot()
        screen_w, screen_h = image_dimensions(screenshot)

        # 2. UI 树(给文本理解)
        elements = []
        tree_text = ""
        if self.include_tree:
            state = await self.driver.get_ui_tree()
            elements = state["a11y_tree"]
            tree_text = self._format_tree_for_llm(elements)

        # 3. 组装 formatted_text:坐标说明 + UI 树
        coord_part = self._coord_instruction(screen_w, screen_h)
        tree_part = f"\n\n## Current UI Elements\n{tree_text}" if tree_text else ""

        return UIState(
            elements=elements,
            formatted_text=coord_part + tree_part,
            phone_state={"observationMode": "screenshot_plus_tree"},
            screen_width=screen_w,
            screen_height=screen_h,
            use_normalized=self.use_normalized,
            ...
        )
```

**UI 树给 LLM 的文本格式**(示例):
```
## Current UI Elements
0. Button [登录] bounds=(100,200,300,250) id=btn_login clickable
1. TextField [请输入账号] bounds=(50,300,450,350) id=input_account
2. Text [忘记密码?] bounds=(200,400,300,430) clickable
3. Image [bounds=(0,0,100,100)] clickable  # 无 text/id 的图标按钮
...
```

### 5.3 Step Orchestrator(脚本框架,管时序)

**目标**:解析文字脚本的 Step 列表,精确控制时序/循环/计时,把需要"找元素"的动作委托给 LLM。

#### 5.3.1 文字脚本 → 结构化 Step

文字脚本格式(用户给定):
```
1. [Step] 每1.5s向上抛滑一次,抛滑距离1/4屏,共滑动15次,共15s(22s)
2. [Step] 发送五张图片,一次发一张 (25s)
3. [Step] 每1.5秒输入一个英文字母"g",输入6次,点击发送,重复5次 (45s)
4. [Step] 间隔1.5s发送5s语音,共发送3次 (18s)
```

**第一阶段用 LLM 把文字脚本解析成结构化 JSON**(一次性,离线):
```json
{
  "app": "微信",
  "target": "对话框:小美",
  "steps": [
    {
      "id": 1,
      "type": "swipe_loop",
      "direction": "up",
      "distance_ratio": 0.25,
      "interval_s": 1.5,
      "count": 15,
      "estimated_s": 22
    },
    {
      "id": 2,
      "type": "image_send",
      "count": 5,
      "sub_flow": "tap_plus → album → select_image → send",
      "estimated_s": 25
    },
    {
      "id": 3,
      "type": "text_input_loop",
      "char": "g",
      "input_count": 6,
      "outer_repeat": 5,
      "interval_s": 1.5,
      "estimated_s": 45
    },
    {
      "id": 4,
      "type": "voice_record_loop",
      "duration_s": 5,
      "count": 3,
      "interval_s": 1.5,
      "estimated_s": 18
    }
  ]
}
```

#### 5.3.2 Step 类型分类

把所有动作分两类:
- **机械动作**(时序/循环/计时/swipe)→ **Orchestrator 直接执行,不调 LLM**
- **定位动作**(找+号、选图、找输入框)→ **委托 LLM 决策**

| Step 类型 | 执行方式 |
|---|---|
| `swipe_loop` / `swipe` | Orchestrator 直接算坐标 + hdc swipe,**无 LLM** |
| `wait` / `sleep` | Orchestrator `asyncio.sleep`,**无 LLM** |
| `key_event` | Orchestrator 直接 press_key,**无 LLM** |
| `text_input`(已聚焦) | Orchestrator 直接 input_text,**无 LLM** |
| `tap_element`(找元素) | **LLM 决策**:截图+UI树→找到元素→点击 |
| `sub_flow`(多步 UI 操作) | **LLM 逐步决策**:每步截图+UI树→动作 |
| `long_press_duration`(精确计时) | Orchestrator 控制时长(hmdriver2 不支持 duration 参数,需自己实现)|

#### 5.3.3 长按精确计时的坑

hmdriver2 的 `long_click` **没有 duration 参数**(设备端固定时长)。语音"5秒长按"需要自己实现:

```python
async def long_press_duration(driver, x, y, duration_s):
    """hmdriver2 不支持自定义长按时长,用 gesture API 实现。"""
    # 方案:用 hmdriver2 的 gesture PointerMatrix
    # 或:swipe(x,y,x,y, speed=极小)模拟按住不动
    gesture = driver.gesture.start(x, y).pause(duration_s).action()
    await asyncio.to_thread(gesture)
```

⚠️ **这是需要验证的技术风险点**(M2 阶段验证)。

### 5.4 固化期:轨迹 → hypium 脚本

#### 5.4.1 定位器降级策略

对执行期记录的每个动作,从对应的 UI 树节点提取**最稳定的定位器**,按优先级降级:

```
1. id 定位(开发者在 ArkTS 里赋的 id,最稳)
   → d(id="btn_login").click()

2. key 定位(ArkUI 的 key 属性,稳定)
   → d(key="submit_btn").click()

3. text + type 组合(文字+类,中等稳定)
   → d(type="Button", text="登录").click()

4. description 定位(content-desc,无障碍描述)
   → d(description="返回").click()

5. 属性组合 + index(兜底)
   → d(type="Image", clickable=True, index=0).click()

6. XPath(最复杂但最灵活)
   → d.xpath('//Row[2]/Button[1]').click()

7. 坐标(最后兜底,标注脆弱性)
   → d.click(540, 1900)  # WARN: 分辨率敏感
```

**微信加号场景**(用户提到的坑:无 id/text):
```python
# 加号是 Image/Button 类,无 text,通常有 description 或可点击属性
# 降级到策略 5/6:
d(type="Image", clickable=True, index=0).click()  # 假设是第一个可点击 Image
# 或 XPath:
d.xpath('//TabBar/Image[@clickable="true"][1]').click()
```

#### 5.4.2 定位器稳定性评分

固化时对每个候选定位器评分,选最高的:

| 信号 | 加分 |
|---|---|
| 有 `id` | +100 |
| 有 `key` | +80 |
| 有 `text` 且非动态(非数字/时间)| +50 |
| 有 `description` | +40 |
| `type` 明确(Button 而非 generic)| +20 |
| 唯一匹配(全树只1个)| +30 |
| 同属性多个匹配(需 index)| -20 |
| 纯坐标 | -100 |

#### 5.4.3 生成的 hypium 脚本样例

```python
# test_wechat_xiaomei_flow.py — 自动生成,无 LLM 依赖
from hmdriver2 import Driver

def test_wechat_xiaomei():
    d = Driver()

    # Step 0:打开微信,进入小美对话(执行期 LLM 决策,固化后定位器确定)
    d.start_app("com.tencent.mm")
    d(text="小美").click()  # 假设有 text

    # Step 1:向上滑动 15 次
    for _ in range(15):
        d.swipe_ext("up", scale=0.25, speed=2000)
        time.sleep(1.5)

    # Step 2:发 5 张图
    for _ in range(5):
        # 加号:无 id/text,降级到 type+clickable+index
        d(type="Image", clickable=True, index=0).click()
        d(text="相册").click()  # 假设
        d(type="Checkbox", index=0).click()  # 第一张图
        d(text="发送").click()
        time.sleep(1)

    # Step 3:输入 g×6 发送×5
    for _ in range(5):
        # 点击输入框
        d(type="TextField", index=0).click()
        for _ in range(6):
            d.input_text("g")
            time.sleep(1.5)
        d(text="发送").click()

    # ... Step 4/5/6 类似
```

---

## 6. 实施路线图

### 6.1 里程碑划分

| 里程碑 | 目标 | 验收标准 | 预估 |
|---|---|---|---|
| **M1** | hmdriver2 集成 + Driver 改造 | `mobilerun` 能通过新 driver 跑通截图+点击+UI树dump | 2-3 天 |
| **M2** | 双通道 State Provider | LLM 能同时看到截图和 UI 树,完成"找+号点击"类任务 | 2 天 |
| **M3** | Step Orchestrator + 文字脚本解析 | 能解析微信脚本成结构化 JSON,机械动作直接执行 | 3-4 天 |
| **M4** | 轨迹记录 + 固化器 | 跑完一个脚本,自动生成可执行的 hypium .py 文件 | 3-4 天 |
| **M5** | 长按计时 / 复杂子流程 / 异常恢复 | 语音长按、视频拍摄等复杂子流程跑通 | 3-5 天 |

### 6.2 M1 详细任务(hmdriver2 集成)

- [ ] T1.1 `pip install hmdriver2`,真机连接跑通 `dump_hierarchy` demo
- [ ] T1.2 改造 `harmonyos.py`:加 `_hm_driver` 字段,`connect()` 懒构造
- [ ] T1.3 用 `asyncio.to_thread` 包装所有 hmdriver2 调用
- [ ] T1.4 实现 `get_ui_tree()` + JSON→元素 dict 转换
- [ ] T1.5 screenshot 适配:hmdriver2 返回 path → 读文件返回 bytes
- [ ] T1.6 验证 `supported` 能力集正确启用对应工具
- [ ] T1.7 端到端测试:`mobilerun run --harmonyos "打开设置"`

### 6.3 关键技术风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| hmdriver2 的 `long_click` 无 duration | 语音长按 5s 做不到 | M5 验证 gesture API 是否可模拟;不行则保留裸 hdc `uitest uiInput` |
| hmdriver2 agent.so 是 ARM64 二进制 | 新鸿蒙版本可能不兼容 | 跟踪上游更新;最坏回退到裸 hdc |
| LLM 把"小美"映射错列表项 | Step 0 就跑偏 | 执行期允许 LLM 重试 + 人工确认关键步骤 |
| 微信加号无 id/text/description | 定位器降级到坐标,固化脚本脆弱 | M2 重点验证;考虑图像识别兜底 |
| `asyncio.to_thread` 包装引入延迟 | 影响时序精度 | 实测;必要时关键路径用裸 hdc 直发 |

---

## 7. 与 mobilerun 上游的关系

### 7.1 分支策略

```
upstream/main (droidrun/mobilerun 官方)
       │
       │  git merge upstream/main (定期同步)
       │
mobileohos (我们的主开发分支)
       │
       │  3 个继承的 commit:
       │  - 0da63ed vision_only + normalized + qwen XML
       │  - 6d00500 HDC driver + create_driver
       │  - 8e21e49 HDC screenshot fix
       │
       └─→ 后续 commit:hmdriver2 集成 / State Provider / Orchestrator / 固化器
```

### 7.2 改动文件清单(预估)

| 文件 | 改动类型 |
|---|---|
| `mobilerun/tools/driver/harmonyos.py` | 重构(裸 hdc → hmdriver2)|
| `mobilerun/tools/driver/__init__.py` | 微调 `create_driver` |
| `mobilerun/tools/ui/harmony_provider.py` | **新建**(双通道 State Provider)|
| `mobilerun/tools/ui/__init__.py` | 导出 `HarmonyStateProvider` |
| `mobilerun/agent/droid/droid_agent.py` | 加 `elif is_harmonyos:` 分支 |
| `mobilerun/cli/main.py` | 加 `--harmonyos` flag(可选)|
| `mobilerun/config_manager/config_manager.py` | `platform` 接受 `"harmonyos"` |
| `mobilerun/hmos/orchestrator.py` | **新建**(Step Orchestrator)|
| `mobilerun/hmos/script_parser.py` | **新建**(文字脚本→JSON)|
| `mobilerun/hmos/solidifier.py` | **新建**(轨迹→hypium)|
| `pyproject.toml` | 加 `hmdriver2` 依赖 |

---

## 8. 开放问题(待讨论)

1. **文字脚本解析器**:用 LLM 一次性解析成 JSON,还是手写规则解析器?前者灵活但不可靠,后者死板但稳定
2. **关键步骤人工确认**:执行期 LLM 决策"打开小美对话"这种关键步骤,要不要弹窗让人确认?
3. **固化脚本的覆盖率**:无法固化的动作(纯动态内容、随机弹窗)怎么处理?标注跳过还是兜底截图识别?
4. **批量转译**:几千个脚本,是否并行执行?设备资源怎么调度?
5. **执行期 vs 固化期的 UI 变化**:执行时 UI 是 A 样,固化后 App 更新 UI 变 B 样,定位器失效怎么自愈?

---

## 参考资料

- **hmdriver2**: [github.com/codematrixer/hmdriver2](https://github.com/codematrixer/hmdriver2) (MIT, PyPI `hmdriver2`)
- **HarmonyOS UI 测试框架(官方)**: [developer.huawei.com/.../ui-test](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides-V2/ui-test-0000001666625453-V2)
- **ArkXTest 使用指导**: [developer.huawei.com/.../arkxtest-guidelines](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/arkxtest-guidelines)
- **Hypium Python 控件定位**: [developer.harmonyos.cool/.../ui-test/python](https://developer.harmonyos.cool/docs/dev/testing/ui-test/python)
- **awesome-hdc 命令合集**: [github.com/codematrixer/awesome-hdc](https://github.com/codematrixer/awesome-hdc)
- **mobilerun 框架**: [github.com/droidrun/mobilerun](https://github.com/droidrun/mobilerun) (本仓库 upstream)
