"""PerceptionPipeline — parallel execution of multiple perception tasks.

Single-frame total latency = max(subtask_latencies), not sum.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from gameauto.core.perception.base import PerceptionResult, PerceptionTask
from gameauto.core.perception.vlm_client import VlmClient

logger = logging.getLogger("gameauto.perception.pipeline")


class PerceptionPipeline:
    """Execute multiple PerceptionTasks in parallel.

    Usage::

        pipeline = PerceptionPipeline(vlm_client)
        pipeline.add_task(PerceptionTask(name="board", task_type="vlm", prompt=...))
        result = await pipeline.run(image_bytes)
    """

    def __init__(self, vlm_client: VlmClient | None = None) -> None:
        self._vlm = vlm_client
        self._tasks: list[PerceptionTask] = []
        self._custom_runners: dict[str, Any] = {}

    def add_task(self, task: PerceptionTask) -> None:
        """Register a perception sub-task."""
        self._tasks.append(task)

    def register_custom_runner(self, task_type: str, runner) -> None:
        """Register a runner callable for custom task types.

        Runner signature: async def runner(image: bytes, task: PerceptionTask) -> Any
        """
        self._custom_runners[task_type] = runner

    async def run(self, image: bytes) -> PerceptionResult:
        """Execute all registered tasks in parallel. Returns merged result."""
        t0 = time.perf_counter()

        coros = [self._run_one(task, image) for task in self._tasks]
        outputs = await asyncio.gather(*coros, return_exceptions=True)

        # Merge results
        tasks_output: dict[str, Any] = {}
        raw_response = ""
        parsed: dict = {}

        for task, output in zip(self._tasks, outputs):
            if isinstance(output, Exception):
                logger.warning("Task '%s' failed: %s", task.name, output)
                tasks_output[task.name] = None
            else:
                tasks_output[task.name] = output
                if task.task_type == "vlm" and isinstance(output, str):
                    raw_response = output

        latency_ms = (time.perf_counter() - t0) * 1000
        return PerceptionResult(
            raw_response=raw_response,
            parsed=parsed,
            tasks_output=tasks_output,
            latency_ms=latency_ms,
        )

    async def _run_one(self, task: PerceptionTask, image: bytes) -> Any:
        if task.task_type == "vlm":
            if not self._vlm:
                raise RuntimeError("VlmClient not configured for vlm task")
            return await self._vlm.chat(
                system_prompt=task.prompt or "",
                user_prompt=task.config.get("user_prompt", "Output the result as JSON."),
                image=image,
                timeout=task.config.get("timeout", 120.0),
            )
        if task.task_type in self._custom_runners:
            return await self._custom_runners[task.task_type](image, task)
        logger.debug("Task '%s' type '%s' has no runner registered — skipped", task.name, task.task_type)
        return None
