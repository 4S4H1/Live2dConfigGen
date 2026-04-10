"""Application stylesheet."""

APP_STYLE = """
QMainWindow, QWidget {
    background: #1e2633;
    color: #e5edf7;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}
QMenuBar {
    background: #2b3546;
    color: #e5edf7;
    border-bottom: 1px solid #3c4a61;
}
QMenuBar::item:selected {
    background: #3c4d66;
}
QMenu {
    background: #293243;
    border: 1px solid #41506b;
    padding: 4px;
}
QMenu::item:selected {
    background: #456791;
}
QPushButton {
    background: #34445c;
    border: 1px solid #4b627f;
    border-radius: 8px;
    padding: 6px 12px;
}
QPushButton:hover {
    background: #405473;
}
QPushButton:pressed {
    background: #2c3c52;
}
QLineEdit, QPlainTextEdit, QDateEdit, QComboBox, QListWidget, QTableWidget, QScrollArea {
    background: #263142;
    border: 1px solid #41506b;
    border-radius: 8px;
    selection-background-color: #4d7cb6;
    selection-color: #ffffff;
}
QTableWidget {
    gridline-color: #3f4d65;
}
QHeaderView::section {
    background: #324157;
    color: #e5edf7;
    border: none;
    border-right: 1px solid #42536d;
    padding: 6px;
}
QListWidget::item {
    padding: 6px;
    border-radius: 6px;
}
QListWidget::item:selected {
    background: #3c597f;
}
QCheckBox {
    spacing: 6px;
}
QStatusBar {
    background: #243043;
    border-top: 1px solid #3c4a61;
}
QScrollArea {
    border: none;
    background: transparent;
}
QFrame#searchPanel {
    background: rgba(40, 51, 69, 0.92);
    border: 1px solid #45556f;
    border-radius: 12px;
}
QLabel#searchTitle, QLabel#sectionTitle, QLabel#validationSummaryTitle {
    color: #f0f5ff;
    font-size: 14px;
    font-weight: 600;
}
QFrame#validationSummary {
    background: #253042;
    border: 1px solid #465671;
    border-radius: 12px;
}
"""
