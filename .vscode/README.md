# VSCode 调试环境

## 一次性配置(打开 VSCode 后做一次)

### 1. 装 Python 扩展

`Ctrl+Shift+X` 打开扩展市场,搜索并安装:

| 扩展 | 作用 |
|---|---|
| **Python**(Microsoft) | Python 语言支持、调试器(debugpy) |
| **Ruff**(Astral) | 代码 lint/格式化(可选) |

> **不需要 "Python: Select Interpreter"** —— launch.json 里每个 configuration
> 都通过 `"python"` 字段硬编码了 venv 路径(`D:\gameauto\mobilerun\.venv`),
> 直接 F5 就用这个 venv 跑,不依赖解释器选择。

### 2. 关于 venv

这个项目**共用 fork 的 venv**:`D:\gameauto\mobilerun\.venv\`
原因:mobilerun 是 editable 安装,指向 mobileohos。在这个 venv 里改了
mobileohos 的代码,立即生效,不用重装。

如果你要在别的机器上用,需要:
1. 在 `D:\gameauto\mobilerun\` 建 venv 并装好依赖(参考 fork 的 requirements.txt)
2. 或者把 launch.json 里所有 `"python"` 字段改成你机器上的 venv 路径

---

## 日常调试

### 跑 FastAgent 任务(主力)

1. 打开 `debug/run_fastagent_harmony.py`,改顶部的 `TASK` 变量定义任务
2. 左侧"运行和调试"面板(图标是个带虫子的三角形)→ 下拉选 **🤖 FastAgent 鸿蒙任务**
3. 按 `F5`(或点绿色 ▶️)
4. 输出在底部的 **"终端"** 标签页(不是"调试控制台")

---

## 日常调试

### 跑 FastAgent 任务(主力)

1. 打开 `debug/run_fastagent_harmony.py`,改顶部的 `TASK` 变量定义任务
2. 左侧"运行和调试"面板(图标是个带虫子的三角形)→ 下拉选 **🤖 FastAgent 鸿蒙任务**
3. 按 `F5`(或点绿色 ▶️)
4. 输出在底部的 **"终端"** 标签页(不是"调试控制台")

### 其他一键调试配置

`launch.json` 里还配了:

| 配置名 | 跑什么 | 用途 |
|---|---|---|
| 🤖 FastAgent 鸿蒙任务 | `debug/run_fastagent_harmony.py` | 主力:完整 FastAgent + 轨迹记录 |
| 🧪 最小闭环 | `debug/test_e2e_wechat.py` | 自写的 LLM 循环,快速验证 |
| 🔌 Driver 测试 | `debug/test_driver.py` | 不调 LLM,只验 driver+provider |
| 🌳 Provider 测试 | `debug/test_provider.py` | 看 UI 树筛选效果 |
| 📡 hmdriver2 连通性 | `debug/test_hmdriver2.py` | 最底层:hmdriver2 能否连真机 |
| 📄 当前文件 | `${file}` | 打开哪个 .py 就跑哪个 |

### 设断点单步调试

代码行号左边点一下就能下断点(红点)。推荐断点位置:

- `mobilerun/tools/driver/harmonyos.py`
  - `tap()` / `swipe()` — 看坐标对不对
  - `get_ui_tree()` — 看 hmdriver2 返回的原始树
- `mobilerun/tools/ui/harmony_provider.py`
  - `get_state()` — 看筛选后的元素列表
  - `_format_flat_tree()` — 看哪些元素被过滤了
- `mobilerun/agent/fast_agent/fast_agent.py`
  - LLM 调用点 — 看 prompt 和响应

> `launch.json` 里 `"justMyCode": false` 已开,意味着**断点能停在 mobilerun / hmdriver2 / llama-index 内部代码**(默认只停自己写的代码)。

下断点后 `F5` 启动,程序会在断点处暂停。然后:
- `F10` 单步跳过(不进函数)
- `F11` 单步进入(进函数)
- `F5` 继续到下一个断点
- 左侧"变量"面板看所有变量值

---

## 常用任务

### 看完整 hmdriver2 RPC 日志(调试连接问题)

`launch.json` 里相应配置的 `env` 加:
```json
"HMDRIVER_LOG_LEVEL": "DEBUG"
```
(默认是 INFO,DEBUG 会刷屏但能看每个 RPC 请求/响应)

### 查看轨迹(FastAgent 跑完后)

`trajectories/<时间戳_uuid>/` 目录:
```
trajectory.json   # 事件流(thought/tool_call/result)
macro.json        # 动作序列(给固化器用)
screenshots/      # 每步截图 0000.png 0001.png ...
ui_states/        # 每步 UI 元素 0000.json 0001.json ...
```

### 改 hdc 路径(换机器时)

如果 hdc 不在 `D:\gameauto\mobilerun\tools\hdc\`,改两个文件里的路径:
- `.vscode/settings.json` 的 `terminal.integrated.env.windows.PATH`
- `.vscode/launch.json` 所有配置的 `env.PATH`

---

## 排错

### Q: F5 启动报 `ModuleNotFoundError: No module named 'mobilerun'`
**A**: venv 不对。检查 `launch.json` 里该 configuration 的 `"python"` 字段路径是不是 `D:\gameauto\mobilerun\.venv\Scripts\python.exe`,这个 venv 里才有 mobilerun。

### Q: F5 没反应 / 提示找不到调试器
**A**: Python 扩展没装。`Ctrl+Shift+X` 搜 Python(Microsoft)装上。debugpy 会随它一起装。

### Q: 报 `hdc: command not found` / `HDC binary not found`
**A**: hdc 不在 PATH。检查 `.vscode/launch.json` 里 `env.PATH` 的 hdc 路径是否真实存在。用终端验证:`D:\gameauto\mobilerun\tools\hdc\hdc.exe list targets`。

### Q: 报 `No HarmonyOS device found via HDC`
**A**: 真机没连上。终端跑 `hdc list targets` 看有没有设备号。没有的话:检查 USB 线、开发者模式、USB 调试。

### Q: 断点不生效(红点变灰圈)
**A**: 检查 `launch.json` 里该配置的 `"justMyCode": false` 是否还在。另外确认断点文件被 Python 实际执行(不是被条件分支跳过)。

### Q: 调试时变量面板看不到 hmdriver2 内部变量
**A**: `justMyCode: false` 已开应该能看到。如果还看不到,在"监视"面板手动加表达式,比如 `self._hm_driver._client`。
