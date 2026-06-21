# 斗地主 DouZero 自动化 — 交接文档

> **新窗口先读此文件**(再扫 README.md / DEVLOG.md 补充)。目标是快速接续工作。
> 最后更新:2026-06-21 | 分支:gameauto-m1

---

## 一、项目定位 & 当前状态

纯视觉操作鸿蒙手机斗地主:`截图(scrcpy) → CV模板匹配(感知) → 规则决策 → 自动点击(scrcpy) → 录制`。

**当前能跑**:真机自动打牌,正确压牌、自动补全多张、完整录制。但有未解决问题(见第八节)。

**关键现状**:
- 感知:纯 CV 模板匹配(无 VLM),分区 ROI + 颜色校验
- 决策:**临时规则决策**(`_USE_RULE=True`),DouZero 暂时绕过(它在方案A的不完整 env 下偏保守)
- 执行:scrcpy 高速(H.264 流截图 + JVM 触控),失败兜底 HDC
- 后端切换、规则/DouZero 切换都有开关,见第三节

---

## 二、架构与数据流

```
scrcpy 截图(原尺寸, scale=1)
   │
   ▼
DouDiZhuDouzeroPerception.recognize(buttons_only)   ← 分层: 先只识别按钮(快)
   │
   ▼
states._handle 分层:
   ├─ 无按钮 → idle(休眠0.5s, 不识别手牌)
   ├─ 要不起 → 直接 pass(点要不起)
   ├─ 叫牌/继续/开始游戏 → 点按钮(固定策略)
   └─ 出牌 → recognize(全量手牌) → decision → 选牌+出牌
   │
   ▼
DouzeroDecision._decide_playing:
   ├─ _calibrate_env(屏幕 last_move 校准 env)
   └─ _rule_select(legal_actions) 或 DeepAgent(_USE_RULE=False)
   │
   ▼
GameLoop → scrcpy tap(归一化[0-1000] → 像素)
```

每轮落盘 `logs/<session>/round_NNN/`:screenshot.png + perception.json/png + actions.json/png。

---

## 三、关键开关 & 配置位置(改这些调行为)

| 开关/配置 | 位置 | 作用 |
|-----------|------|------|
| `_USE_RULE` | [decision.py](decision.py) 类属性 | `True`=临时规则决策(当前);`False`=换回 DouZero DeepAgent |
| `ROUNDS` | [run_doudizhu_douzero.py](../../run_doudizhu_douzero.py) 顶部常量 | 循环轮数(每轮=一次截图决策点击) |
| `step_interval` | run 里 `GameLoop(..., step_interval=0.03)` | 多步点击间隔(30ms) |
| tap duration | [loop.py](../../core/orchestration/loop.py) `_execute_action` | 固定 50ms(<50ms HOS 触控不识别) |
| scrcpy scale | run 里 `ScrcpyCapture(serial, scale=1)` | **必须 1**(原尺寸,模板匹配用) |
| scrcpy 兜底 | run Step 3 | scrcpy 启动失败 → HDC 整局 |
| ROI 像素 | [perception.py](perception.py) `PX` | 手牌/对手/底牌/按钮/继续/ingame 区(见下) |

**像素 ROI**(本机 2848×1276 横屏,基于此标定):
```
hand      (350, 770, 2600, 970)    手牌角标
landlord3 (1000, 0, 2100, 130)     底牌(scale 0.65 匹配)
play_up   (700, 200, 1424, 600)    上家出牌(我前一位, 要压的牌)
play_down (1425, 200, 2100, 600)   下家出牌
buttons   (500, 600, 2200, 800)    操作按钮(出牌/不出/叫牌/开始游戏)
continue  (1899, 957, 2848, 1276)  继续(右1/3, 下1/4)
ingame    (2450, 1175, 2680, 1276) 游戏中独有小按钮
```

---

## 四、中间做的妥协(重要!)

### 1. 方案 A 校准式 env(过渡,非完整 DouZero)

DouZero 需要完整 infoset(三家手牌 + 全部出牌历史),但屏幕只给我方手牌 + 两家最近出牌 + 底牌 —— **对手手牌永远未知、完整历史缺失,精确匹配 env 不可能**。

妥协:**每帧用屏幕 last_move 校准 env**(`_calibrate_env`):设 `acting=我方`、`last_move=屏幕上家牌`、`我方手牌=屏幕`、自由出时清 last_move,然后 `get_infoset()` 重算 legal_actions。**不 step、不跨帧累积**(那是漂移根源)。

代价:infoset 仍不完整(对手手牌随机、历史空),DouZero 据此决策偏保守(该压不压)。所以**临时改用规则决策**(下条)。

### 2. 规则决策替代 DouZero(`_USE_RULE=True`)

DouZero 在不完整 env 下 `-0.9672` 选 pass(保守)。临时用规则:`_rule_select` 从 legal_actions 选 —— 能压就压(不轻易 pass)、排除炸弹/王炸(留底)、压牌阶段不出炸弹、自由出优先组合牌型(顺子/连对/三带 > 对子 > 单张)、飞机默认不出。

