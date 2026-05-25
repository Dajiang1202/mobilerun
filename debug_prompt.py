"""
调试脚本：截图 + 自定义提示词 → VLM 响应

直接编辑下面 ==== 可调参数 ==== 区域的变量即可。
使用 openai 原生 SDK 调用，不经过 llama_index。
"""
import asyncio
import base64
import logging
from pathlib import Path

from openai import AsyncOpenAI
from jinja2 import Template

from mobilerun.config_manager.loader import ConfigLoader
from mobilerun.agent.utils.game_visualizer import annotate_swipe, save_game_log
from mobilerun.tools.driver.android import AndroidDriver
from mobilerun.tools.helpers.images import (
    image_dimensions,
    resize_image_to_max_side,
    resize_image_to_max_side_with_grid,
)


# ======================================================================
# ==== 可调参数 ==========================================================
# ======================================================================

# 系统提示词文件路径（None = 使用默认 fast_game_agent/system.jinja2）
SYSTEM_PROMPT_PATH = None

# 用户任务描述
GOAL = "在开心消消乐棋盘上找到一步有效三消并执行"

# LLM profile 名称（对应 config.yaml 中 llm_profiles 的 key）
LLM_PROFILE = "fast_game_agent"

# 是否在截图上叠加 [0-1000] 归一化坐标网格
SHOW_GRID = True

# 是否流式输出
STREAM = True

# 只打印 VLM 原始回复，不解析工具调用
RAW_OUTPUT = False

# 额外附加的系统提示词（拼接到模板后面，用于快速微调）
EXTRA_SYSTEM_TEXT = ""

# 解析到 swipe 后自动在截图上标注坐标并保存（game_logs/ 目录）
VISUALIZE_SWIPE = True

# ======================================================================


def _get_tool_descriptions() -> str:
    return """<tools>
<tool name="swipe">
<description>Swipe from one coordinate to another on the screen. Use normalized [0-1000] coordinates. Duration in seconds (default 1.0).</description>
<parameters>
<parameter name="coordinate" type="list" required="true">Start point [x, y] in [0-1000] range.</parameter>
<parameter name="coordinate2" type="list" required="true">End point [x2, y2] in [0-1000] range.</parameter>
<parameter name="duration" type="number" required="false">Swipe duration in seconds (default 1.0).</parameter>
</parameters>
<usage>{"action": "swipe", "coordinate": [x1, y1], "coordinate2": [x2, y2], "duration": 1.0}</usage>
</tool>
<tool name="click_at">
<description>Click at normalized screen position (x, y) in [0-1000] range.</description>
<parameters>
<parameter name="x" type="number" required="true">X coordinate in [0-1000].</parameter>
<parameter name="y" type="number" required="true">Y coordinate in [0-1000].</parameter>
</parameters>
<usage>{"action": "click_at", "x": 500, "y": 300}</usage>
</tool>
<tool name="wait">
<description>Wait for a specified duration in seconds.</description>
<parameters>
<parameter name="duration" type="number" required="false">Seconds to wait (default 1.0).</parameter>
</parameters>
<usage>{"action": "wait", "duration": 1.5}</usage>
</tool>
<tool name="complete">
<description>Mark task as complete. success=true if succeeded, false if failed. message contains the result.</description>
<parameters>
<parameter name="success" type="boolean" required="true">Whether the task succeeded.</parameter>
<parameter name="message" type="string" required="true">Result description.</parameter>
</parameters>
<usage>{"action": "complete", "success": true, "message": "Executed a valid 3-match swap"}</usage>
</tool>
<tool name="system_button">
<description>Press a system button (back, home, enter).</description>
<parameters>
<parameter name="button" type="string" required="true">Button name: back, home, enter.</parameter>
</parameters>
<usage>{"action": "system_button", "button": "back"}</usage>
</tool>
</tools>"""


