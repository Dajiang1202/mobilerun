#!/usr/bin/env python3
"""工具 A —— TFT OCR ROI 标注。

在游戏截图上框选各 OCR 区域, 比例 ROI 写进 rois.yaml 的 ocr: section,
被 run_tft_replay.py 的 OCR 后端直接读取。

用法:
    python gameauto/tools/annotate_tft_ocr_rois.py --screens D:/screenshots
    (或改下面 SCREENS_DIR 默认值)

操作: 鼠标框选=保存并下一项 | ← →切项 | ↑ ↓切截图 | u 撤销 | q 退出
ROI 存成比例 {left,top,right,bottom}∈[0,1], 分辨率无关。

新增 OCR 区域 = 在下面 OCR_ROIS 加一行 (key, 描述)。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from gameauto.tools.roi_annotator import RoiAnnotator, load_screenshots, ROIS_YAML

# ═══════════════════════════════════════════════════════════════════════
#  配置区
# ═══════════════════════════════════════════════════════════════════════

# 截图目录 (各游戏阶段的 TFT 截图, 至少一张备战阶段)
SCREENS_DIR = r"D:\screenshots"

# OCR 区域清单 —— 想到新的就加一行 (key 必须唯一, 建议 ASCII)
# key 会同时作为 rois.yaml 的字段名 和 OCR 后端的输出键。
OCR_ROIS: list[tuple[str, str]] = [
    # 商店 5 格
    ("shop0", "商店槽 1 (棋子名)"),
    ("shop1", "商店槽 2 (棋子名)"),
    ("shop2", "商店槽 3 (棋子名)"),
    ("shop3", "商店槽 4 (棋子名)"),
    ("shop4", "商店槽 5 (棋子名)"),
    # 基础信息
    ("stage", "关卡/阶段 (如 3-5)"),
    ("timer", "剩余时间"),
    ("xp_bar", "经验条区域"),
    ("champion", "角色识别区域"),
    # 数字字段 (可选, 和上面的并列)
    ("gold", "金币数"),
    ("level", "等级"),
    ("hp", "血量"),
]

# ═══════════════════════════════════════════════════════════════════════


def main():
    screens = load_screenshots(Path(SCREENS_DIR))
    if not screens:
        print(f"错误: 在 {SCREENS_DIR} 找不到截图 (png/jpg)。改脚本顶部 SCREENS_DIR。")
        sys.exit(1)

    checklist = [{"key": k, "desc": d} for k, d in OCR_ROIS]
    RoiAnnotator(
        screens, checklist,
        yaml_path=ROIS_YAML,
        section="ocr",
        win_title="OCR ROI Annotator (框选→保存)",
    ).run()


if __name__ == "__main__":
    main()
