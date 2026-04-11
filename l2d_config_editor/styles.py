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
QListWidget#configFileList::item {
    padding: 7px 8px;
    margin: 2px 0;
}
QCheckBox {
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #6f88ab;
    border-radius: 4px;
    background: #223044;
}
QCheckBox::indicator:checked {
    background: #4d7cb6;
    border: 1px solid #8db8f5;
}
QCheckBox::indicator:unchecked:hover,
QCheckBox::indicator:checked:hover {
    border: 1px solid #a6c7f5;
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
QFrame#sidebarToolPanel {
    background: rgba(40, 51, 69, 0.96);
    border: 1px solid #4d607d;
    border-radius: 12px;
}
QFrame#inlineNodeForm {
    background: transparent;
    border: none;
}
QFrame#inlineNodeForm QLabel {
    background: transparent;
    color: #eef4ff;
}
QFrame#inlineNodeForm QLineEdit,
QFrame#inlineNodeForm QPlainTextEdit,
QFrame#inlineNodeForm QDateEdit,
QFrame#inlineNodeForm QComboBox {
    background: rgba(36, 49, 69, 0.68);
    border: 1px solid rgba(122, 150, 196, 0.48);
    border-radius: 8px;
}
QFrame#inlineNodeForm QLineEdit:focus,
QFrame#inlineNodeForm QPlainTextEdit:focus,
QFrame#inlineNodeForm QDateEdit:focus,
QFrame#inlineNodeForm QComboBox:focus {
    border: 1px solid rgba(168, 204, 255, 0.92);
    background: rgba(40, 55, 77, 0.9);
}
QFrame#inlineNodeForm QCheckBox {
    background: transparent;
}
QFrame#inlineNodeForm QCheckBox::indicator {
    background: rgba(36, 49, 69, 0.86);
    border: 1px solid rgba(168, 204, 255, 0.68);
}
QFrame#inlineNodeForm QCheckBox::indicator:checked {
    background: rgba(120, 177, 255, 0.85);
    border: 1px solid rgba(216, 234, 255, 0.92);
}
QLabel#searchTitle, QLabel#sectionTitle, QLabel#validationSummaryTitle {
    color: #f0f5ff;
    font-size: 14px;
    font-weight: 600;
}
QLabel#sidebarHint {
    color: #c2d0e4;
    font-size: 12px;
    line-height: 1.35em;
}
QFrame#validationSummary {
    background: #253042;
    border: 1px solid #465671;
    border-radius: 12px;
}
"""
