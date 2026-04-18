"""Application stylesheet and theme tokens."""

from __future__ import annotations

from copy import deepcopy


THEME_PALETTES: dict[str, dict[str, object]] = {
    "light": {
        "font_family": '"Arial", "Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI", sans-serif',
        "app_bg": "#fffaeb",
        "root_bg": "#fff7df",
        "toolbar_bg": "rgba(255, 250, 235, 0.94)",
        "toolbar_border": "rgba(127, 99, 21, 0.14)",
        "panel_bg": "#fff0c2",
        "panel_overlay_bg": "rgba(255, 240, 194, 0.96)",
        "panel_border": "rgba(127, 99, 21, 0.16)",
        "input_bg": "#ffffff",
        "input_bg_readonly": "#fff6e1",
        "input_border": "rgba(127, 99, 21, 0.18)",
        "input_focus": "#fa520f",
        "button_bg": "#fff0c2",
        "button_hover": "#ffe7aa",
        "button_pressed": "#ffd88b",
        "button_disabled": "#f4e6bb",
        "button_border": "rgba(127, 99, 21, 0.18)",
        "button_accent_bg": "#fa520f",
        "button_accent_hover": "#fb6424",
        "button_accent_pressed": "#e24a0d",
        "button_accent_text": "#ffffff",
        "text_primary": "#1f1f1f",
        "text_secondary": "#4b3f33",
        "text_muted": "#7f6315",
        "text_placeholder": "#9d8450",
        "text_inverse": "#ffffff",
        "selection_bg": "#fa520f",
        "selection_text": "#ffffff",
        "generated_text": "#fa520f",
        "diagnostic_text": "#8b5d10",
        "widget_generated_bg": "#fff0d0",
        "widget_diagnostic_bg": "#fff4dc",
        "header_bg": "#fff6e2",
        "header_border": "rgba(127, 99, 21, 0.12)",
        "splitter": "rgba(127, 99, 21, 0.10)",
        "splitter_hover": "rgba(127, 99, 21, 0.22)",
        "scrollbar": "rgba(127, 99, 21, 0.22)",
        "scrollbar_hover": "rgba(127, 99, 21, 0.34)",
        "canvas_bg": "#fffaeb",
        "canvas_minor_grid": "rgba(127, 99, 21, 0.06)",
        "canvas_major_grid": "rgba(127, 99, 21, 0.12)",
        "canvas_hint": "rgba(31, 31, 31, 0.42)",
        "canvas_temp_connection": "#fb6424",
        "connection": "#9d8450",
        "connection_selected": "#fa520f",
        "pin_fill": "#fa520f",
        "pin_outline": "#fff7e2",
        "pin_selected": "#1f1f1f",
        "warning_border": "#cf4b2f",
        "search_border": "#ff8a00",
        "lock_fill": "#fff5d5",
        "lock_border": "rgba(127, 99, 21, 0.34)",
        "lock_text": "#6e5310",
        "shadow": "rgba(127, 99, 21, 0.16)",
        "card_shell": "#fff0c2",
        "card_inner": "#fffaeb",
        "card_note_fill": "#96b45c",
        "card_note_text": "#ffffff",
        "card_draw_fill": "#ffb83e",
        "card_draw_border": "#ffa110",
        "card_draw_text": "#1f1f1f",
        "card_action_fill": "#fff3df",
        "card_action_border": "#fb6424",
        "card_action_text": "#5a3518",
        "card_target_fill": "#ffffff",
        "card_target_border": "#d8c79c",
        "card_target_text": "#1f1f1f",
        "card_parameter_fill": "#ffe8bf",
        "card_parameter_border": "#ff8a00",
        "card_parameter_text": "#463117",
        "frame_fill": "rgba(255, 240, 194, 0.58)",
        "frame_border": "#fa520f",
        "frame_title_fill": "#fa520f",
        "frame_title_text": "#ffffff",
        "node_defaults": {
            "function": {"body": "#fff0c2", "border": "#ffa110", "text": "#1f1f1f"},
            "initial": {"body": "#fff0c2", "border": "#fa520f", "text": "#1f1f1f"},
            "comment": {"body": "#fff6e1", "border": "#ffb83e", "text": "#1f1f1f"},
            "drawframe": {"body": "#fff8e8", "border": "#fb6424", "text": "#1f1f1f"},
        },
    },
    "dark": {
        "font_family": '"Arial", "Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI", sans-serif',
        "app_bg": "#1f1f1f",
        "root_bg": "#26211c",
        "toolbar_bg": "rgba(31, 31, 31, 0.96)",
        "toolbar_border": "rgba(255, 161, 16, 0.14)",
        "panel_bg": "#2a2a2a",
        "panel_overlay_bg": "rgba(42, 42, 42, 0.96)",
        "panel_border": "rgba(255, 161, 16, 0.16)",
        "input_bg": "#333333",
        "input_bg_readonly": "#2d2d2d",
        "input_border": "rgba(255, 161, 16, 0.18)",
        "input_focus": "#ffa110",
        "button_bg": "#2f2a26",
        "button_hover": "#3a332e",
        "button_pressed": "#26211d",
        "button_disabled": "#282523",
        "button_border": "rgba(255, 161, 16, 0.18)",
        "button_accent_bg": "#fff0c2",
        "button_accent_hover": "#ffe295",
        "button_accent_pressed": "#ffd06a",
        "button_accent_text": "#1f1f1f",
        "text_primary": "#ffffff",
        "text_secondary": "rgba(255, 255, 255, 0.74)",
        "text_muted": "rgba(255, 255, 255, 0.48)",
        "text_placeholder": "rgba(255, 255, 255, 0.34)",
        "text_inverse": "#1f1f1f",
        "selection_bg": "#ffa110",
        "selection_text": "#1f1f1f",
        "generated_text": "#ffd06a",
        "diagnostic_text": "#ffe295",
        "widget_generated_bg": "#332d24",
        "widget_diagnostic_bg": "#2d2822",
        "header_bg": "#2f2a26",
        "header_border": "rgba(255, 161, 16, 0.12)",
        "splitter": "rgba(255, 161, 16, 0.10)",
        "splitter_hover": "rgba(255, 161, 16, 0.22)",
        "scrollbar": "rgba(255, 161, 16, 0.24)",
        "scrollbar_hover": "rgba(255, 161, 16, 0.36)",
        "canvas_bg": "#1f1f1f",
        "canvas_minor_grid": "rgba(255, 161, 16, 0.06)",
        "canvas_major_grid": "rgba(255, 161, 16, 0.12)",
        "canvas_hint": "rgba(255, 255, 255, 0.38)",
        "canvas_temp_connection": "#ffd06a",
        "connection": "#c9a76d",
        "connection_selected": "#ffa110",
        "pin_fill": "#ffe295",
        "pin_outline": "#2a2a2a",
        "pin_selected": "#ffffff",
        "warning_border": "#ff7a52",
        "search_border": "#ffd06a",
        "lock_fill": "#2d2822",
        "lock_border": "rgba(255, 255, 255, 0.32)",
        "lock_text": "#ffe295",
        "shadow": "rgba(0, 0, 0, 0.34)",
        "card_shell": "#2a2a2a",
        "card_inner": "#333333",
        "card_note_fill": "#7b9449",
        "card_note_text": "#ffffff",
        "card_draw_fill": "#ff8a00",
        "card_draw_border": "#ffa110",
        "card_draw_text": "#1f1f1f",
        "card_action_fill": "#3a2d24",
        "card_action_border": "#fb6424",
        "card_action_text": "#fff0c2",
        "card_target_fill": "#f1ebdf",
        "card_target_border": "#b9a67d",
        "card_target_text": "#1f1f1f",
        "card_parameter_fill": "#4a3726",
        "card_parameter_border": "#ffb83e",
        "card_parameter_text": "#fff0c2",
        "frame_fill": "rgba(255, 161, 16, 0.08)",
        "frame_border": "#ffa110",
        "frame_title_fill": "#ffa110",
        "frame_title_text": "#1f1f1f",
        "node_defaults": {
            "function": {"body": "#2a2a2a", "border": "#ffa110", "text": "#ffffff"},
            "initial": {"body": "#2a2a2a", "border": "#fb6424", "text": "#ffffff"},
            "comment": {"body": "#2f2a26", "border": "#ffb83e", "text": "#ffffff"},
            "drawframe": {"body": "#292522", "border": "#ffa110", "text": "#ffffff"},
        },
    },
}


