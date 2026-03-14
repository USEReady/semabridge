"""
SemaBridge Premium UI Template
A modular, enterprise-grade PyQt6 dashboard template featuring:
- Three-column layout with visual hierarchy
- YAML Editor with line numbers and syntax highlighting
- Enhanced search with integrated icons
- Modern button styles (Solid vs Outline)
- Modular QSS design system
"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPlainTextEdit, QLabel, QPushButton, QSplitter, QLineEdit,
    QFrame, QLayout, QTextEdit
)
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import (
    QColor, QPainter, QTextFormat, QFont, QSyntaxHighlighter, 
    QTextCharFormat, QIcon, QAction
)

# === DESIGN SYSTEM (QSS) ===
QSS = """
QMainWindow {
    background-color: #F8FAFC;
}

/* Sidebar Styling (Left/Right) */
QWidget[class="sidebar"] {
    background-color: #F8FAFC;
    border: none;
}

/* Workspace Styling (Center) */
QWidget[class="workspace"] {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
}

/* Typography */
QLabel {
    color: #475569;
    font-size: 13px;
}

QLabel[class="h1"] {
    color: #0F172A;
    font-size: 18px;
    font-weight: bold;
}

/* Search Box */
QLineEdit[class="search"] {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 8px 12px 8px 32px; /* Pad for icon */
    color: #0F172A;
}

QLineEdit[class="search"]:focus {
    border: 2px solid #2563EB;
}

/* Buttons */
QPushButton {
    font-weight: 600;
    font-size: 13px;
    padding: 8px 16px;
    border-radius: 6px;
}

/* Primary Button (Solid) */
QPushButton[class="primary"] {
    background-color: #2563EB;
    color: #FFFFFF;
    border: none;
}

QPushButton[class="primary"]:hover {
    background-color: #1D4ED8;
}

/* Secondary Button (Outline Only) */
QPushButton[class="secondary"] {
    background-color: transparent;
    color: #2563EB;
    border: 1px solid #2563EB;
}

QPushButton[class="secondary"]:hover {
    background-color: #EFF6FF;
}

/* Editor Gutter */
QWidget#LineNumberArea {
    background-color: #F8FAFC;
    border-right: 1px solid #E2E8F0;
}
"""

# === COMPONENTS ===

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.setObjectName("LineNumberArea")
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)

class ProfessionalEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        
        self.update_line_number_area_width(0)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        
        # Font for code
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
            line_color = QColor("#F1F5F9")
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#F8FAFC"))
        
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#94A3B8"))
                painter.drawText(0, top, self.line_number_area.width() - 8, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, number)
            
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

class ModernSearchBox(QLineEdit):
    def __init__(self, placeholder="Search...", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setProperty("class", "search")
        
        # Add a magnifying glass action (visual only or functional)
        search_action = QAction(self)
        search_action.setIcon(QIcon.fromTheme("edit-find")) # Fallback
        # In a real app, you'd use a better SVG icon for the "leading" effect
        # For now, custom QSS padding handles the visual space.

# === MAIN WINDOW ===

class SemaBridgeDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SemaBridge - Premium Unified Interface")
        self.resize(1200, 850)
        self.setStyleSheet(QSS)
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        # Header Area
        header = QHBoxLayout()
        title = QLabel("SemaBridge Dashboard")
        title.setProperty("class", "h1")
        header.addWidget(title)
        header.addStretch()
        
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("class", "secondary")
        header.addWidget(refresh_btn)
        
        sync_btn = QPushButton("Sync Now")
        sync_btn.setProperty("class", "primary")
        header.addWidget(sync_btn)
        
        main_layout.addLayout(header)
        
        # Dashboard Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        
        # 1. Source Browser (Left Sidebar)
        left_pnl = QFrame()
        left_pnl.setProperty("class", "sidebar")
        left_layout = QVBoxLayout(left_pnl)
        left_layout.setContentsMargins(0, 0, 10, 0) # Pad against center
        
        left_layout.addWidget(QLabel("SOURCE BROWSER"))
        left_layout.addWidget(ModernSearchBox("Filter models..."))
        
        models_list = QPlainTextEdit() # Placeholder for QTreeWidget
        models_list.setPlainText("continent\nindustry\nprobablility\nannual\nSalesAnalytics\nnew_rep\nEmployee")
        models_list.setReadOnly(True)
        left_layout.addWidget(models_list, 1)
        
        splitter.addWidget(left_pnl)
        
        # 2. YAML Editor (Center Workspace)
        center_pnl = QWidget()
        center_pnl.setObjectName("Workspace")
        center_pnl.setProperty("class", "workspace")
        center_layout = QVBoxLayout(center_pnl)
        center_layout.setContentsMargins(15, 15, 15, 15)
        
        center_layout.addWidget(QLabel("YAML CONFIGURATION EDITOR"))
        self.editor = ProfessionalEditor()
        self.editor.setPlainText(
            "source:\n"
            "  type: fabric\n"
            "  workspace_id: \"444e2fde-3066-4781-8b3d-1a8f698c960\"\n"
            "  model: \"SalesAnalytics\"\n\n"
            "target:\n"
            "  type: snowflake\n"
            "  deploy: true\n"
        )
        center_layout.addWidget(self.editor, 1)
        
        validate_row = QHBoxLayout()
        validate_row.addStretch()
        validate_btn = QPushButton("Validate YAML")
        validate_btn.setProperty("class", "secondary")
        validate_row.addWidget(validate_btn)
        center_layout.addLayout(validate_row)
        
        splitter.addWidget(center_pnl)
        
        # 3. Version History (Right Sidebar)
        right_pnl = QFrame()
        right_pnl.setProperty("class", "sidebar")
        right_layout = QVBoxLayout(right_pnl)
        right_layout.setContentsMargins(10, 0, 0, 0)
        
        right_layout.addWidget(QLabel("VERSION HISTORY"))
        hist_list = QPlainTextEdit()
        hist_list.setPlainText("v1.2 - Added Customer info\nv1.1 - Fixed data formats\nv1.0 - Initial sync")
        hist_list.setReadOnly(True)
        right_layout.addWidget(hist_list, 1)
        
        splitter.addWidget(right_pnl)
        
        # Balancing sizes
        splitter.setSizes([250, 700, 250])
        main_layout.addWidget(splitter, 1)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SemaBridgeDashboard()
    window.show()
    sys.exit(app.exec()) # Start the event loop
