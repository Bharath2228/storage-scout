import os

from .path_utils import _path_key


def folder_is_physically_empty(cursor, path):
    try:
        cursor.execute(
            "SELECT physical_child_count FROM folder_summary "
            "WHERE path = ? COLLATE NOCASE",
            (path,),
        )
        row = cursor.fetchone()
    except Exception:
        row = None
    if row and row[0] is not None and row[0] >= 0:
        return row[0] == 0
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except OSError:
        return False

def filesystem_folder_has_visible_entries(
    folder_path,
    exclusions,
    cancel_check=None,
    visited=None,
):
    if cancel_check:
        cancel_check()
    visited = visited if visited is not None else set()
    folder_key = _path_key(folder_path)
    if folder_key in visited:
        return False
    visited.add(folder_key)

    try:
        with os.scandir(folder_path) as entries:
            folder_entries = list(entries)
    except OSError:
        return False
    if not folder_entries:
        return True

    for entry in folder_entries:
        if cancel_check:
            cancel_check()
        try:
            is_folder = entry.is_dir(follow_symlinks=True)
            size = 0 if is_folder else entry.stat(follow_symlinks=True).st_size
        except OSError:
            continue
        if exclusions is not None:
            if is_folder and exclusions.matches_excluded_folder(entry.name):
                continue
            if not is_folder and (
                exclusions.matches_excluded_extension(
                    os.path.splitext(entry.name)[1].lower()
                )
                or (
                    exclusions.min_file_size_bytes > 0
                    and size < exclusions.min_file_size_bytes
                )
            ):
                continue
        if not is_folder:
            return True
        if filesystem_folder_has_visible_entries(
            entry.path,
            exclusions,
            cancel_check=cancel_check,
            visited=visited,
        ):
            return True
    return False

def escape_sql_like(value):
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )

def descendant_like_patterns(path):
    bases = {
        str(path).rstrip("\\/"),
        os.path.normpath(path).rstrip("\\/"),
    }
    patterns = []
    for base in bases:
        if not base:
            continue
        for separator in ("\\", "/"):
            pattern = escape_sql_like(base + separator) + "%"
            if pattern not in patterns:
                patterns.append(pattern)
    return patterns

def descendant_like_sql(path, column="path"):
    patterns = descendant_like_patterns(path)
    if not patterns:
        return "1=0", []
    sql = " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for _ in patterns)
    return sql, patterns

def descendant_scope_sql(path, column="path"):
    prefix_sql, prefix_params = descendant_like_sql(path, column)
    normalized = os.path.normpath(str(path)).rstrip("\\/")
    variants = list(dict.fromkeys((
        str(path).rstrip("\\/"),
        normalized,
        normalized.replace("\\", "/"),
        normalized.replace("/", "\\"),
    )))
    variants = [variant for variant in variants if variant]
    if not variants:
        return prefix_sql, prefix_params

    placeholders = ",".join("?" * len(variants))
    hierarchy_sql = (
        f"{column} IN ("
        "WITH RECURSIVE scoped_paths(path) AS ("
        f"SELECT seed.path FROM file_index seed "
        f"WHERE seed.path COLLATE NOCASE IN ({placeholders}) "
        "UNION "
        "SELECT child.path FROM file_index child "
        "JOIN scoped_paths parent "
        "ON child.parent_path = parent.path COLLATE NOCASE"
        ") "
        f"SELECT path FROM scoped_paths WHERE path COLLATE NOCASE NOT IN ({placeholders})"
        ")"
    )
    return f"(({prefix_sql}) OR ({hierarchy_sql}))", [
        *prefix_params,
        *variants,
        *variants,
    ]

def case_insensitive_path_sql(column="path"):
    return f"{column} = ? COLLATE NOCASE"

def build_sort_order_clause(view_mode, sort_column, sort_desc):
    primary_dir = "DESC" if sort_desc else "ASC"
    age_dir = "ASC" if sort_desc else "DESC"

    if sort_column == 0:
        return f"lower(name) {primary_dir}, lower(path) ASC"
    if sort_column == 1:
        if view_mode == 'Tree':
            return f"is_folder {primary_dir}, lower(name) ASC, lower(path) ASC"
        return f"lower(parent_path) {primary_dir}, lower(name) ASC, lower(path) ASC"
    if sort_column == 2:
        return f"modified_time {primary_dir}, lower(path) ASC"
    if sort_column == 3:
        return f"modified_time {age_dir}, lower(path) ASC"
    if sort_column == 4:
        return f"size {primary_dir}, lower(path) ASC"
    if sort_column == 5:
        return f"modified_time {primary_dir}, is_folder DESC, lower(name) ASC"
    return f"lower(path) {primary_dir}"

def tree_sort_value(node, sort_column):
    if sort_column == 1:
        return 0 if node.get('is_dir') else 1
    if sort_column in (2, 3, 5):
        return node.get('last_modified', 0) or 0
    if sort_column == 4:
        return node.get('size', 0) or 0
    return (node.get('name', '') or '').lower()

