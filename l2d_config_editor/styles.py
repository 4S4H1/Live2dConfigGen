"""Application stylesheet."""

from dataclasses import dataclass
from enum import Enum


class ThemeMode(str, Enum):
    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True, slots=True)
class ThemePalette:
    mode: ThemeMode
    canvas_background: str
    grid_minor: str
    grid_major: str
    canvas_hint: str
    connection_normal: str
    connection_preview: str
    connection_selected: str
    connection_related: str
    neutral_panel: str
    editor_background: str
    editor_text: str


DARK_PALETTE = ThemePalette(
    ThemeMode.DARK,
    "#15181e",
    "#20242c",
    "#2a3039",
    "#efeff1",
    "#7d8aa0",
    "#6fb6ff",
    "#2b89ff",
    "#fff4a8",
    "#0d0e12",
    "rgba(13,14,18,242)",
    "#f8fafc",
)

LIGHT_PALETTE = ThemePalette(
    ThemeMode.LIGHT,
    "#f3f6fa",
    "#dce4ee",
    "#b7c4d3",
    "#172033",
    "#52657a",
    "#0369a1",
    "#1d4ed8",
    "#854d0e",
    "#ffffff",
    "rgba(255,255,255,246)",
    "#101828",
)


DARK_APP_STYLE = """
QMainWindow, QWidget {
    background: #15181e;
    color: #efeff1;
    font-family: "Segoe UI Variable Text", "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}
QWidget#appRoot {
    background: #15181e;
}
QSplitter::handle {
    background: #1b2028;
    width: 8px;
}
QSplitter::handle:hover {
    background: #232935;
}
QMenuBar {
    background: #15181e;
    color: #efeff1;
    border-bottom: 1px solid #2a303a;
    padding: 2px 6px;
}
QMenuBar::item {
    padding: 6px 10px;
    background: transparent;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background: #202631;
}
QMenu {
    background: #171b22;
    color: #efeff1;
    border: 1px solid #303743;
    padding: 6px;
}
QMenu::item {
    padding: 6px 12px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #233452;
}
QStatusBar {
    background: #11141a;
    border-top: 1px solid #2a303a;
    color: #bfc5cf;
}
QToolBar {
    background: #15181e;
    border-bottom: 1px solid #2a303a;
    spacing: 8px;
    padding: 4px 8px;
}
QToolBar QLabel {
    color: #c9ced6;
    background: transparent;
    font-weight: 600;
}
QToolBar QWidget {
    background: transparent;
}
QPushButton,
QToolButton,
QComboBox,
QDateEdit,
QSpinBox {
    background: #171b22;
    color: #efeff1;
    border: 1px solid #394150;
    border-radius: 5px;
    padding: 7px 12px;
}
QPushButton:hover,
QToolButton:hover,
QComboBox:hover,
QDateEdit:hover,
QSpinBox:hover {
    background: #1d222b;
    border-color: #4b5669;
}
QPushButton:pressed,
QToolButton:pressed {
    background: #0f1217;
}
QPushButton:disabled,
QToolButton:disabled,
QComboBox:disabled,
QDateEdit:disabled,
QSpinBox:disabled {
    background: #11141a;
    color: #707887;
    border-color: #2a303a;
}
QPushButton[accentButton="true"] {
    background: #2264d6;
    border-color: #2b74f0;
    color: #ffffff;
    font-weight: 600;
}
QPushButton[accentButton="true"]:hover {
    background: #2b74f0;
    border-color: #4a8eff;
}
QPushButton[accentButton="true"]:pressed {
    background: #174ba6;
}
QPushButton[graphViewSwitch="true"]:checked {
    background: #2264d6;
    border-color: #4a8eff;
    color: #ffffff;
    font-weight: 700;
}
QPushButton[graphViewSwitch="true"]:checked:hover {
    background: #2b74f0;
}
QPushButton[graphViewSwitch="true"]:checked:pressed {
    background: #174ba6;
}
QLineEdit,
QPlainTextEdit,
QDateEdit,
QComboBox,
QListWidget,
QTableWidget,
QScrollArea {
    background: #0d0e12;
    color: #efeff1;
    border: 1px solid #303743;
    border-radius: 5px;
    selection-background-color: #2264d6;
    selection-color: #ffffff;
}
QLineEdit,
QDateEdit,
QComboBox {
    min-height: 18px;
}
QLineEdit:focus,
QPlainTextEdit:focus,
QDateEdit:focus,
QComboBox:focus,
QListWidget:focus,
QTableWidget:focus {
    border: 2px solid #2b89ff;
}
QLineEdit[readOnly="true"],
QPlainTextEdit[readOnly="true"] {
    background: #12161c;
    color: #c7ccd5;
}
QFrame#filePanelCard,
QFrame#sidebarToolPanel,
QFrame#inspectorShell,
QFrame#searchPanel,
QFrame#validationSummary {
    background: #171b22;
    border: 1px solid #2f3642;
    border-radius: 8px;
}
QFrame#searchPanel {
    background: rgba(23, 27, 34, 0.96);
}
QFrame#canvasToolbarCard {
    background: transparent;
    border: none;
}
QFrame#inlineNodeForm,
QFrame#inspectorNodeForm {
    background: transparent;
    border: none;
}
QLabel#sectionTitle {
    color: #f7f8fa;
    font-size: 17px;
    font-weight: 700;
}
QLabel#searchTitle {
    color: #d5d7db;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
}
QLabel#sectionEyebrow {
    color: #8d96a5;
    font-size: 11px;
    font-weight: 600;
}
QLabel#sidebarHint,
QLabel#panelHint {
    background: transparent;
    border: none;
    color: #9ea6b3;
    font-size: 12px;
    line-height: 1.4em;
}
QLabel#inspectorMeta {
    color: #b8bec8;
    font-size: 12px;
}
QLabel#inspectorPlaceholder {
    color: #7d8593;
    font-size: 13px;
}
QFrame#inlineNodeForm QLabel,
QFrame#inspectorNodeForm QLabel {
    color: #d5d7db;
    background: transparent;
}
QLabel[fieldRole="generated"] {
    color: #ffcf25;
    font-weight: 700;
}
QLabel[fieldRole="diagnostic"] {
    color: #9eb8e8;
}
QWidget[fieldRole="generated"],
QWidget[fieldRole="diagnostic"] {
    background: #10141b;
}
QFrame#inlineNodeForm QLineEdit,
QFrame#inlineNodeForm QPlainTextEdit,
QFrame#inlineNodeForm QDateEdit,
QFrame#inlineNodeForm QComboBox,
QFrame#inspectorNodeForm QLineEdit,
QFrame#inspectorNodeForm QPlainTextEdit,
QFrame#inspectorNodeForm QDateEdit,
QFrame#inspectorNodeForm QComboBox {
    background: #0d0e12;
    border: 1px solid #344051;
    border-radius: 5px;
}
QFrame#inlineNodeForm QLineEdit:focus,
QFrame#inlineNodeForm QPlainTextEdit:focus,
QFrame#inlineNodeForm QDateEdit:focus,
QFrame#inlineNodeForm QComboBox:focus,
QFrame#inspectorNodeForm QLineEdit:focus,
QFrame#inspectorNodeForm QPlainTextEdit:focus,
QFrame#inspectorNodeForm QDateEdit:focus,
QFrame#inspectorNodeForm QComboBox:focus {
    border: 2px solid #2b89ff;
}
QListWidget {
    padding: 4px;
}
QListWidget::item {
    padding: 7px 8px;
    margin: 2px 0;
    border-radius: 4px;
}
QListWidget::item:selected {
    background: #223452;
    color: #ffffff;
}
QTableWidget {
    gridline-color: #262d37;
}
QHeaderView::section {
    background: #171b22;
    color: #c9ced6;
    border: none;
    border-right: 1px solid #262d37;
    border-bottom: 1px solid #262d37;
    padding: 7px 6px;
    font-weight: 600;
}
QScrollArea {
    border: none;
    background: transparent;
}
QCheckBox {
    spacing: 6px;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #5c6575;
    border-radius: 3px;
    background: #0d0e12;
}
QCheckBox::indicator:checked {
    background: #2264d6;
    border-color: #4b95ff;
}
QRadioButton {
    spacing: 6px;
}
QRadioButton::indicator {
    width: 14px;
    height: 14px;
}
QRadioButton::indicator::unchecked {
    border: 1px solid #5c6575;
    border-radius: 7px;
    background: #0d0e12;
}
QRadioButton::indicator::checked {
    border: 1px solid #4b95ff;
    border-radius: 7px;
    background: #2264d6;
}
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 4px 0;
}
QScrollBar::handle:vertical {
    background: #37404d;
    border-radius: 6px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background: #465162;
}
QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 0 4px;
}
QScrollBar::handle:horizontal {
    background: #37404d;
    border-radius: 6px;
    min-width: 28px;
}
QScrollBar::handle:horizontal:hover {
    background: #465162;
}
QScrollBar::add-line,
QScrollBar::sub-line,
QScrollBar::add-page,
QScrollBar::sub-page {
    background: transparent;
    border: none;
}
"""


