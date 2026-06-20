# 斗地主 DouZero 开发日志与问题记录

> 本文件记录斗地主自动化开发过程中遇到的问题、根因、解决方案, 以及当前架构的局限和未来方向。
> **未来恢复上下文时先读此文件 + [README.md](README.md)。**

---

## 当前状态(2026-06-21)

- 纯 CV 模板匹配(感知)+ DouZero(决策)+ HDC(执行)跑通, 能正确压牌。
- 决策层当前用**方案 A(校准式)**: 每帧用屏幕 last_move 校准 env, 让 DeepAgent 压牌。**这是过渡方案**, 未来需要完整的 DouZero env(见末尾)。

---

## 架构概览

```
截图(HDC, ~850ms) → 分层 handler → DouZero 决策 → HDC 点击
                       │
       第1层: buttons_only 识别(快) → 按按钮决定:
         · 无按钮 → 休眠0.5s(不在自己轮次)
         · 要不起 → 直接 pass
         · 叫牌/继续/开始游戏 → 点按钮(固定策略)
         · 出牌 → 第2层: 全量识别手牌 → DouZero
```

- 感知: [perception.py](perception.py) 分区模板匹配 + 颜色校验
- 决策: [decision.py](decision.py) 方案 A 校准 env + DeepAgent
- 状态机: [states.py](states.py) 分层 handler
- 工具: [crop_template.py](../../tools/crop_template.py) / [viz_doudizhu.py](../../tools/viz_doudizhu.py) / [offline_doudizhu.py](../../tools/offline_doudizhu.py)

---

## 已解决的问题(按类别, 含根因)

### 一、感知层(CV 模板匹配)

| 问题 | 根因 | 解决 |
|------|------|------|
| VLM 识别牌但坐标对不齐 | 大模型说不清"在哪" | 切纯 CV 模板匹配(模板即坐标) |
| 红牌/黑牌互串(红3匹配到黑3) | `TM_CCOEFF_NORMED` 减均值归一化削弱颜色, 红3/黑3字形相同 | 命中后颜色校验 `_is_red`(R 明显> G/B)只认对应色 |
| 中文按钮模板加载失败 | `cv2.imread` 在 Windows 读中文路径返回 None | 改 `cv2.imdecode(np.fromfile(..., dtype=uint8))` |
| 决策选不到牌坐标 | 模板名 `mb3`/`mr3` 当牌名, 但决策查点数 `3` | `_strip_color` 剥离颜色前缀 → 点数 |
| 出对子点同一位置 | `card_positions` 按点数存单坐标 | 改 `{点数: [(x,y),...]}` 列表, 消费式取位 |
| 全图匹配慢 + 跨区误匹配 | 28×2 手牌模板全图搜 | **分区匹配**(手牌/对手/底牌/按钮各 ROI), 物理隔离 + 提速 3 倍 |
| 底牌识别不到 | 底牌比对手出牌区小 ~65% | 底牌 ROI 单独 scale 0.65 匹配 |
| 对手两区牌合并成非法牌型 | play_up + play_down 合并成一个 last_play | 分别产 `last_play_up`/`last_play_down`, 要压的牌取上家 |

### 二、状态机 / handler

| 问题 | 根因 | 解决 |
|------|------|------|
| 「继续」「开始游戏」识别不到 | `detect_any_button` 传 `roi=None`, `_match_inline` 内部 `roi[0]` 抛异常被 except 吞 → 静默 False | None 时传全图 `(0,0,1,1)` |
| detector 漏判按钮 | detector 阈值 0.88 > recognize 0.85, 边界得分丢 | detector 降到 0.78(宽松路由, handler 内 recognize 精确把关) |
| 「不出」+「出牌」时误 pass | pass 分支把"不出"也算进去 | 只有「要不起」才直接 pass; 「不出+出牌」交 DouZero |
| 无按钮帧密集截图空转 | 状态机 unknown 不进 handler, loop 无 sleep | 加 `idle` 兜底状态(detector 恒真)→ handler 判无按钮 sleep 0.5s |
| 每帧全量识别手牌浪费 | 大部分帧只需按钮 | **分层识别** `recognize(buttons_only=True)`, 只在出牌轮全量 |
| 点击后立刻截图识别到动画帧 | 发牌/出牌动画未结束 | 点击后追加 `wait` action(开始游戏 5s / 其他 3s) |
| 决策崩了连识别结果都丢 | `_save_debug` 在 decide 之后 | 改成识别先落盘、再决策 |

### 三、决策 / env(**最棘手的一块**)

