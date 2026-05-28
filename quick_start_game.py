"""
Quick Start: 开心消消乐 — Skill 模式连续执行

流程: 截图 → VLM 识别棋盘为 JSON → Python 贪心求解 → 执行 swipe → 循环
"""
import asyncio
import base64
import json
import logging
import re
import time

from jinja2 import Template
from openai import AsyncOpenAI

from mobilerun.agent.utils.game_skill import solve_board
from mobilerun.agent.utils.game_visualizer import annotate_swipe, save_game_log
from mobilerun.config_manager.loader import ConfigLoader
from mobilerun.config_manager.path_resolver import PathResolver
from mobilerun.tools.driver import create_driver
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

    driver = create_driver(config.device)
    await driver.connect()
    print(f"📱 已连接: {serial}  Model: {model}")

    # ── 2. 加载感知提示词 ──────────────────────────────────────────
    prompt_path = PathResolver.resolve(
        "config/prompts/fast_game_agent/system_skill.jinja2", must_exist=True
    )
    system_text = Template(prompt_path.read_text(encoding="utf-8")).render()

    # ── 3. 主循环 ──────────────────────────────────────────────────
    success_count = 0
    t_start = time.time()

    for rnd in range(1, ROUNDS + 1):
        print(f"\n{'=' * 50}")
        print(f"🔄 Round {rnd}/{ROUNDS}")
        print(f"{'=' * 50}")

        print("📸 截图...")
        img_bytes = await driver.screenshot()
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
        # 直接使用 AsyncOpenAI 客户端调用 VLM，非流式
        response = await client.chat.completions.create(
            model=model, messages=messages, timeout=120,
        )
        content = response.choices[0].message.content or ""
        vlm_time = time.time() - t0
        print(f"   VLM 耗时: {vlm_time:.1f}s")

        json_str = _extract_json(content)
        if not json_str:
            print("❌ VLM 回复中未找到 JSON，跳过本轮")
            continue
        board = json.loads(json_str)
        rows, cols = board.get("rows", "?"), board.get("cols", "?")
        print(f"📐 棋盘: {rows}x{cols}")
        for i, row in enumerate(board.get("tiles", [])):
            print(f"   row{i}: {row}")

        # ── Python 贪心求解 ─────────────────────────────────────────
        t0 = time.time()
        result = solve_board(board)
        solve_time = (time.time() - t0) * 1000
        print(f"   求解耗时: {solve_time:.0f}ms")

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

        # ── 可视化保存 ──────────────────────────────────────────────
        x1 = int(coord_from[0] * native_w / 1000)
        y1 = int(coord_from[1] * native_h / 1000)
        x2 = int(coord_to[0] * native_w / 1000)
        y2 = int(coord_to[1] * native_h / 1000)
        annotated = annotate_swipe(img_bytes, x1, y1, x2, y2)
        info = (
            f"Round {rnd}/{ROUNDS}\n"
            f"VLM Board JSON:\n{json.dumps(board, ensure_ascii=False, indent=2)}\n\n"
            f"Skill Result:\n{json.dumps(result, ensure_ascii=False, indent=2)}\n\n"
            f"Absolute: ({abs_x1},{abs_y1}) -> ({abs_x2},{abs_y2})"
        )
        log_dir = save_game_log(annotated, info, "swipe", {"coordinate": coord_from, "coordinate2": coord_to})
        if log_dir:
            print(f"📸 可视化: {log_dir}")

        await asyncio.sleep(0.5)

    elapsed = time.time() - t_start
    print(f"\n{'=' * 50}")
    print(f"🏁 完成: {success_count}/{ROUNDS} 轮成功, 总耗时 {elapsed:.1f}s")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
