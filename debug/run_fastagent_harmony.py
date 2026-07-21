"""通过 mobilerun FastAgent 跑鸿蒙任务(注入路径,零代码改动)

策略 A:直接注入 driver + state_provider,绕过 droid_agent 的平台分支。
这样不用改 droid_agent.py,先验证 FastAgent 端到端能跑。

任务:在辅测机聊天页发一条文字消息"Hello from mobilerun"
(假设手机已停在辅测机聊天页)
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# hdc 加 PATH
os.environ["PATH"] = r"D:\gameauto\mobilerun\tools\hdc;" + os.environ["PATH"]

from mobilerun import MobileAgent, load_llm
from mobilerun.config_manager import MobileConfig
from mobilerun.tools.driver.harmonyos import HarmonyOSDriver
from mobilerun.tools.ui.harmony_provider import HarmonyStateProvider

# ── 配置(从环境变量读,key 不入库;参考 .env.example) ────────────────
import os as _os
_qwen_key = _os.environ.get("QWEN_API_KEY")
if not _qwen_key:
    # 尝试从 .env 读(开发便利,生产用环境变量)
    _env_file = Path(__file__).parent.parent / ".env"
    if _env_file.exists():
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line.startswith("QWEN_API_KEY="):
                _qwen_key = _line.split("=", 1)[1].strip().strip('"').strip("'")
                break
if not _qwen_key:
    print("❌ 未找到 QWEN_API_KEY。请设置环境变量,或在项目根建 .env 文件:")
    print("   QWEN_API_KEY=sk-你的key")
    print("   (.env 已在 .gitignore,不会提交)")
    sys.exit(1)
QWEN_API_KEY = _qwen_key
QWEN_BASE_URL = _os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = _os.environ.get("QWEN_MODEL", "qwen3-vl-flash")
HDC_PATH = _os.environ.get("HDC_PATH", r"D:\gameauto\mobilerun\tools\hdc\hdc.exe")

TASK = (
    "在当前的微信聊天页,发送一条文字消息:Hello"
)


async def main():
    print("=" * 60)
    print(f"任务: {TASK}")
    print("=" * 60)

    # 1) 构造 LLM (DashScope OpenAI 兼容)
    print("\n[1] 构造 qwen3-vl-flash LLM")
    fast_llm = load_llm(
        "OpenAILike",
        model=QWEN_MODEL,
        api_base=QWEN_BASE_URL,
        api_key=QWEN_API_KEY,
        is_chat_model=True,
        temperature=0.2,
    )
    print("  ✅ LLM 构造成功")

    # 2) 构造 driver + provider(注入路径)
    print("\n[2] 构造 HarmonyOSDriver + HarmonyStateProvider")
    driver = HarmonyOSDriver(hdc_path=HDC_PATH)
    await driver.connect()
    provider = HarmonyStateProvider(driver, use_normalized=True, max_elements=40)
    print(f"  ✅ driver+provider 就绪, 屏幕尺寸 {driver._screen_width}x{driver._screen_height}")

    # 2.5) 启动前状态校验:确认在辅测机聊天页,避免跑错页面
    print("\n[2.5] 校验初始状态")
    pre_state = await driver.get_ui_tree()
    import json as _json
    blob = _json.dumps(pre_state, ensure_ascii=False)
    in_chat = "辅测机" in blob and "editorId" in blob and "搜索" not in blob and "取消" not in blob
    print(f"  当前 App: {pre_state['phone_state']['packageName']}")
    print(f"  在辅测机聊天页: {'✅ 是' if in_chat else '❌ 否'}")
    if not in_chat:
        print("  ⚠️ 状态不对!请手动把手机弄回辅测机聊天页(顶部标题'辅测机',底部有输入框)")
        print("  ⚠️ 然后重新运行。当前 UI 含关键词:")
        for kw in ["辅测机", "editorId", "搜索", "取消", "RichEditor"]:
            if kw in blob:
                print(f"     - {kw}")
        # 不直接退出,让用户看到诊断后自己决定
        ans = input("  仍要继续运行吗?(y/N): ").strip().lower()
        if ans != "y":
            print("  已取消运行。")
            return

    # 3) 构造最小 config
    print("\n[3] 构造 MobileConfig")
    config = MobileConfig()
    config.agent.reasoning = False             # 走 FastAgent 路径
    config.agent.streaming = False             # OpenAILike 流式可能不稳
    config.agent.max_steps = 12
    config.agent.use_normalized_coordinates = True
    config.agent.fast_agent.vision = True      # 必须 True,截图才会发给 LLM
    config.agent.after_sleep_action = 1.5      # 鸿蒙 UI 切换慢
    config.agent.wait_for_stable_ui = 0.3
    config.agent.game_mode = False             # 不走游戏模式
    # 轨迹记录(为 M4 固化器准备)
    config.logging.save_trajectory = "step"
    config.logging.trajectory_path = "trajectories"
    config.logging.debug = True
    config.logging.rich_text = False
    # 设备信息(注入路径下主要用于信息展示)
    config.device.platform = "harmonyos"
    config.device.auto_setup = False           # 不要尝试装 Portal
    # 关闭 app_cards / credentials / mcp(注入路径不需要)
    config.agent.app_cards.enabled = False
    config.credentials.enabled = False
    config.mcp.enabled = False
    config.telemetry.enabled = False
    config.tracing.enabled = False
    print("  ✅ config 就绪")

    # 4) 构造 + 运行 MobileAgent
    print("\n[4] 构造 MobileAgent(注入 driver + provider)")
    agent = MobileAgent(
        goal=TASK,
        config=config,
        llms={"fast_agent": fast_llm, "app_opener": fast_llm},
        driver=driver,                  # 注入 → 绕过平台分支
        state_provider=provider,        # 注入 → 绕过平台分支
        timeout=600,
    )
    print("  ✅ agent 构造成功")

    print("\n[5] 运行 agent...")
    handler = agent.run()
    step = 0
    async for event in handler.stream_events():
        step += 1
        # 只打印关键事件类型,避免刷屏
        event_type = type(event).__name__
        if event_type in (
            "FastAgentToolCallEvent",
            "FastAgentOutputEvent",
            "ResultEvent",
            "ErrorEvent",
            "StepStartEvent",
        ):
            # 安全取属性
            thought = getattr(event, "thought", None) or getattr(event, "reason", None) or ""
            code = getattr(event, "code", None) or ""
            success = getattr(event, "success", None)
            line = f"  [{event_type}]"
            if thought:
                line += f" thought={str(thought)[:120]!r}"
            if code:
                line += f" code={str(code)[:150]!r}"
            if success is not None:
                line += f" success={success}"
            print(line[:300])

    result = await handler
    print()
    print("=" * 60)
    print(f"结果: success={result.success}  reason={result.reason!r}  steps={result.steps}")
    print("=" * 60)

    # 轨迹位置(Trajectory 对象用不同属性名,容错取)
    if hasattr(agent, "trajectory") and agent.trajectory:
        traj = agent.trajectory
        folder = (
            getattr(traj, "folder_path", None)
            or getattr(traj, "trajectory_path", None)
            or getattr(traj, "base_path", None)
            or str(traj)
        )
        print(f"轨迹保存在: {folder}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
