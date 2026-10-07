import os
from bisect import bisect_left, insort


def _path_key(path):
    return os.path.normcase(os.path.normpath(path))

def bulk_scope_excluded_keys(scope):
    return {
        _path_key(path)
        for path in (scope or {}).get("excluded_paths", [])
        if path
    }

def equivalent_path_variants(path):
    if not path:
        return []
    normalized = os.path.normpath(path)
    stripped = normalized.rstrip("\\/")
    variants = {
        path,
        normalized,
        stripped,
        stripped + "\\",
        stripped + "/",
    }
    variants.update(value.replace("\\", "/") for value in list(variants))
    variants.update(value.replace("/", "\\") for value in list(variants))
    return [value for value in variants if value]

class PathKeyIndex:
    """Indexed normalized paths with depth-based ancestor and bisected descendant lookup."""

    def __init__(self, keys=()):
        self._keys = set(keys)
        self._sorted_keys = sorted(self._keys)

    def add(self, key):
        if key in self._keys:
            return
        self._keys.add(key)
        insort(self._sorted_keys, key)

    def discard(self, key):
        if key not in self._keys:
            return
        self._keys.remove(key)
        position = bisect_left(self._sorted_keys, key)
        if position < len(self._sorted_keys) and self._sorted_keys[position] == key:
            self._sorted_keys.pop(position)

    def clear(self):
        self._keys.clear()
        self._sorted_keys.clear()

    def has_ancestor(self, key, include_self=True):
        current = key if include_self else os.path.dirname(key)
        while current:
            if current in self._keys:
                return True
            parent = os.path.dirname(current.rstrip("\\/"))
            if not parent or parent == current:
                break
            current = parent
        return False

    def descendant_keys(self, key, include_self=False):
        matches = []
        if include_self and key in self._keys:
            matches.append(key)
        prefix = key if key.endswith(("\\", "/")) else key + os.sep
        position = bisect_left(self._sorted_keys, prefix)
        while position < len(self._sorted_keys):
            candidate = self._sorted_keys[position]
            if not candidate.startswith(prefix):
                break
            if candidate != key:
                matches.append(candidate)
            position += 1
        return matches

    def has_descendant(self, key):
        prefix = key if key.endswith(("\\", "/")) else key + os.sep
        position = bisect_left(self._sorted_keys, prefix)
        while (
            position < len(self._sorted_keys)
            and self._sorted_keys[position] == key
        ):
            position += 1
        return (
            position < len(self._sorted_keys)
            and self._sorted_keys[position].startswith(prefix)
        )

    def remove_many(self, keys):
        removed = set(keys)
        if not removed:
            return
        self._keys.difference_update(removed)
        self._sorted_keys = [
            key for key in self._sorted_keys
            if key not in removed
        ]

class IndexedPathDict(dict):
    """Dictionary that keeps a path-prefix index synchronized with key mutations."""

    _MISSING = object()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.path_index = PathKeyIndex(self.keys())

    def __setitem__(self, key, value):
        is_new = key not in self
        super().__setitem__(key, value)
        if is_new:
            self.path_index.add(key)

    def __delitem__(self, key):
        super().__delitem__(key)
        self.path_index.discard(key)

    def pop(self, key, default=_MISSING):
        if key in self:
            value = super().pop(key)
            self.path_index.discard(key)
            return value
        if default is self._MISSING:
            raise KeyError(key)
        return default

    def clear(self):
        super().clear()
        self.path_index.clear()

    def update(self, *args, **kwargs):
        values = dict(*args, **kwargs)
        for key, value in values.items():
            self[key] = value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def popitem(self):
        key, value = super().popitem()
        self.path_index.discard(key)
        return key, value

    def __ior__(self, other):
        self.update(other)
        return self

    def remove_descendants(self, key, include_self=False):
        keys = self.path_index.descendant_keys(key, include_self=include_self)
        for descendant_key in keys:
            dict.__delitem__(self, descendant_key)
        self.path_index.remove_many(keys)
