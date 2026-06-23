#!/usr/bin/env python3
"""天天象棋模板截取工具 —— 清单驱动 + 实时放大镜 + 无损裁剪。

与斗地主的 crop_template.py 完全分离, 仅复用其 skill 无关的 GUI 逻辑
(Cropper / load_screenshots / save_crop), 清单与默认路径为本工具自有。

模板命名规范(与 skills/xiangqi/engine.py 的 _piece_id 无缝对接)
  - 棋子      pieces/{r|b}_{字形}   r_帥 r_車 ... b_将 b_卒 (红黑各7, 共14)
      馬/車/炮 红黑字形相同, 仍拆两个模板, 运行时靠颜色校验区分。
      字形用繁体(天天象棋棋面渲染), engine._piece_id 全覆盖简繁。
  - 按钮      buttons/              开始游戏 再来一局 返回大厅
  - UI 标志   ui/                   playing_marker(对局中独有) game_over_marker(胜负横幅)
  - 棋盘标定  _calib/board_rect     从 TL 交叉点(col1,row10)拖到 BR 交叉点(col9,row1)
      不作模板, 仅记录框到 rois.json, 由 xiangqi_finalize_board.py 换算成 board.json。

工作流
  1. 准备若干游戏阶段截图放 --screens 目录(菜单/对局中/结束 各阶段都要)
  2. 运行本工具: 左窗显示模板名, 右窗显示截图
  3. ← →切清单项, ↑ ↓切截图; 鼠标在右窗框选 → 松开自动以模板名无损保存
  4. 截不到的项按 x 跳过; u 撤销上次保存; q 退出
  显示图会缩放, 但裁剪始终用原始分辨率坐标 → 无损。
  裁完后运行 xiangqi_finalize_board.py 生成 board.json。

用法
  python gameauto/tools/crop_xiangqi_template.py
  python gameauto/tools/crop_xiangqi_template.py --screens D:/screenshots --out <skill assets dir>
  python gameauto/tools/crop_xiangqi_template.py --export-checklist checklist.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 复用斗地主裁剪器中 skill 无关的 GUI 逻辑(不耦合任何斗地主清单/默认路径)
from gameauto.tools.crop_template import (
    Cropper, load_screenshots, DEFAULT_SCREENS,
)

# ── 默认路径(象棋专用) ───────────────────────────────────────────
# 模板直落 assets/templates/, 与 perception 加载路径 (template_dir=assets/templates) 一致;
# progress.json / rois.json 也在该目录下。board.json 由 finalize 脚本写到 assets/ 根。
DEFAULT_OUT = (Path(__file__).resolve().parent.parent
               / "skills" / "xiangqi" / "assets" / "templates")
# 象棋无原版参考模板, 左窗不显示参考图
DEFAULT_REF = Path("<无参考图>")

# 棋子模板清单: (文件名stem, 字形, 阵营)
_PIECES = [
    ("r_帥", "帥", "红"), ("r_仕", "仕", "红"), ("r_相", "相", "红"),
    ("r_馬", "馬", "红"), ("r_車", "車", "红"), ("r_炮", "炮", "红"), ("r_兵", "兵", "红"),
    ("b_将", "将", "黑"), ("b_士", "士", "黑"), ("b_象", "象", "黑"),
    ("b_馬", "馬", "黑"), ("b_車", "車", "黑"), ("b_炮", "炮", "黑"), ("b_卒", "卒", "黑"),
]


def build_checklist() -> list[dict]:
    """生成天天象棋模板清单: 14 棋子 + 按钮 + 1 棋盘标定。

    画面识别(screen_type)靠「检测到 再来一局 按钮 → game_over」「检测到足够多棋子 → playing」
    自动判定, 不依赖 UI 标志模板。开始游戏仅当从菜单界面启动时才需要(否则按 x 跳过)。
    """
    items: list[dict] = []
    for stem, glyph, side in _PIECES:
        items.append({"file": stem, "cat": "pieces",
                      "desc": f"{side}方 {glyph}", "rw": 70, "rh": 70})
    # 开始游戏: 可选(从菜单界面启动时才需); 再来一局: 必需(结束自动续局); 返回大厅: 可选
    for nm in ["开始游戏", "再来一局", "返回大厅"]:
        items.append({"file": nm, "cat": "buttons", "desc": f"按钮-{nm}", "rw": 0, "rh": 0})
    items.append({"file": "board_rect", "cat": "_calib",
                  "desc": "棋盘外框(TL交叉点→BR交叉点)", "rw": 0, "rh": 0})
    return items


def main():
    ap = argparse.ArgumentParser(description="天天象棋模板截取工具(清单驱动+放大镜+无损)")
    ap.add_argument("--screens", default=str(DEFAULT_SCREENS), help="游戏截图目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="模板输出目录(默认 skill assets/)")
    ap.add_argument("--roi", default=None, help="ROI 记录 json(默认 out/rois.json)")
    ap.add_argument("--export-checklist", default=None, help="导出清单为 json 后退出")
    args = ap.parse_args()

    checklist = build_checklist()

    if args.export_checklist:
        Path(args.export_checklist).write_text(
            json.dumps(checklist, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已导出 {len(checklist)} 项清单 -> {args.export_checklist}")
        return

    screens = load_screenshots(Path(args.screens))
    if not screens:
        print(f"错误: 在 {args.screens} 找不到截图(png/jpg)。用 --screens 指定。")
        sys.exit(1)

    out_dir = Path(args.out)
    roi_path = Path(args.roi) if args.roi else out_dir / "rois.json"
    ref_dir = DEFAULT_REF
    print(f"截图 {len(screens)} 张 | 清单 {len(checklist)} 项 | 输出 {out_dir}")
    print("操作: 鼠标框选=保存并下一项 | ←→ 切项 | ↑↓ 切截图 | x 跳过 | u 撤销 | q 退出")
    print("提示: board_rect 项需从左上交叉点拖到右下交叉点; 完成后运行 "
          "xiangqi_finalize_board.py 生成 board.json\n")

    # 竖屏游戏: 给 Cropper 传竖屏友好的显示参数。
    #   max_disp_w 较小、max_disp_h 较大 → 竖屏图按宽高比放大到可用的纵向尺寸, 不变形。
    #   zoom_radius=40 → 棋子(~70px)放大采样足够看清字形。
    Cropper(
        screens, checklist, out_dir, ref_dir, roi_path,
        max_disp_w=560, max_disp_h=1000,
        zoom_radius=40,
        win_name="crop xiangqi - screenshot",
    ).run()


if __name__ == "__main__":
    main()