def normalize_theme_mode(mode: str | None) -> str:
    return "dark" if str(mode or "").strip().lower() == "dark" else "light"


def theme_palette(mode: str | None = None) -> dict[str, object]:
    return deepcopy(THEME_PALETTES[normalize_theme_mode(mode)])


def build_app_style(mode: str | None = None) -> str:
    palette = theme_palette(mode)
    return f"""
QMainWindow, QWidget {{
    background: {palette["app_bg"]};
    color: {palette["text_primary"]};
    font-family: {palette["font_family"]};
    font-size: 13px;
}}
QWidget#appRoot {{
    background: {palette["root_bg"]};
}}
QSplitter::handle {{
    background: {palette["splitter"]};
    width: 8px;
}}
QSplitter::handle:hover {{
    background: {palette["splitter_hover"]};
}}
QMenuBar {{
    background: {palette["toolbar_bg"]};
    color: {palette["text_primary"]};
    border-bottom: 1px solid {palette["toolbar_border"]};
    padding: 2px 6px;
}}
QMenuBar::item {{
    padding: 6px 10px;
    background: transparent;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background: {palette["button_hover"]};
}}
QMenu {{
    background: {palette["panel_bg"]};
    color: {palette["text_primary"]};
    border: 1px solid {palette["panel_border"]};
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {palette["button_hover"]};
}}
QStatusBar {{
    background: {palette["toolbar_bg"]};
    border-top: 1px solid {palette["toolbar_border"]};
    color: {palette["text_secondary"]};
}}
QToolBar {{
    background: {palette["toolbar_bg"]};
    border-bottom: 1px solid {palette["toolbar_border"]};
    spacing: 8px;
    padding: 4px 8px;
}}
QToolBar QLabel {{
    color: {palette["text_secondary"]};
    background: transparent;
    font-weight: 600;
}}
QToolBar QWidget {{
    background: transparent;
}}
QPushButton,
QToolButton,
QComboBox,
QDateEdit,
QSpinBox {{
    background: {palette["button_bg"]};
    color: {palette["text_primary"]};
    border: 1px solid {palette["button_border"]};
    border-radius: 4px;
    padding: 7px 12px;
}}
QPushButton:hover,
QToolButton:hover,
QComboBox:hover,
QDateEdit:hover,
QSpinBox:hover {{
    background: {palette["button_hover"]};
    border-color: {palette["input_focus"]};
}}
QPushButton:pressed,
QToolButton:pressed {{
    background: {palette["button_pressed"]};
}}
QPushButton:disabled,
QToolButton:disabled,
QComboBox:disabled,
QDateEdit:disabled,
QSpinBox:disabled {{
    background: {palette["button_disabled"]};
    color: {palette["text_muted"]};
    border-color: {palette["button_border"]};
}}
QPushButton[accentButton="true"] {{
    background: {palette["button_accent_bg"]};
    border-color: {palette["button_accent_bg"]};
    color: {palette["button_accent_text"]};
    font-weight: 600;
}}
QPushButton[accentButton="true"]:hover {{
    background: {palette["button_accent_hover"]};
    border-color: {palette["button_accent_hover"]};
}}
QPushButton[accentButton="true"]:pressed {{
    background: {palette["button_accent_pressed"]};
    border-color: {palette["button_accent_pressed"]};
}}
QLineEdit,
QPlainTextEdit,
QDateEdit,
QComboBox,
QListWidget,
QTableWidget,
QScrollArea {{
    background: {palette["input_bg"]};
    color: {palette["text_primary"]};
    border: 1px solid {palette["input_border"]};
    border-radius: 4px;
    selection-background-color: {palette["selection_bg"]};
    selection-color: {palette["selection_text"]};
}}
QLineEdit,
QDateEdit,
QComboBox {{
    min-height: 18px;
}}
QLineEdit:focus,
QPlainTextEdit:focus,
QDateEdit:focus,
QComboBox:focus,
QListWidget:focus,
QTableWidget:focus {{
    border: 2px solid {palette["input_focus"]};
}}
QLineEdit[readOnly="true"],
QPlainTextEdit[readOnly="true"] {{
    background: {palette["input_bg_readonly"]};
    color: {palette["text_secondary"]};
}}
QFrame#filePanelCard,
QFrame#sidebarToolPanel,
QFrame#inspectorShell,
QFrame#searchPanel,
QFrame#validationSummary {{
    background: {palette["panel_bg"]};
    border: 1px solid {palette["panel_border"]};
    border-radius: 6px;
}}
QFrame#searchPanel {{
    background: {palette["panel_overlay_bg"]};
}}
QFrame#canvasToolbarCard {{
    background: transparent;
    border: none;
}}
QFrame#inlineNodeForm,
QFrame#inspectorNodeForm {{
    background: transparent;
    border: none;
}}
QLabel#sectionTitle {{
    color: {palette["text_primary"]};
    font-size: 17px;
    font-weight: 700;
}}
QLabel#searchTitle {{
    color: {palette["text_secondary"]};
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
}}
QLabel#sectionEyebrow {{
    color: {palette["text_muted"]};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#sidebarHint,
QLabel#panelHint {{
    background: transparent;
    border: none;
    color: {palette["text_muted"]};
    font-size: 12px;
    line-height: 1.4em;
}}
QLabel#inspectorMeta {{
    color: {palette["text_secondary"]};
    font-size: 12px;
}}
QLabel#inspectorPlaceholder {{
    color: {palette["text_placeholder"]};
    font-size: 13px;
}}
QFrame#inlineNodeForm QLabel,
QFrame#inspectorNodeForm QLabel {{
    color: {palette["text_secondary"]};
    background: transparent;
}}
QLabel[fieldRole="generated"] {{
    color: {palette["generated_text"]};
    font-weight: 700;
}}
QLabel[fieldRole="diagnostic"] {{
    color: {palette["diagnostic_text"]};
}}
QWidget[fieldRole="generated"] {{
    background: {palette["widget_generated_bg"]};
}}
QWidget[fieldRole="diagnostic"] {{
    background: {palette["widget_diagnostic_bg"]};
}}
QFrame#inlineNodeForm QLineEdit,
QFrame#inlineNodeForm QPlainTextEdit,
QFrame#inlineNodeForm QDateEdit,
QFrame#inlineNodeForm QComboBox,
QFrame#inspectorNodeForm QLineEdit,
QFrame#inspectorNodeForm QPlainTextEdit,
QFrame#inspectorNodeForm QDateEdit,
QFrame#inspectorNodeForm QComboBox {{
    background: {palette["input_bg"]};
    border: 1px solid {palette["input_border"]};
    border-radius: 4px;
}}
QFrame#inlineNodeForm QLineEdit:focus,
QFrame#inlineNodeForm QPlainTextEdit:focus,
QFrame#inlineNodeForm QDateEdit:focus,
QFrame#inlineNodeForm QComboBox:focus,
QFrame#inspectorNodeForm QLineEdit:focus,
QFrame#inspectorNodeForm QPlainTextEdit:focus,
QFrame#inspectorNodeForm QDateEdit:focus,
QFrame#inspectorNodeForm QComboBox:focus {{
    border: 2px solid {palette["input_focus"]};
}}
QListWidget {{
    padding: 4px;
}}
QListWidget::item {{
    padding: 7px 8px;
    margin: 2px 0;
    border-radius: 4px;
}}
QListWidget::item:selected {{
    background: {palette["selection_bg"]};
    color: {palette["selection_text"]};
}}
QTableWidget {{
    gridline-color: {palette["header_border"]};
}}
QHeaderView::section {{
    background: {palette["header_bg"]};
    color: {palette["text_secondary"]};
    border: none;
    border-right: 1px solid {palette["header_border"]};
    border-bottom: 1px solid {palette["header_border"]};
    padding: 7px 6px;
    font-weight: 600;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QCheckBox {{
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {palette["input_border"]};
    border-radius: 3px;
    background: {palette["input_bg"]};
}}
QCheckBox::indicator:checked {{
    background: {palette["selection_bg"]};
    border-color: {palette["selection_bg"]};
}}
QRadioButton {{
    spacing: 6px;
}}
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
}}
QRadioButton::indicator::unchecked {{
    border: 1px solid {palette["input_border"]};
    border-radius: 7px;
    background: {palette["input_bg"]};
}}
QRadioButton::indicator::checked {{
    border: 1px solid {palette["selection_bg"]};
    border-radius: 7px;
    background: {palette["selection_bg"]};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 4px 0;
}}
QScrollBar::handle:vertical {{
    background: {palette["scrollbar"]};
    border-radius: 6px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {palette["scrollbar_hover"]};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0 4px;
}}
QScrollBar::handle:horizontal {{
    background: {palette["scrollbar"]};
    border-radius: 6px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {palette["scrollbar_hover"]};
}}
QScrollBar::add-line,
QScrollBar::sub-line,
QScrollBar::add-page,
QScrollBar::sub-page {{
    background: transparent;
    border: none;
}}
"""


APP_STYLE = build_app_style("light")