| 问题 | 根因 | 解决 |
|------|------|------|
| 对手出牌后我方乱出更小的牌 | env 靠逐帧 perception 推进, 漏识别/误判一帧就累积漂移 → env 不知对手出牌 → DeepAgent 当自由出 | 方案 A: 每帧用屏幕 last_move 校准 env |
| `is_pass` 误判盖过真实出牌 | `_handle_opponent_turn` 里 `if is_pass` 优先于 `elif last_play` | 改 last_play 优先(牌比 pass 标志可信) |
| `_opponent_play` step 抛 AssertionError | 对手 set 的牌不在 legal_actions(轮次错位/牌型非法) | try/except + reset env 兜底 |
| 方案 A 校准"没生效"(仍出 3) | `_calibrate_env` 笔误 `RealCard2RealCard`(不存在)→ NameError 被 try/except 吞 | 改 `RealCard2EnvCard`; **教训: 关键路径别用宽 try/except 吞异常** |
| 校准后 legal_actions 还是旧的 | GameEnv.game_infoset 是字段(init/step 才更新), 校准没重算 | 校准末尾调 `get_infoset()` 重算 legal_actions |
| env 手牌是开局全量(含已出牌) | 单步模式不 step, 手牌没减 | 校准时把 env 我方手牌设为屏幕当前 my_hand |

### 四、工程 / 工具

| 问题 | 根因 | 解决 |
|------|------|------|
| 实战点击位置全偏 | perception 输出像素, 但框架 Action 约定 [0-1000] 归一化 | perception 输出归一化, visualizer 画图转回像素 |
| 可视化文字太浅看不清 | PIL 细字无描边 | 粗体字(msyhbd) + 黑色描边 stroke_width |
| ROUNDS=1 只跑一轮 | round = 循环次数(每轮一次截图决策), 非局数 | 设大值(100); 顶部常量配置 |
| 截图 bat 中文乱码 | UTF-8 bat 被 cmd 按 GBK 解析 | bat 纯 ASCII 内容 |
| HDC 截图/点击慢(~850ms) | HDC 子进程开销 | **待办: 换 scrcpy**(见 README 8.3) |

---

## 当前方案 A(校准式)原理与局限

**原理**(decision.py `_calibrate_env`): 每帧我方决策前, 用屏幕 ground truth 校准 env:
1. `last_move`(要压的牌)= 屏幕 `last_play_up`(上家), 上家 pass 则 `last_play_down`
2. 写入 env `card_play_action_seq[-1]` → 让 `get_last_move` 返回它
3. env 我方手牌 = 屏幕 `my_hand`
4. `acting_player_position` = 我方(屏幕[出牌]=轮我)
5. `get_infoset()` 重算 → `legal_actions` 基于屏幕 last_move + 手牌
6. DeepAgent 从 legal_actions 选最优

**局限**(已知, 决策非最优但能正确压牌):
- 对手手牌仍是 init 时的随机猜测(env 无法知道真牌)→ DeepAgent 特征里这块不准
- 校准替换 `action_play_action_seq[-1]`, LSTM 历史特征失真
- 不 step env(单步), 各家剩余牌数特征不准
- 只保证"压对牌", 不保证"最优策略"

---

## 未来: 完整 DouZero env(目标)

当前方案 A 是为绕开「env 漂移」的妥协。要发挥 DouZero 全部实力, 需要精确维护 env 状态(三家手牌 + 完整出牌历史 + 轮次), 让 DeepAgent 基于准确 infoset 决策。

**难点**:
1. **对手手牌永远未知** —— 只能蒙特卡洛采样(多局平均)或统计推断
2. **完整出牌历史需逐帧准确识别 + 推进** —— 这是当前漂移根源, 必须先把识别做准
3. **轮次同步** —— env.acting_player_position 必须与屏幕按钮一致

**推进路径**:
1. 先把识别做准(手牌不漏、last_play 合法、is_pass 不误判)—— 漂移少了, 跨帧 env 才稳
2. 轮次同步以**屏幕按钮为权威**(屏幕[出牌]=轮我, 强制校准 acting)
3. 对手手牌: 每局 init 时用「整副牌 − 我手牌 − 底牌 − 已知出牌」推断剩余, 蒙特卡洛分配
4. 每局重新 init + 严格推进(而非跨局累积 env)

---

## 教训(给未来的自己)

1. **别用宽 `try/except: pass` 吞异常** —— 方案 A 笔误被吞了 N 轮才靠日志发现。关键路径至少 `logger.exception`。
2. **决策崩了也要保留识别** —— 识别先落盘, 否则崩溃帧无从诊断。
3. **屏幕 UI 是 ground truth** —— env 状态会漂移, 屏幕「要不起/出牌」按钮比 env.acting 可靠。
4. **两个对手区绝不能合并** —— 各家出牌独立, 合并必出非法牌型。
5. **模板匹配颜色不敏感** —— CCOEFF 减均值, 红/黑要靠后处理颜色校验区分。
