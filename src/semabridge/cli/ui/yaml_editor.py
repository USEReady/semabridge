"""
YAML Editor Widget.

Real-time YAML configuration editor with syntax highlighting
and dynamic generation from UI selections.
"""

from __future__ import annotations

from typing import Dict, Any, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QLabel,
    QPushButton,
    QSplitter,
    QGroupBox,
    QCheckBox,
    QLineEdit,
    QComboBox,
    QTextEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QSize
from PyQt6.QtGui import (
    QFont, QTextCharFormat, QColor, QSyntaxHighlighter, 
    QTextDocument, QPainter, QTextFormat
)

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class YAMLHighlighter(QSyntaxHighlighter):
    """Simple syntax highlighter for YAML content."""
    
    def __init__(self, document: QTextDocument):
        super().__init__(document)
        
        # Format for keys
        self._key_format = QTextCharFormat()
        self._key_format.setForeground(QColor("#7C3AED"))  # Purple accent
        self._key_format.setFontWeight(QFont.Weight.Bold)
        
        # Format for strings
        self._string_format = QTextCharFormat()
        self._string_format.setForeground(QColor("#10B981"))  # Green
        
        # Format for comments
        self._comment_format = QTextCharFormat()
        self._comment_format.setForeground(QColor("#6B6B7B"))  # Muted
        self._comment_format.setFontItalic(True)
        
        # Format for numbers/booleans
        self._value_format = QTextCharFormat()
        self._value_format.setForeground(QColor("#F59E0B"))  # Amber
    
    def highlightBlock(self, text: str):
        """Apply highlighting rules to a block of text."""
        import re
        
        # Comments
        comment_pattern = re.compile(r'#.*$')
        for match in comment_pattern.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._comment_format)
        
        # Keys (word followed by colon)
        key_pattern = re.compile(r'^(\s*)([a-zA-Z_][a-zA-Z0-9_-]*)\s*:')
        for match in key_pattern.finditer(text):
            start = match.start(2)
            length = match.end(2) - match.start(2)
            self.setFormat(start, length, self._key_format)
        
        # Strings in quotes
        string_pattern = re.compile(r'["\']([^"\']*)["\']')
        for match in string_pattern.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._string_format)
        
        # Numbers and booleans
        value_pattern = re.compile(r'\b(true|false|null|\d+\.?\d*)\b', re.IGNORECASE)
        for match in value_pattern.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._value_format)


class LineNumberArea(QWidget):
    """Gutter area for displaying line numbers."""
    def __init__(self, editor: ProfessionalEditor):
        super().__init__(editor)
        self.setObjectName("LineNumberArea")
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)