**不是最优策略,但稳定(该压就压)**。完整 DouZero env 调好后 `_USE_RULE=False` 一行切回。

### 3. scrcpy 旧帧问题(未解决!)

scrcpy `screenshot()` 读 `_latest_png` 缓存(JVM 线程异步更新)。视频流停滞时缓存冻在旧帧 → 识别到已消失的按钮 → **重复操作(90%+ 概率)**。已加 `wait_new`(等新帧),但**没根治**(见第八节)。

---

## 五、踩过的坑 + 修复(按出现顺序)

| 坑 | 根因 | 修复 |
|----|------|------|
| VLM 坐标对不齐 | 大模型说不清"在哪" | 切纯 CV 模板匹配 |
| 红黑牌互串 | `TM_CCOEFF_NORMED` 减均值削弱颜色 | 命中后颜色校验(`_is_red`) |
| 中文模板加载失败 | `cv2.imread` 中文路径返回 None | `cv2.imdecode(np.fromfile)` |
| 选牌坐标查不到 | 模板名 `mb3` 当牌名,决策查点数 `3` | `_strip_color` 剥离颜色前缀 |
| 出对子点同一位置 | card_positions 单坐标 | 改 `{点数:[(x,y),...]}` 消费式取位 |
| 对手两区合并成非法牌 | play_up+play_down 合并 last_play | 分别 `last_play_up`/`down`,要压取上家 |
| detector 漏判按钮 | `detect_any_button` 传 roi=None,_match_inline 异常被吞 | None 时传全图 `(0,0,1,1)` |
| 「不出」误当 pass | pass 分支含"不出" | 只有「要不起」才 pass |
| env 跨帧漂移致乱出牌 | 逐帧推进累积识别误差 | 方案A:每帧屏幕校准 env |
| `_action_pass` step assert | 单步模式不该 step | 去掉 step |
| 自由出 pass 死循环 | last_move 残留致 legal 含 pass | 自由出(无对手牌)清 last_move |
| 校准"没生效" | `RealCard2RealCard` 笔误(不存在) | 改 `RealCard2EnvCard`(**教训:别用宽 try/except 吞异常**) |
| 决策崩了识别也丢 | _save_debug 在 decide 后 | 识别先落盘再决策 |
| tap 点击不生效 | duration 10ms 太短 HOS 不识别 | 改 50ms |
| 拆三张/炸弹不补全 | auto_fill 一律点一张 | 按牌型 `_fill_count`(顺子2/连对3/对子天然1) |
| 坐标全偏 | perception 像素 vs Action 归一化[0-1000] | perception 输出归一化,visualizer 画图转像素 |
| Ctrl+C 不退 | jpype JVM 接管 SIGINT | scrcpy connect 后重新 signal.signal |
| landlord/底牌每帧全图 | 重复识别 | 一局缓存(首次锁定,底牌3张;新局 unlock) |

---

## 六、UI 补全边界(点击逻辑,易踩坑)

游戏点「起始张数」后**自动补全**剩余牌,然后点出牌:

| 牌型 | 点几张 | 例子 |
|------|--------|------|
| 顺子 | 前 2 张 | 34 → 补全 34567 |
| 连对 | 前 3 张 | 334 → 补全 334455 |
| 对子(天然,手牌正好2) | 1 张 | J → 补全 JJ |
| 对子(拆三张/拆炸弹) | 全部 | JJJ 出 JJ → 点 2 张 |
| 三张(天然) | 1 张 | — |
| 三带一/三带二/混合 | 全部 | 不补全 |
| 单张 | 1 张 | — |
| 飞机 | **不出** | UI 边界未确认,规则排最后 |
| 炸弹(压牌阶段) | **不出** | 只能炸弹压时 pass;自由出可主动炸 |

实现:[decision.py](decision.py) `_fill_count`(补全张数)+ `_action_priority`(出牌优先级)+ `_rule_select`(压牌不bomb)。

---

## 七、部署 & 依赖

### Python 依赖
- `torch`(DouZero 模型,即使 `_USE_RULE=True` 也 import;CPU 推理够)
- `opencv-python` (cv2)、`numpy`、`Pillow`
- `jpype1`(scrcpy JVM 桥)、`av`(PyAV,H.264 解码)
- PaddleOCR(仅 tft 用,斗地主不需要)

### 外部大文件(硬编码路径,遵循 DEPLOY.md)
- **scrcpy SDK jar**:`gameauto/resource/hosScrcpy-1.0.15-beta.jar`(run 脚本 Step 3 显式传)
- **Java (jbr)**:`D:/resource/jbr` 或 `E:/DevEco Studio/jbr`(bridge 自动探测)
- **DouZero 模型**:`D:/resource/douzero/{landlord,landlord_up,landlord_down}.ckpt`
- **hdc**:`tools/hdc/hdc.exe`(HDC 兜底用)

### 模板
`skills/doudizhu_douzero/assets/templates/{cards,others,ui,buttons}/` —— 69 张(cards 28 + others 28 + ui 4 + buttons 10)。命名见 [TEMPLATES.md](TEMPLATES.md)。

