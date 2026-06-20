# 斗地主(DouZero)视觉层模板清单

> VLM「认得是什么、说不清在哪」,坐标对齐困难 → 斗地主切纯模板匹配。
> 本文档列出 DouZero 视觉感知需要的全部模板、用途、命名规范与截取要点,
> 并附原版开源实现的参考路径。模板截取见 `gameauto/tools/crop_template.py`。

## 1. 原版参考实现

- **路径**:`D:\gameauto\mobilerun\tmp\DouZero_For_HappyDouDiZhu-2.0\`
- **识别代码**:`main.py`(PyQt5 + `pyautogui.locateAll` 模板匹配)
- **模板图**:`pics/`(借鉴自 cardRecorder 项目)
- **重要**:原版 Kwai/DouZero 是**纯模拟环境,无任何视觉代码**;这套截图+识别是 HappyDouDiZhu fork 补的。原版在 PC 1920×1080 窗口用 `pyautogui` 实时截屏;我们是 HDC/scrcpy 手机截图,识别方案可移植,但 **ROI 坐标必须重新标定**。

牌名约定与框架 `douzero/env/game.py` 的 `RealCard2EnvCard` 完全一致:
`3 4 5 6 7 8 9 T J Q K A 2 X D`(T=10, X=小王, D=大王)。

## 2. 模板命名规范

| 前缀 | 含义 |
|------|------|
| `m{b\|r}{点数}` | 我的手牌角标(b 黑 / r 红) |
| `o{b\|r}{点数}` | 对手出牌 / 3 张底牌角标 |

- **颜色必须分开**:同点数红(红桃/方块)与黑(黑桃/梅花)的点数字符颜色不同,不分色会误识别。
- **m 与 o 必须分开**:我的牌渲染大、对手牌小,模板尺寸不同。
- **模板是「左上角角标」小图,不是整张牌**(手牌横向堆叠,只露左上角点数+小花色)。
- 参考尺寸:手牌角标 34×46、对手角标 23×33(来自原版 pics 实测)。

## 3. 完整清单(67 项)

| 类别 | 子目录 | 命名 | 数量 | 用途(对应 perception/decision 字段) | 原版参考 |
|------|--------|------|------|--------------------------------------|----------|
| 手牌角标 | `cards/` | `mb*` `mr*` | 28 | `my_hand`(init_round)+ `card_positions`(选牌点击) | pics/mb3.png ~ mrD.png |
| 对手角标 | `others/` | `ob*` `or*` | 28 | `last_play`(对手上一手)+ `landlord_cards`(3 张底牌,复用 o 系列) | pics/ob3.png ~ orD.png |
| 地主标志 | `ui/` | `landlord_words` | 1 | `is_landlord` / 我的位置(landlord / landlord_up / landlord_down) | pics/landlord_words.png |
| 不出气泡 | `ui/` | `pass` | 1 | `is_pass` | pics/pass.png |
| 清牌白块 | `ui/` | `white` | 1 | 出牌区清空检测(轮次切换/动画结束) | pics/white.png |
| 按钮 | `buttons/` | 中文 | 8 | `buttons`(坐标+文字),供 bidding/playing/settlement 点击与状态机 detector | 原版无(人手动点) |

- 黑色点数(14):`3 4 5 6 7 8 9 T J Q K A 2 X`(小王偏黑)
- 红色点数(14):`3 4 5 6 7 8 9 T J Q K A 2 D`(大王偏红)
- 按钮 8 个:`叫地主 不叫 抢地主 加倍 不加倍 出牌 不出 继续`

## 4. 原版识别关键参数

- 置信度:我的牌 **0.95**、对手/底牌 **0.9**(main.py:53-57)
- **距离去重 `cards_filter`**(main.py:310):同一模板在同张牌的角标上可能多次命中,以距离阈值(我的 40px、对手 25px)合并,否则同一张牌被数成多张。我们的 `template_match.py` 有 NMS,需确认 IoU 阈值适合堆叠角标。
- 地主判定:`landlord_words.png` 在 3 个玩家头像位置匹配,命中者为地主(main.py:271)。
- 轮次切换:反复轮询出牌区 `white.png`(清空)或 `pass.png`(不出),用于等待对手出牌动画(main.py:188-260)。

## 5. 跑通还需补的卡点(为什么现在没通)

1. **颜色前缀剥离缺失**:`perception.py` 的 `_parse_card_matches` 把模板名 `mb3`/`mr3` 当牌名,但 `decision.py` 查 `card_positions.get("3")`(只要点数)→ 选牌点击永远查不到坐标。需在 perception 把 `mb3/mr3 → 3`(去掉首字母颜色)。
2. **中文按钮模板路径**:`buttons/` 文件名是中文(`叫地主.png`),`template_match.py` 若用 `cv2.imread` 在 Windows 读中文路径会失败,需改 `cv2.imdecode(np.fromfile(...))` 或用 PIL。
3. **置信度偏低**:`config.yaml` 的 `card_confidence=0.85` 建议提到 0.9+(原版我的牌用 0.95)。
4. **ROI 未标定**:`assets/rois.json` 当前为空。需标定手牌区 / 上家出牌区 / 下家出牌区 / 底牌区 / 按钮区(归一化 [0-1])。`crop_template.py` 截取时会顺带把每个模板的来源框写进 `rois.json`,可作标定参考。
