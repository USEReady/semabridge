"""
SemaBridge Main Window.

Professional-grade PyQt6 application implementing:
- Monochromatic and high-contrast palettes
- Optimized spacing (4px/8px/12px grid)
- High-density widgets for enterprise data management
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QTabWidget,
    QStatusBar,
    QMenuBar,
    QMenu,
    QToolBar,
    QLabel,
    QPushButton,
    QProgressBar,
    QMessageBox,
    QSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QIcon, QPalette, QColor

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# Design system constants (4px grid)
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24

# Professional color palette (Light Blue-White Theme)
COLORS = {
    "bg_primary": "#F8FAFC",      # Soft Slate/White (Main Background)
    "bg_secondary": "#F1F5F9",    # Light Gray (Sections/Groups)
    "bg_tertiary": "#FFFFFF",     # White (Cards/Panels)
    "bg_selection": "#DBEAFE",    # Light Blue selection
    "text_primary": "#0F172A",    # Dark Navy (Primary)
    "text_secondary": "#475569",  # Slate Gray (Secondary)
    "text_muted": "#94A3B8",      # Light Gray (Muted)
    "accent": "#2563EB",          # Brand Blue
    "accent_hover": "#1D4ED8",    # Hover Blue
    "success": "#059669",         # Success green
    "warning": "#D97706",         # Warning amber
    "error": "#DC2626",           # Error red
    "border": "#E2E8F0",          # Subtle Border
    "divider": "#CBD5E1",         # Strong Divider
    "button_secondary": "#E2E8F0", # Secondary Button BG
}


def get_stylesheet() -> str:
    """Generate the application stylesheet."""
    return f"""
        QMainWindow, QDialog, QMessageBox {{
            background-color: {COLORS['bg_primary']};
        }}
        
        QDialog QLabel, QMessageBox QLabel {{
            color: {COLORS['text_primary']};
        }}
        
        /* ===== GENERIC TEXT ===== */
        QWidget {{
            color: {COLORS['text_primary']};
            font-family: 'Segoe UI', 'Inter', sans-serif;
            font-size: 10pt;
        }}
        
        /* ===== CARD PANELS & SIDEBARS ===== */
        QWidget[class="panel"], QWidget[class="sidebar"] {{
            background-color: {COLORS['bg_tertiary']};
            border: 1px solid {COLORS['border']};
            border-radius: 8px;
        }}
        
        /* ===== GROUP BOX (Cards) ===== */
        QGroupBox {{
            background-color: {COLORS['bg_tertiary']};
            border: 1px solid {COLORS['border']};
            border-radius: 8px;
            margin-top: 12px;
            padding: 12px;
            font-weight: bold;
        }}
        
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 12px;
            padding: 0 {SPACING_XS}px;
            color: {COLORS['text_primary']};
        }}
        
        QLabel {{
            padding: 2px {SPACING_XS}px;
        }}
        
        QLabel[class="title"] {{
            font-size: 11pt;
            font-weight: 700;
            color: {COLORS['text_primary']};
            padding-bottom: {SPACING_SM}px;
        }}
        
        /* ===== BUTTONS ===== */
        QPushButton {{
            background-color: {COLORS['accent']};
            color: #FFFFFF;
            border: none;
            border-radius: 6px;
            padding: 8px 16px;
            font-weight: 600;
        }}
        
        QPushButton:hover {{
            background-color: {COLORS['accent_hover']};
        }}
        
        QPushButton[class="secondary"] {{
            background-color: transparent;
            color: {COLORS['accent']};
            border: 1px solid {COLORS['accent']};
            border-radius: 6px;
        }}
        
        QPushButton[class="secondary"]:hover {{
            background-color: #EFF6FF;
        }}

        /* ===== ITEM VIEWS ===== */
        QTreeView, QListView, QTableView, QTreeWidget {{
            background-color: {COLORS['bg_tertiary']};
            border: none;
            border-radius: 4px;
            outline: none;
        }}
        
        QHeaderView::section {{
            background-color: {COLORS['bg_secondary']};
            color: {COLORS['text_secondary']};
            padding: {SPACING_SM}px;
            border: none;
            border-bottom: 1px solid {COLORS['border']};
            font-weight: bold;
        }}
        
        QTreeView::item, QListView::item {{
            padding: {SPACING_SM}px;
        }}
        
        QTreeView::item:hover, QListView::item:hover {{
            background-color: {COLORS['bg_secondary']};
        }}
        
        QTreeView::item:selected, QListView::item:selected {{
            background-color: {COLORS['bg_selection']};
            color: {COLORS['accent']};
            font-weight: 600;
        }}

        /* ===== TABS ===== */
        QTabWidget::pane {{
            border: 1px solid {COLORS['border']};
            background-color: {COLORS['bg_tertiary']};
            border-radius: 8px;
        }}
        
        QTabBar::tab {{
            background-color: {COLORS['bg_secondary']};
            color: {COLORS['text_secondary']};
            padding: 8px 16px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            margin-right: 2px;
            font-weight: 600;
        }}
        
        QTabBar::tab:selected {{
            background-color: {COLORS['bg_tertiary']};
            border-bottom: 2px solid {COLORS['accent']};
            color: {COLORS['accent']};
        }}
        
        /* ===== SPLITTERS & HANDLES ===== */
        QSplitter::handle {{
            background-color: transparent;
        }}
        
        QSplitter::handle:horizontal {{
            width: 1px;
            background-color: {COLORS['border']};
        }}
        
        /* ===== TOOLBAR / STATUSBAR ===== */
        QToolBar {{
            background-color: #FFFFFF;
            border-bottom: 1px solid {COLORS['border']};
            spacing: {SPACING_MD}px;
            padding: {SPACING_SM}px;
        }}
        
        QStatusBar {{
            background-color: #FFFFFF;
            color: {COLORS['text_secondary']};
            border-top: 1px solid {COLORS['border']};
            padding: 2px;
        }}
        
        /* ===== INPUT FIELDS ===== */
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
            background-color: {COLORS['bg_tertiary']};
            border: 1px solid {COLORS['divider']};
            border-radius: 6px;
            padding: 8px;
            color: {COLORS['text_primary']};
        }}
        
        QLineEdit[class="search"] {{
            border-radius: 8px;
            padding-left: 32px; /* Pad for icon */
        }}
        
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
            border: 2px solid {COLORS['accent']};
            padding: 7px;
        }}

        /* Editor Gutter */
        QWidget#LineNumberArea {{
            background-color: {COLORS['bg_primary']};
            border-right: 1px solid {COLORS['border']};
        }}

        /* ===== PROGRESS BAR ===== */
        QProgressBar {{
            background-color: {COLORS['bg_primary']};
            border: 1px solid {COLORS['border']};
            border-radius: 6px;
            height: 16px;
            text-align: center;
            font-size: 8pt;
            font-weight: 700;
            color: {COLORS['text_primary']};
        }}
        
        QProgressBar::chunk {{
            background-color: {COLORS['accent']};
            border-radius: 5px;
        }}
        
        /* ===== SCROLLBARS ===== */
        QScrollBar:vertical {{
            background-color: {COLORS['bg_secondary']};
            width: 10px;
            border-radius: 5px;
        }}
        
        QScrollBar::handle:vertical {{
            background-color: {COLORS['divider']};
            border-radius: 5px;
        }}
        
        QScrollBar::handle:vertical:hover {{
            background-color: {COLORS['text_muted']};
        }}

        /* Monospace for code */
        QPlainTextEdit[class="code"], QLabel[class="monospace"] {{
            font-family: 'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace;
            font-size: 9pt;
        }}
    """


class SemaBridgeMainWindow(QMainWindow):
    """
    Main application window for SemaBridge UI.
    
    Implements a professional, data-dense interface with:
    - Source browser panel (left)
    - Content/editor panel (center)
    - Properties/history panel (right)
    """
    
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("SemaBridge - Semantic Integration Framework")
        self.setMinimumSize(1200, 800)
        
        # Initialize Core Managers
        from semabridge.core.config_manager import ConfigurationManager
        from semabridge.core.version_manager import VersionManager
        from semabridge.repository.duckdb_manager import DuckDBManager
        from semabridge.core.config_loader import get_default_config_path, load_yaml_file
        
        # Determine repository path from global config
        # Determine repository path from global config
        db_path = None
        try:
            config_path = get_default_config_path()
            
            # Initialization Check
            if not config_path or not config_path.exists():
                from semabridge.cli.ui.init_wizard import InitializationWizard
                wizard = InitializationWizard(self)
                if wizard.exec() != 1:  # 1 = QDialog.Accepted
                    logger.warning("Initialization cancelled by user.")
                    sys.exit(0)
                
                # Reload config path after initialization
                config_path = get_default_config_path()

            if config_path and config_path.exists():
                config = load_yaml_file(config_path)
                # Check for core.repository_path
                if "core" in config and "repository_path" in config["core"]:
                    db_path = config["core"]["repository_path"]
                    # Expand user path (~)
                    if db_path.startswith("~"):
                        db_path = str(Path(db_path).expanduser())
                    logger.info(f"Using repository from config: {db_path}")
        except Exception as e:
            logger.warning(f"Could not load global config for repository path: {e}")
            # If critical config load fails, we might want to show error or re-init
            QMessageBox.critical(self, "Configuration Error", f"Failed to load configuration: {e}")

        self.config_manager = ConfigurationManager()
        self.version_manager = VersionManager()
        self.duckdb_manager = DuckDBManager(db_path=db_path)
        
        # Worker instances
        self._sync_worker = None
        self._connection_worker = None
        self._parallel_sync_worker = None
        
        # Setup UI
        self._setup_menu_bar()
        self._setup_toolbar()
        self._setup_central_widget()
        self._setup_status_bar()
        
        # Connect Manager Signals
        self.config_manager.validation_changed.connect(self._on_validation_changed)
        self.config_manager.config_loaded.connect(self._on_config_loaded)
        self.config_manager.saved.connect(self._on_config_saved)
        
        # Check connection on startup
        self._check_connection()
        
        # Initial Load
        self.config_manager.load()
        
        logger.info("SemaBridge UI initialized")
    
    def _setup_menu_bar(self):
        """Setup the application menu bar."""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        
        new_project = QAction("&New Project", self)
        new_project.setShortcut("Ctrl+N")
        file_menu.addAction(new_project)
        
        open_project = QAction("&Open Project...", self)
        open_project.setShortcut("Ctrl+O")
        file_menu.addAction(open_project)
        
        load_default = QAction("&Load Template", self)
        load_default.setToolTip("Reset to standard project configuration")
        load_default.triggered.connect(self._load_default_config)
        file_menu.addAction(load_default)
        
        file_menu.addSeparator()
        
        save_config = QAction("&Save Configuration", self)
        save_config.setShortcut("Ctrl+S")
        save_config.triggered.connect(self._save_configuration)
        file_menu.addAction(save_config)
        
        file_menu.addSeparator()
        
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Edit menu
        edit_menu = menubar.addMenu("&Edit")
        
        refresh = QAction("&Refresh", self)
        refresh.setShortcut("F5")
        edit_menu.addAction(refresh)
        
        # View menu
        view_menu = menubar.addMenu("&View")
        
        show_yaml = QAction("Show &YAML Preview", self)
        show_yaml.setCheckable(True)
        show_yaml.setChecked(True)
        view_menu.addAction(show_yaml)
        
        show_history = QAction("Show &History Panel", self)
        show_history.setCheckable(True)
        show_history.setChecked(True)
        view_menu.addAction(show_history)
        
        # Tools menu
        tools_menu = menubar.addMenu("&Tools")
        
        run_sync = QAction("&Run Sync", self)
        run_sync.setShortcut("Ctrl+Enter")
        tools_menu.addAction(run_sync)
        
        validate = QAction("&Validate Configuration", self)
        validate.triggered.connect(self.config_manager._validate) # Explicit re-validation
        tools_menu.addAction(validate)
        
        tools_menu.addSeparator()
        
        compare = QAction("&Compare Versions...", self)
        tools_menu.addAction(compare)
        
        # Help menu
        help_menu = menubar.addMenu("&Help")
        
        docs = QAction("&Documentation", self)
        docs.setShortcut("F1")
        help_menu.addAction(docs)
        
        help_menu.addSeparator()
        
        about = QAction("&About SemaBridge", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)
    
    def _setup_toolbar(self):
        """Setup the main toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # Sync button
        self._sync_btn = QPushButton("▶ Sync")
        self._sync_btn.setProperty("class", "primary")
        self._sync_btn.setToolTip("Run semantic model sync (Ctrl+Enter)")
        self._sync_btn.clicked.connect(self._on_sync_clicked)
        self._sync_btn.setEnabled(False) # Disabled until valid config
        toolbar.addWidget(self._sync_btn)

        # Parallel Sync button (Temporarily hidden)
        self._parallel_sync_btn = QPushButton("⚡ Parallel Sync")
        self._parallel_sync_btn.setToolTip("Run concurrent multi-model sync with ProcessPoolExecutor")
        self._parallel_sync_btn.clicked.connect(self._on_parallel_sync_clicked)
        self._parallel_sync_btn.setEnabled(False)
        self._parallel_sync_btn.setVisible(True)
        toolbar.addWidget(self._parallel_sync_btn)

        toolbar.addSeparator()
        
        # Refresh button
        refresh_btn = QPushButton("⟳ Refresh")
        refresh_btn.setProperty("class", "secondary")
        refresh_btn.setToolTip("Refresh source models (F5)")
        refresh_btn.clicked.connect(self._check_connection)
        toolbar.addWidget(refresh_btn)
        
        # Validate button
        validate_btn = QPushButton("✓ Validate")
        validate_btn.setProperty("class", "secondary")
        validate_btn.setToolTip("Validate configuration")
        validate_btn.clicked.connect(self.config_manager._validate)
        toolbar.addWidget(validate_btn)
        
        # Spacer
        spacer = QWidget()
        spacer.setFixedWidth(SPACING_XL)
        toolbar.addWidget(spacer)
        
        # Project label
        project_label = QLabel("Project:")
        toolbar.addWidget(project_label)
        
        self._project_name_label = QLabel("semabridge.yaml")
        self._project_name_label.setProperty("class", "monospace")
        toolbar.addWidget(self._project_name_label)

        # Parallel sync status label (hidden until running)
        spacer2 = QWidget()
        spacer2.setFixedWidth(SPACING_XL)
        toolbar.addWidget(spacer2)
        self._parallel_status_label = QLabel("")
        self._parallel_status_label.setVisible(False)
        toolbar.addWidget(self._parallel_status_label)

    def _setup_central_widget(self):
        """Setup the main content area with splitters."""
        central = QWidget()
        self.setCentralWidget(central)
        
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Main horizontal splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        
        # Add layout wrapper for card separation
        # Left panel: Source Browser
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(15, 15, 10, 15)
        left_layout.setSpacing(10)
        self._source_browser = self._create_source_browser_panel()
        left_layout.addWidget(self._source_browser)
        splitter.addWidget(left_container)
        
        # Center: Editor
        center_container = QWidget()
        center_layout = QVBoxLayout(center_container)
        center_layout.setContentsMargins(10, 15, 10, 15)
        center_layout.setSpacing(10)
        self._center_panel = self._create_editor_panel()
        center_layout.addWidget(self._center_panel)
        splitter.addWidget(center_container)
        
        # Right panel: History
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(10, 15, 15, 15)
        right_layout.setSpacing(10)
        self._right_panel = self._create_history_panel()
        right_layout.addWidget(self._right_panel)
        splitter.addWidget(right_container)
        
        # Set initial sizes
        splitter.setSizes([300, 600, 300])
        
        layout.addWidget(splitter)
    
    def _create_source_browser_panel(self) -> QWidget:
        """Create the source browser panel (left)."""
        from semabridge.cli.ui.source_browser import SourceBrowserWidget
        self._source_browser = SourceBrowserWidget()
        self._source_browser.setProperty("class", "sidebar")
        self._source_browser.model_highlighted.connect(self._on_model_highlighted)
        self._source_browser.selection_changed.connect(self._on_source_selection_changed)
        return self._source_browser
    
    def _create_editor_panel(self) -> QWidget:
        """Create the editor/content panel (center)."""
        panel = QWidget()
        panel.setProperty("class", "panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(SPACING_SM, SPACING_SM, SPACING_SM, SPACING_SM)
        layout.setSpacing(SPACING_SM)
        
        # Tab widget for different views
        self._editor_tabs = QTabWidget()
        
        # YAML Preview tab (Config Manager Injected)
        from semabridge.cli.ui.yaml_editor import YAMLEditorWidget
        self._yaml_editor = YAMLEditorWidget(self.config_manager)
        self._editor_tabs.addTab(self._yaml_editor, "YAML Configuration")
        
        # Diff Viewer tab
        from semabridge.cli.ui.diff_viewer import DiffViewerWidget
        self._diff_viewer = DiffViewerWidget()
        self._editor_tabs.addTab(self._diff_viewer, "Version Comparison")
        
        layout.addWidget(self._editor_tabs)
        return panel
    
    def _create_history_panel(self) -> QWidget:
        """Create the history/properties panel (right)."""
        from semabridge.cli.ui.version_history import VersionHistoryWidget
        self._version_history = VersionHistoryWidget(
            self.version_manager, 
            self.config_manager,
            self.duckdb_manager
        )
        self._version_history.setProperty("class", "sidebar")
        # Connect compare signal to diff viewer
        self._version_history.compare_requested.connect(self._on_compare_versions)
        return self._version_history
    
    def _on_model_highlighted(self, model_data: dict):
        """Handle model highlighted in source browser."""
        # UsedisplayName as fallback for project_id if not present
        model_id = model_data.get("id") or model_data.get("displayName")
        if model_id:
            logger.info(f"Model highlighted: {model_id}")
            self._version_history.set_model_context(model_id)
        else:
            self._version_history.set_model_context(None)
    
    def _setup_status_bar(self):
        """Setup the status bar."""
        status = QStatusBar()
        self.setStatusBar(status)
        
        # Connection status
        self._connection_label = QLabel("● Disconnected")
        self._connection_label.setStyleSheet(f"color: {COLORS['text_muted']};")
        status.addWidget(self._connection_label)
        
        # Spacer
        status.addWidget(QLabel(), 1)
        
        # Progress bar (hidden by default)
        self._progress = QProgressBar()
        self._progress.setFixedWidth(200)
        self._progress.setVisible(False)
        status.addPermanentWidget(self._progress)
        
        # Model count
        self._model_count = QLabel("0 models")
        status.addPermanentWidget(self._model_count)
    
    def _show_about(self):
        """Show the about dialog."""
        from semabridge import __version__
        QMessageBox.about(
            self,
            "About SemaBridge",
            f"<h3>SemaBridge</h3>"
            f"<p>Version {__version__}</p>"
            f"<p>Semantic Integration and Multi-Target Broadcast Framework</p>"
            f"<p>Professional metadata management for enterprise data platforms.</p>"
        )
    
    def _check_connection(self):
        """Check connection status to source system."""
        from semabridge.cli.ui.workers import ConnectionCheckWorker
        
        self._connection_label.setText("● Checking...")
        self._connection_label.setStyleSheet(f"color: {COLORS['warning']};")
        
        self._connection_worker = ConnectionCheckWorker("fabric")
        self._connection_worker.finished.connect(self._on_connection_checked)
        self._connection_worker.start()
    
    def _on_connection_checked(self, is_connected: bool, message: str):
        """Handle connection check result."""
        if is_connected:
            self._connection_label.setText(f"● {message}")
            self._connection_label.setStyleSheet(f"color: {COLORS['success']};")
        else:
            self._connection_label.setText(f"● {message}")
            self._connection_label.setStyleSheet(f"color: {COLORS['error']};")

    def _on_validation_changed(self, is_valid: bool, errors: list):
        """Handle validation state changes."""
        self._sync_btn.setEnabled(is_valid)
        self._parallel_sync_btn.setEnabled(is_valid)
        if not is_valid:
            self._sync_btn.setToolTip(f"Fix {len(errors)} validation errors to sync")
            self._parallel_sync_btn.setToolTip(f"Fix {len(errors)} validation errors to sync")
        else:
            self._sync_btn.setToolTip("Run semantic model sync (Ctrl+Enter)")
            self._parallel_sync_btn.setToolTip("Run concurrent multi-model sync")

    def _on_config_loaded(self, config):
        """Handle valid config loaded."""
        self.statusBar().showMessage("Configuration loaded successfully", 3000)

    def _on_config_saved(self, path: str):
        """Handle successful save."""
        self.statusBar().showMessage(f"Configuration saved to {path}", 3000)
        
        # Create Version Snapshot
        try:
            content = self.config_manager._current_text
            self.version_manager.create_version(content, description="User Save")

            # Record DB-backed config version history (ORM model_version_history)
            try:
                from semabridge.repository.schema_sync import SchemaVersionManager

                schema_version_mgr = SchemaVersionManager()
                schema_version_mgr.sync_model_version(path)
            except Exception as db_ver_err:
                logger.warning(f"Failed to record DB-backed config version: {db_ver_err}")
            
            # Refresh history panel if in YAML mode
            if self._version_history._model_id is None:
                self._version_history.refresh()
        except Exception as e:
            logger.error(f"Failed to create version snapshot: {e}")
    
    def _on_sync_clicked(self):
        """Handle sync button click."""
        # Auto-save before syncing
        if not self._save_configuration(silent=True):
            return

        from semabridge.cli.ui.workers import SyncWorker
        
        # Disable button during sync
        self._sync_btn.setEnabled(False)
        self._sync_btn.setText("⏳ Syncing...")
        
        # Show progress bar
        self._progress.setVisible(True)
        self._progress.setValue(0)
        
        # Get Validated Config Object
        config = self.config_manager.get_config()
        if not config:
            self._on_sync_error("Invalid configuration state.")
            return

        # Start sync worker
        self._sync_worker = SyncWorker(config=config, duckdb_manager=self.duckdb_manager) # Pass config and duckdb
        self._sync_worker.progress.connect(self._on_sync_progress)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.error.connect(self._on_sync_error)
        self._sync_worker.start()
    
    def _on_sync_progress(self, percent: int, message: str):
        """Handle sync progress update."""
        self._progress.setValue(percent)
        self.statusBar().showMessage(message, 2000)
    
    def _on_sync_finished(self, result: dict):
        """Handle sync completion."""
        self._sync_btn.setEnabled(True)
        self._sync_btn.setText("▶ Sync")
        self._progress.setVisible(False)
        
        if result.get("success"):
            self._model_count.setText(f"{result.get('models_processed', 0)} models")
            
            changed = result.get('changed_models', [])
            models_processed = result.get('models_processed', 0)
            
            # Determine if this was a dry run
            broadcasts = result.get('broadcast_results', [])
            is_dry_run = all(not b.get('deploy', True) for b in broadcasts) if broadcasts else False
            
            if models_processed > 0 and len(changed) == 0:
                 title = "Dry Run Complete" if is_dry_run else "Sync Complete"
                 QMessageBox.information(
                    self,
                    title,
                    f"Processed {models_processed} model(s).\n"
                    "No changes detected in version control."
                )
            else:
                title = "Dry Run Complete" if is_dry_run else "Sync Complete"
                prefix = "Checked" if is_dry_run else "Successfully synced"
                suffix = "(Dry Run - No physical changes made)" if is_dry_run else ""
                
                QMessageBox.information(
                    self,
                    title,
                    f"{prefix} {models_processed} models to "
                    f"{result.get('targets_succeeded', 0)} targets.\n\n"
                    f"Versioning: New version created in DuckDB.\n"
                    f"{suffix}\n\n"
                    f"Duration: {result.get('total_duration_ms', 0):.0f}ms"
                )
        else:
            errors = result.get("errors", [])
            QMessageBox.warning(
                self,
                "Sync Completed with Errors",
                f"Processed {result.get('models_processed', 0)} models.\n"
                f"Succeeded: {result.get('targets_succeeded', 0)}\n"
                f"Failed: {result.get('targets_failed', 0)}\n\n"
                f"Errors:\n" + "\n".join(errors[:3])
            )
        
        # Refresh history panel to show new snapshot
        self._version_history.refresh()
        
        # Refresh connection status
        self._check_connection()
    
    def _on_sync_error(self, error_message: str):
        """Handle sync error."""
        self._sync_btn.setEnabled(True)
        self._sync_btn.setText("▶ Sync")
        self._progress.setVisible(False)
        
        QMessageBox.critical(
            self,
            "Sync Failed",
            f"An error occurred during sync:\n\n{error_message}"
        )

    # ── Parallel Sync Handlers ──────────────────────────────────────────

    def _on_parallel_sync_clicked(self):
        """Handle parallel sync button click — opens settings dialog then runs."""
        # Auto-save before syncing
        if not self._save_configuration(silent=True):
            return

        config = self.config_manager.get_config()
        if not config:
            self._on_parallel_sync_error("Invalid configuration state.")
            return

        # Build settings dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Parallel Sync Settings")
        dialog.setMinimumWidth(340)
        layout = QFormLayout(dialog)

        workers_spin = QSpinBox()
        workers_spin.setRange(0, 32)
        workers_spin.setValue(0)
        workers_spin.setToolTip("0 = auto-detect based on CPU cores")
        layout.addRow("Max Workers:", workers_spin)

        retries_spin = QSpinBox()
        retries_spin.setRange(0, 10)
        retries_spin.setValue(3)
        layout.addRow("Max Retries:", retries_spin)

        strict_check = QCheckBox("Strict Mode (fail-fast)")
        strict_check.setToolTip("Stop batch on first failure")
        layout.addRow(strict_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        max_workers = workers_spin.value()
        max_retries = retries_spin.value()
        strict_mode = strict_check.isChecked()

        from semabridge.cli.ui.workers import ParallelSyncWorker

        # Disable both sync buttons during execution
        self._sync_btn.setEnabled(False)
        self._parallel_sync_btn.setEnabled(False)
        self._parallel_sync_btn.setText("⏳ Running...")
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._parallel_status_label.setVisible(True)
        self._parallel_status_label.setText("Starting parallel sync...")

        self._parallel_sync_worker = ParallelSyncWorker(
            config=config,
            duckdb_manager=self.duckdb_manager,
            max_workers=max_workers,
            strict_mode=strict_mode,
            max_retries=max_retries,
        )
        self._parallel_sync_worker.progress.connect(self._on_parallel_sync_progress)
        self._parallel_sync_worker.model_completed.connect(self._on_parallel_model_completed)
        self._parallel_sync_worker.finished.connect(self._on_parallel_sync_finished)
        self._parallel_sync_worker.error.connect(self._on_parallel_sync_error)
        self._parallel_sync_worker.start()

    def _on_parallel_sync_progress(self, percent: int, message: str):
        """Handle parallel sync progress update."""
        self._progress.setValue(percent)
        self._parallel_status_label.setText(message)
        self.statusBar().showMessage(message, 2000)

    def _on_parallel_model_completed(self, model_name: str, success: bool, duration: float):
        """Handle individual model completion during parallel sync."""
        status = "✓" if success else "✗"
        self._parallel_status_label.setText(
            f"{status} {model_name} ({duration:.1f}s)"
        )

    def _on_parallel_sync_finished(self, result: dict):
        """Handle parallel sync batch completion."""
        self._sync_btn.setEnabled(True)
        self._parallel_sync_btn.setEnabled(True)
        self._parallel_sync_btn.setText("⚡ Parallel Sync")
        self._progress.setVisible(False)
        self._parallel_status_label.setVisible(False)

        batch_id = result.get("batch_id", "unknown")
        total = result.get("total_models", 0)
        succeeded = len(result.get("successful_models", []))
        failed_dict = result.get("failed_models", {})
        failed_count = len(failed_dict)
        duration = result.get("duration_seconds", 0.0)

        if failed_count == 0:
            QMessageBox.information(
                self,
                "Parallel Sync Complete",
                f"All {total} model(s) synced successfully.\n\n"
                f"Batch ID: {batch_id}\n"
                f"Duration: {duration:.1f}s",
            )
        else:
            failed_lines = "\n".join(
                f"  • {name}: {err}" for name, err in list(failed_dict.items())[:10]
            )
            QMessageBox.warning(
                self,
                "Parallel Sync Completed with Failures",
                f"Total models: {total}\n"
                f"Succeeded: {succeeded}\n"
                f"Failed: {failed_count}\n\n"
                f"Failed models:\n{failed_lines}\n\n"
                f"Batch ID (for resume): {batch_id}\n"
                f"Duration: {duration:.1f}s",
            )

        # Refresh history panel and connection status
        self._version_history.refresh()
        self._check_connection()

    def _on_parallel_sync_error(self, error_message: str):
        """Handle parallel sync fatal error."""
        self._sync_btn.setEnabled(True)
        self._parallel_sync_btn.setEnabled(True)
        self._parallel_sync_btn.setText("⚡ Parallel Sync")
        self._progress.setVisible(False)
        self._parallel_status_label.setVisible(False)

        QMessageBox.critical(
            self,
            "Parallel Sync Failed",
            f"An error occurred during parallel sync:\n\n{error_message}",
        )

    def _on_compare_versions(self, left_version_id: str, right_version_id: str):
        """Handle compare request from version history."""
        try:
            # Check version types from history
            left_version = None
            right_version = None
            
            # Find version data in history cache
            for v in self._version_history._versions:
                if v["version_id"] == left_version_id:
                    left_version = v
                if v["version_id"] == right_version_id:
                    right_version = v
            
            if left_version and left_version.get("type") == "model":
                # Model comparison from DuckDB
                import yaml
                
                left_snapshot = self.duckdb_manager.get_snapshot(left_version_id)
                left_blob = self._scrub_sml_blob(left_snapshot.sml_blob) if left_snapshot else {}
                left_content = yaml.dump(left_blob, default_flow_style=False, sort_keys=False) if left_snapshot else "# Not found"
                
                if right_version_id == "current":
                    # Compare with current state in DuckDB (HEAD)
                    head = self.duckdb_manager.get_head(left_snapshot.project_id) if left_snapshot else None
                    right_blob = self._scrub_sml_blob(head.sml_blob) if head else {}
                    right_content = yaml.dump(right_blob, default_flow_style=False, sort_keys=False) if head else "# Not found"
                else:
                    right_snapshot = self.duckdb_manager.get_snapshot(right_version_id)
                    right_blob = self._scrub_sml_blob(right_snapshot.sml_blob) if right_snapshot else {}
                    right_content = yaml.dump(right_blob, default_flow_style=False, sort_keys=False) if right_snapshot else "# Not found"
            else:
                # Legacy YAML config history
                left_data = self.version_manager.get_version(left_version_id)
                left_content = left_data["content"] if left_data else f"# Version {left_version_id} not found"
                
                if right_version_id == "current":
                    right_content = self.config_manager._current_text
                else:
                    right_data = self.version_manager.get_version(right_version_id)
                    right_content = right_data["content"] if right_data else f"# Version {right_version_id} not found"
            
            # Set content in diff viewer
            self._diff_viewer.set_title(left_version_id, right_version_id)
            self._diff_viewer.set_content(left_content, right_content)
            
            # Switch to comparison tab
            self._editor_tabs.setCurrentWidget(self._diff_viewer)
            
            logger.info(f"Comparing versions: {left_version_id} → {right_version_id}")
            
        except Exception as e:
            logger.exception("Error comparing versions")
            QMessageBox.warning(
                self,
                "Compare Error",
                f"Could not load version snapshots:\n\n{str(e)}"
            )

    def _scrub_sml_blob(self, blob: dict) -> dict:
        """Strip volatile metadata fields for comparison purposes."""
        if not blob:
            return blob
            
        import copy
        from typing import Any
        scrubbed = copy.deepcopy(blob)
        # Fields that change on every capture but aren't semantic changes
        volatile = ["created_at", "modified_at", "last_sync", "snapshot_id"]
        
        def _scrub_recursive(item: Any):
            if isinstance(item, dict):
                for v_field in volatile:
                    if v_field in item:
                        item[v_field] = "VOLATILE"
                for k, v in item.items():
                    _scrub_recursive(v)
            elif isinstance(item, list):
                for entry in item:
                    _scrub_recursive(entry)
                    
        _scrub_recursive(scrubbed)
        return scrubbed

    def _save_configuration(self, silent: bool = False) -> bool:
        """Save the current configuration to semabridge.yaml."""
        success = self.config_manager.save()
        
        if not success:
            msg = "Failed to save configuration to disk."
            if not silent:
                QMessageBox.critical(self, "Save Error", msg)
            return False
            
        return True

    def _load_default_config(self):
        """Handle Load Template action."""
        # Confirm before overwriting current editor state
        reply = QMessageBox.question(
            self,
            "Load Template",
            "This will discard any unsaved changes in the editor and load a fresh project template.\n\n"
            "Do you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.config_manager.load_default()
            self.statusBar().showMessage("Default template loaded", 3000)
            # Switch to YAML tab to show the template
            self._editor_tabs.setCurrentWidget(self._yaml_editor)
    
    def _get_selected_models(self):
        """Proxy for source browser selections (Legacy support for YamlEditor)."""
        if hasattr(self, '_source_browser'):
            return self._source_browser.get_selected_models()
        return []

    def _on_source_selection_changed(self, selected_models: list):
        """Handle checkbox selection changes in source browser."""
        # This could be used for auto-generating YAML if desired
        logger.debug(f"Source selection changed: {selected_models}")


def launch_ui():
    """Launch the SemaBridge UI application."""
    app = QApplication(sys.argv)
    app.setApplicationName("SemaBridge")
    app.setApplicationVersion("1.0.0")
    
    # Set professional default font to avoid QFont warnings on some systems
    default_font = QFont("Segoe UI", 10)
    if not default_font.exactMatch():
        default_font = QFont("Inter", 10)
    if not default_font.exactMatch():
        default_font = QFont("sans-serif", 10)
    app.setFont(default_font)
    
    # Apply global stylesheet
    app.setStyleSheet(get_stylesheet())
    
    window = SemaBridgeMainWindow()
    window.show()
    
    return app.exec()


if __name__ == "__main__":
    sys.exit(launch_ui())
