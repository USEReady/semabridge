"""
Version History Widget.

Timeline view of configuration changes with commit-style
history and rollback capabilities.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QGroupBox,
    QTextEdit,
    QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class VersionHistoryWidget(QWidget):
    """
    Version history panel for configuration tracking.
    
    Features:
    - Timeline of changes (commit-style)
    - Change author and timestamp
    - Commit message/description
    - Rollback and compare actions
    - Powered by VersionManager (YAML) and DuckDBManager (Models)
    """
    
    version_selected = pyqtSignal(dict)  # Emitted when a version is selected
    compare_requested = pyqtSignal(str, str)  # (left_version_id, right_version_id)
    
    def __init__(self, version_manager, config_manager, duckdb_manager=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.version_manager = version_manager
        self.config_manager = config_manager
        self.duckdb_manager = duckdb_manager
        self._versions: List[Dict[str, Any]] = []
        self._model_id: Optional[str] = None  # Current model ID filter
        self._model_name: Optional[str] = None
        
        self._setup_ui()
        self.refresh()
    
    def _setup_ui(self):
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        
        # Header
        header = QHBoxLayout()
        self._title = QLabel("Version History")
        self._title.setProperty("class", "title")
        layout.addWidget(self._title)
        
        header.addStretch()
        
        refresh_btn = QPushButton("⟳")
        refresh_btn.setToolTip("Refresh History")
        refresh_btn.clicked.connect(self.refresh)
        refresh_btn.setFixedWidth(30)
        header.addWidget(refresh_btn)
        
        layout.addLayout(header)
        
        # Version list
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSpacing(2)
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection) # Allow multiple selection
        self._list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)
        
        # Selected version details
        details_group = QGroupBox("Details")
        details_layout = QVBoxLayout(details_group)
        details_layout.setSpacing(4)
        
        # Version info
        self._version_label = QLabel("Select a version")
        self._version_label.setProperty("class", "monospace")
        details_layout.addWidget(self._version_label)
        
        self._timestamp_label = QLabel("")
        self._timestamp_label.setProperty("class", "subtitle")
        details_layout.addWidget(self._timestamp_label)
        
        # Change description
        self._description = QTextEdit()
        self._description.setReadOnly(True)
        self._description.setMaximumHeight(80)
        self._description.setPlaceholderText("Change description")
        details_layout.addWidget(self._description)
        
        layout.addWidget(details_group)
        
        # Action buttons
        actions = QHBoxLayout()
        
        compare_btn = QPushButton("⇔ Compare")
        compare_btn.setProperty("class", "secondary")
        compare_btn.setToolTip("Compare with current config")
        compare_btn.clicked.connect(self._on_compare)
        actions.addWidget(compare_btn)
        
        rollback_btn = QPushButton("↩ Rollback")
        rollback_btn.setProperty("class", "secondary")
        rollback_btn.setToolTip("Restore this version")
        rollback_btn.clicked.connect(self._on_rollback)
        actions.addWidget(rollback_btn)
        
        layout.addLayout(actions)
    
    def refresh(self):
        """Refresh version history from backend."""
        try:
            if self._model_id and self.duckdb_manager:
                snapshots = self.duckdb_manager.list_snapshots(self._model_id)
                model_versions = [
                    {
                        "version_id": s.snapshot_id,
                        "timestamp": datetime.fromisoformat(s.timestamp.replace('Z', '+00:00')).timestamp(),
                        "description": s.version_tag or f"Snapshot {s.snapshot_id[:8]}",
                        "status": s.status,
                        "sml_blob": s.sml_blob,
                        "type": "model"
                    }
                    for s in snapshots
                ]
                if model_versions:
                    self._versions = model_versions
                else:
                    self._versions = self._load_sync_schema_versions(self._model_name)
            else:
                db_versions = self._load_db_config_versions()
                if db_versions:
                    self._versions = db_versions
                else:
                    self._versions = self.version_manager.list_versions()
                    for v in self._versions:
                        v["type"] = "config"
            
            self._populate_list()
        except Exception as e:
            logger.error(f"Failed to refresh history: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def set_model_context(self, model_data: Optional[Dict[str, Any]] = None):
        """Focus history on a specific model."""
        if isinstance(model_data, dict):
            self._model_id = model_data.get("id")
            self._model_name = model_data.get("displayName")
            model_name = self._model_name or self._model_id
            self._title.setText(f"History: {model_name}")
            logger.info(f"Setting model context to {self._model_id} ({model_name})")
        elif isinstance(model_data, str):
            self._model_id = model_data
            self._model_name = model_data
            self._title.setText(f"History: {model_data}")
        else:
            self._model_id = None
            self._model_name = None
            self._title.setText("Global Config History")
            logger.info("Clearing model context")
            
        self.refresh()
            
    def _populate_list(self):
        """Populate the version list."""
        self._list.clear()
        
        for version in self._versions:
            # Format: "v123456... - Description"
            vid = version.get("version_id", "unknown")
            desc = version.get("description", "No description")
            ts = version.get("timestamp", 0)
            
            # Simple readable label
            dt = datetime.fromtimestamp(ts).strftime("%H:%M")
            text = f"{dt} - {desc}"
            
            item = QListWidgetItem(text)
            
            # Highlight status
            if version.get("status") == "failed":
                item.setForeground(QColor("#EF4444")) # Error color
            
            item.setData(Qt.ItemDataRole.UserRole, version)
            self._list.addItem(item)
    
    def _on_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        """Handle version selection change."""
        if current is None:
            return
        
        version = current.data(Qt.ItemDataRole.UserRole)
        if version:
            vid = version.get("version_id", "???")
            ts = version.get("timestamp", 0)
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            
            self._version_label.setText(f'{vid[:15]}...')
            self._timestamp_label.setText(f'📅 {dt}')
            self._description.setText(version.get("description", ""))
            
            self.version_selected.emit(version)
            
    def _on_compare(self):
        """Handle compare button click."""
        selected_items = self._list.selectedItems()
        if not selected_items:
            # Fallback to current item if no selection (shouldn't happen with button enabled)
            item = self._list.currentItem()
            if not item: return
            selected_items = [item]
            
        if len(selected_items) == 2:
            # Compare two specific versions
            v1 = selected_items[0].data(Qt.ItemDataRole.UserRole)
            v2 = selected_items[1].data(Qt.ItemDataRole.UserRole)
            # Ensure chronological order (optional, main_window handles left/right)
            self.compare_requested.emit(v1['version_id'], v2['version_id'])
        else:
            # Compare one version against current/HEAD
            version = selected_items[0].data(Qt.ItemDataRole.UserRole)
            version_id = version['version_id']
            self.compare_requested.emit(version_id, "current")

    def _on_rollback(self):
        """Handle rollback button click."""
        from PyQt6.QtWidgets import QMessageBox
        
        item = self._list.currentItem()
        if not item:
            return
            
        version = item.data(Qt.ItemDataRole.UserRole)
        version_id = version['version_id']
        
        # Confirm rollback
        result = QMessageBox.question(
            self,
            "Confirm Rollback",
            f"Rollback to {version.get('description')}?\n\n"
            f"This will restore the semantic state from this version.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if result != QMessageBox.StandardButton.Yes:
            return

        if version.get("type") == "model" and self.duckdb_manager:
            logger.info(f"Rolling back model to {version_id}")
            try:
                success, new_id, _ = self.duckdb_manager.rollback(
                    self._model_id, 
                    version_id, 
                    f"Rollback to {version_id[:8]}"
                )
                if success:
                    QMessageBox.information(self, "Rollback Complete", f"Restored model to version {version_id[:8]}")
                else:
                    QMessageBox.warning(self, "Rollback Aborted", "Model is already at this version.")
            except Exception as e:
                logger.error(f"DuckDB Rollback failed: {e}")
                QMessageBox.critical(self, "Rollback Error", f"Failed to restore model version: {e}")
        elif version.get("type") in {"config_db", "model_schema"}:
            QMessageBox.information(
                self,
                "Rollback Not Available",
                "Rollback is currently supported for DuckDB snapshots and legacy config history only.",
            )
        else:
            content = version.get("content", "")
            logger.info(f"Rolling back config to {version_id}")
            self.config_manager.update_text(content, source="rollback")
            self.version_manager.create_version(
                content, 
                description=f"Rollback to {version_id[:8]}"
            )
            QMessageBox.information(self, "Rollback Complete", "Configuration restored.")
            
        self.refresh()

    def _load_db_config_versions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Load config versions from ORM-backed ModelVersionHistory."""
        try:
            from semabridge.repository.orm.models import ModelVersionHistory
            from semabridge.repository.orm.session_factory import get_session_factory

            SessionLocal = get_session_factory()
            with SessionLocal() as session:
                rows = (
                    session.query(ModelVersionHistory)
                    .order_by(ModelVersionHistory.applied_at.desc())
                    .limit(limit)
                    .all()
                )

            versions: List[Dict[str, Any]] = []
            for row in rows:
                ts = row.applied_at.timestamp() if row.applied_at else datetime.now().timestamp()
                versions.append(
                    {
                        "version_id": f"cfgdb-{row.id}",
                        "timestamp": ts,
                        "description": f"{row.model_name} ({row.version_tag})",
                        "status": "completed",
                        "model_name": row.model_name,
                        "version_tag": row.version_tag,
                        "yaml_hash": row.yaml_hash,
                        "type": "config_db",
                    }
                )
            return versions
        except Exception as exc:
            logger.debug(f"DB config history unavailable: {exc}")
            return []

    def _load_sync_schema_versions(
        self, model_name: Optional[str], limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Load sync schema versions for the selected model as fallback."""
        if not model_name:
            return []

        try:
            from semabridge.sync.repository import SyncRepository

            repo = SyncRepository()
            rows = repo.get_schema_history(model_name=model_name, limit=limit)

            versions: List[Dict[str, Any]] = []
            for row in rows:
                created_at = row.created_at or ""
                try:
                    ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = datetime.now().timestamp()

                versions.append(
                    {
                        "version_id": row.version_id,
                        "timestamp": ts,
                        "description": f"Schema v{row.version_number}",
                        "status": "completed",
                        "schema_hash": row.schema_hash,
                        "changes_count": len(row.changes_from_previous),
                        "type": "model_schema",
                    }
                )
            return versions
        except Exception as exc:
            logger.debug(f"Sync schema history unavailable for {model_name}: {exc}")
            return []

