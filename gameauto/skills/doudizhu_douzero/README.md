# 斗地主 DouZero 自动化(CV 模板匹配方案)

> 纯视觉方案:截图 → CV 模板匹配(感知)→ DouZero 深度强化学习(决策)→ HDC 点击(执行)。
> 无 VLM 依赖,单帧 <1s,真机实战就绪。

---

## 一、背景与方案选型

VLM 能识别「是什么」但说不清「在哪」——扑克牌/棋子的**坐标对齐**是最大瓶颈。
因此斗地主(及象棋)从 VLM 切换到**纯 CV 模板匹配**:模板即坐标,匹配即定位。

决策层复用开源 [DouZero](https://github.com/kwai/DouZero)(ICML 2021,Deep Monte Carlo,
Botzone 排名第一)。DouZero 原版是**纯模拟环境无视觉代码**,视觉模块参考
`tmp/DouZero_For_HappyDouDiZhu-2.0`(借鉴 cardRecorder 的扑克模板)。

整体定位:**CV 做眼睛,DouZero 做大脑**。

---

## 二、架构与数据流

```
HDC 截图(原尺寸)
   │
   ▼
DouDiZhuDouzeroPerception.recognize()        ← 分区模板匹配 + 颜色校验
   │   产出: phase / my_hand / card_positions / last_play /
   │         landlord_cards / is_pass / is_landlord / buttons
   ▼
StateMachine(detector 选状态 → handler)       ← LOBBY/BIDDING/PLAYING/SETTLEMENT
   │
   ▼
DouzeroDecision.decide()                      ← DouZero DeepAgent(playing)/ 按钮策略
   │   产出: list[Action] (坐标归一化 [0-1000])
   ▼
GameLoop → HdcInput.tap/swipe                 ← 归一化→像素, 点击真机
   │
   ▼
每轮落盘 round_NNN/ (截图+识别+决策+可视化)
```

---

## 三、核心实现

### 3.1 分区模板匹配(解决互串 + 提速)

模板按区域隔离匹配,而非全图:[perception.py](perception.py) `PX` + `TASKS`。

| 区域 | 像素 ROI (x1,y1,x2,y2) | 模板 | scale |
|------|------------------------|------|-------|
| 手牌 | (350, 770, 2600, 970) | cards (mb/mr) | 1.0 |
| 上家出牌 | (700, 200, 1424, 600) | others (ob/or) | 1.0 |
| 下家出牌 | (1425, 200, 2100, 600) | others | 1.0 |
| 底牌 | (1000, 0, 2100, 130) | others | **0.65** |
| 按钮 | (500, 600, 2200, 800) | buttons | 1.0 |
| 继续 / 开始游戏 / 地主标 | **全图** | buttons / ui | 1.0 |

- **物理隔离**:cards 只在手牌区,others 只在对手/底牌区 → 不跨区误匹配。
- **单 scale**:模板就是从该区截的,尺寸固定,无需多尺度探索(提速 3 倍)。
- 像素 ROI 运行时按图尺寸转归一化,传给 `TemplateMatchTask.run(roi=...)`。

### 3.2 颜色校验(红黑防互串)

`cv2.matchTemplate` 的 `TM_CCOEFF_NORMED` 减均值归一化会削弱颜色 —— 红3/黑3 字形
相同,会互相匹配高分。解法:命中后取区域做颜色后校验([perception.py](perception.py)
`_is_red` / `_color_matches`)—— 红牌(R 明显高于 G/B)只认 `mr/or`,黑牌只认 `mb/ob`。

### 3.3 底牌识别(scale 0.65)

底牌相对对手出牌区缩小约 65%。用同一套 `others` 模板,在底牌 ROI 以 `scale=0.65`
匹配。复用模板,免额外标注。产出 `landlord_cards`(原 perception 缺失,已补)。

### 3.4 坐标归一化 [0-1000]

perception 输出的 `card_positions`、`buttons[].x/y` 全部归一化到 [0-1000]
(框架 `Action` 约定)。decision 直接用,HdcInput 执行时 `to_absolute` 还原像素。
单台手机内分辨率固定,归一化保证语义一致。

### 3.5 状态机(四状态流转)

[states.py](states.py),first-match 语义,注册顺序即优先级:

