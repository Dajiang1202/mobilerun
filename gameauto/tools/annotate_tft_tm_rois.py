#!/usr/bin/env python3
"""工具 C —— TFT 模板匹配搜索区 ROI 标注。

为每个模板/类别框选它在屏幕上可能出现的大致区域 (缩小 matchTemplate 搜索范围、
提速、防误匹配)。比例 ROI 写进 rois.yaml 的 template_match: section,
供 TemplateMatchTask.run(roi=...) 使用。

用法:
    python gameauto/tools/annotate_tft_tm_rois.py --screens D:/screenshots
    (或改下面 SCREENS_DIR 默认值)

操作: 鼠标框选=保存并下一项 | ← →切项 | ↑ ↓切截图 | u 撤销 | q 退出
ROI 存成比例 {left,top,right,bottom}∈[0,1]。

新增搜索区 = 在下面 TM_ROIS 加一行 (template_name, 描述)。
template_name 应与 skills/tft/assets/templates/ 下的模板文件名 (stem) 一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from gameauto.tools.roi_annotator import RoiAnnotator, load_screenshots, ROIS_YAML

# ═══════════════════════════════════════════════════════════════════════
#  配置区
# ═══════════════════════════════════════════════════════════════════════

SCREENS_DIR = r"D:\screenshots"

# 模板匹配搜索区清单 —— key 与模板文件名 (stem) 对应。
# 标的是"这个模板大概在屏幕哪个区域找", 不是模板本身的精确位置。
TM_ROIS: list[tuple[str, str]] = [
    # 状态检测按钮 (与 crop_tft_template.py 的 state 清单对应)
    ("lobby_play_btn", "开始游戏按钮所在区域"),
    ("planning_timer", "备战阶段计时器区域"),
    ("combat_indicator", "战斗中标识区域"),
    ("result_rank", "结算排名区域"),
    ("carousel_banner", "选秀横幅区域"),
    ("augment_frame", "海克斯强化框区域"),
    ("pve_indicator", "野怪关标识区域"),
    # 按钮类
    ("refresh_btn", "刷新商店按钮区域"),
    ("buy_xp_btn", "购买经验按钮区域"),
]

# ═══════════════════════════════════════════════════════════════════════


def main():
    import argparse
    ap = argparse.ArgumentParser(description="TFT 模板匹配搜索区 ROI 标注 → rois.yaml template_match: section")
    ap.add_argument("--screens", default=SCREENS_DIR, help="游戏截图目录")
    ap.add_argument("--yaml", default=str(ROIS_YAML), help="rois.yaml 路径")
    args = ap.parse_args()

    screens = load_screenshots(Path(args.screens))
    if not screens:
        print(f"错误: 在 {args.screens} 找不到截图 (png/jpg)。用 --screens 指定。")
        sys.exit(1)

    checklist = [{"key": k, "desc": d} for k, d in TM_ROIS]
    RoiAnnotator(
        screens, checklist,
        yaml_path=Path(args.yaml),
        section="template_match",
        win_title="TM ROI Annotator (框选搜索区→保存)",
    ).run()


if __name__ == "__main__":
    main()
