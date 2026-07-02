# TFT 项目总结 & 交接（2026-07-02）

> 给下一个窗口快速接上下文。详细设计见引用的各 doc。

## 一、目标 & 现状

**目标**：gameauto 框架驱动 HarmonyOS 真机自动打金铲铲之战（TFT）。

**分支**：`tft-adapt-v2`（远程 `myrepo` = `Dajiang1202/mobilerun`，fork；`origin` = droidrun 无写权限）。

**进度**：感知层 + 分层动作 + 回放验证 + 真机入口**代码全齐**，已在**视频回放**验证；**真机 M1/M2 尚未实跑**（等连手机）。

---

## 二、已建成（按层）

### 感知层
- **绿色血条检测**（定位我方棋子）：RGB(131,222,117)±**30**（后排暗绿需±30）+ morphology(15,5) 桥接黑线 + 长宽比≥6。棋盘+战备区分开阈值（bench 用 ratio≥4, w≥8）。
- **OCR 微服务**：RapidOCR PP-OCRv4，WSL2 `127.0.0.1:8089`。`/ocr` 已改 `asyncio.to_thread` 解除 event loop 阻塞（9 并发 130→53ms）。
- **模板匹配**：`TemplateMatchTask`（多尺度+NMS）。
- 感知后端（在 `run_tft_replay.py`）：`stub`/`ocr`/`champions`/`full`。`full` = 血条+OCR+ROI 同屏。

### 动作层（声明式 `list[Action]`，归一化 [0-1000]）
- **中层** `skills/tft/actions.py` `TftActions(rois,w,h)`：refresh / buy_xp / buy_shop_slot / click_champion / sell_champion（长按1s+垂直拖到底）/ equip / equip_from_slot（标注槽过渡）/ move_champion
- **顶层** `skills/tft/workflows.py`：iterate_champions / iterate_item_drops / buy_all_core
- **底层/转换层**：复用 `core/input`（BaseInput，HDC+scrcpy 切换）+ `utils/coordinate`（[0-1000]↔像素↔[0-1]）

### 工具
- `tools/annotate_tft_ocr_rois.py` —— ROI 标注（比例，写 `ocr:` section，ruamel 保留注释）
- `tools/crop_tft_template.py` —— TFT 模板裁剪
- `tools/annotate_tft_tm_rois.py` —— TM 搜索区
- `tools/roi_annotator.py` —— 共享标注核心
- `tools/cv_text.py` —— PIL 中文渲染（修 cv2.putText 中文???）

### 入口
- `run_tft_replay.py` —— **视频回放**工作台（无设备，可视化感知+动作）
- `run_tft_device.py` —— **真机**入口（M1 observe 打印识别+阶段 / M2 act 交互动作）
- `run_tft_scrcpy.py` —— 旧真机入口（挂旧 TftPerception，待重接）

---

## 三、关键配置 / 调过的值

| 位置 | 项 | 值 | 原因 |
|------|----|----|------|
| `run_tft_replay.py` | `HP_GREEN_TOL` | 30 | 后排血条暗绿 G 低到 177，±20 漏检 |
| 同上 | `HP_BAR_MIN_RATIO` | 6（棋盘）/ `BENCH_BAR_MIN_RATIO` 4 | bench 血条更短 |
| 同上 | `OCR_URL` | `http://127.0.0.1:8089/ocr` | **别用 localhost**（Win→WSL2 IPv6 超时 15s） |
| 同上 | `PERCEIVE`/`DECIDE` | `full` / `tft` | 默认全屏感知+遍历棋子动作 |
| `services/ocr/ocr_server.py` | `/ocr` | `asyncio.to_thread` | 并行不被串行化 |
| rois.yaml | `round_timer:` | 已补空格 | 原 YAML 语法 bug 整文件解析失败 |

---

## 四、坑 & 注意

