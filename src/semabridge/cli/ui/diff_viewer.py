"""
Diff Viewer Widget.

Side-by-side comparison view for semantic model versions
using GitHub-style unified diff.
"""

from __future__ import annotations

from typing import Optional, Tuple
from difflib import unified_diff

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QLabel,
    QPushButton,
    QSplitter,
    QComboBox,
    QFrame,
    QButtonGroup,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QTextCharFormat, QColor, QSyntaxHighlighter, QTextDocument

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# Diff visualization colors (Light Theme Modern)
DIFF_COLORS = {
    "addition_bg": "#DCFCE7",      # Light emerald background
    "addition_text": "#166534",     # Dark green text
    "deletion_bg": "#FEE2E2",       # Light crimson background
    "deletion_text": "#991B1B",     # Dark red text
    "header_bg": "#F8FAFC",         # Soft slate background
    "header_text": "#475569",       # Gray for headers
    "context_text": "#0F172A",      # Dark blue/black context
}


class DiffHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for unified diff output."""
    
    def __init__(self, document: QTextDocument):
        super().__init__(document)
        
        # Addition format
        self._addition = QTextCharFormat()
        self._addition.setBackground(QColor(DIFF_COLORS["addition_bg"]))
        self._addition.setForeground(QColor(DIFF_COLORS["addition_text"]))
        
        # Deletion format
        self._deletion = QTextCharFormat()
        self._deletion.setBackground(QColor(DIFF_COLORS["deletion_bg"]))
        self._deletion.setForeground(QColor(DIFF_COLORS["deletion_text"]))
        
        # Header format
        self._header = QTextCharFormat()
        self._header.setBackground(QColor(DIFF_COLORS["header_bg"]))
        self._header.setForeground(QColor(DIFF_COLORS["header_text"]))
        self._header.setFontWeight(QFont.Weight.Bold)
    
    def highlightBlock(self, text: str):
        """Apply highlighting based on diff markers."""
        if text.startswith('+') and not text.startswith('+++'):
            self.setFormat(0, len(text), self._addition)
        elif text.startswith('-') and not text.startswith('---'):
            self.setFormat(0, len(text), self._deletion)
        elif text.startswith('@@') or text.startswith('---') or text.startswith('+++'):
            self.setFormat(0, len(text), self._header)


class DiffViewerWidget(QWidget):
    """
    Version diff viewer widget.
    
    Features:
    - Side-by-side or unified diff view
    - Addition/deletion highlighting
    - Version selector dropdowns
    - Change summary statistics
    """
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._left_content: str = ""
        self._right_content: str = ""
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Header with version selectors (Optional: can be used for direct selection)
        self._header_widget = QWidget()
        header = QHBoxLayout(self._header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        
        self._title_label = QLabel("Version Comparison")
        self._title_label.setProperty("class", "title")
        header.addWidget(self._title_label)
        header.addStretch()
        
        # View mode toggle
        self._unified_btn = QPushButton("Unified")
        self._unified_btn.setCheckable(True)
        self._unified_btn.setChecked(True)
        self._unified_btn.setProperty("class", "secondary")
        self._unified_btn.clicked.connect(self._toggle_view_mode)
        header.addWidget(self._unified_btn)
        
        self._split_btn = QPushButton("Split")
        self._split_btn.setCheckable(True)
        self._split_btn.setProperty("class", "secondary")
        self._split_btn.clicked.connect(self._toggle_view_mode)
        header.addWidget(self._split_btn)
        
        # Enforce mutual exclusivity
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._unified_btn)
        self._mode_group.addButton(self._split_btn)
        
        layout.addWidget(self._header_widget)
        
        # Change summary
        summary_frame = QFrame()
        summary_frame.setStyleSheet(f"background-color: #EFF6FF; border: 1px solid #E2E8F0; border-radius: 4px;")
        summary_layout = QHBoxLayout(summary_frame)
        summary_layout.setContentsMargins(8, 4, 8, 4)
        
        self._additions_label = QLabel(f"+0 additions")
        self._additions_label.setStyleSheet(f"color: {DIFF_COLORS['addition_text']}; font-weight: bold;")
        summary_layout.addWidget(self._additions_label)
        
        self._deletions_label = QLabel(f"-0 deletions")
        self._deletions_label.setStyleSheet(f"color: {DIFF_COLORS['deletion_text']}; font-weight: bold;")
        summary_layout.addWidget(self._deletions_label)
        
        summary_layout.addStretch()
        
        self._files_label = QLabel("0 files changed")
        self._files_label.setStyleSheet(f"color: {DIFF_COLORS['context_text']};")
        summary_layout.addWidget(self._files_label)
        
        layout.addWidget(summary_frame)
        
        # Diff content area
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Unified diff view (default)
        self._unified_view = QPlainTextEdit()
        self._unified_view.setReadOnly(True)
        self._unified_view.setProperty("class", "code")
        self._unified_highlighter = DiffHighlighter(self._unified_view.document())
        self._splitter.addWidget(self._unified_view)
        
        # Split view panels (hidden by default)
        self._left_view = QPlainTextEdit()
        self._left_view.setReadOnly(True)
        self._left_view.setProperty("class", "code")
        self._left_view.setVisible(False)
        
        self._right_view = QPlainTextEdit()
        self._right_view.setReadOnly(True)
        self._right_view.setProperty("class", "code")
        self._right_view.setVisible(False)
        
        # Identical view (single pane, shown when content matches)
        self._identical_view = QPlainTextEdit()
        self._identical_view.setReadOnly(True)
        self._identical_view.setProperty("class", "code")
        self._identical_view.setStyleSheet(f"background-color: #FFFFFF; color: #0F172A;")
        self._identical_view.setVisible(False)
        
        self._splitter.addWidget(self._left_view)
        self._splitter.addWidget(self._right_view)
        self._splitter.addWidget(self._identical_view)
        
        layout.addWidget(self._splitter, 1)
    def set_title(self, left: str, right: str):
        """Update the comparison title."""
        self._title_label.setText(f"Comparison: {left[:15]} → {right[:15]}")

    def set_content(self, left: str, right: str):
        """Set the content for comparison."""
        self._left_content = left
        self._right_content = right
        self._update_diff()
    
    def _update_diff(self):
        """Update the diff display."""
        left_lines = self._left_content.splitlines(keepends=True)
        right_lines = self._right_content.splitlines(keepends=True)
        
        # Handle identical content
        if self._left_content == self._right_content:
            self._identical_view.setPlainText(self._right_content)
            self._identical_view.setVisible(True)
            self._unified_view.setVisible(False)
            self._left_view.setVisible(False)
            self._right_view.setVisible(False)
            self._unified_btn.setVisible(True)
            self._split_btn.setVisible(True)
            self._additions_label.setVisible(False)
            self._deletions_label.setVisible(False)
            self._files_label.setText("Versions are identical")
            return

        # Restore UI elements if not identical
        self._identical_view.setVisible(False)
        self._unified_btn.setVisible(True)
        self._split_btn.setVisible(True)
        self._toggle_view_mode()  # Restore correct view mode

        diff = list(unified_diff(
            left_lines,
            right_lines,
            fromfile="previous",
            tofile="current",
            lineterm=""
        ))
        
        unified_text = "".join(diff) if diff else "No changes detected"
        self._unified_view.setPlainText(unified_text)
        
        self._left_view.setPlainText(self._left_content)
        self._right_view.setPlainText(self._right_content)

        # Update split views with highlighting
        from difflib import SequenceMatcher
        
        # Apply split-view highlighting
        s = SequenceMatcher(None, left_lines, right_lines)
        
        # Formats for split view
        left_fmt = QTextCharFormat()
        left_fmt.setBackground(QColor(DIFF_COLORS["deletion_bg"]))
        
        right_fmt = QTextCharFormat()
        right_fmt.setBackground(QColor(DIFF_COLORS["addition_bg"]))
        
        for tag, i1, i2, j1, j2 in s.get_opcodes():
            if tag == 'replace':
                # Highlight in both
                self._highlight_lines(self._left_view, i1, i2, left_fmt)
                self._highlight_lines(self._right_view, j1, j2, right_fmt)
            elif tag == 'delete':
                # Highlight in left
                self._highlight_lines(self._left_view, i1, i2, left_fmt)
            elif tag == 'insert':
                # Highlight in right
                self._highlight_lines(self._right_view, j1, j2, right_fmt)

        # Update summary
        additions = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
        deletions = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
        
        self._additions_label.setText(f"+{additions} additions")
        self._additions_label.setVisible(additions > 0)
        self._deletions_label.setText(f"-{deletions} deletions")
        self._deletions_label.setVisible(deletions > 0)
        self._files_label.setText("1 file changed")
    
    def _on_version_changed(self, version: str):
        """Handle version selector change."""
        # In a full implementation, this would load the actual version content
        logger.debug(f"Version changed: {version}")
    
    def _toggle_view_mode(self):
        """Toggle between unified and split view."""
        # Detect if identical state is active
        if self._left_content == self._right_content:
            self._identical_view.setVisible(True)
            self._unified_view.setVisible(False)
            self._left_view.setVisible(False)
            self._right_view.setVisible(False)
            return

        # QButtonGroup handles the checked states automatically now
        if self._unified_btn.isChecked():
            self._unified_view.setVisible(True)
            self._left_view.setVisible(False)
            self._right_view.setVisible(False)
        else:
            self._unified_view.setVisible(False)
            self._left_view.setVisible(True)
            self._right_view.setVisible(True)
        
        # Always hide identical view if not identical
        self._identical_view.setVisible(False)
    def _highlight_lines(self, editor: QPlainTextEdit, start_line: int, end_line: int, fmt: QTextCharFormat):
        """Apply formatting to a range of lines in an editor."""
        cursor = editor.textCursor()
        doc = editor.document()
        
        for line_idx in range(start_line, end_line):
            block = doc.findBlockByLineNumber(line_idx)
            if block.isValid():
                cursor.setPosition(block.position())
                cursor.movePosition(cursor.MoveOperation.EndOfBlock, cursor.MoveMode.KeepAnchor)
                cursor.setBlockFormat(self._get_block_fmt(fmt.background().color()))
                # Also apply char format for text color if needed
                
    def _get_block_fmt(self, color: QColor):
        """Create a QTextBlockFormat with a background color."""
        from PyQt6.QtGui import QTextBlockFormat
        fmt = QTextBlockFormat()
        fmt.setBackground(color)
        return fmt
