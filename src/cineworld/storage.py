from __future__ import annotations

import json
from pathlib import Path

from .models import CineworldSession


class SessionStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> CineworldSession:
        if not self.path.exists():
            return CineworldSession()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return CineworldSession()
            return CineworldSession.from_dict(data)
        except (OSError, ValueError, TypeError):
            return CineworldSession()

    def save(self, session: CineworldSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(session.to_dict(), indent=2),
            encoding="utf-8",
        )
        temp.replace(self.path)
