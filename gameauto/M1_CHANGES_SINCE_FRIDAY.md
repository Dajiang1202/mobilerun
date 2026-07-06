# gameauto-m1 自上周五以来的变更（f0f9049 → HEAD）

> 基准：`f0f9049`（2026-06-18 15:17，远端上周五 6/19 当天无提交，此为最近一笔）
> 当前：`e57e505`（2026-06-21）
> 统计：128 个文件，+3984 / -544（大头为模板 PNG 资产）

---

## 一、象棋（xiangqi）

### 代码
| 文件 | 类型 | 说明 |
|---|---|---|
| `skills/xiangqi/perception_template.py` | ✨新 +394 | 模板匹配感知核心（替代受阻的 VLM） |
| `skills/xiangqi/perception.py` | 改 +94 | 加 `make_perception` 工厂 + Protocol |
| `skills/xiangqi/engine.py` | 改 +104 | UCI rank 翻转修复 + Pikafish 搜索 stdin 修复 + 线程/哈希提升 |
| `skills/xiangqi/decision.py` | 改 | `decide` 返回元组 + 走子休眠 1→1.5s |
| `skills/xiangqi/states.py` | 改 +64 | 类型标注放宽 + 棋盘日志改矩阵 + 等对手守卫 |
| `skills/xiangqi/skill.py` | 改 | 类型标注放宽 |
| `skills/xiangqi/visualizer.py` | 改 +66 | 新增 `format_board_matrix` 文本矩阵 |
| `skills/xiangqi/prompts/xiangqi.jinja2` | 改 +116 | VLM prompt 压缩为 10×9 网格 |
| `skills/xiangqi/config.yaml` | 改 +10 | `perception_method` 开关 + 阈值 |
| `run_xiangqi.py` | 改 +33 | 接 `make_perception` 工厂 |

### 工具
| 文件 | 类型 | 说明 |
|---|---|---|
| `tools/crop_xiangqi_template.py` | ✨新 +118 | 竖屏专用裁剪器 |
| `tools/xiangqi_finalize_board.py` | ✨新 +103 | rois.json → board.json 标定 |
| `tools/test_xiangqi_template.py` | ✨新 +148 | 批量离线测试 |
| `tools/test_xiangqi_perception.py` | 改 +22 | （VLM 版测试，适配） |
| `tools/crop_template.py` | ✨新 +468 | 裁剪器（本周引入，含竖屏显示修复） |

### 文档
- `skills/xiangqi/HANDOFF.md` ✨新 +106

### 资产
- `assets/templates/pieces/`：14 棋子（`r_帥`…`b_卒`）
- `assets/templates/buttons/`：开始游戏 / 再来一局 / 返回大厅
- `assets/templates/_calib/board_rect.png`（棋盘标定框）
- `assets/board.json`、`assets/templates/rois.json`、`progress.json`

---

## 二、斗地主（doudizhu_douzero）

### 代码
| 文件 | 说明 |
|---|---|
| `skills/doudizhu_douzero/perception.py` | +437/-… 分区模板匹配重写 |
| `skills/doudizhu_douzero/decision.py` | +252 决策优化（压牌不出炸弹等） |
| `skills/doudizhu_douzero/states.py` | +147 |
| `skills/doudizhu_douzero/visualizer.py` | +73 |
| `run_doudizhu_douzero.py` | +53 scrcpy 后端等 |

### 框架（共享）
| 文件 | 说明 |
|---|---|
| `core/capture/scrcpy/bridge.py` | scrcpy 桥接改动 |
| `core/capture/scrcpy/capture.py` | scrcpy 采集 |
| `core/orchestration/loop.py` | 主循环（tap 50ms 等） |
| `core/perception/cv/template_match.py` | 模板匹配引擎微调 |
| `tools/capture_screenshot.py` | 截图工具 |

### 工具 / 文档
- `tools/offline_doudizhu.py` ✨新 +164
- `tools/viz_doudizhu.py` ✨新 +203
- `skills/doudizhu_douzero/DEVLOG.md` / `HANDOFF.md` / `README.md` / `TEMPLATES.md` ✨新

### 资产
- `assets/templates/cards/`：28（手牌角标 mb/mr）
- `assets/templates/others/`：28（对手/底牌 ob/or）
- `assets/templates/buttons/`：11（叫地主/不叫/出牌/继续…）
- `assets/templates/ui/`：4（landlord_words/pass/white/ingame_marker）
- `assets/templates/rois.json`

---

## 三、提交时间线（6/18 → 6/21）
| commit | 日期 | 说明 |
|---|---|---|
| `f0f9049` | 06-18 15:17 | **基准（上周五远端状态）** perf(xiangqi): VLM prompt 压缩 |
| `6c012a4` | 06-20 21:50 | feat(doudizhu): 纯 CV 模板匹配实战就绪 |
| `495ea7c` | 06-21 01:00 | feat(doudizhu): 实战调优 |
| `543ff4e` | 06-21 01:26 | feat(xiangqi): 模板匹配感知替代 VLM + 引擎 bug 修复 |
| `7b612d3` | 06-21 08:27 | refactor(xiangqi): 棋盘日志矩阵化 + 走子休眠 1.5s |
| `d0887a7` | 06-21 10:19 | feat(doudizhu): scrcpy 高速后端 |
| `8edbcd7` | 06-21 10:34 | fix(doudizhu): Ctrl+C 强制退出 |
| `5c0f821` | 06-21 11:14 | fix+opt(doudizhu): tap 50ms + 规则决策优化 |
| `296578a` | 06-21 11:54 | docs+fix(doudizhu): 交接文档 + 压牌不出炸弹 |
| `e57e505` | 06-21 12:08 | docs(xiangqi): 交接文档 |

---

## 四、象棋本次关键修复（实战阻塞 bug）
1. **Pikafish stdin bug**：`communicate(input=...)` 发完即关 stdin → 引擎读 EOF 秒回垃圾走法 `a3a4` 不搜索。改逐行读 bestmove。修复后开局走中炮 `b2e2`（depth 25）。
2. **UCI rank 翻转**：`_parse_uci_move` 把 UCI rank 直接当 grid 行号，致走子上下镜像。改 `9-rank`。
3. **颜色判定**：木色背景主导，原 `_is_red` 红黑都判红。改字形墨色（R-G 阈值 40）。
4. **搜索 roi 扩 margin**：锁定 rect 是棋子中心 min/max，边缘棋子被裁 → 漏检 → 画面冻结死循环。扩 80px。
5. **阈值 0.96→0.90**：带走子高亮的棋子分数降到 ~0.93，靠网格去重防误匹。
6. **screen_type 改将帅判定**：残局少子（<20）也能决策。

详细见 [skills/xiangqi/HANDOFF.md](skills/xiangqi/HANDOFF.md)。
