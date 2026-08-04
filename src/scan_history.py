import json
import os
import tempfile
import time
from pathlib import Path


HISTORY_FILE = Path(__file__).resolve().parent.parent / "data" / "scan_history.json"


def normalize_root(root):
    return os.path.normcase(os.path.normpath(str(root or "")))


def load_scan_history(path=HISTORY_FILE):
    history_path = Path(path)
    if not history_path.exists():
        return {}
    try:
        with history_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def get_scan_history(root, path=HISTORY_FILE):
    if not root:
        return None
    entry = load_scan_history(path).get(normalize_root(root))
    return entry if isinstance(entry, dict) else None


def record_scan_history(root, item_count, total_size=0, duration_secs=0, path=HISTORY_FILE):
    if not root:
        return

    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    data = load_scan_history(history_path)
    data[normalize_root(root)] = {
        "root": str(root),
        "item_count": max(0, int(item_count or 0)),
        "total_size": max(0, int(total_size or 0)),
        "last_scan_duration_secs": max(0.0, float(duration_secs or 0.0)),
        "last_scanned_at": time.time(),
    }

    fd, temp_name = tempfile.mkstemp(
        prefix=f"{history_path.name}.",
        suffix=".tmp",
        dir=str(history_path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(temp_name, history_path)
    finally:
        if os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass
