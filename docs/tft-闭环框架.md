# TFT 真机闭环框架 (感知 → 决策 → 动作)

> 目标: 把已做好的**感知层**(血条/OCR/模板)和**分层动作**(中层/顶层)串成真机自动对局。
> 本文档是框架设计, 供审阅"还差什么"再开发。

---

## 一、全景数据流

```
        ┌─────────────────────────────────────────────────────────┐
        │  GameLoop (每轮, 复用 core/orchestration/loop.py)        │
        └─────────────────────────────────────────────────────────┘
          │
   1. 截图  ▼   ScrcpyCapture / HdcCapture  (core/capture, 后端可换)
          │   → PNG bytes / BGR frame
          │
   2. 感知  ▼   TftSkill.perceive(frame)  →  state
          │   分层感知 (按当前阶段决定调哪些, 省 OCR):
          │     · 轻量: 模板匹配判阶段 (LOBBY/PLANNING/COMBAT/RESULT/...)
          │     · 重:   PLANNING 才 full_perceive (血条+商店OCR+金币)
          │   state 含: phase, board_clicks, bench_clicks, shop[], gold, ...
          │
   3. 决策  ▼   TftSkill.decide(state)  →  list[Action]
          │   双层:
          │     · 长期 (LLM, 每备战阶段 1 次, 3-5s): 给战略指令写 context
          │     · 短期 (规则, 每 tick): 按 state + 长期指令 → 中层/顶层动作
          │   动作全程归一化 [0-1000], 与分辨率无关
          │
   4. 执行  ▼   GameLoop._execute_action(action)  →  BaseInput
          │     tap/swipe/drag  →  ScrcpyInput 或 HdcInput (后端切换)
          │   步间 step_interval 等动画 (tap 150ms 起)
          │
   5. 落盘  ▼   每轮 screenshot + perception.json + actions.json
                   (识别先于决策, 决策崩也保留)
```

**关键**: 感知和动作都已就绪并解耦——感知出 `state`, 决策用中层 `TftActions` 产出 `list[Action]`,
`GameLoop` 执行。换 HDC/scrcpy = 换 BaseInput, 动作层零改动。

### ⚡ 按状态机阶段选择性识别 (省算力, 重要设计)

**不是每帧全量识别**, 而是根据当前阶段/时机只跑需要的感知:

| 何时识别 | 识别什么 | 为什么 |
|---------|---------|--------|
| 战斗阶段 (COMBAT) | **血条** (棋盘+战备棋子定位) | 战斗中棋子在打, 血条最直观; 此时商店无关 |
| 备战阶段 (PLANNING) | 商店 OCR + 金币 + 血条 | 要决策买/卖/升级 |
| 特定回合 (如 3-2) | **海克斯强化符文** (augment) | 只在这几个回合出现, 平时识别是浪费 |
| 判断是否在商店界面 | **刷新按钮**模板匹配 | 看见刷新按钮 = 在商店; 看不见 = 商店关着 |
| 每回合结束后 | **全图 OCR 一次** | 兜底读结算/状态, 不在 ROI 里单列 |

**已从常规 OCR ROI 移除 (省算力)**: 血量 hp / 商店开关 shop_toggle / 结算 continue_btn。
- hp: 不影响执行, 后期决策再用 (商店开关=点 gold 位置即可)。
- 结算: 靠每回合后全图 OCR, 不在 ROI 单列。
掉落物 (问号) 暂时全图 OCR 找, 后续标 `drop_region` 小框提速。

---

## 二、状态机 (游戏阶段)

每个阶段一个 handler, 决定"调什么感知 + 产什么动作"。

| 阶段 | 触发检测 (模板) | 感知 | 动作 |
|------|----------------|------|------|
| LOBBY | `lobby_play_btn` | 无 | tap 开始游戏 → wait 6s |
| LOADING | 都不匹配 | 无 | sleep 等 |
| PLANNING (备战) | `planning_timer` + 刷新按钮在 | full_perceive | 短期决策: 买/卖/装备/站位/刷新/升级 |
| COMBAT (战斗) | `combat_indicator` | 血条 (棋盘+战备) | sleep 2s |
| CAROUSEL (选秀) | `carousel_banner` | 无 | tap 中心棋子 |
| AUGMENT (海克斯) | `augment_frame` | 无 | tap 第一个 |
| PVE (野怪) | `pve_indicator` | 同 PLANNING | 同 PLANNING |
| RESULT (结算) | `result_rank` | 无 | tap 继续/返回大厅 |

**分层 handler** (斗地主验证过的模式): 先轻量模板判阶段, 只有 PLANNING/PVE 才跑全量感知。

---