def _img_to_data_url(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _visualize(
    call_params: dict,
    content: str,
    img_bytes: bytes,
    native_w: int,
    native_h: int,
    model_w: int,
    model_h: int,
) -> None:
    """将归一化坐标标注在原始截图上并保存。"""
    coord = call_params.get("coordinate", [])
    coord2 = call_params.get("coordinate2", [])
    if len(coord) != 2 or len(coord2) != 2:
        print("   ⚠️ 坐标格式不正确，跳过可视化")
        return

    # [0-1000] → 原始截图像素
    x1 = int(coord[0] * native_w / 1000)
    y1 = int(coord[1] * native_h / 1000)
    x2 = int(coord2[0] * native_w / 1000)
    y2 = int(coord2[1] * native_h / 1000)

    annotated = annotate_swipe(img_bytes, x1, y1, x2, y2)
    log_dir = save_game_log(
        annotated_img_bytes=annotated,
        thought_text=content,
        tool_name="swipe",
        tool_params=call_params,
        logs_dir="game_logs",
    )
    if log_dir:
        print(f"\n📸 可视化已保存: {log_dir}")
        print(f"   归一化: [{coord[0]},{coord[1]}] → [{coord2[0]},{coord2[1]}]")
        print(f"   原始像素: ({x1},{y1}) → ({x2},{y2})")
        print(f"   模型图尺寸: {model_w}x{model_h}")


async def main():
    # ── 1. 连接设备 & 截图 ──────────────────────────────────────────
    print("=" * 60)
    print("📱 连接设备...")
    config = ConfigLoader.load()
    serial = config.device.serial
    driver = AndroidDriver(serial=serial)
    await driver.connect()
    print(f"   已连接: {serial}")

    print("📸 截图...")
    img_bytes = await driver.screenshot()
    native_w, native_h = image_dimensions(img_bytes)
    print(f"   原始尺寸: {native_w}x{native_h}")

    if SHOW_GRID:
        model_img = resize_image_to_max_side_with_grid(img_bytes, use_normalized=True)
    else:
        model_img = resize_image_to_max_side(img_bytes)
    model_w, model_h = image_dimensions(model_img)
    print(f"   送入模型尺寸: {model_w}x{model_h}")

    # ── 2. 构建客户端 ───────────────────────────────────────────────
    if LLM_PROFILE not in config.llm_profiles:
        print(f"   ⚠️ profile '{LLM_PROFILE}' 不存在，可用: {list(config.llm_profiles.keys())}")
        return
    profile = config.llm_profiles[LLM_PROFILE]
    llm_kwargs = profile.to_load_llm_kwargs()
    api_key = llm_kwargs.pop("api_key", None)
    base_url = llm_kwargs.pop("base_url", None) or llm_kwargs.pop("api_base", None)
    model = llm_kwargs.pop("model")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    print(f"🤖 Model: {model}  Base: {base_url}")

    # ── 3. 加载系统提示词 ───────────────────────────────────────────
    if SYSTEM_PROMPT_PATH:
        prompt_path = Path(SYSTEM_PROMPT_PATH)
    else:
        from mobilerun.config_manager.path_resolver import PathResolver
        prompt_path = PathResolver.resolve(
            config.agent.fast_game_agent.system_prompt, must_exist=True
        )
    system_text = prompt_path.read_text(encoding="utf-8")
    system_text = Template(system_text).render(
        tool_descriptions=_get_tool_descriptions(),
        available_secrets=[],
        available_tools={"swipe", "click_at", "click_area", "wait", "complete", "system_button"},
        variables={},
        output_schema=None,
        parallel_tools=False,
        vision=True,
        platform="android",
        screenshot_only=True,
    )
    if EXTRA_SYSTEM_TEXT:
        system_text += "\n\n" + EXTRA_SYSTEM_TEXT
    print(f"📝 系统提示词: {prompt_path} ({len(system_text)} 字符)")

    # ── 4. 调用 VLM (openai 原生格式) ──────────────────────────────
    user_text = (
        f"**Current Game Request:**\n{GOAL}\n\n"
        "**Analyze the game board and call the appropriate tool.**"
    )
    data_url = _img_to_data_url(model_img)

    messages = [
        {"role": "system", "content": system_text},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    print("=" * 60)
    print("🚀 调用 VLM...")
    print("=" * 60)

    content = ""
    try:
        if STREAM:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                timeout=120,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    print(delta.content, end="", flush=True)
                    content += delta.content
            print()
        else:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=120,
            )
            content = response.choices[0].message.content or ""
            print(content)
    except Exception as e:
        print(f"\n❌ 调用失败: {e}")
        return

    print("=" * 60)

    # ── 5. 解析工具调用 ─────────────────────────────────────────────
    if not RAW_OUTPUT and content:
        from mobilerun.agent.fast_agent.xml_parser import (
            parse_tool_calls,
            format_tool_calls,
        )
        _, tool_calls = parse_tool_calls(content)
        if tool_calls:
            print("\n📋 解析到的工具调用:")
            print(format_tool_calls(tool_calls))

            # 可视化 swipe
            if VISUALIZE_SWIPE:
                for call in tool_calls:
                    if call.name == "swipe":
                        _visualize(
                            call_params=call.parameters,
                            content=content,
                            img_bytes=img_bytes,
                            native_w=native_w,
                            native_h=native_h,
                            model_w=model_w,
                            model_h=model_h,
                        )
        else:
            print("\n⚠️ 未检测到工具调用")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
