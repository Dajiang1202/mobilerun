"""Session manager — directory and file lifecycle for a game session."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class SessionManager:
    """Manage session directory creation and metadata.

    Directory structure::

        logs/<session_id>/
        ├── metadata.json
        ├── summary.json
        ├── events/
        └── frames/
            ├── 000000/
            │   ├── screenshot.png
            │   ├── perception.json
            │   ├── decisions.json
            │   └── actions.json
            └── ...
    """

    def __init__(self, base_dir: str | Path = "logs") -> None:
        self._base = Path(base_dir)
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        self.session_dir = self._base / session_id
        self.frames_dir = self.session_dir / "frames"
        self.events_dir = self.session_dir / "events"

    def setup(self, metadata: dict | None = None) -> Path:
        """Create session directories and write metadata."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.frames_dir.mkdir(exist_ok=True)
        self.events_dir.mkdir(exist_ok=True)

        if metadata is None:
            metadata = {}
        metadata.setdefault("session_id", self.session_dir.name)
        metadata.setdefault("started_at", datetime.now().isoformat())

        (self.session_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return self.session_dir

    def frame_dir(self, frame_id: int) -> Path:
        """Return Path for frame NNNNNN directory, creating it."""
        d = self.frames_dir / f"{frame_id:06d}"
        d.mkdir(exist_ok=True)
        return d

    def event_path(self, frame_id: int, event_type: str) -> Path:
        """Return Path for an event file."""
        return self.events_dir / f"{frame_id:06d}_{event_type}.json"

    def write_summary(self, summary: dict) -> None:
        """Write end-of-session summary."""
        (self.session_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )
