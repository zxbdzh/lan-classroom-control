"""Centralized Qt styles for the teacher console."""

LIGHT = {
    "window": "#F4F6F9",
    "panel": "#FFFFFF",
    "panel_alt": "#EEF2F6",
    "ink": "#172033",
    "muted": "#667085",
    "line": "#D8DEE8",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "success": "#16836B",
    "warning": "#B66A13",
    "danger": "#C23B4A",
    "seat": "#F9FAFC",
}

DARK = {
    "window": "#11151B",
    "panel": "#191F28",
    "panel_alt": "#232B36",
    "ink": "#EDF1F7",
    "muted": "#A7B0BF",
    "line": "#343E4C",
    "primary": "#5B8DEF",
    "primary_hover": "#7AA2F7",
    "success": "#43B89C",
    "warning": "#E3A54F",
    "danger": "#E06272",
    "seat": "#1E2631",
}


def stylesheet(theme: str = "light") -> str:
    c = DARK if theme == "dark" else LIGHT
    return """
    * { font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 13px; }
    QMainWindow, QDialog, QMessageBox, QWidget#Root { background: %(window)s; color: %(ink)s; }
    QWidget#Sidebar, QWidget#CommandBar, QWidget#OperationDrawer,
    QWidget#PageHeader, QStatusBar { background: %(panel)s; }
    QLabel { color: %(ink)s; background: transparent; }
    QLabel[muted="true"] { color: %(muted)s; }
    QLabel#PageTitle { font-size: 20px; font-weight: 600; }
    QLabel#Brand { font-size: 17px; font-weight: 600; color: %(ink)s; }
    QLabel#Lectern { background: %(panel_alt)s; border: 1px solid %(line)s;
        border-radius: 4px; padding: 9px; color: %(muted)s; font-weight: 600; }
    QFrame#Divider { background: %(line)s; min-height: 1px; max-height: 1px; }
    QPushButton { min-height: 34px; padding: 0 12px; border: 1px solid %(line)s;
        border-radius: 5px; background: %(panel)s; color: %(ink)s; }
    QPushButton:hover { background: %(panel_alt)s; }
    QPushButton:pressed { background: %(line)s; }
    QPushButton:disabled { color: %(muted)s; background: %(panel_alt)s; }
    QPushButton[primary="true"] { background: %(primary)s; border-color: %(primary)s; color: white; }
    QPushButton[primary="true"]:hover { background: %(primary_hover)s; }
    QPushButton[danger="true"] { color: %(danger)s; border-color: %(danger)s; }
    QPushButton[compact="true"] { min-height: 26px; padding: 0 8px; }
    QLabel#SectionTitle { font-size: 15px; font-weight: 600; }
    QFrame#ApplicationComposer { background: %(panel_alt)s; border: 1px solid %(line)s; border-radius: 5px; }
    QTabWidget#ApplicationTabs::pane { border: 1px solid %(line)s; background: %(panel)s; }
    QTabWidget#ApplicationTabs QTabBar::tab { background: %(panel_alt)s; color: %(muted)s;
        border: 1px solid %(line)s; padding: 9px 20px; margin-right: 4px; }
    QTabWidget#ApplicationTabs QTabBar::tab:selected { background: %(panel)s; color: %(primary)s;
        border-bottom: 2px solid %(primary)s; font-weight: 600; }
    QCheckBox { color: %(ink)s; spacing: 6px; background: transparent; }
    QPushButton#NavButton { border: 0; border-radius: 4px; text-align: left;
        padding-left: 14px; min-height: 42px; color: %(muted)s; background: transparent; }
    QPushButton#NavButton:hover { color: %(ink)s; background: %(panel_alt)s; }
    QPushButton#NavButton:checked { color: %(primary)s; background: %(panel_alt)s;
        border-left: 3px solid %(primary)s; padding-left: 11px; font-weight: 600; }
    QPushButton#ScopeButton { border-radius: 0; min-height: 30px; padding: 0 14px; }
    QPushButton#ScopeButton:first { border-radius: 4px; }
    QPushButton#ScopeButton:checked { background: %(ink)s; color: %(panel)s; border-color: %(ink)s; }
    QFrame#SeatTile { background: %(seat)s; border: 1px solid %(line)s; border-radius: 5px; }
    QFrame#SeatTile[selected="true"] { border: 2px solid %(primary)s; background: %(panel)s; }
    QFrame#SeatTile[offline="true"] { background: %(panel_alt)s; }
    QFrame#PatrolCard { background: #101318; border: 1px solid %(line)s; border-radius: 5px; }
    QFrame#PatrolCard[selected="true"] { border: 4px solid %(primary)s; }
    QFrame#EmptySeat { border: 1px dashed %(line)s; border-radius: 5px; background: transparent; }
    QLineEdit, QComboBox, QSpinBox, QTextEdit, QTableWidget, QListWidget {
        background: %(panel)s; color: %(ink)s; border: 1px solid %(line)s;
        border-radius: 4px; selection-background-color: %(primary)s; padding: 5px; }
    QTableWidget { gridline-color: %(line)s; border-radius: 0; }
    QHeaderView::section { background: %(panel_alt)s; color: %(muted)s; border: 0;
        border-bottom: 1px solid %(line)s; padding: 7px; font-weight: 600; }
    QScrollArea { border: 0; background: transparent; }
    QScrollArea > QWidget > QWidget { background: transparent; }
    QScrollBar:vertical { width: 10px; background: transparent; }
    QScrollBar::handle:vertical { background: %(line)s; min-height: 24px; border-radius: 4px; }
    QProgressBar { border: 0; background: %(panel_alt)s; height: 6px; border-radius: 3px; }
    QProgressBar::chunk { background: %(primary)s; border-radius: 3px; }
    QToolTip { background: %(ink)s; color: %(panel)s; border: 0; padding: 5px; }
    """ % c


__all__ = ["DARK", "LIGHT", "stylesheet"]
