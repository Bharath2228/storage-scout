import os
import time

from ...scan_exclusions import ScanExclusions
from ..path_utils import _path_key
from ..workers.page_load import LazyChildrenLoadThread


class _FolderBrowserMixin:
    def _toggle_folder_browser(self):
        is_visible = self.folder_browser.isVisible()
        self.folder_browser.setVisible(not is_visible)
        self.btn_folder_browser.setChecked(not is_visible)

    def _set_folder_browser_scope(self, path, reload=True):
        root = getattr(self, 'current_scan_root', None)
        normalized = os.path.normpath(path) if path else None
        if root and normalized and self._path_key(normalized) == self._path_key(root):
            normalized = None
        if normalized == self.folder_browser_scope:
            return
        self.folder_browser_scope = normalized
        update_cached_size = getattr(
            self,
            '_update_scoped_folder_size_from_cache',
            None,
        )
        if callable(update_cached_size):
            update_cached_size(normalized)
        if reload:
            self._on_filter_changed(clear_extension=False)

    def _cached_scoped_folder_total(self, path=None):
        scoped_path = path if path is not None else self.folder_browser_scope
        cache = getattr(self, 'folder_cache', None)
        if not scoped_path or cache is None or not hasattr(cache, 'folder_metadata'):
            return None
        metadata = cache.folder_metadata(scoped_path)
        return None if metadata is None else metadata[0]

    def _update_scoped_folder_size_from_cache(self, path=None):
        scoped_path = path if path is not None else self.folder_browser_scope
        if not scoped_path:
            self.chip_folder_size.setVisible(False)
            return
        cached_total = self._cached_scoped_folder_total(scoped_path)
        if cached_total is None:
            self._set_chip_text(
                self.chip_folder_size,
                "Folder Size calculating...",
            )
        else:
            self._set_chip_text(
                self.chip_folder_size,
                f"Folder Size {self._format_chip_size(cached_total)}",
            )
        self.chip_folder_size.setVisible(True)

    def _on_folder_browser_scope_changed(self, path):
        self._set_folder_browser_scope(path)

    def _load_folder_browser_children(self, folder_path):
        if not folder_path or not getattr(self, 'current_scan_root', None):
            return
        if (
            getattr(self, 'is_scanning', False)
            and getattr(self, 'hide_partial_scan_results', False)
        ):
            self.folder_browser.defer_load(folder_path)
            return
        cache = getattr(self, 'folder_cache', None)
        if cache:
            children = []
            if cache.has_children_for(folder_path):
                children = [
                    child
                    for child in cache.children_for(folder_path, 0, False)
                    if child.get('is_dir', False)
                ]
                for child in children:
                    child['_children_loaded'] = not cache.has_folder_children(
                        child.get('path')
                    )
            if children:
                self.folder_browser.apply_children(folder_path, children)
                if self.is_scanning:
                    return
            if self.is_scanning:
                # Keep the node retryable while the background loader falls back
                # to enumerating folders directly from the selected location.
                self.folder_browser.defer_load(folder_path)
        self.folder_browser_request_id += 1
        request_id = self.folder_browser_request_id
        thread = LazyChildrenLoadThread(
            request_id,
            folder_path,
            sort_column=0,
            sort_desc=False,
            options=None,
            cache=cache,
            folders_only=True,
            filesystem_fallback=True,
            force_filesystem=not self.is_scanning,
            scan_exclusions=getattr(
                getattr(self, 'fp', None),
                'get_scan_exclusions',
                lambda: ScanExclusions(),
            )(),
            parent=self,
        )
        self.folder_browser_threads[request_id] = thread
        thread.children_ready.connect(self._on_folder_browser_children_ready)
        thread.children_failed.connect(self._on_folder_browser_children_failed)
        thread.finished.connect(
            lambda request_id=request_id: self.folder_browser_threads.pop(request_id, None)
        )
        thread.start()

    def _on_folder_browser_children_ready(self, request_id, folder_path, children):
        if request_id not in self.folder_browser_threads:
            return
        if (
            getattr(self, 'is_scanning', False)
            and getattr(self, 'hide_partial_scan_results', False)
        ):
            return
        self.folder_browser.apply_children(folder_path, children)

    def _on_folder_browser_children_failed(self, request_id, folder_path, error):
        if request_id not in self.folder_browser_threads:
            return
        self.folder_browser.mark_load_failed(folder_path)

    def _reset_folder_browser(self, root_path=None):
        self.folder_browser_request_id += 1
        self.folder_browser_threads.clear()
        self.folder_browser_scope = None
        self.folder_browser.set_root(root_path)

    def _refresh_folder_browser_from_cache(self, force=False):
        if (
            getattr(self, 'is_scanning', False)
            and getattr(self, 'hide_partial_scan_results', False)
        ):
            return
        cache = getattr(self, 'folder_cache', None)
        root_path = getattr(self, 'current_scan_root', None)
        if not cache or not root_path or not hasattr(self, 'folder_browser'):
            return

        now = time.monotonic()
        if not force and now - self.folder_browser_live_refresh_at < 0.75:
            return
        self.folder_browser_live_refresh_at = now

        browser_root = self.folder_browser.root_path
        if not browser_root or _path_key(browser_root) != _path_key(root_path):
            self._reset_folder_browser(root_path)

        model = self.folder_browser.model
        nodes = list(model.nodes_by_path.values())
        for node in nodes:
            index = model.index_for_path(node.path)
            should_refresh = (
                _path_key(node.path) == _path_key(root_path)
                or node.loaded
                or (index.isValid() and self.folder_browser.tree.isExpanded(index))
            )
            if not should_refresh or not cache.has_children_for(node.path):
                continue
            children = [
                child
                for child in cache.children_for(node.path, 0, False)
                if child.get('is_dir', False)
            ]
            for child in children:
                child['_children_loaded'] = not cache.has_folder_children(
                    child.get('path')
                )
            if children:
                self.folder_browser.merge_children(node.path, children)
            elif self.is_scanning and not node.loaded:
                self.folder_browser.defer_load(node.path)
            elif (
                not self.is_scanning
                and index.isValid()
                and self.folder_browser.tree.isExpanded(index)
            ):
                model.fetchMore(index)