### 配置
- `~/.gameauto/settings.yaml`:device.serial + vlm(斗地主不用 vlm 但脚本会查)
- `skills/doudizhu_douzero/config.yaml`:max_rounds / model_dir / 置信度

### 运行
```bash
python gameauto/run_doudizhu_douzero.py
```
ROUNDS 在脚本顶部常量改。scrcpy 优先,失败自动 HDC。Ctrl+C 强制退(SIGINT handler)。

---

## 八、未解决问题(明天优先)

### 🔴 1. scrcpy 旧帧致重复操作(最高优先,90%+ 概率)

**现象**:点「要不起」后画面已变,但下一轮截图仍是要不起 → 重复点。round 33→34 证据:`Screenshot: 0.5ms`(只读缓存,非真截图),间隔 1.67s 却仍旧帧。

**根因**:scrcpy `screenshot()` 读 `_latest_png` 缓存,on_data(JVM 线程)异步更新。**视频流停滞**(on_data 不推新帧)时缓存冻在旧帧。

**已尝试**:`screenshot(wait_new=True)` 等 `_frame_count` 增加。**没根治** —— 如果流真停了,等不到新帧,超时仍返回旧帧。

**下一步排查方向**:
- `on_data` 的 `except: pass`(bridge.py:232)**吞了所有异常** —— 先 log 出来看流为什么停(gRPC 断?解码错?)
- 检查是否上次 scrcpy 进程/JVM 没杀干净(用户怀疑)
- 加**视频流心跳检测** + 停滞时**自动重连**(`_device.startCaptureScreen` 重启)
- 或退一步:点击后**显式 sleep + 校验屏幕变了**(识别不到原按钮才算成功,否则重试)

### 🟡 2. 完整 DouZero env(释放 AI 全部实力)

当前规则决策稳但非最优。要换回 DouZero 需精确 env:
- **精确推断对手手牌**:整副牌 − 我手牌 − 底牌 − 已知出过的牌(而非随机)
- **完整出牌历史**:逐帧准确推进(当前靠校准,历史空)
- **轮次同步**:env.acting 与屏幕按钮一致

### 🟡 3. 不拆组合(规则决策改进)

当前 `_rule_select` 优先组合但没做「手牌组合分解」—— 出对子仍可能从三张拆。要精确不拆,需贪心分解手牌成「顺子+连对+对子+三带+单张」不重叠组合。

### 🟢 4. 多手机适配

像素 ROI 基于本机 2848×1276。换机需重标(crop_template)或 ROI 归一化(README 8.1)。

---

## 九、关键文件索引

| 文件 | 职责 |
|------|------|
| [perception.py](perception.py) | 分区 CV 感知(PX/TASKS/缓存/buttons_only/颜色校验) |
| [decision.py](decision.py) | 决策(`_USE_RULE`/`_calibrate_env`/`_rule_select`/`_fill_count`/`_action_priority`) |
| [states.py](states.py) | 分层 handler(idle/pass/叫牌/出牌)+ landlord 缓存 unlock |
| [visualizer.py](visualizer.py) | 可视化(归一化转像素,加粗描边,ROI框) |
| [run_doudizhu_douzero.py](../../run_doudizhu_douzero.py) | 入口(scrcpy/HDC/ROUNDS/SIGINT) |
| [../../core/capture/scrcpy/bridge.py](../../core/capture/scrcpy/bridge.py) | scrcpy JVM 桥(screenshot/on_data/touch) |
| [../../core/orchestration/loop.py](../../core/orchestration/loop.py) | 主循环(tap 50ms/step_interval/wait action) |
| tools: crop_template / viz_doudizhu / offline_doudizhu | 标注/可视化/离线决策 |

---

## 十、调试技巧

- **看某轮识别+决策**:`logs/<session>/round_NNN/perception.json` + actions.json + perception.png + actions.png
- **重新可视化某 session**:`python tools/offline_doudizhu.py`(离线单步决策)或写脚本对 screenshot.png 跑 `perception.recognize`
- **game.log**:人类可读复盘(GAME 级);**debug.log**:全量(含栈)
- **scrcpy 是否停滞**:看 log 有无 `scrcpy: X.Xs 内无新帧`
- **决策走哪条路**:log `[rule]`/`[douzero]`/`[UI判定]`/`[固定策略]`

---

## 十一、给新窗口的快速上手

1. 先读本文件(已读)+ 扫 [README.md](README.md)(全景)+ [DEVLOG.md](DEVLOG.md)(问题全集)
2. 当前主线:**scrcpy 旧帧重复操作**(第八节🔴)—— 这是最高优先,影响可用性
3. 代码主线:decision.py(`_USE_RULE` 规则决策 + 校准)、bridge.py(scrcpy 截图)、loop.py(执行)
4. 跑:`python gameauto/run_doudizhu_douzero.py`,看 `logs/<最新session>/game.log`
5. 改决策:`_USE_RULE` 开关 + `_rule_select`/`_fill_count`/`_action_priority`
6. 改感知:perception.py `PX`(ROI)/`TASKS`(模板任务)

**最近 commit**:`5c0f821`(tap 50ms + 规则决策优化)。scrcpy wait_new 修复 + 炸弹边界未 commit(在新窗口决定)。
