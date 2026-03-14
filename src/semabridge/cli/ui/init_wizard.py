"""
SemaBridge Initialization Wizard.

Guides the user through the first-run setup process, creating the
repository and global configuration file.
"""

from pathlib import Path
from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QLabel, QLineEdit, 
    QPushButton, QFileDialog, QMessageBox, QFrame, QHBoxLayout
)
from PyQt6.QtCore import Qt, pyqtSignal

from semabridge.core.initializer import SemabridgeInitializer
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


COLORS = {
    "bg_primary": "#1E1E2E",
    "bg_secondary": "#2A2A3E",
    "text_primary": "#FFFFFF",
    "text_secondary": "#B4B4C0",
    "accent": "#7C3AED",
    "border": "#404060",
}

STYLESHEET = f"""
    QWizard {{
        background-color: {COLORS['bg_primary']};
        color: {COLORS['text_primary']};
    }}
    QWizardPage {{
        background-color: {COLORS['bg_primary']};
        color: {COLORS['text_primary']};
    }}
    QLabel {{
        color: {COLORS['text_primary']};
        font-size: 10pt;
    }}
    QLabel[class="header"] {{
        font-size: 16pt;
        font-weight: bold;
        color: {COLORS['accent']};
        margin-bottom: 10px;
    }}
    QLabel[class="desc"] {{
        color: {COLORS['text_secondary']};
        margin-bottom: 20px;
    }}
    QLineEdit {{
        background-color: {COLORS['bg_secondary']};
        border: 1px solid {COLORS['border']};
        border-radius: 4px;
        padding: 8px;
        color: {COLORS['text_primary']};
        selection-background-color: {COLORS['accent']};
    }}
    QPushButton {{
        background-color: {COLORS['bg_secondary']};
        border: 1px solid {COLORS['border']};
        border-radius: 4px;
        padding: 5px 10px;
        color: {COLORS['text_primary']};
    }}
    QPushButton:hover {{
        background-color: {COLORS['accent']};
        border: 1px solid {COLORS['accent']};
    }}
"""


class InitializationWizard(QWizard):
    """Wizard for initializing Semabridge repository and config."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SemaBridge Initialization")
        self.setStyleSheet(STYLESHEET)
        
        # Wizard options
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoCancelButton, False)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        
        # Add pages
        self.addPage(WelcomePage())
        self.addPage(RepositoryPage())
        self.addPage(LoggingPage())
        self.addPage(ConfirmationPage())
        
        # Set window size
        self.resize(600, 400)


class WelcomePage(QWizardPage):
    """Introduction page."""
    
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome to SemaBridge")
        
        layout = QVBoxLayout()
        
        header = QLabel("First Run Setup", objectName="header")
        header.setProperty("class", "header")
        layout.addWidget(header)
        
        desc = QLabel(
            "It looks like this is your first time running SemaBridge (or the configuration is missing).\n\n"
            "This wizard will help you initialize the repository and global settings needed to track "
            "semantic models and manage deployments.\n\n"
            "Click 'Next' to continue."
        )
        desc.setWordWrap(True)
        desc.setProperty("class", "desc")
        layout.addWidget(desc)
        
        self.setLayout(layout)


class RepositoryPage(QWizardPage):
    """Page to select repository location."""
    
    def __init__(self):
        super().__init__()
        self.setTitle("Repository Location")
        
        layout = QVBoxLayout()
        
        header = QLabel("Where should data be stored?")
        header.setProperty("class", "header")
        layout.addWidget(header)
        
        desc = QLabel(
            "SemaBridge uses a DuckDB database to store version history, metadata, and snapshots. "
            "Choose a location for this repository."
        )
        desc.setWordWrap(True)
        desc.setProperty("class", "desc")
        layout.addWidget(desc)
        
        # Path selection
        path_layout = QHBoxLayout()
        
        default_path = SemabridgeInitializer.get_default_repository_path().parent
        self.path_input = QLineEdit(str(default_path))
        path_layout.addWidget(self.path_input)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_layout.addWidget(browse_btn)
        
        layout.addLayout(path_layout)
        
        # Register field for next pages
        self.registerField("repo_path", self.path_input)
        
        self.setLayout(layout)
    
    def _browse(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Repository Directory", self.path_input.text())
        if directory:
            self.path_input.setText(directory)


class LoggingPage(QWizardPage):
    """Page to select log directory."""
    
    def __init__(self):
        super().__init__()
        self.setTitle("Logging Configuration")
        
        layout = QVBoxLayout()
        
        header = QLabel("Where should logs be stored?")
        header.setProperty("class", "header")
        layout.addWidget(header)
        
        desc = QLabel(
            "SemaBridge generates operation, error, and audit logs. "
            "Select a directory to store these log files."
        )
        desc.setWordWrap(True)
        desc.setProperty("class", "desc")
        layout.addWidget(desc)
        
        # Path selection
        path_layout = QHBoxLayout()
        
        # Default logs next to repo or ./logs
        default_log = Path.home() / ".semabridge" / "logs"
        self.path_input = QLineEdit(str(default_log))
        path_layout.addWidget(self.path_input)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_layout.addWidget(browse_btn)
        
        layout.addLayout(path_layout)
        
        # Register field
        self.registerField("log_path", self.path_input)
        
        self.setLayout(layout)
    
    def _browse(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Log Directory", self.path_input.text())
        if directory:
            self.path_input.setText(directory)


class ConfirmationPage(QWizardPage):
    """Summary and Action page."""
    
    def __init__(self):
        super().__init__()
        self.setTitle("Ready to Initialize")
        
        layout = QVBoxLayout()
        
        header = QLabel("Summary")
        header.setProperty("class", "header")
        layout.addWidget(header)
        
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setProperty("class", "desc")
        layout.addWidget(self.summary_label)
        
        layout.addStretch()
        
        self.setLayout(layout)
    
    def initializePage(self):
        repo = self.field("repo_path")
        logs = self.field("log_path")
        
        self.summary_label.setText(
            f"Repository Directory:\n{repo}\n\n"
            f"Log Directory:\n{logs}\n\n"
            "Click 'Finish' to create the repository and configuration file."
        )
    
    def validatePage(self) -> bool:
        """Perform initialization when user clicks Finish."""
        repo_dir = Path(self.field("repo_path"))
        log_dir = Path(self.field("log_path"))
        
        # Construct db path
        db_path = repo_dir / "semabridge.db"
        
        try:
            initializer = SemabridgeInitializer(
                repository_path=db_path,
                log_dir=str(log_dir)
            )
            result = initializer.initialize()
            
            if result.success:
                return True
            else:
                QMessageBox.critical(self, "Initialization Failed", result.message)
                return False
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An unexpected error occurred: {e}")
            return False