class ProfessionalEditor(QPlainTextEdit):
    """
    Advanced code editor with:
    - Line number gutter
    - Current line highlighting
    - No-wrap mode for data-dense YAML
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        
        self.update_line_number_area_width(0)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        
        # Font for code - consistent with design system
        font = QFont("JetBrains Mono", 10)
        font.setFixedPitch(True)
        self.setFont(font)
        
    def line_number_area_width(self):
        digits = 1
        max_val = max(1, self.blockCount())
        while max_val >= 10:
            max_val /= 10
            digits += 1
        space = 20 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def highlight_current_line(self):
        extra_selections = []
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            # Selection color should be subtle
            line_color = QColor("#F8FAFC")
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        # Match gutter background to theme
        painter.fillRect(event.rect(), QColor("#F8FAFC"))
        
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#94A3B8")) # Muted text color
                painter.drawText(0, top, self.line_number_area.width() - 8, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, number)
            
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1


class YAMLEditorWidget(QWidget):
    """
    YAML configuration editor widget.
    
    Features:
    - Syntax highlighting
    - Real-time generation from source browser selections
    - Validation feedback (Syntax, Schema, Semantic)
    - Bound to ConfigurationManager (Single Source of Truth)
    """
    
    def __init__(self, config_manager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config_manager = config_manager
        self._setup_ui()
        self._connect_signals()
        
        # Initial load if available
        if self.config_manager._current_text:
            self._update_editor_text(self.config_manager._current_text)
            
    def _setup_ui(self):
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Toolbar
        toolbar = QHBoxLayout()
        
        self._status_label = QLabel("○ Unvalidated")
        self._status_label.setStyleSheet("color: #6B6B7B;")
        toolbar.addWidget(self._status_label)
        
        toolbar.addStretch()
        
        generate_btn = QPushButton("⟳ Generate from Selection")
        generate_btn.setProperty("class", "secondary")
        generate_btn.clicked.connect(self._on_generate)
        toolbar.addWidget(generate_btn)
        
        copy_btn = QPushButton("📋 Copy")
        copy_btn.setProperty("class", "secondary")
        copy_btn.clicked.connect(self._on_copy)
        toolbar.addWidget(copy_btn)
        
        layout.addLayout(toolbar)
        
        # Splitter for editor and validation errors (if any)
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # YAML text editor - Upgraded to ProfessionalEditor with Gutter
        self._editor = ProfessionalEditor()
        self._editor.setPlaceholderText("# semabridge.yaml configuration")
        self._editor.textChanged.connect(self._on_text_changed)
        
        # Apply syntax highlighting
        self._highlighter = YAMLHighlighter(self._editor.document())
        
        splitter.addWidget(self._editor)
        
        # Error panel (hidden by default)
        self._error_panel = QPlainTextEdit()
        self._error_panel.setReadOnly(True)
        self._error_panel.setStyleSheet("color: #EF4444; background-color: #2A2A3E;")
        self._error_panel.setVisible(False)
        self._error_panel.setMaximumHeight(150)
        splitter.addWidget(self._error_panel)
        
        splitter.setSizes([400, 100])
        layout.addWidget(splitter, 1)

    def _connect_signals(self):
        """Connect to ConfigurationManager signals."""
        self.config_manager.text_changed.connect(self._update_editor_text)
        self.config_manager.validation_changed.connect(self._on_validation_changed)

    def _update_editor_text(self, text: str):
        """Update editor text from external source (e.g. disk load, rollback)."""
        blocked = self._editor.blockSignals(True)
        self._editor.setPlainText(text)
        self._editor.blockSignals(blocked)

    def _on_text_changed(self):
        """Handle user typing in the editor."""
        text = self._editor.toPlainText()
        self.config_manager.update_text(text, source="ui")

    def _on_validation_changed(self, is_valid: bool, errors: list):
        """Update UI based on validation state."""
        if is_valid:
            self._status_label.setText("✓ Valid Configuration")
            self._status_label.setStyleSheet("color: #10B981;")
            self._error_panel.setVisible(False)
        else:
            self._status_label.setText(f"✗ Invalid ({len(errors)} errors)")
            self._status_label.setStyleSheet("color: #EF4444;")
            
            # Format errors
            error_text = []
            for err in errors:
                line = err.get('line', '?')
                msg = err.get('message', 'Unknown error')
                error_text.append(f"[Line {line}] {msg}")
            
            self._error_panel.setPlainText("\n".join(error_text))
            self._error_panel.setVisible(True)

    def _on_generate(self):
        """Generate YAML from current UI state (delegated to main window)."""
        main_window = self.window()
        if hasattr(main_window, '_get_selected_models'):
            models = main_window._get_selected_models()
            self._generate_yaml(models)

    def _generate_yaml(self, selected_models: list):
        """Generate context-aware YAML configuration matching standard template."""
        from semabridge.core.settings import get_settings
        
        settings = get_settings()
        
        # Determine source info from settings
        workspace_id = ""
        dataset_id = ""
        try:
            fb = settings.fabric
            workspace_id = fb.workspace_id
        except: pass
        
        # Build lines manually to ensure exact comment placement and structure
        lines = [
            "# Generated SemaBridge Configuration",
            "# Use 'Generate from Selection' after picking models in the Source Browser",
            "",
            "# Required: Source connector configuration",
            "source:",
            "  type: fabric",
        ]
        
        # Add workspace_id
        lines.append(f"  workspace_id: \"{workspace_id or '00000000-0000-0000-0000-000000000000'}\"")
        
        # Add dataset_id only if available (optional — defaults to model name)
        if dataset_id:
            lines.append(f"  dataset_id: \"{dataset_id}\"")
        
        # Handle selected models
        if selected_models:
            if len(selected_models) == 1:
                lines.append(f"  model: \"{selected_models[0]}\"")
                model_name = selected_models[0]
            else:
                lines.append("  models:")
                for m in selected_models:
                    lines.append(f"    - \"{m}\"")
                model_name = selected_models[0] # Use first as default name
        else:
            lines.append("  model: \"*\"")
            model_name = "New Model"
            
        lines.extend([
            "",
            "# Optional: Target connector configuration",
            "target:",
            "  type: snowflake",
            "  deploy: true  # Set to true to enable deployment",
            "",
            "# Optional: Semantic model name (defaults to source.model if omitted)",
            f"# model_name: \"{model_name}\"",
            "",
            "# Optional: Snapshot version to sync",
            "version_tag: \"v1.0\"",
            "",
            "# Optional: Sync direction",
            "# sync_direction: source_to_target",
            "",
            "# Optional: Logging configuration",
            "logging:",
            "  level: INFO",
            "  format: text",
            "",
            "# Optional: Behavior policy path",
            "policy_path: \"policies/standard.yaml\"",
            ""
        ])
        
        yaml_str = "\n".join(lines)
        
        # This will trigger textChanged -> update_text -> validate
        self._editor.setPlainText(yaml_str)
    
    def _on_copy(self):
        """Copy YAML content to clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(self._editor.toPlainText())
        self._status_label.setText("✓ Copied to clipboard")

