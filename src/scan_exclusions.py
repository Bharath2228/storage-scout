from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch


DEFAULT_EXCLUDED_FOLDERS = ["venv", "__pycache__", "node_modules", ".git"]


def normalize_extension(value: str) -> str:
    ext = (value or "").strip().lower()
    if not ext:
        return ""
    return ext if ext.startswith(".") else f".{ext}"


@dataclass
class ScanExclusions:
    folder_names: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_FOLDERS))
    extensions: list[str] = field(default_factory=list)
    min_file_size_bytes: int = 0

    def matches_excluded_folder(self, name: str) -> bool:
        value = (name or "").lower()
        return any(
            fnmatch.fnmatch(value, pattern.strip().lower())
            for pattern in self.folder_names
            if pattern and pattern.strip()
        )

    def matches_excluded_extension(self, ext: str) -> bool:
        value = normalize_extension(ext)
        return bool(value) and value in {normalize_extension(item) for item in self.extensions}

    def to_dict(self) -> dict:
        return {
            "folder_names": [item.strip() for item in self.folder_names if item and item.strip()],
            "extensions": [
                normalize_extension(item)
                for item in self.extensions
                if normalize_extension(item)
            ],
            "min_file_size_bytes": max(0, int(self.min_file_size_bytes or 0)),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScanExclusions":
        if not isinstance(data, dict):
            return cls()
        folders = data.get("folder_names", DEFAULT_EXCLUDED_FOLDERS)
        extensions = data.get("extensions", [])
        min_size = data.get("min_file_size_bytes", 0)
        if not isinstance(folders, list):
            folders = DEFAULT_EXCLUDED_FOLDERS
        if not isinstance(extensions, list):
            extensions = []
        try:
            min_size = int(min_size or 0)
        except (TypeError, ValueError):
            min_size = 0
        return cls(
            folder_names=[str(item).strip() for item in folders if str(item).strip()],
            extensions=[
                normalize_extension(str(item))
                for item in extensions
                if normalize_extension(str(item))
            ],
            min_file_size_bytes=max(0, min_size),
        )

    def differs_from_default(self) -> bool:
        return self.to_dict() != ScanExclusions().to_dict()