## 三、决策双层 (接感知 → 出动作)

```
┌─ 长期 (LLM API, 每 PLANNING 1 次) ──────────────────────┐
│  输入: state 快照 (棋盘棋子/商店/金币/等级/阶段)          │
│  输出: 战略指令 → 写入 GameContext                       │
│    {comp_target:"凯尔体系", roll_at:"7级", spend_floor:20, │
│     keep:["凯尔","瑟提"], level_now:true}                │
│  模型: Qwen3-70B / DeepSeek-Reasoner (走 API)           │
└─────────────────┬───────────────────────────────────────┘
                  │ GameContext.strategy
┌─ 短期 (规则, 每 PLANNING tick) ──────────────────────────┐
│  读 state + strategy, 用 TftActions 产出 list[Action]:   │
│    · 商店出现 keep 棋子 → buy_shop_slot(i)               │
│    · 金 ≤ spend_floor → 停止花钱                         │
│    · level_now 且金够 → buy_xp()                         │
│    · 多余棋子 → sell_champion(pos)                       │
│    · 装备槽有 → equip_from_slot(slot, 主C位置)           │
│    · 站位 → move_champion(from, to)                      │
│    · 需要找棋子时 → iterate_champions(点开看名再关)       │
└─────────────────────────────────────────────────────────┘
```

短期规则 = 把 LLM 的抽象指令翻译成中层动作序列。LLM 不直接出坐标, 只出意图。

---

## 四、已就绪 vs 待补 (真机实验前)

### ✅ 已就绪
- 截图: ScrcpyCapture (HarmonyOS, scale=2, 低延迟)
- 输入: ScrcpyInput / HdcInput (tap/swipe, 归一化[0-1000]→像素)
- 感知: 血条检测(棋盘+bench), OCR微服务, 模板匹配引擎
- 动作中层: TftActions (refresh/buy_xp/buy_slot/click_champion/sell/equip/equip_from_slot/move)
- 动作顶层: iterate_champions / iterate_item_drops / buy_all_core
- 坐标转换: utils/coordinate ([0-1000]↔像素↔[0-1])
- 录制: SessionManager (per-round 落盘)
- 回放验证: run_tft_replay.py (可视化感知+动作, 不触设备)

### ❌ 待补 (按优先级)
1. **状态检测模板** — 阶段判断靠它。需裁: lobby_play_btn / planning_timer / combat_indicator / result_rank / carousel_banner / augment_frame / pve_indicator / shop_toggle。(`crop_tft_template.py` 清单已就绪, 标注即可)
2. **TftSkill / 状态机 handler** — `skills/tft/states.py` 骨架在但 handler 是空/旧版。要按上表实现分层 handler (LOBBY/PLANNING/COMBAT/RESULT 闭环先通)。
3. **close_panel 动作** — click_champion 弹面板后要有关闭 (点空白或关闭键), 否则 iterate 卡住。
4. **真机主循环** — `run_tft_scrcpy.py` 入口已有, 要接 TftSkill + 新 actions + 后端配置 (CAPTURE_BACKEND/INPUT_BACKEND, scale=2, SIGINT)。
5. **长期 LLM 接入** — `llm_decide(state)` 钩子 + API client (Qwen3-70B/Reasoner)。短期规则可先不依赖它 (用固定战略) 跑通闭环。
6. **选秀/海克斯动作** — click 中心/第一个, 简单 tap, 中层加 `pick_carousel()`/`pick_augment()`。
7. **timing 校准** — tap 150ms, 各阶段 wait 时长 (斗地主经验: 开始游戏 6s, 回合切换)。

### ⏳ 后续优化
- 装备图标识别 (替固定槽)
- 圆形掉落物检测 (iterate_item_drops 的输入)
- OCR 纠错字典 + 错误样本落盘
- 站位预设坐标库

---

## 五、实验递进计划 (从观察到全自动)

| 里程碑 | 做什么 | 通过标准 |
|--------|--------|---------|
| M1 观察 | 真机只跑感知, 不动作, 落盘每帧 state | 阶段判断正确, 血条/OCR 读数合理 |
| M2 单动作 | 手动触发单个中层动作 (如 refresh) | 刷新生效 |
| M3 备战闭环 | PLANNING 阶段短期规则跑通 (买/卖/升级) | 一局内 PLANNING 自动操作不卡死 |
| M4 全阶段 | + LOBBY/COMBAT/RESULT 流转 | 完整跑一局 |
| M5 接 LLM | 长期层接入, 短期跟指令 | 按 LLM 阵容运营, 平均排名 ≤5 |

**建议先冲 M1→M3**: 感知准 + 备战闭环, 这是 80% 价值。
