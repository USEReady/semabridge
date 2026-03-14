"""
Source Browser Widget.

Tree-view component for real-time discovery and selection
of semantic models from source connectors.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QLineEdit,
    QLabel,
    QPushButton,
    QComboBox,
    QCheckBox,
    QGroupBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class ModelDiscoveryWorker(QThread):
    """Background worker for discovering models from source."""
    
    finished = pyqtSignal(list)  # List of model dictionaries
    error = pyqtSignal(str)
    
    def __init__(self, source_type: str, pattern: str = "*"):
        super().__init__()
        self.source_type = source_type
        self.pattern = pattern
    
    def run(self):
        """Execute discovery in background thread."""
        try:
            if self.source_type == "fabric":
                from semabridge.connectors.fabric_extractor import FabricExtractor
                from semabridge.core.settings import get_settings
                
                settings = get_settings()
                extractor = FabricExtractor(settings.fabric)
                models = extractor.list_semantic_models()
                self.finished.emit(models)
            elif self.source_type == "snowflake":
                from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
                from semabridge.core.settings import get_settings
                
                settings = get_settings()
                extractor = SnowflakeExtractor(settings.snowflake)
                models = extractor.list_tables()
                self.finished.emit(models)
            elif self.source_type == "repository":
                from semabridge.repository.duckdb_manager import DuckDBManager
                db = DuckDBManager()
                models = db.list_projects()
                self.finished.emit(models)
            else:
                self.finished.emit([])
        except Exception as e:
            print(f"[DEBUG] Worker error: {e}")
            logger.exception("Model discovery failed")
            self.error.emit(str(e))


class SourceBrowserWidget(QWidget):
    """
    Source Browser panel for model discovery and selection.
    
    Features:
    - Source type selector (Fabric, Snowflake)
    - Pattern-based filtering with glob support
    - Tree-view with checkboxes for multi-select
    - Real-time search/filter
    """
    
    # Signals
    selection_changed = pyqtSignal(list)  # List of selected model names
    models_loaded = pyqtSignal(int)  # Number of models discovered
    model_highlighted = pyqtSignal(dict)  # Emitted when a model is clicked/highlighted
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._models: List[Dict[str, Any]] = []
        self._worker: Optional[ModelDiscoveryWorker] = None
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        
        # Header
        header = QLabel("Source Browser")
        header.setProperty("class", "title")
        # Use stylesheet class for font, avoid direct QFont calls that cause DPI warnings
        layout.addWidget(header)
        
        # Source selector
        source_group = QGroupBox("Source")
        source_layout = QVBoxLayout(source_group)
        source_layout.setSpacing(4)
        
        # Type dropdown
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Type:"))
        self._source_type = QComboBox()
        self._source_type.addItems(["fabric", "snowflake", "repository"])
        self._source_type.currentIndexChanged.connect(self._on_refresh)
        type_row.addWidget(self._source_type, 1)
        source_layout.addLayout(type_row)
        
        # Pattern filter
        pattern_row = QHBoxLayout()
        pattern_row.addWidget(QLabel("Pattern:"))
        self._pattern_input = QLineEdit()
        self._pattern_input.setPlaceholderText("* (glob pattern)")
        self._pattern_input.setText("*")
        pattern_row.addWidget(self._pattern_input, 1)
        source_layout.addLayout(pattern_row)
        
        # Refresh button
        self._refresh_btn = QPushButton("⟳ Discover Models")
        self._refresh_btn.setProperty("class", "secondary")
        self._refresh_btn.clicked.connect(self._on_refresh)
        source_layout.addWidget(self._refresh_btn)
        
        layout.addWidget(source_group)
        
        # Search/filter
        self._search_input = QLineEdit()
        self._search_input.setProperty("class", "search")
        self._search_input.setPlaceholderText("Filter models...")
        self._search_input.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._search_input)
        
        # Model tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Model", "ID"])
        self._tree.setColumnWidth(0, 180)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._tree, 1)
        
        # Selection summary
        summary_row = QHBoxLayout()
        self._selection_label = QLabel("0 selected")
        self._selection_label.setProperty("class", "subtitle")
        summary_row.addWidget(self._selection_label)
        summary_row.addStretch()
        
        select_all_btn = QPushButton("Select All")
        select_all_btn.setProperty("class", "secondary")
        select_all_btn.clicked.connect(self._select_all)
        summary_row.addWidget(select_all_btn)
        
        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("class", "secondary")
        clear_btn.clicked.connect(self._clear_selection)
        summary_row.addWidget(clear_btn)
        
        layout.addLayout(summary_row)
    
    def _on_refresh(self):
        """Handle refresh button click."""
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("Discovering...")
        
        source_type = self._source_type.currentText()
        pattern = self._pattern_input.text() or "*"
        
        self._worker = ModelDiscoveryWorker(source_type, pattern)
        self._worker.finished.connect(self._on_models_loaded)
        self._worker.error.connect(self._on_discovery_error)
        self._worker.start()
    
    def _on_models_loaded(self, models: List[Dict[str, Any]]):
        """Handle successful model discovery."""
        self._models = models
        self._populate_tree()
        
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("⟳ Discover Models")
        
        self.models_loaded.emit(len(models))
        logger.info(f"Loaded {len(models)} models into browser")
    
    def _on_discovery_error(self, error: str):
        """Handle discovery error."""
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("⟳ Discover Models")
        
        # Show error in tree
        self._tree.clear()
        error_item = QTreeWidgetItem(["Error: " + error[:50], ""])
        self._tree.addTopLevelItem(error_item)
        
        logger.error(f"Discovery error: {error}")
    
    def _populate_tree(self):
        """Populate the tree with discovered models."""
        self._tree.clear()
        filter_text = self._search_input.text().lower()
        source_type = self._source_type.currentText()
        
        # Adjust columns based on source type
        if source_type == "repository":
            self._tree.setHeaderLabels(["ID", "Model"])
            # Ensure both columns are visible
            self._tree.setColumnHidden(1, False)
        else:
            self._tree.setHeaderLabels(["Model", "ID"])
            # Hide ID column for snowflake as it's redundant (same as name)
            self._tree.setColumnHidden(1, source_type == "snowflake")
        
        for model in self._models:
            name = model.get("displayName", model.get("id", "Unknown"))
            model_id = model.get("id", "")
            
            # Apply filter if set
            if filter_text and filter_text not in name.lower():
                continue
            
            if source_type == "repository":
                # Swap ID and Name
                item = QTreeWidgetItem([model_id, name])
            else:
                item = QTreeWidgetItem([name, model_id])
                
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, model)
            self._tree.addTopLevelItem(item)
        
        self._update_selection_label()
    
    def _on_filter_changed(self, text: str):
        """Handle filter text change."""
        self._populate_tree()
    
    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handle item check state change."""
        if column == 0:
            self._update_selection_label()
            self.selection_changed.emit(self.get_selected_models())

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle item click/highlight."""
        model_data = item.data(0, Qt.ItemDataRole.UserRole)
        if model_data:
            self.model_highlighted.emit(model_data)
    
    def _update_selection_label(self):
        """Update the selection count label."""
        count = len(self.get_selected_models())
        self._selection_label.setText(f"{count} selected")
    
    def _select_all(self):
        """Select all visible models."""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            item.setCheckState(0, Qt.CheckState.Checked)
    
    def _clear_selection(self):
        """Clear all selections."""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            item.setCheckState(0, Qt.CheckState.Unchecked)
    
    def get_selected_models(self) -> List[str]:
        """Get list of selected model names."""
        selected = []
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                selected.append(item.text(0))
        return selected
    
    def get_selected_model_data(self) -> List[Dict[str, Any]]:
        """Get full model data for selected items."""
        selected = []
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data:
                    selected.append(data)
        return selected
