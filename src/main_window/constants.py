import os


VIDEO_EXTENSIONS = (
    ".3g2", ".3gp", ".avi", ".divx", ".flv", ".m2ts", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ogv",
    ".rm", ".rmvb", ".ts", ".vob", ".webm", ".wmv",
)

PERF_DEBUG = os.environ.get("STORAGE_SCOUT_PERF_DEBUG", "").strip().lower() in {
    "1", "true", "yes", "on",
}

EMPTY_FOLDER_SQL = (
    "is_folder = 1 AND EXISTS ("
    "SELECT 1 FROM folder_summary summary "
    "WHERE summary.path = file_index.path COLLATE NOCASE "
    "AND summary.physical_child_count = 0"
    ") AND lower(name) NOT IN ('.git', '__pycache__', 'venv', '.venv', 'node_modules')"
)

EXPORT_COLUMNS = (
    ("name", "Name"),
    ("path", "Full Path"),
    ("type", "Type"),
    ("location", "Location / Parent Path"),
    ("last_modified", "Last Modified"),
    ("age", "Age"),
    ("size_bytes", "Size (bytes)"),
    ("size_formatted", "Size (formatted)"),
    ("extension", "Extension"),
    ("status", "Status"),
)

DEFAULT_EXPORT_COLUMNS = (
    "name",
    "type",
    "last_modified",
    "age",
    "size_formatted",
)
