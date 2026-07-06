# 金铲铲参考项目调研

> 调研 3 个金铲铲/云顶项目，为我们的 gameauto TFT bot 找可借鉴的点。
> 结论先行：**三个都不是端到端真机 bot**（分别是纯数据包 / 悬浮攻略窗 / PC 辅助拿牌），感知和操控层面参考有限，但**数据资产和几个工程技巧很值得抄**。我们的"真机 + 全链路感知 + 绿血定位棋子"路线反而比它们都更进一步。

---

## 一、对比总览

| | GoldenShovel-S17-Skill | golden-spatula-assistant | JinChanChanTool |
|---|---|---|---|
| 是什么 | 纯数据/知识包（喂 LLM） | PyQt 阵容攻略悬浮窗 | C# PC 辅助拿牌工具 |
| 平台 | 无（纯数据） | Windows PC（腾讯手游助手旁） | Windows PC（模拟器/PC云顶） |
| 截图 | 无 | 无（设计写了没实现） | `Graphics.CopyFromScreen` |
| 输入 | 无 | 无 | Win32 `mouse_event` |
| 感知 | 无 | 无 | **仅商店 OCR**（PaddleOCR PP-OCRv5） |
| 决策 | beam search 凑羁绊 | 无（人工选） | 无（用户勾目标→命中即点） |
| 能自动打一局 | ❌ | ❌ | ❌（明确说不能） |
| 最值得抄 | S17 数据 JSON + beam search | 阵容 schema（timing 阶段化） | OCR 纠错字典 + 操作时序状态机 + 错误样本落盘 |

---

## 二、各项目详情

### 1. GoldenShovel-S17-Skill（纯数据包）
- **是什么**：为 OpenAI/Claude Skill 设计的本地知识库，给 LLM 提供 S17（赛博之城，2025）棋子/羁绊/装备/海克斯查询 + 阵容推荐。零操控。
- **技术栈**：纯 Python 标准库（`zipfile`+`xml.etree` 读 xlsx，零三方依赖），argparse 命令行。
- **感知**：无。唯一"解析"是用正则从羁绊文本抽档位 `[\(（]\s*(\d+)\s*[\)）]`。
- **决策**：确定性 beam search + 局部交换，打分=激活羁绊数>指定羁绊/棋子>高档位>接近下一档>总费用。无 LLM/RL。
- **数据资产（强）**：`assets/s17_data.json` 完整——63 棋子（1费14/2费13/3费16/4费13/5费7）、36 羁绊+档位、9 散件+45 成装+转职纹章、白银/黄金/棱彩海克斯、升级经验表、D 牌概率、利息规则、运营阶段建议。

### 2. golden-spatula-assistant（悬浮攻略窗）
- **是什么**：PyQt5 悬浮窗，展示 S17 的 12 套 T0/T1 阵容文字攻略。设计方案写了 OCR/模板匹配蓝图，**主程序完全没实现**，纯展示。
- **技术栈**：Python + PyQt5 + JSON。requirements 里 mss/opencv/Pillow 全是注释占位。
- **感知**：零（设计文档里的 ROI 分区、模板匹配、Tesseract OCR 都是设想）。
- **数据资产（中）**：`data/compositions.json` 12 套阵容结构完整（core_units 带 cost+position、timing 按 2-1/3-2/4-2 阶段、equipment 按主C/副C/主坦分配）；`heroes.json` 较脏（traits 名字与阵容对不上）。
- **附带价值**：设计方案点名了几个**真正实现感知**的上游项目——TFT-OCR-BOT、TFT-Overlay（C# 装备合成 DFS+羁绊状态机），后续可再看。

