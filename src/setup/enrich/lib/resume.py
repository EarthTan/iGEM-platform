"""resume.py — checkpoint 持久化。

DB 是真理来源,upsert 即 commit;checkpoint 只是加速"批量重启"统计。
如果 checkpoint 丢了,重新 from-scratch 也无妨(DB 会通过 LEFT JOIN 自动跳过 done 的)。
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


_CHECKPOINT_DIR = Path("logs/checkpoints")


@dataclass
class Checkpoint:
    tool: str
    last_peptide_id: int = 0
    total_done: int = 0
    started_at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    updated_at: str = ""
    estimated_seq_per_sec: float = 0.0
    total_eligible: int = 0

    def path(self) -> Path:
        _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        return _CHECKPOINT_DIR / f"{self.tool}.json"

    def save(self):
        self.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
        self.path().write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, tool: str) -> "Checkpoint":
        p = _CHECKPOINT_DIR / f"{tool}.json"
        if not p.exists():
            return cls(tool=tool)
        try:
            data = json.loads(p.read_text())
            return cls(**data)
        except Exception:
            return cls(tool=tool)


def save_checkpoint(ckpt: Checkpoint):
    ckpt.save()


def load_checkpoint(tool: str) -> Checkpoint:
    return Checkpoint.load(tool)