1. **OCR 服务非自启**：WSL/服务重启要手动拉起：
   `wsl -d Ubuntu-24.04 -- bash -lc 'cd /mnt/d/gameauto/mobilerun/services/ocr && nohup .venv/bin/python -m uvicorn ocr_server:app --host 0.0.0.0 --port 8089 > /tmp/ocr_server.log 2>&1 &'`
2. **CUDA EP 没起来**（cuDNN9 缺失）→ RapidOCR 回退 CPU（单图仍 16ms，够用）。
3. **vLLM Qwen2-VL-7B 在 8000**（WSL2）——视觉兜底用，非 OCR。
4. **`services/` 大部分未跟踪**（含 .venv）；只跟踪了 `ocr_server.py`。
5. **`_os._exit(0)`**：真机入口用它绕 JVM 残留；回放入口不能加（会丢 stdout print）。
6. **Window cv2.putText 不支持中文** → 一律走 `put_text_zh`。

---

## 五、决策（已定）

- **决策架构**：长期 LLM（Qwen3-70B/DeepSeek-Reasoner，走 API，每备战1次出"意图"）+ 短期规则（每 tick 翻译成 `TftActions`）。见记忆 `tft-bot-decision-architecture`。
- **生产部署**：bot 机无本地 GPU → 复用同网段 V100 的 Flask+gunicorn OCR（待适配：worker≥9、实测 RTT<30ms、引擎准确率对齐）。dev 用 WSL RapidOCR。见记忆 `tft-ocr-deployment`。
- **战备区棋子**：复用小血条检测（同棋盘法，阈值放宽）。
- **装备掉落（圆形问号）**：OCR 识别，不模板。
- **参考项目调研**：3 个项目（数据包/悬浮窗/PC拿牌）都不是端到端真机 bot，主要可借数据层；结论已**搁置**（用户：没太多可学）。见 `docs/tft-参考项目调研.md`。

---

## 六、文档地图

| doc | 内容 |
|-----|------|
| `docs/tft-闭环框架.md` | **核心**：感知→决策→动作全景 + 状态机 + 双层决策 + 已就绪/待补 + 实验递进 M1→M5 |
| `docs/tft-video-replay-workbench-design.md` | 回放工作台设计 |
| `docs/金铲铲适配方案.md` | 原始方案 + 第十一节完整 Backlog（识别/操作/决策） |
| `docs/tft-参考项目调研.md` | 3 个参考项目（搁置） |
| `docs/OCR服务部署需求.md` | OCR 服务 API 规格 |

---

## 七、动作集 done / 缺

**done**：refresh / buy_xp / buy_shop_slot / click_champion / sell_champion / equip / equip_from_slot / move_champion；顶层 iterate_champions / iterate_item_drops / buy_all_core。

**缺**（真机闭环前补）：`close_panel`（点棋子后关面板，否则遍历卡死）、`pick_carousel`、`pick_augment`、`tap_continue`、`toggle_shop`。

---

## 八、下一步（真机实验递进）

- **M1 observe**（代码就绪，待连手机）：跑 `run_tft_device.py` MODE=observe，看血条/OCR 读数 + 阶段判断对不对
- **M2 act**（代码就绪）：MODE=act，交互输 refresh/buy/click 验证点击生效
- **M3 备战闭环**：短期规则接真机自动循环（买/卖/升级）—— **待开发**：补缺失动作 + TftSkill/状态机 handler（`states.py` 现是空骨架）+ 接 `run_tft_scrcpy.py`
- **M4 全阶段** / **M5 接 LLM**

**标注待办**（用户做）：`crop_tft_template` 标状态模板（lobby_play_btn/planning_timer/combat_indicator/result_rank/shop_toggle/...）；`annotate_tft_ocr_rois` 标 refresh_btn/buy_xp_btn/item0-2/own_board/bench（部分已标）。

---

## 九、记忆文件（`~/.claude/projects/d--gameauto-mobilerun/memory/`）

- `tft-bot-decision-architecture.md` — 双层决策
- `tft-ocr-deployment.md` — V100/gunicorn 部署决策
- `斗地主todom1随机出牌死胡同.md` — （既有）
