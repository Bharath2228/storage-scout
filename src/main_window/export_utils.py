import os
import time
from datetime import datetime

from ..models import format_age, format_size
from .constants import PERF_DEBUG


def _perf_log(label, started_at, **counts):
    if not PERF_DEBUG or started_at is None:
        return
    details = " ".join(f"{name}={value}" for name, value in counts.items())
    print(f"[storage-scout-perf] {label}: {(time.perf_counter() - started_at) * 1000:.1f} ms {details}".rstrip())

def paint_tree_row_border(painter, option):
    return

def _preferred_csv_delimiter():
    """Use the separator configured for spreadsheet lists on Windows."""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Control Panel\International",
            ) as key:
                delimiter = str(winreg.QueryValueEx(key, "sList")[0] or "")
                if delimiter in {",", ";", "\t", "|"}:
                    return delimiter
        except (OSError, ValueError):
            pass
    return ","

def _export_status(is_folder, modified_time, is_empty, age_cutoff):
    if is_folder and is_empty:
        return "Empty"
    if not modified_time:
        return "Active"
    if age_cutoff is None:
        return "Inactive"
    return "Inactive" if modified_time <= age_cutoff else "Active"

def _export_listing_row(row, columns, age_cutoff=None):
    path, name, is_folder, size, modified_time, parent_path, extension, is_empty = row
    size = int(size or 0)
    modified_time = float(modified_time or 0)
    values = {
        "name": name or os.path.basename(path),
        "path": path,
        "type": "Folder" if is_folder else "File",
        "location": parent_path or "",
        "last_modified": (
            datetime.fromtimestamp(modified_time).strftime("%b %d, %Y")
            if modified_time else ""
        ),
        "age": format_age(modified_time),
        "size_bytes": size,
        "size_formatted": format_size(size),
        "extension": "" if is_folder else (extension or ""),
        "status": _export_status(bool(is_folder), modified_time, bool(is_empty), age_cutoff),
    }
    return [values[column] for column in columns]