### 3. JinChanChanTool（PC 辅助拿牌）
- **是什么**：C# (.NET) WinForms 工具，**只做商店 OCR + 自动点目标棋子 + 自动 D 牌**。README 明确"不能自动决策、不能自动对局"。
- **技术栈**：C# + WinForms；截图 `Graphics.CopyFromScreen`；输入 Win32 `SetCursorPos`+`mouse_event`（非 ADB）。
- **感知（三者最强）**：Sdcb.PaddleOCR (PP-OCRv5 mobile) + OpenCvSharp，CPU/GPU 双模式。**只裁商店 5 个矩形 OCR 棋子名**，不识棋盘/装备/金币/血条/海克斯。
- **巧妙点**：① OCR 纠错字典 `CorrectionsList.json`（错认→正名，宽松包含匹配）；② **刷新时序状态机**——"上轮商店==本轮"+"空商店"+轮次/3秒超时 判断 D 牌是否成功，防重复；③ 错误图片自动落盘便于补字典；④ 锚点+缩放坐标（按客户区高度等比缩放偏移）自适应分辨率。
- **数据资产**：按赛季分目录（S15/S16/S17），`HeroData.json`{Name,Cost,Profession[],Peculiarity[]}、`Equipment.json`+图片、`RecommendedLineUps.json`（爬 metatft）。

---

## 三、对我们项目的启发（按优先级）

### 🔴 高优先 — 直接拿来用

1. **复用 S17 数据 JSON 作先验知识层**
   拿 GoldenShovel-S17-Skill 的 `s17_data.json`，OCR/血条定位出棋子名后查它的费用/羁绊/技能，喂规则层 + 长期 LLM。**省去自己爬 WIKI**。放到 `gameauto/skills/tft/data/s17.json`，perception 出棋子名→查表→决策。

2. **OCR 纠错字典**（JinChanChanTool）
   我们用 RapidOCR，棋子名（娑娜/索娜、嘉文四世）易错。建一份 `data/ocr_corrections.json`（错认→正名），识别后过一遍纠错，低成本提精度。比调阈值/换模型便宜得多。

3. **错误样本落盘**（JinChanChanTool）
   识别失败/低置信度的切片自动存图到 `logs/ocr_errors/`，便于离线补纠错字典、调 ROI。照搬到我们的 OCR 后端。

### 🟡 中优先 — 工程技巧

4. **操作时序状态机**（JinChanChanTool）
   用"连续 N 轮商店/画面不变 + 超时"判定点击/刷新是否生效，替代纯 `sleep`。真机延迟抖动大（scrcpy 滞后、tap 150ms），这比固定等待健壮——直接对应我们 backlog 的"点击后等动画"痛点。

5. **阵容数据 schema**（golden-spatula-assistant）
   `compositions.json` 的结构可抄：core_units 带 cost+position、timing 按 2-1/3-2/4-2 写阶段操作（几级 D/升人口）、equipment 按角色分配。这套 schema 适合喂长期 LLM 当阵容知识库。

6. **beam search 凑羁绊**（GoldenShovel-S17-Skill）
   作规则层启发式兜底（"当前棋盘还差几个激活某羁绊"），比让 LLM 硬算便宜且稳定。适合短期规则层。

### 🟢 已具备 / 无需借鉴

7. **锚点+缩放坐标**：JinChanChanTool 用屏幕高度等比缩放。我们**已经用归一化 [0,1] ROI**，比它更彻底（分辨率无关），这点我们做对了，不用回头。

---

## 四、它们的短板 = 我们的差异化

三个项目**都不做**：棋盘状态识别、装备合成、金币/血量/经验全量感知、选秀/海克斯交互、真机链路、LLM 决策。

而我们已经有的：
- ✅ 真机 scrcpy 链路（HarmonyOS）
- ✅ **绿色血条检测定位棋子**（它们都没有，JinChanChanTool 只 OCR 商店不识棋盘）
- ✅ OCR 微服务 + 多 ROI 并行
- ✅ 双层决策（规则 + LLM）架构

**结论**：参考项目主要补我们的**数据层和工程细节**（S17 数据、纠错字典、错误落盘、时序状态机），核心感知/决策路线我们自己走得比它们远。下一步把上面 🔴 三项落地，能立刻提精度、省工作量。
