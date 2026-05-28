"""
Quick Start: Match-3 Game — Skill 模式连续执行 (通用版)

流程: 截图 → VLM 识别棋盘为 JSON → Python 贪心求解 → 执行 swipe → 循环
适配任意三消游戏，VLM 自动识别棋盘布局和棋子类型。
"""
import asyncio
import base64
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from jinja2 import Template
from openai import AsyncOpenAI

from mobilerun.agent.utils.game_skill import solve_board
from mobilerun.agent.utils.game_visualizer import annotate_board, annotate_swipe
from mobilerun.config_manager.loader import ConfigLoader
from mobilerun.config_manager.path_resolver import PathResolver
from mobilerun.tools.driver.android import AndroidDriver
from mobilerun.tools.helpers.coordinate import to_absolute
from mobilerun.tools.helpers.images import (
    image_dimensions,
    resize_image_to_max_side_with_grid,
)

JSON_RE = re.compile(r"\{[\s\S]*\}")

# 连续执行轮数
ROUNDS = 10


def _img_to_data_url(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _extract_json(text: str) -> str | None:
    text = text.strip()
    if text.startswith("```"):
        end = text.rfind("```")
        if end > 3:
            text = text[text.index("\n") + 1 : end].strip()
    m = JSON_RE.search(text)
    return m.group(0) if m else None


def _validate_and_correct_board(board: dict) -> dict:
    """Validate and correct common board parsing errors.

    Fixes:
    - Extra columns/rows (e.g., 10 columns when only 9 tiles exist)
    - Inconsistent row lengths
    - Auto-detect blocked tile regions (contiguous square/block tiles)

    Returns:
        Corrected board dict
    """
    tiles = board.get("tiles", [])
    if not tiles:
        return board

    rows = len(tiles)
    if rows == 0:
        return board

    # Step 1: Common fix - 10 columns is often a mistake (should be 9)
    # Check if we have exactly 10 columns, and see if the 10th looks suspicious
    cols = board.get("cols", len(tiles[0]) if tiles else 0)
    if cols == 10 and rows >= 7:
        # Check if the last column has duplicates or looks suspicious
        suspicious = False
        for row in tiles:
            if len(row) >= 10:
                # Check if last element is same as previous (common error mode)
                if row[-1] == row[-2]:
                    suspicious = True
                    break
        # Also check if board_right - board_left looks more like 9 columns
        board_width = board.get("board_right", 1000) - board.get("board_left", 0)
        if rows > 0:
            board_height = board.get("board_bottom", 1000) - board.get("board_top", 0)
            cell_width_10 = board_width / 10
            cell_height = board_height / rows
            # If cells would be much wider than tall with 10 cols, try 9
            if cell_width_10 / cell_height > 1.3:
                suspicious = True

        if suspicious:
            # Remove the last column
            cols = 9
            tiles = [row[:9] if len(row) >= 9 else row for row in tiles]
            board["rows"] = rows
            board["cols"] = 9
            board["tiles"] = tiles
            print(f"   [WARNING] Corrected board dimensions to {rows}x{cols} (removed extra column)")

    # Step 2: Ensure all rows have the same length
    if len(tiles) > 0:
        target_cols = board.get("cols", len(tiles[0]) if tiles else 0)
        normalized_tiles = []
        for row in tiles:
            if len(row) > target_cols:
                normalized_tiles.append(row[:target_cols])
            elif len(row) < target_cols:
                normalized_tiles.append(row + ["empty"] * (target_cols - len(row)))
            else:
                normalized_tiles.append(row)
        tiles = normalized_tiles
        board["tiles"] = tiles
        board["rows"] = len(tiles)
        board["cols"] = target_cols

    # Step 3: Detect blocked tile regions (contiguous square/block-like tiles)
    # Keywords that suggest a tile might actually be a blocked obstacle
    blocked_suggestive_keywords = {"square", "block", "cube", "box", "brick", "stone"}
    # Also check for uniform label regions that are large and contiguous
    tiles = board["tiles"]
    rows = len(tiles)
    cols = len(tiles[0]) if rows > 0 else 0

    if rows >= 3 and cols >= 3:
        # First, collect label frequency to identify dominant labels
        from collections import defaultdict
        label_counts = defaultdict(int)
        for r in range(rows):
            for c in range(cols):
                label = tiles[r][c].lower()
                if label not in {"empty", "blocked"}:
                    label_counts[label] += 1

        # Identify labels that are suspicious (contain blocked keywords OR are very frequent)
        suspicious_labels = set()
        for label, count in label_counts.items():
            # Check if label contains blocked keywords
            if any(kw in label for kw in blocked_suggestive_keywords):
                suspicious_labels.add(label)
            # Check if label is extremely frequent (dominates a large region)
            if count >= rows * cols * 0.3:  # >= 30% of board
                suspicious_labels.add(label)

        # Find contiguous regions of suspicious labels
        if suspicious_labels:
            visited = [[False for _ in range(cols)] for _ in range(rows)]
            regions_to_mark_blocked = []

            for r in range(rows):
                for c in range(cols):
                    if not visited[r][c] and tiles[r][c].lower() in suspicious_labels:
                        # BFS to find region size
                        from collections import deque
                        queue = deque()
                        queue.append((r, c))
                        visited[r][c] = True
                        region = [(r, c)]
                        region_label = tiles[r][c].lower()

                        while queue:
                            curr_r, curr_c = queue.popleft()
                            # Check 4-directional neighbors
                            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                                nr, nc = curr_r + dr, curr_c + dc
                                if 0 <= nr < rows and 0 <= nc < cols and not visited[nr][nc]:
                                    if tiles[nr][nc].lower() == region_label:
                                        visited[nr][nc] = True
                                        queue.append((nr, nc))
                                        region.append((nr, nc))

                        # If region is large enough (>=3x3), mark as blocked
                        if len(region) >= 9:
                            regions_to_mark_blocked.extend(region)

            # Apply blocking
            if regions_to_mark_blocked:
                for r, c in regions_to_mark_blocked:
                    tiles[r][c] = "blocked"
                print(f"   [INFO] Marked {len(regions_to_mark_blocked)} tiles as blocked (large uniform region detected)")
                board["tiles"] = tiles

    return board


async def main():
    # ── 1. 加载配置 & 连接设备 ─────────────────────────────────────
    config = ConfigLoader.load()
    serial = config.device.serial
    # 从配置中读取 fast_game_agent 的 LLM profile，构建 AsyncOpenAI 客户端
    profile = config.llm_profiles["fast_game_agent"]
    llm_kwargs = profile.to_load_llm_kwargs()
    api_key = llm_kwargs.pop("api_key")
    base_url = llm_kwargs.pop("base_url", None) or llm_kwargs.pop("api_base", None)
    model = llm_kwargs.pop("model")
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    driver = AndroidDriver(serial=serial)
    await driver.connect()
    print(f"📱 已连接: {serial}  Model: {model}")

    # ── 2. 加载感知提示词 ──────────────────────────────────────────
    prompt_path = PathResolver.resolve(
        "config/prompts/fast_game_agent/system_skill_generic.jinja2", must_exist=True
    )
    system_text = Template(prompt_path.read_text(encoding="utf-8")).render()

    # ── 3. 主循环 ──────────────────────────────────────────────────
    success_count = 0
    t_start = time.time()
    session_dir = Path("game_logs") / datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]

    for rnd in range(1, ROUNDS + 1):
        print(f"\n{'=' * 50}")
        print(f"🔄 Round {rnd}/{ROUNDS}")
        print(f"{'=' * 50}")

        round_dir = session_dir / f"round_{rnd:03d}"
        round_dir.mkdir(parents=True, exist_ok=True)

        print("📸 截图...")
        img_bytes = await driver.screenshot()
        (round_dir / "step0_original.png").write_bytes(img_bytes)
        native_w, native_h = image_dimensions(img_bytes)
        model_img = resize_image_to_max_side_with_grid(img_bytes, use_normalized=True)
        model_w, model_h = image_dimensions(model_img)
        print(f"   原始: {native_w}x{native_h}  送入模型: {model_w}x{model_h}")

        # OpenAI Vision API 多模态消息：base64 内嵌图片
        data_url = _img_to_data_url(model_img)
        messages = [
            {"role": "system", "content": system_text},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Output the board as JSON."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]

        print("🚀 调用 VLM 识别棋盘...")
        t0 = time.time()
        response = await client.chat.completions.create(
            model=model, messages=messages, timeout=120,
        )
        content = response.choices[0].message.content or ""
        vlm_time = time.time() - t0
        print(f"   VLM 耗时: {vlm_time:.1f}s")

        # 保存 VLM 原始返回
        (round_dir / "step0_vlm_response.txt").write_text(content, encoding="utf-8")

        json_str = _extract_json(content)
        if not json_str:
            print("❌ VLM 回复中未找到 JSON，跳过本轮")
            continue
        board = json.loads(json_str)
        # Validate and correct board
        board = _validate_and_correct_board(board)
        rows, cols = board.get("rows", "?"), board.get("cols", "?")
        print(f"📐 棋盘: {rows}x{cols}")
        for i, row in enumerate(board.get("tiles", [])):
            print(f"   row{i}: {row}")

        # 保存解析后的棋盘 JSON
        (round_dir / "step0_board.json").write_text(
            json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        # ── 棋盘网格可视化 ──────────────────────────────────────────
        board_img = annotate_board(img_bytes, board)
        (round_dir / "step0_board.png").write_bytes(board_img)
        print(f"📸 棋盘可视化: {round_dir}")

        # ── Python 贪心求解 ─────────────────────────────────────────
        t0 = time.time()
        result = solve_board(board)
        solve_time = (time.time() - t0) * 1000
        print(f"   求解耗时: {solve_time:.0f}ms")

        (round_dir / "step0_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        if not result.get("found"):
            print(f"❌ {result.get('reason', 'No match')}")
            continue

        print(f"✅ {result['match_description']}")
        coord_from = result["coordinates"]["from"]
        coord_to = result["coordinates"]["to"]
        print(f"   归一化坐标: {coord_from} → {coord_to}")

        # ── 坐标转换 & 执行滑动 ─────────────────────────────────────
        input_size = getattr(driver, "input_coordinate_size", None)
        if input_size is None:
            input_w, input_h = native_w, native_h
        else:
            input_w, input_h = await input_size(native_w, native_h)

        abs_x1, abs_y1 = to_absolute(coord_from[0], coord_from[1], input_w, input_h)
        abs_x2, abs_y2 = to_absolute(coord_to[0], coord_to[1], input_w, input_h)
        print(f"   绝对坐标: ({abs_x1},{abs_y1}) → ({abs_x2},{abs_y2})")

        await driver.swipe(abs_x1, abs_y1, abs_x2, abs_y2, duration_ms=1000)
        print("✅ 滑动已执行")
        success_count += 1

        # ── 滑动可视化 ──────────────────────────────────────────────
        x1 = int(coord_from[0] * native_w / 1000)
        y1 = int(coord_from[1] * native_h / 1000)
        x2 = int(coord_to[0] * native_w / 1000)
        y2 = int(coord_to[1] * native_h / 1000)
        annotated = annotate_swipe(img_bytes, x1, y1, x2, y2)
        (round_dir / "step0_swipe.png").write_bytes(annotated)
        print(f"📸 滑动可视化: {round_dir}")

        await asyncio.sleep(0.5)

    elapsed = time.time() - t_start
    print(f"\n{'=' * 50}")
    print(f"🏁 完成: {success_count}/{ROUNDS} 轮成功, 总耗时 {elapsed:.1f}s")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
