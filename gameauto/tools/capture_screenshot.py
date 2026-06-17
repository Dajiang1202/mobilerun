#!/usr/bin/env python3
"""HDC 截图小工具 — 一键截图到本地。

用法:
    python gameauto/tools/capture_screenshot.py              # 自动命名
    python gameauto/tools/capture_screenshot.py playing      # 指定名称
    python gameauto/tools/capture_screenshot.py bidding      # 叫地主画面
    python gameauto/tools/capture_screenshot.py settlement    # 结算画面

截图保存到: D:\screenshots\
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("D:/screenshots")
REMOTE_PATH = "/data/local/tmp/gameauto_shot.jpeg"


def hdc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["hdc", *args], capture_output=True, text=True)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 文件名
    name = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%H%M%S")
    local_path = OUT_DIR / f"{name}.jpeg"

    # 截图
    print(f"截图中...")
    r = hdc("shell", f"snapshot_display -f {REMOTE_PATH}")
    if "success" not in r.stdout:
        print(f"截图失败:\n{r.stdout}\n{r.stderr}")
        sys.exit(1)

    # 拉取
    print(f"拉取到 {local_path} ...")
    r = hdc("file", "recv", f"data/local/tmp/gameauto_shot.jpeg", str(local_path))
    if "FileTransfer finish" not in r.stdout:
        print(f"拉取失败:\n{r.stdout}\n{r.stderr}")
        sys.exit(1)

    # 显示大小
    size_kb = local_path.stat().st_size / 1024
    print(f"完成: {local_path}  ({size_kb:.0f} KB)")

    # 清理远端
    hdc("shell", f"rm {REMOTE_PATH}")


if __name__ == "__main__":
    main()
