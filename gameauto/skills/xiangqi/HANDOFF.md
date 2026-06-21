# 天天象棋 Skill — 交接文档

> 最后更新：2026-06-21 ｜ 分支：`gameauto-m1` ｜ 远端：`myrepo`（github.com:Dajiang1202/mobilerun）

## 一、项目目标
通过图像识别 + AI 引擎自动控制鸿蒙手机玩「天天象棋」：开局点开始/续局 → 识别棋盘 → 引擎算招 → 点击走子 → 循环到一局结束。

## 二、本次对话做了什么（核心成果）
原方案用 **VLM 单次识别整盘**，受阻（网络不稳、坐标非法被静默丢弃、漏判红方跳步）。本次改为 **纯 CV 模板匹配**，并修了两个阻塞实战的引擎 bug，现已能实跑整局。

### 1. 模板匹配感知（替代 VLM）
- 新增 [perception_template.py](perception_template.py) `XiangqiTemplatePerception`，复用框架 [core/perception/cv/template_match.py](../../core/perception/cv/template_match.py) 的 `TemplateMatchTask`。
- 产出与 VLM **完全相同**的 `parsed` dict（`screen_type/board/pieces/buttons`），故 `decision.py`/`engine.py`/`visualizer.py`/`states.py` **零改动**。
- 切换开关：[config.yaml](config.yaml) `perception_method: vlm|template`（默认 `template`），工厂 [perception.py](perception.py) `make_perception()` 路由，VLM 类原样保留，**随时可切回**。

### 2. 引擎 bug 修复（[engine.py](engine.py)，预存 bug，不修无法实战）
- **`_parse_uci_move` UCI rank 翻转**：原把 Pikafish UCI rank 直接当 grid 行号，但 UCI rank 从红方底线数起（rank0=grid[9]），需 `9-rank`。不修则走子整盘上下镜像、点到错的棋子。
- **`PikafishEngine.search` stdin bug**：原用 `communicate(input=...)` 发完即关 stdin，Pikafish 读到 EOF **秒回垃圾走法 `a3a4`、完全不搜索**。改为「写命令不关 stdin、逐行读到 bestmove 再 quit」。修复后开局走 **中炮 `b2e2`**（depth 25）。线程 1→4、哈希 64→256MB。

### 3. 感知调优（实战踩坑得来，参数集中在 config.yaml）
- **颜色判定**：木色背景主导整块区域，原 `_is_red`(R>G&B 占比) 对红黑都返回 True。改为**字形墨色**判定（中心 60% 区域最暗 25% 像素的 R-G，红≈75/黑≈5，阈值 40）。
- **匹配阈值 `piece_confidence`**：0.80→**0.90**。带走子高亮的棋子分数会降到 ~0.93，0.96 太严会漏；靠**网格去重**（每格取所有模板最高分）防 0.90 的跨类型误匹。
- **棋盘定位**：静态标定 `board.json` 优先；缺失时**聚类锁定**——开局（≥28子）用棋子中心 min/max 包围盒推断四角，锁定后**整局复用**（棋盘不动），中后期棋子被吃也不漂移。**零手动标定**。
- **搜索 roi 扩 margin 80px**：锁定的 board rect 是棋子中心 min/max，底排/边列棋子中心在 rect 边缘、本体一半会被 roi 裁掉 → 漏检 → screen=unknown → 不走子 → 画面冻结死循环。扩 margin 修复。
- **screen_type 改将帅判定**：原 `n_pieces>=20` 判 playing，残局少子被误判 unknown 不决策。改为**双方将帅（帥+将）都在 → playing**。

### 4. 日志/体验
- 棋盘识别结果日志改**紧凑 10×9 文本矩阵**（[visualizer.py](visualizer.py) `format_board_matrix`），不再整段 JSON 刷屏。完整 JSON 仍写入 `vlm_response.json`。
- 走子后休眠 1s→**1.5s**（[decision.py](decision.py) `_MOVE_DELAY`）。

## 三、架构与数据流
```
截图(bytes) → perception.recognize() → PerceptionResult(parsed dict)
                                              │
   parsed = { screen_type, board{left,top,right,bottom 归一化0-1000},
              pieces[{piece,side,board_pos{col1-9,row1-10},pixel_pos{x,y 0-1000}}],
              buttons[{text,x,y}] }
                                              ↓
   decision.decide(state) → (actions[Action], move)
        playing: Board.from_pieces → find_best_move_pikafish → _pixel_from_board 几何投影算点击坐标
        menu: 点开始游戏 | game_over: 点再来一局
                                              ↓
   states._handle → 保存调试(vlm_response.json/perception.png/move.png) → 返回 actions
```
关键契约：`decision._pixel_from_board` 仅凭 `board` rect 做几何投影（`cell_w=(right-left)/8`, `cell_h=(bottom-top)/9`, `x=left+(col-1)*cell_w`, `y=bottom-(row-1)*cell_h`，row1=红底）。**棋盘 rect 准 + 棋子归对格，下游全对**。