```
LOBBY(开始游戏) → BIDDING(叫牌) → PLAYING(出牌) → SETTLEMENT(继续) → LOBBY/下一局
```

- detector 用 `perception.detect_any_button()`(同步快路径,封装 `_match_inline`)。
- 「继续」「开始游戏」位置不固定 → 全图搜;叫牌/出牌按钮 → buttons ROI。
- handler 统一:`recognize → decide → 录制 → 返回 actions`。
- 「要不起」按钮当作 pass(与「不出」等价)。

### 3.6 DouZero 决策集成

[decision.py](decision.py) 包装 `douzero/env` + `DeepAgent`:

- **init_round**:用识别的手牌+底牌初始化 env;对手手牌未知 → 从整副牌扣除已知后
  随机分配(蒙特卡洛单样本,不完全信息→完全信息化)。
- **playing**:轮到自己 → `DeepAgent.act(infoset)` 给最优出牌 → `_build_play_actions`
  转成选牌 tap + 「出牌」按钮(同点数多张时**消费式取位**,保证对子/三带二点不同位置)。
- **bidding/settlement/lobby**:纯按钮策略(保守:不叫/不加倍;点继续/开始游戏)。
- 三个位置(landlord / landlord_up / landlord_down)分别懒加载 `.ckpt`。

---

## 四、模板体系(69 张)

详见 [TEMPLATES.md](TEMPLATES.md)。命名与 DouZero `RealCard2EnvCard` 无缝对接。

| 类别 | 子目录 | 命名 | 数量 |
|------|--------|------|------|
| 手牌角标 | cards/ | `m{b\|r}{点数}` | 28 |
| 对手/底牌角标 | others/ | `o{b\|r}{点数}` | 28 |
| UI 标志 | ui/ | landlord_words / pass / white | 3 |
| 按钮 | buttons/ | 叫地主/不叫/抢地主/加倍/不加倍/出牌/不出/要不起/继续/开始游戏 | 10 |

点数表:`3 4 5 6 7 8 9 T J Q K A 2 X(小王) D(大王)`;模板是牌**左上角角标**小图(非整牌)。

---

## 五、工具链(`gameauto/tools/`)

| 工具 | 用途 |
|------|------|
| [crop_template.py](../../tools/crop_template.py) | 清单驱动标注模板:实时放大镜 + 无损裁剪 + 进度持久化。输出 `D:/screenshots/doudizhu_templates/` |
| [viz_doudizhu.py](../../tools/viz_doudizhu.py) | 分区匹配可视化:画 ROI 框 + 命中框 + JSON,验证标注质量 |
| [offline_doudizhu.py](../../tools/offline_doudizhu.py) | 离线单步决策:截图→perception→decision,紫色序号圈标决策步骤,不碰手机 |
| [capture_screenshot.py](../../tools/capture_screenshot.py) | HDC 一键截图 |
| `D:/screenshots/截图.bat` | 纯 bat 调 hdc 截图(原尺寸,自动命名),双击即截 |

`extract_templates.py` / `label_templates.py` 为早期自动/对照标注工具,已被 crop_template 取代。

---

## 六、运行时录制

每轮产物落 `logs/<session>/round_NNN/`(由 [states.py](states.py) `_save_debug` + GameLoop 写):

```
round_NNN/
├── screenshot.png     # 原始截图(GameLoop)
├── perception.json    # 识别结果
├── perception.png     # 识别可视化(手牌/按钮框)
├── actions.png        # 决策点击可视化
└── actions.json       # 决策步骤明细(step/type/x/y/description)
```

会话级:`debug.log`(全量结构化)+ `game.log`(GAME 级人类可读复盘)。

> 注:`DataRecorder.record_frame`(frames/ 结构化录制)尚未接入 GameLoop,当前 round_NNN/ 一套已足够。

---

## 七、已验证指标

| 指标 | 结果 |
|------|------|
| 感知+决策(离线) | 平均 **745ms/帧**,最快 661ms,全部 <1s |
| 可视化匹配 | 平均 0.87s/张(含全图地主/继续搜) |
| 模板完整度 | 69/69(0 跳过) |
| 互串 | 消除(分区 + 颜色校验双保险) |
| DeepAgent 出牌 | 验证通过(单张/对子/顺子建议合理) |

---

## 八、下一步计划

