"""端到端闭环测试: 打开微信 → 找辅测机对话

最小 LLM 闭环(不走完整 mobilerun agent),验证:
- HarmonyOSDriver + HarmonyStateProvider 能驱动真机
- qwen3-vl-flash 能看截图 + UI 树做决策
- 整个"截图→LLM→执行"链路跑通

任务:
1. 打开微信 (com.tencent.wechat)
2. 找到名为"辅测机"的对话并点进去
"""
import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path

import httpx

os.environ["PATH"] = r"d:\gameauto\mobilerun\tools\hdc;" + os.environ["PATH"]

from mobilerun.tools.driver.harmonyos import HarmonyOSDriver
from mobilerun.tools.ui.harmony_provider import HarmonyStateProvider

# ── 配置 ──────────────────────────────────────────────────────────────────
QWEN_API_KEY = "sk-REDACTED-IN- HISTORY"
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL = "qwen3-vl-flash"
HDC_PATH = r"d:\gameauto\mobilerun\tools\hdc\hdc.exe"
WECHAT_BUNDLE = "com.tencent.wechat"
MAX_STEPS = 12
TASK = '打开微信,然后找到名为"辅测机"的聊天对话并点进去。完成后回复 DONE。'

SYSTEM_PROMPT = """你是一个鸿蒙手机自动化助手。你能看到当前屏幕截图和 UI 控件树,需要决定下一步动作。

可用动作(用 JSON 格式回复,只输出 JSON,不要其他文字):
- {"action": "tap_element", "index": <UI树中的编号>}  点击指定编号的控件
- {"action": "tap_coord", "x": <0-1000>, "y": <0-1000>}  点击归一化坐标(0=左/上,1000=右/下)
- {"action": "swipe", "x1": <>, "y1": <>, "x2": <>, "y2": <>, "duration_ms": <800>}  滑动
- {"action": "input_text", "text": "<文字>"}  在已聚焦的输入框输入文字
- {"action": "back"}  按返回键
- {"action": "home"}  回桌面
- {"action": "wait", "seconds": <数>}  等待
- {"action": "done", "reason": "<完成原因>"}  任务完成

严格规则(违反就失败):
1. 必须根据【当前截图】和【当前 UI 树】做判断,不要假设或想象
2. 只有当截图里【确实能看到】目标结果(如已进入指定聊天页)时,才能 done
3. 如果截图显示的不是预期界面(如还在桌面/其他App),绝不能 done,要先导航
4. 优先用 tap_element(按编号),编号必须来自当前给的 UI 树
5. 每次只输出一个动作的 JSON,不要其他文字"""


