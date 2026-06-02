"""ReplaySimulator — drive modules offline with recorded data for debugging.

Since the game can't be paused, recorded frames are the only way to
reproduce and debug visual perception and decision logic offline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger("gameauto.replay")


class ReplaySimulator:
    """Load recorded session data and replay through any module.

    Usage::

        replay = ReplaySimulator("logs/session_001/")
        for frame in replay.frames():
            result = my_perception.recognize(frame.screenshot)
            replay.compare(result, frame.perception)
    """

    def __init__(self, replay_dir: str | Path) -> None:
        self._dir = Path(replay_dir)
        if not self._dir.exists():
            raise FileNotFoundError(f"Replay directory not found: {replay_dir}")

        self.metadata = self._load_json(self._dir / "metadata.json")
        self.summary = self._load_json(self._dir / "summary.json") or {}
        self._frames_dir = self._dir / "frames"

    def frame_count(self) -> int:
        """Count recorded frames."""
        if not self._frames_dir.exists():
            return 0
        return len([d for d in self._frames_dir.iterdir() if d.is_dir()])

    def load_frame(self, frame_id: int) -> dict[str, Any] | None:
        """Load a single frame's recorded data."""
        frame_dir = self._frames_dir / f"{frame_id:06d}"
        if not frame_dir.exists():
            return None

        screenshot_path = frame_dir / "screenshot.png"
        if not screenshot_path.exists():
            return None

        return {
            "frame_id": frame_id,
            "screenshot": screenshot_path.read_bytes(),
            "perception": self._load_json(frame_dir / "perception.json"),
            "decisions": self._load_json(frame_dir / "decisions.json"),
            "actions": self._load_json(frame_dir / "actions.json"),
        }

    def frames(self) -> Generator[dict[str, Any], None, None]:
        """Iterate over all recorded frames."""
        count = self.frame_count()
        for i in range(count):
            frame = self.load_frame(i)
            if frame:
                yield frame

    def replay_perception(self, perception_fn, frame_range=None) -> list[dict]:
        """Replay recorded screenshots through a perception function.

        Args:
            perception_fn: async fn(image: bytes) -> PerceptionResult
            frame_range: (start, end) or None for all frames.

        Returns:
            List of {frame_id, recorded, replayed, match} for comparison.
        """
        results = []
        start, end = frame_range or (0, self.frame_count())
        for i in range(start, min(end, self.frame_count())):
            frame = self.load_frame(i)
            if not frame:
                continue

            # Run perception on recorded screenshot
            import asyncio
            replayed = asyncio.run(perception_fn(frame["screenshot"]))

            recorded = frame.get("perception", {})
            results.append({
                "frame_id": i,
                "recorded": recorded,
                "replayed": replayed.model_dump() if hasattr(replayed, "model_dump") else str(replayed),
            })
        return results

    @staticmethod
    def _load_json(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