### 8.1 适配其他手机(多分辨率)

当前像素 ROI 和模板都基于**一台手机**标定,换机即失效。三个层次方案:

1. **ROI 归一化(短期,推荐)**:把 `PX` 存成归一化 [0-1] 而非像素(基准机标一次),
   换机后按 `×新分辨率` 自动缩放 ROI。手牌/按钮的相对位置跨机一致,归一化后通用。
2. **模板多尺度容忍(短期)**:换机后角标像素尺寸变化,匹配时用 `scales=[0.9,1.0,1.1]`
   覆盖(框架已支持,牺牲少量速度)。模板图本身可跨机复用(同游戏 UI 比例一致)。
3. **每机独立标定(中期)**:换机时跑 `crop_template` 在新机重新标 ROI + 模板。
   工作量大但最准。可结合方案 1/2 减少标注量。
4. **自动 ROI 检测(长期)**:用特征(如手牌区的卡背排列、按钮的颜色块)自动定位
   各区域,免标定。复杂度高,作为终极目标。

落地路径:先做方案 1(ROI 归一化,改动小)→ 配合方案 2 多尺度 → 大多数同比例手机即可通用。

### 8.2 鲁棒性提升

- **手牌漏识别**(离线已见,如图 26/27 只识到 9~11 张):该帧手牌处于选中/上浮态角标
  变位 → 补「选中态」模板,或扩大手牌 ROI y 范围,或针对性降阈值。
- **状态稳定滤波**:出牌动画/过渡帧识别不稳定 → 连续 N 帧同状态才动作,避免抖动误点。
- **错误恢复**:点击后画面无变化(检测同一 ROI 不变)→ 重试或重截图;卡死超时回大厅。
- **底牌时机**:底牌仅开局翻开时可识别,需在正确帧捕获(实战靠轮询 + 状态判断)。
- **对手出牌(last_play)准确性**:对手牌小且短暂,阈值/ROI 需实测调优。
- **DouZero init 容错**:手牌识别不全致 env 牌数不平衡 → init 前校验牌数,不全则跳过
  本帧决策等下一帧,而非抛异常。
- **阈值自适应**:不同截图亮度/质量,固定 0.88 可能偏高/低 → 记录 score 分布,动态调整。

### 8.3 实战待观察项

- **点击归一化执行**:坐标已归一化,但真机 HdcInput 的 `input_resolution` 必须设成
  native(已做),首次实战确认点击位置准确。
- **HDC 截图/点击慢 → 待办:换 scrcpy**:实测 HDC 单次截图 ~850ms、点击也慢,
  是当前闭环最大性能瓶颈。`core/capture/scrcpy/` 已实现(H.264 流截图 ~17ms、
  JVM 内触控 ~0.01ms),未来用 `ScrcpyCapture` + `ScrcpyInput` 替换 HDC 的截图和点击
  即可大幅提速 —— 接入方式参考 `run_match3_scrcpy.py`(改 2 行 import + 末尾 `_os._exit(0)`)。
  注:scrcpy 依赖 JVM + HOScrcpy SDK JAR(`D:/resource/hosScrcpy-*.jar`),首启较重。
- **轮次同步**:当前靠 `is_pass` + `last_play` 推进 env;若实战出现轮次错乱,
  考虑接入 `white`(清牌检测)做同步信号(原版 main.py 方案)。
- **多局连续**:LOBBY→...→SETTLEMENT→LOBBY 闭环,需验证状态机跨局正确复位。

---

## 九、关键文件索引

| 文件 | 职责 |
|------|------|
| [perception.py](perception.py) | 分区 CV 感知 |
| [decision.py](decision.py) | DouZero 决策封装 |
| [states.py](states.py) | 四状态机 + 录制 |
| [visualizer.py](visualizer.py) | 识别/动作可视化 |
| [skill.py](skill.py) | Skill 门面 |
| [TEMPLATES.md](TEMPLATES.md) | 模板清单与命名规范 |
| [config.yaml](config.yaml) | rounds / model_dir / 置信度 |
| `douzero/` | 第三方 DouZero(env + DeepAgent) |
| `assets/templates/` | 69 张模板 + rois.json |
| [run_doudizhu_douzero.py](../../run_doudizhu_douzero.py) | 真机实战入口 |
