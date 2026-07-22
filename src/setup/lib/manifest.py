"""MANIFEST.json 读写 — v2 spec 配套"""
from __future__ import annotations
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

PUBLIC_DATA_ROOT = "/media/lenovo/Data/public_databases_v2"
DEFAULT_MANIFEST_PATH = Path(PUBLIC_DATA_ROOT) / "manifest" / "MANIFEST.json"


class ManifestError(Exception):
    pass


def load(p=DEFAULT_MANIFEST_PATH):
    p = Path(p)
    if not p.exists():
        raise ManifestError(f"MANIFEST.json 不存在: {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save(m, p=DEFAULT_MANIFEST_PATH):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    m["generated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with p.open("w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, ensure_ascii=False)
        f.write("\n")


def list_sources(m):
    return sorted(m.get("sources", {}).keys())


def get_source(m, sid):
    if sid not in m.get("sources", {}):
        raise ManifestError(f"source '{sid}' 不在 MANIFEST 中")
    return m["sources"][sid]


def filter_sources(m, only_ids=None, status=None):
    out = []
    for sid, sdef in m.get("sources", {}).items():
        if only_ids and sid not in only_ids:
            continue
        if status and sdef.get("status") != status:
            continue
        out.append(sid)
    return out


def resolve_path(m, sid):
    return str(Path(m["public_data_root"]) / m["sources"][sid]["current_on_disk"]["file"])


def set_sha256(m, sid, sha256, p=DEFAULT_MANIFEST_PATH):
    m["sources"][sid]["current_on_disk"]["sha256"] = sha256
    save(m, p)


def promote_to_latest(m, sid, sha256, nbytes, version, version_date, downloaded_file, p=DEFAULT_MANIFEST_PATH):
    sdef = m["sources"][sid]
    sdef["current_on_disk"] = {
        "version": version,
        "version_date": version_date,
        "file": str(Path(downloaded_file).relative_to(m["public_data_root"])),
        "sha256": sha256,
        "bytes": nbytes,
    }
    save(m, p)
