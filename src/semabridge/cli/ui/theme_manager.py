from PyQt6.QtCore import QSettings


class ThemeManager:
    def __init__(self, app):
        self.app = app
        self.settings = QSettings("SemaBridge", "App")
        self.current_theme = self.settings.value("theme", "light")

    def apply_theme(self):
        if self.current_theme == "dark":
            self._apply_dark()
        else:
            self._apply_light()

    def toggle_theme(self):
        self.current_theme = "dark" if self.current_theme == "light" else "light"
        self.settings.setValue("theme", self.current_theme)
        self.apply_theme()

    def _apply_light(self):
        self.app.setStyleSheet("""
            QWidget {
                background-color: #f4f6f9;
                color: #1e1e1e;
                font-size: 13px;
            }
            QPushButton {
                background-color: #2563eb;
                color: white;
                border-radius: 6px;
                padding: 6px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)

    def _apply_dark(self):
        self.app.setStyleSheet("""
            QWidget {
                background-color: #0f172a;
                color: #e5e7eb;
                font-size: 13px;
            }
            QPushButton {
                background-color: #3b82f6;
                color: white;
                border-radius: 6px;
                padding: 6px;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
        """)