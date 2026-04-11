"""Application stylesheet."""

APP_STYLE = """
QMainWindow, QWidget {
    background: #f5f4ed;
    color: #141413;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 14px;
}
QWidget#appShell {
    background: #f5f4ed;
}
QMenuBar {
    background: rgba(245, 244, 237, 0.96);
    color: #3d3d3a;
    border-bottom: 1px solid #e8e6dc;
    padding: 6px 10px;
}
QMenuBar::item {
    background: transparent;
    padding: 8px 10px;
    border-radius: 8px;
}
QMenuBar::item:selected {
    background: #e8e6dc;
    color: #141413;
}
QMenu {
    background: #faf9f5;
    color: #141413;
    border: 1px solid #e8e6dc;
    padding: 6px;
}
QMenu::item {
    padding: 8px 12px;
    border-radius: 8px;
}
QMenu::item:selected {
    background: #ece5d7;
}
QToolTip {
    background: #30302e;
    color: #faf9f5;
    border: 1px solid #3d3d3a;
    padding: 6px 8px;
}
QStatusBar#appStatusBar {
    background: transparent;
    color: #5e5d59;
    border-top: 1px solid #e8e6dc;
    padding: 4px 8px;
}
QStatusBar::item {
    border: none;
}
QSplitter::handle {
    background: transparent;
    width: 10px;
}
QSplitter::handle:hover {
    background: rgba(201, 100, 66, 0.10);
}
QFrame#heroPanel,
QFrame#heroMetricCard,
QFrame#sidePanelCard,
QFrame#canvasPanelCard {
    background: #faf9f5;
    border: 1px solid #ece8dd;
    border-radius: 18px;
}
QFrame#canvasShell {
    background: #141413;
    border: 1px solid #30302e;
    border-radius: 20px;
}
QLabel#heroEyebrow,
QLabel#panelEyebrow,
QLabel#metricLabel {
    color: #c96442;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.2px;
    text-transform: uppercase;
}
QLabel#heroTitle {
    color: #141413;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 30px;
    font-weight: 600;
    line-height: 1.2em;
}
QLabel#heroDescription,
QLabel#panelCopy,
QLabel#metricNote,
QLabel#panelMeta {
    color: #5e5d59;
    font-size: 13px;
    line-height: 1.5em;
}
QLabel#panelTitle,
QLabel#sectionTitle {
    color: #141413;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 24px;
    font-weight: 600;
}
QLabel#metricValue {
    color: #141413;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 18px;
    font-weight: 600;
}
QLabel#canvasContext {
    color: #faf9f5;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 18px;
    font-weight: 600;
}
QLabel#canvasHint {
    color: #b0aea5;
    font-size: 13px;
    line-height: 1.5em;
}
QLabel#searchTitle {
    color: #faf9f5;
    font-size: 13px;
    font-weight: 600;
}
QLabel#sidebarHint {
    background: transparent;
    border: none;
    color: #87867f;
    font-size: 12px;
    line-height: 1.45em;
    padding: 0;
}
QPushButton {
    background: #e8e6dc;
    color: #4d4c48;
    border: 1px solid #d1cfc5;
    border-radius: 12px;
    padding: 9px 14px;
    font-size: 14px;
    font-weight: 600;
}
QPushButton:hover {
    background: #ece5d7;
    border-color: #c2c0b6;
}
QPushButton:pressed {
    background: #ddd7c8;
}
QPushButton:disabled {
    background: #efede4;
    color: #a49d92;
    border-color: #e1ddd2;
}
QPushButton[variant="primary"] {
    background: #c96442;
    color: #faf9f5;
    border: 1px solid #c96442;
}
QPushButton[variant="primary"]:hover {
    background: #d97757;
    border-color: #d97757;
}
QPushButton[variant="primary"]:pressed {
    background: #b95c3d;
}
QPushButton[variant="danger"] {
    background: #f5e3df;
    color: #9b3d36;
    border: 1px solid #e4bdb7;
}
QPushButton[variant="danger"]:hover {
    background: #f1d4cf;
}
QPushButton[variant="dark"] {
    background: #30302e;
    color: #faf9f5;
    border: 1px solid #30302e;
}
QPushButton[variant="dark"]:hover {
    background: #3a3937;
}
QLineEdit,
QPlainTextEdit,
QDateEdit,
QComboBox,
QListWidget,
QTableWidget,
QScrollArea {
    background: #fffdf8;
    color: #141413;
    border: 1px solid #e8e6dc;
    border-radius: 12px;
    selection-background-color: #d97757;
    selection-color: #ffffff;
}
QLineEdit,
QDateEdit,
QComboBox {
    padding: 7px 10px;
}
QLineEdit:focus,
QPlainTextEdit:focus,
QDateEdit:focus,
QComboBox:focus,
QListWidget:focus,
QTableWidget:focus {
    border: 1px solid #3898ec;
}
QTableWidget {
    gridline-color: #ece8dd;
}
QHeaderView::section {
    background: #f0ece2;
    color: #4d4c48;
    border: none;
    border-right: 1px solid #e3ddd1;
    border-bottom: 1px solid #e3ddd1;
    padding: 8px;
    font-weight: 600;
}
QListWidget::item {
    padding: 8px 10px;
    border-radius: 10px;
    margin: 2px 0;
}
QListWidget::item:selected {
    background: #ebe2d2;
    color: #141413;
}
QListWidget#configFileList {
    background: #fffdf9;
    border-radius: 16px;
}
QListWidget#configFileList::item {
    padding: 10px 12px;
    margin: 3px 0;
}
QCheckBox,
QRadioButton {
    spacing: 8px;
    color: #3d3d3a;
}
QCheckBox::indicator,
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #c8c3b6;
    background: #fffdf8;
}
QCheckBox::indicator {
    border-radius: 5px;
}
QRadioButton::indicator {
    border-radius: 8px;
}
QCheckBox::indicator:checked,
QRadioButton::indicator:checked {
    background: #c96442;
    border: 1px solid #c96442;
}
QScrollArea {
    border: none;
    background: transparent;
}
QFrame#searchPanel {
    background: rgba(48, 48, 46, 0.92);
    border: 1px solid rgba(176, 174, 165, 0.22);
    border-radius: 16px;
}
QFrame#searchPanel QLineEdit,
QFrame#searchPanel QListWidget {
    background: rgba(250, 249, 245, 0.96);
}
QFrame#validationSummary {
    background: #fffdf8;
    border: 1px solid #ece8dd;
    border-radius: 16px;
}
QLabel#validationSummaryTitle {
    color: #141413;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 18px;
    font-weight: 600;
}
QFrame#inlineNodeForm {
    background: transparent;
    border: none;
}
QFrame#inlineNodeForm QLabel {
    background: transparent;
    color: #ece5d7;
}
QFrame#inlineNodeForm QLabel#nodeFormTitle {
    color: #faf9f5;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 17px;
    font-weight: 600;
}
QFrame#inlineNodeForm QLabel#nodeFormSubtitle {
    color: #b0aea5;
    font-size: 12px;
}
QFrame#inlineNodeForm QLineEdit,
QFrame#inlineNodeForm QPlainTextEdit,
QFrame#inlineNodeForm QDateEdit,
QFrame#inlineNodeForm QComboBox {
    background: rgba(250, 249, 245, 0.08);
    color: #faf9f5;
    border: 1px solid rgba(176, 174, 165, 0.26);
    border-radius: 10px;
}
QFrame#inlineNodeForm QLineEdit:focus,
QFrame#inlineNodeForm QPlainTextEdit:focus,
QFrame#inlineNodeForm QDateEdit:focus,
QFrame#inlineNodeForm QComboBox:focus {
    border: 1px solid rgba(217, 119, 87, 0.95);
    background: rgba(250, 249, 245, 0.12);
}
QFrame#inlineNodeForm QCheckBox {
    background: transparent;
    color: #ece5d7;
}
QFrame#inlineNodeForm QCheckBox::indicator,
QFrame#inlineNodeForm QRadioButton::indicator {
    background: rgba(250, 249, 245, 0.08);
    border: 1px solid rgba(176, 174, 165, 0.4);
}
QFrame#inlineNodeForm QCheckBox::indicator:checked,
QFrame#inlineNodeForm QRadioButton::indicator:checked {
    background: #d97757;
    border: 1px solid #d97757;
}
QFrame#inspectorNodeForm {
    background: transparent;
    border: none;
}
QFrame#inspectorNodeForm QLabel {
    color: #3d3d3a;
}
QFrame#inspectorNodeForm QLabel#nodeFormTitle {
    color: #141413;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 20px;
    font-weight: 600;
}
QFrame#inspectorNodeForm QLabel#nodeFormSubtitle {
    color: #87867f;
    font-size: 12px;
}
QFrame#inspectorNodeForm QLineEdit,
QFrame#inspectorNodeForm QPlainTextEdit,
QFrame#inspectorNodeForm QDateEdit,
QFrame#inspectorNodeForm QComboBox {
    background: #fffdf8;
}
QScrollArea#inspectorScrollArea {
    background: transparent;
}
"""