async def call_qwen(screenshot_b64: str, ui_tree_text: str, history: list) -> dict:
    """调用 qwen3-vl-flash,返回动作 dict."""
    user_content = [
        {
            "type": "text",
            "text": f"任务: {TASK}\n\n当前 UI 控件树:\n{ui_tree_text}\n\n"
                    f"请决定下一步动作(只输出 JSON):",
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
        },
    ]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [
        {"role": "user", "content": user_content}
    ]

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{QWEN_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {QWEN_API_KEY}"},
            json={
                "model": QWEN_MODEL,
                "messages": messages,
                "max_tokens": 300,
                "temperature": 0.1,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    # 提取 JSON (模型可能包裹在 ```json ... ``` 里)
    m = re.search(r"\{[^{}]*\}", content, re.DOTALL)
    if not m:
        # 尝试更宽松的匹配
        m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return {"action": "error", "raw": content}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"action": "error", "raw": content, "match": m.group(0)}


async def execute_action(driver: HarmonyOSDriver, action: dict, state_elements: list) -> str:
    """执行 LLM 返回的动作,返回执行描述."""
    a = action.get("action", "unknown")
    # 动作名容错(LLM 可能输出 click_element / click / tap 等变体)
    if a in ("click_element", "click", "tap"):
        a = "tap_element"
    if a in ("click_coord", "tap_at"):
        a = "tap_coord"
    if a == "tap_element":
        idx = action.get("index") or action.get("element_index")
        # 找对应元素
        target = next((e for e in state_elements if e.get("index") == idx), None)
        if not target:
            return f"❌ index {idx} 不在元素列表里"
        bounds = target.get("bounds", "")
        # 解析 [l,t][r,b]
        nums = re.findall(r"\d+", bounds)
        if len(nums) < 4:
            return f"❌ bounds 解析失败: {bounds}"
        cx = (int(nums[0]) + int(nums[2])) // 2
        cy = (int(nums[1]) + int(nums[3])) // 2
        await driver.tap(cx, cy)
        return f"✅ tap_element index={idx} ({target.get('type')}/{target.get('text','')!r}) at ({cx},{cy})"
    elif a == "tap_coord":
        # 归一化坐标 → 像素
        sw, sh = driver._screen_width or 1276, driver._screen_height or 2848
        x = int(action["x"] / 1000 * sw)
        y = int(action["y"] / 1000 * sh)
        await driver.tap(x, y)
        return f"✅ tap_coord norm=({action['x']},{action['y']}) px=({x},{y})"
    elif a == "swipe":
        sw, sh = driver._screen_width or 1276, driver._screen_height or 2848
        x1 = int(action["x1"] / 1000 * sw)
        y1 = int(action["y1"] / 1000 * sh)
        x2 = int(action["x2"] / 1000 * sw)
        y2 = int(action["y2"] / 1000 * sh)
        await driver.swipe(x1, y1, x2, y2, action.get("duration_ms", 800))
        return f"✅ swipe ({x1},{y1})->({x2},{y2})"
    elif a == "input_text":
        await driver.input_text(action["text"])
        return f"✅ input_text {action['text']!r}"
    elif a == "back":
        await driver.press_button("back")
        return "✅ back"
    elif a == "home":
        await driver.press_button("home")
        return "✅ home"
    elif a == "wait":
        await asyncio.sleep(float(action.get("seconds", 1)))
        return f"✅ wait {action.get('seconds')}s"
    elif a == "done":
        return f"🎯 DONE: {action.get('reason', '')}"
    elif a == "error":
        return f"❌ LLM 输出无法解析: {action.get('raw', '')[:200]}"
    return f"❌ 未知动作: {a}"


async def main():
    print("=" * 60)
    print(f"任务: {TASK}")
    print("=" * 60)

    driver = HarmonyOSDriver(hdc_path=HDC_PATH)
    provider = HarmonyStateProvider(driver, use_normalized=True, max_elements=40)
    await driver.connect()

    # Step 0: 启动微信并等待(用 UI 树判断是否到了微信界面,不依赖 packageName)
    print("\n[启动] 启动微信 com.tencent.wechat")
    result = await driver.start_app(WECHAT_BUNDLE)
    print(f"  {result}")
    # 轮询等待微信界面出现(用 UI 树特征判断,最多 15s)
    wechat_keywords = ["微信", "wechat", "WeChat", "聊天", "通讯录", "发现", "我"]
    for i in range(15):
        await asyncio.sleep(1.0)
        try:
            state = await driver.get_ui_tree()
            tree_blob = json.dumps(state, ensure_ascii=False)
            hit = [k for k in wechat_keywords if k.lower() in tree_blob.lower()]
            print(f"  等待[{i+1}s] 当前 App={state.get('phone_state',{}).get('packageName')} UI树含关键词: {hit}")
            if hit:
                print(f"  ✅ 检测到微信界面特征")
                break
        except Exception as e:
            print(f"  等待[{i+1}s] UI树获取失败: {e}")
    else:
        print(f"  ⚠️ 未检测到微信特征,继续尝试(可能 UI 树无 text,靠截图判断)")
    await asyncio.sleep(2.0)  # 额外等界面稳定

    history: list = []
    for step in range(1, MAX_STEPS + 1):
        print(f"\n{'='*60}\n[Step {step}] 获取状态 + 决策\n{'='*60}")

        # 截图 + UI 树
        state = await provider.get_state()
        screenshot = await driver.screenshot()
        screenshot_b64 = base64.b64encode(screenshot).decode("ascii")

        # 保存轨迹
        Path(f"debug/step_{step:02d}.jpg").write_bytes(screenshot)

        print(f"  元素数: {len(state.elements)}")
        print(f"  当前 App: {state.phone_state.get('packageName')}")

        # 调 LLM
        action = await call_qwen(screenshot_b64, state.formatted_text, history)
        print(f"  LLM 决策: {json.dumps(action, ensure_ascii=False)[:200]}")

        # 执行
        desc = await execute_action(driver, action, state.elements)
        print(f"  执行: {desc}")

        # 加入历史
        history.append({
            "role": "assistant",
            "content": json.dumps(action, ensure_ascii=False),
        })

        if action.get("action") == "done":
            print(f"\n🎉 任务在 {step} 步完成!")
            break
        if action.get("action") == "error":
            # 给 LLM 一次纠正机会
            history.append({
                "role": "user",
                "content": "上一轮输出无法解析,请重新输出正确的 JSON 动作。",
            })

        await asyncio.sleep(1.5)  # 等界面稳定
    else:
        print(f"\n⚠️ 达到最大步数 {MAX_STEPS},任务未完成")

    print("\n轨迹截图已保存到 debug/step_XX.jpg")


if __name__ == "__main__":
    asyncio.run(main())
