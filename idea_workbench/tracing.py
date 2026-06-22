from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TraceLogger:
    trace_dir: Path

    def __post_init__(self) -> None:
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def new_id(self, stage: str) -> str:
        return f"{stage}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    def write_event(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        path = self.trace_dir / "llm_calls.jsonl"
        with path.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write_artifact(self, trace_id: str, suffix: str, data: Any) -> Path:
        path = self.trace_dir / f"{trace_id}.{suffix}"
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def summarize_text(text: str, *, limit: int = 500) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "..."
