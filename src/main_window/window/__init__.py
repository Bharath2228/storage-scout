from PyQt6.QtWidgets import QMainWindow

from .base import _BaseMixin
from .folder_browser_mixin import _FolderBrowserMixin
from .filters_mixin import _FiltersMixin
from .selection_mixin import _SelectionMixin
from .scan_mixin import _ScanMixin
from .tree_interactions_mixin import _TreeInteractionsMixin
from .delete_mixin import _DeleteMixin
from .export_mixin import _ExportMixin


class MainWindow(_BaseMixin, _FolderBrowserMixin, _FiltersMixin, _SelectionMixin, _ScanMixin, _TreeInteractionsMixin, _DeleteMixin, _ExportMixin, QMainWindow):
    """Main application window.

    Behaviour is implemented across the mixins above, split by responsibility
    (folder browser, filters, selection, scan/pagination, tree interactions,
    delete flow, export flow) with general window chrome in ``_BaseMixin``.
    No mixin method name is duplicated across the others, so this composition
    is behaviourally identical to the previous single ~5,500-line class.

    ``QMainWindow`` is deliberately listed *last* in the bases (the standard
    mixin convention): ``_BaseMixin.__init__`` calls bare ``super().__init__()``,
    which must walk through the whole MRO and land on ``QMainWindow.__init__``
    to construct the underlying Qt C++ object. Putting ``QMainWindow`` first
    would make that ``super()`` call skip past it instead, leaving the widget's
    C++ side uninitialized and crashing on first use.
    """