LIGHT_COLOR_REPLACEMENTS = {
    "#15181e": "#f3f6fa",
    "#1b2028": "#d9e0e8",
    "#232935": "#cbd5e1",
    "#efeff1": "#172033",
    "#2a303a": "#cfd6df",
    "#202631": "#e8eef6",
    "#171b22": "#ffffff",
    "#303743": "#cfd6df",
    "#233452": "#dbeafe",
    "#11141a": "#e9eef4",
    "#bfc5cf": "#667085",
    "#c9ced6": "#344054",
    "#394150": "#b8c2cf",
    "#1d222b": "#eef2f7",
    "#4b5669": "#94a3b8",
    "#0f1217": "#dfe6ee",
    "#707887": "#667085",
    "#2264d6": "#2563eb",
    "#2b74f0": "#3b82f6",
    "#4a8eff": "#60a5fa",
    "#174ba6": "#1d4ed8",
    "#0d0e12": "#f8fafc",
    "#12161c": "#eef2f6",
    "#c7ccd5": "#475467",
    "#2f3642": "#cfd6df",
    "rgba(23, 27, 34, 0.96)": "rgba(255, 255, 255, 0.98)",
    "#f7f8fa": "#111827",
    "#d5d7db": "#344054",
    "#8d96a5": "#475467",
    "#9ea6b3": "#475467",
    "#b8bec8": "#344054",
    "#7d8593": "#667085",
    "#10141b": "#eef4fb",
    "#344051": "#b8c2cf",
    "#223452": "#dbeafe",
    "#262d37": "#d9e0e8",
    "#5c6575": "#98a2b3",
    "#4b95ff": "#3b82f6",
    "#37404d": "#b7c1ce",
    "#465162": "#94a3b8",
}


def normalize_theme_mode(value: ThemeMode | str | None) -> ThemeMode:
    try:
        return value if isinstance(value, ThemeMode) else ThemeMode(str(value or ThemeMode.DARK.value))
    except ValueError:
        return ThemeMode.DARK


def palette_for_theme(value: ThemeMode | str | None) -> ThemePalette:
    return LIGHT_PALETTE if normalize_theme_mode(value) is ThemeMode.LIGHT else DARK_PALETTE


def stylesheet_for_theme(value: ThemeMode | str | None) -> str:
    if normalize_theme_mode(value) is ThemeMode.DARK:
        return DARK_APP_STYLE
    style = DARK_APP_STYLE
    for source, target in LIGHT_COLOR_REPLACEMENTS.items():
        style = style.replace(source, target)
    return style


APP_STYLE = stylesheet_for_theme(ThemeMode.DARK)