坐标约定：`col 1-9`、`row 1-10`（**row1=红方底，row10=黑方顶**）；像素/tap 归一化 `[0-1000]`。

## 四、关键参数（config.yaml，调优改这里）
| 参数 | 值 | 说明 |
|---|---|---|
| `perception_method` | `template` | `vlm`/`template` 切换 |
| `template.piece_confidence` | `0.90` | 棋子匹配阈值；高亮棋子~0.93，再漏可降到 0.88 |
| `template.button_confidence` | `0.85` | 按钮匹配阈值 |
| `_MOVE_DELAY`（decision.py） | `1500ms` | 走子后等待 |
| Pikafish movetime（decision.py） | `2000ms` | 引擎思考时间 |
| Pikafish 路径（engine.py `_PIKAFISH_PATH`） | `D:/resource/pikafish/Windows/pikafish-bmi2.exe` | 需带 `pikafish.nnue` |

## 五、资产（[assets/](assets/)，已裁好并提交）
- `templates/pieces/`：14 棋子 `r_帥/r_仕/.../b_将/b_士/...`（紅黑各7，馬/車/炮 同形靠颜色区分）
- `templates/buttons/`：`开始游戏` `再来一局` `返回大厅`
- `templates/ui/`：空（playing_marker/game_over_marker 可选，未裁；靠将帅+按钮自动判屏）
- `templates/_calib/board_rect.png` + `templates/rois.json`：棋盘标定记录
- `board.json`：占位（用聚类锁定，无需手填；要静态标定则跑 `tools/xiangqi_finalize_board.py`）

## 六、工具
| 工具 | 作用 |
|---|---|
| [tools/crop_xiangqi_template.py](../../tools/crop_xiangqi_template.py) | 竖屏专用裁剪器，裁棋子/按钮/board_rect 模板 |
| [tools/xiangqi_finalize_board.py](../../tools/xiangqi_finalize_board.py) | rois.json→board.json 标定（聚类方案下可选） |
| [tools/test_xiangqi_template.py](../../tools/test_xiangqi_template.py) | 批量离线测试：感知+引擎+决策+可视化，看 engine_ok |
| [tools/capture_screenshot.py](../../tools/capture_screenshot.py) | HDC 一键截图到 D:/screenshots |
| [run_xiangqi.py](../../run_xiangqi.py) | 实跑入口 |

## 七、如何运行
```bash
# 离线验证（无需真机）：把截图放 D:/screenshots
python gameauto/tools/test_xiangqi_template.py --dir D:/screenshots
# 看 test_template_output/xiangqi/<图名>/summary.json 的 engine_ok、perception.png

# 实跑（接鸿蒙真机 HDC）
python gameauto/run_xiangqi.py
# 每轮日志在 gameauto/logs/<session>/round_xxx/ (vlm_response.json/perception.png/move.png)
```

## 八、已知问题 / 后续可做
- **棋子模板是单分辨率单机型裁的**：换机型/分辨率需重裁模板（裁剪器已就绪）。棋盘有聚类兜底但模板字形需重裁。
- **马/车/炮红黑同形靠颜色**：极端光照下墨色判定可能误判，必要时调 `_is_red` 阈值 40 或限制采样区域。
- **走子动画/高亮**：0.90 阈值已覆盖大部分，若个别棋子仍漏可降到 0.88。
- **UI marker 未裁**：靠「再来一局按钮」「将帅在场」判屏，目前够用；若 menu/game_over 误判可补裁 playing_marker/game_over_marker。
- **Pikafish 每步起独立进程**：无 hash 续算，2s movetime 够强（depth~25）。要更强可改常驻进程。

## 九、关键文件索引
- 感知：[perception_template.py](perception_template.py)（CV）、[perception.py](perception.py)（VLM + 工厂）
- 决策：[decision.py](decision.py)（`decide` 返回 `(actions, move)` 元组）
- 引擎：[engine.py](engine.py)（`Board.from_pieces`、`find_best_move_pikafish`、`PikafishEngine.search`）
- 状态机：[states.py](states.py)（含「等对手走子」守卫 `_board_sig`/`_post_move_sig`）
- 可视化：[visualizer.py](visualizer.py)（图像标注 + `format_board_matrix` 文本矩阵）
- 配置：[config.yaml](config.yaml)

## 十、Git
- 已推送 `myrepo/gameauto-m1`，最近 3 笔象棋提交：
  - `7b612d3` refactor: 棋盘日志矩阵化 + 走子休眠 1.5s
  - `543ff4e` feat: 模板匹配感知替代 VLM + 引擎 UCI/搜索 bug 修复
  - `f0f9049` perf: VLM prompt 压缩（更早的 VLM 时期）
- 上游跟踪：`gameauto-m1 → myrepo/gameauto-m1`，直接 `git push`。