def sort_tree_siblings(node, sort_column, sort_desc):
    children = node.get('children', [])
    for child in children:
        sort_tree_siblings(child, sort_column, sort_desc)

    children.sort(key=lambda child: (
        tree_sort_value(child, sort_column),
        (child.get('name', '') or '').lower(),
        (child.get('path', '') or '').lower(),
    ), reverse=sort_desc)

def prune_contained_paths(paths, cancel_check=None, sort_alpha=False):
    unique_paths = list(dict.fromkeys(path for path in paths if path))
    if sort_alpha:
        sort_key = lambda value: (len(os.path.normpath(value)), value.lower())
    else:
        sort_key = lambda value: len(os.path.normpath(value))
    sorted_paths = sorted(unique_paths, key=sort_key)
    kept = []
    kept_keys = set()

    for path in sorted_paths:
        if cancel_check:
            cancel_check()
        normalized = _path_key(path)
        current = normalized
        is_contained = False
        while current:
            if current in kept_keys:
                is_contained = True
                break
            parent = os.path.dirname(current.rstrip("\\/"))
            if not parent or parent == current:
                break
            current = parent
        if is_contained:
            continue
        kept.append(path)
        kept_keys.add(normalized)

    return kept

def summarize_paths_batch(cursor, paths, cancel_check=None):
    def check_cancelled():
        if cancel_check:
            cancel_check()

    pruned_paths = prune_contained_paths(paths)
    if not pruned_paths:
        return [], 0, 0, 0

    rows_by_key = {}
    for start in range(0, len(pruned_paths), 900):
        check_cancelled()
        batch = pruned_paths[start:start + 900]
        placeholders = ",".join("?" * len(batch))
        cursor.execute(
            f"SELECT path, is_folder, size FROM file_index WHERE path COLLATE NOCASE IN ({placeholders})",
            batch,
        )
        for path, is_folder, size in cursor.fetchall():
            rows_by_key[os.path.normcase(os.path.normpath(path))] = (path, is_folder, size)

    folders = 0
    files = 0
    total_size = 0
    folder_paths = []

    def summarize_filesystem_path(path):
        check_cancelled()
        if os.path.isfile(path):
            try:
                return 0, 1, os.path.getsize(path)
            except OSError:
                return 0, 1, 0
        if not os.path.isdir(path):
            return 0, 1, 0

        folder_count = 1
        file_count = 0
        size = 0
        for current, dir_names, file_names in os.walk(path):
            check_cancelled()
            folder_count += len(dir_names)
            file_count += len(file_names)
            for file_name in file_names:
                try:
                    size += os.path.getsize(os.path.join(current, file_name))
                except OSError:
                    continue
        return folder_count, file_count, size

    for path in pruned_paths:
        check_cancelled()
        row = rows_by_key.get(os.path.normcase(os.path.normpath(path)))
        if not row:
            path_folders, path_files, path_size = summarize_filesystem_path(path)
            folders += path_folders
            files += path_files
            total_size += path_size
            continue

        _, is_folder, size = row
        if is_folder:
            folder_paths.append(path)
        else:
            files += 1
            total_size += size or 0

    remaining_folder_paths = []
    for start in range(0, len(folder_paths), 900):
        check_cancelled()
        batch = folder_paths[start:start + 900]
        placeholders = ",".join("?" * len(batch))
        cursor.execute(
            f"""
            SELECT path, total_size, file_count, folder_count
            FROM folder_summary
            WHERE path COLLATE NOCASE IN ({placeholders})
            """,
            batch,
        )
        summary_by_key = {
            os.path.normcase(os.path.normpath(path)): (total_size, file_count, folder_count)
            for path, total_size, file_count, folder_count in cursor.fetchall()
        }
        for folder_path in batch:
            summary = summary_by_key.get(os.path.normcase(os.path.normpath(folder_path)))
            if summary:
                summary_size, summary_files, summary_folders = summary
                folders += summary_folders or 0
                files += summary_files or 0
                total_size += summary_size or 0
            else:
                remaining_folder_paths.append(folder_path)

    for start in range(0, len(remaining_folder_paths), 120):
        check_cancelled()
        batch = remaining_folder_paths[start:start + 120]
        clauses = []
        params = []
        for folder_path in batch:
            descendant_sql, descendant_params = descendant_like_sql(folder_path)
            clauses.append(f"({case_insensitive_path_sql()} OR ({descendant_sql}))")
            params.extend([folder_path, *descendant_params])
        cursor.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN is_folder = 1 THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_folder = 0 THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_folder = 0 THEN size ELSE 0 END), 0) "
            "FROM file_index WHERE " + " OR ".join(clauses),
            params,
        )
        batch_folders, batch_files, batch_size = cursor.fetchone()
        folders += batch_folders or 0
        files += batch_files or 0
        total_size += batch_size or 0

    return pruned_paths, folders, files, total_size
