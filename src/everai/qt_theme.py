"""Two complete Qt palettes; no platform theme or web runtime required."""
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QColor, QPalette, QPixmap, QIcon, QPainter, QPen
import math

COLORS = {
    'light': dict(bg='#f4f6fb', surface='#ffffff', raised='#f8faff', sidebar='#eef2f9',
                  text='#18233b', muted='#57647b', border='#d9dfeb', accent='#345de2',
                  hover='#e7eeff', selection='#dfe9ff', danger='#b92c48', success='#087352',
                  ambient='#eeedf9', ambient_end='#f4f6fb', primary_end='#5558d5', on_primary='#ffffff'),
    'dark': dict(bg='#0b0c10', surface='#13151b', raised='#1a1c24', sidebar='#0d0e13',
                 text='#eff0f8', muted='#a0a7b8', border='#30333f', accent='#b0a1fa',
                 hover='#252332', selection='#2c263e', danger='#ff96aa', success='#78d6b2',
                 ambient='#171420', ambient_end='#0b0e13', primary_end='#92b0ee', on_primary='#10121a'),
}


def make_icon(name, mode='light', color=None):
    """Small native vector icons, sharp on high-DPI screens in both themes."""
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(2)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    ink = QColor(color or COLORS[mode]['accent'])
    painter.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    if name in ('wave', 'studio'):
        for x, height in ((3, 5), (7, 11), (11, 16), (15, 9), (19, 4)):
            painter.drawLine(QPointF(x, 10-height/2), QPointF(x, 10+height/2))
    elif name == 'moon':
        painter.drawArc(QRectF(3, 2, 14, 15), 55*16, 275*16)
        painter.drawArc(QRectF(8, 0, 12, 13), 170*16, 135*16)
    elif name == 'sun':
        painter.drawEllipse(QRectF(6, 6, 8, 8))
        for angle in range(0, 360, 45):
            a = math.radians(angle)
            painter.drawLine(QPointF(10+6*math.cos(a), 10+6*math.sin(a)),
                             QPointF(10+9*math.cos(a), 10+9*math.sin(a)))
    elif name == 'play':
        painter.drawLine(QPointF(6, 3), QPointF(16, 10))
        painter.drawLine(QPointF(16, 10), QPointF(6, 17))
        painter.drawLine(QPointF(6, 17), QPointF(6, 3))
    elif name == 'search':
        painter.drawEllipse(QRectF(3, 3, 10, 10))
        painter.drawLine(QPointF(12, 12), QPointF(17, 17))
    elif name == 'copy':
        painter.drawRoundedRect(QRectF(7, 7, 10, 10), 2, 2)
        painter.drawLine(QPointF(3, 12), QPointF(3, 3))
        painter.drawLine(QPointF(3, 3), QPointF(12, 3))
    elif name in ('pause', 'stop'):
        if name == 'stop':
            painter.drawRoundedRect(QRectF(4, 4, 12, 12), 2, 2)
        else:
            painter.drawLine(QPointF(7, 4), QPointF(7, 16))
            painter.drawLine(QPointF(13, 4), QPointF(13, 16))
    elif name == 'queue':
        for y in (5, 10, 15):
            painter.drawPoint(QPointF(3, y))
            painter.drawLine(QPointF(7, y), QPointF(18, y))
    elif name == 'proxy':
        painter.drawEllipse(QRectF(2, 2, 16, 16))
        painter.drawEllipse(QRectF(6, 2, 8, 16))
        painter.drawLine(QPointF(2, 10), QPointF(18, 10))
    elif name == 'history':
        painter.drawEllipse(QRectF(2, 2, 16, 16))
        painter.drawLine(QPointF(10, 5), QPointF(10, 10))
        painter.drawLine(QPointF(10, 10), QPointF(14, 12))
    elif name == 'settings':
        for y, x in ((5, 7), (10, 14), (15, 9)):
            painter.drawLine(QPointF(2, y), QPointF(18, y))
            painter.setBrush(QColor(COLORS[mode]['surface']))
            painter.drawEllipse(QPointF(x, y), 2.3, 2.3)
            painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.end()
    return QIcon(pixmap)


def apply_theme(app, mode):
    colors = COLORS.get(mode, COLORS['light'])
    palette = QPalette()
    for role, key in ((QPalette.ColorRole.Window, 'bg'), (QPalette.ColorRole.Base, 'surface'),
                      (QPalette.ColorRole.AlternateBase, 'raised'), (QPalette.ColorRole.Button, 'surface'),
                      (QPalette.ColorRole.WindowText, 'text'), (QPalette.ColorRole.Text, 'text'),
                      (QPalette.ColorRole.ButtonText, 'text'), (QPalette.ColorRole.Highlight, 'accent'),
                      (QPalette.ColorRole.PlaceholderText, 'muted'), (QPalette.ColorRole.ToolTipBase, 'raised'),
                      (QPalette.ColorRole.ToolTipText, 'text')):
        palette.setColor(role, QColor(colors[key]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors['on_primary']))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(colors['muted']))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(colors['muted']))
    app.setPalette(palette)
    c = colors
    app.setStyleSheet(f'''
        QWidget {{ color: {c['text']}; font-family: "Segoe UI"; font-size: 13px; }}
        QMainWindow, QDialog, QStackedWidget {{ background: {c['bg']}; }}
        QWidget#workspace {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c['ambient']}, stop:0.55 {c['bg']}, stop:1 {c['ambient_end']}); }}
        QStackedWidget {{ background: transparent; }}
        QWidget#sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
        QFrame#card, QFrame#transport {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
        QLabel#title {{ font-size: 25px; font-weight: 700; }}
        QLabel#section {{ font-size: 16px; font-weight: 600; }}
        QLabel#brand {{ font-size: 20px; font-weight: 700; color: {c['accent']}; }}
        QLabel#muted {{ color: {c['muted']}; }}
        QLabel#fieldError {{ color: {c['danger']}; }}
        QLabel#badge {{ color: {c['accent']}; background: {c['selection']}; padding: 6px 10px; border-radius: 8px; font-weight: 600; }}
        QLabel#eyebrow {{ color: {c['muted']}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
        QPushButton {{ background: {c['surface']}; border: 2px solid {c['border']}; border-radius: 8px; padding: 7px 12px; min-height: 18px; }}
        QPushButton:hover {{ background: {c['hover']}; border-color: {c['accent']}; }}
        QPushButton:pressed {{ background: {c['selection']}; }}
        QPushButton:disabled {{ color: {c['muted']}; border-color: {c['border']}; }}
        QPushButton#primary {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['accent']}, stop:1 {c['primary_end']}); color: {c['on_primary']}; border: 2px solid transparent; font-weight: 700; padding: 9px 18px; }}
        QPushButton#primary:hover {{ background: {c['accent']}; }}
        QPushButton#primary:pressed {{ background: {c['primary_end']}; }}
        QPushButton#primary:disabled {{ background: {c['border']}; color: {c['muted']}; }}
        QPushButton#danger {{ color: {c['danger']}; }}
        QPushButton#nav {{ text-align: left; background: transparent; border: 2px solid transparent; padding: 10px 8px; border-radius: 9px; font-weight: 500; }}
        QPushButton#nav:checked {{ background: {c['selection']}; color: {c['accent']}; font-weight: 700; }}
        QPushButton#nav:hover {{ background: {c['hover']}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
            background: {c['raised']}; border: 2px solid {c['border']}; border-radius: 8px; padding: 7px;
            selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
        QPushButton:focus, QPushButton#nav:focus, QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
        QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {c['accent']}; }}
        QPushButton#primary:focus {{ border-color: {c['text']}; }}
        QPlainTextEdit#editor {{ background: {c['surface']}; border: 2px solid transparent; border-radius: 8px; font-size: 15px; padding: 6px; }}
        QPlainTextEdit#editor:focus {{ border-color: {c['accent']}; }}
        QLineEdit[invalid="true"], QPlainTextEdit[invalid="true"] {{ border-color: {c['danger']}; }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']}; selection-background-color: {c['selection']}; outline: none; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 3px; min-height: 30px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QTableWidget, QTableView {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 9px; gridline-color: {c['border']}; selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
        QHeaderView::section {{ background: {c['raised']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']}; padding: 10px; font-weight: 600; }}
        QTableWidget::item, QTableView::item {{ padding: 7px; border-bottom: 1px solid {c['border']}; }}
        QProgressBar {{ background: {c['raised']}; border: none; border-radius: 4px; height: 7px; text-align: center; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
        QSlider::groove:horizontal {{ background: {c['border']}; height: 5px; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {c['accent']}; width: 12px; margin: -4px 0; border-radius: 6px; }}
        QSlider:focus {{ border: 2px solid {c['accent']}; border-radius: 5px; }}
        QTableWidget:focus, QTableView:focus {{ border: 2px solid {c['accent']}; }}
        QCheckBox {{ spacing: 8px; padding: 4px 0; }}
        QCheckBox:focus {{ outline: 2px solid {c['accent']}; }}
        QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 7px; }}
        QSplitter::handle {{ background: transparent; width: 12px; }}
        QTabWidget::pane {{ border: none; }}
        QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 9px 15px; border-bottom: 2px solid transparent; }}
        QTabBar::tab:selected {{ color: {c['accent']}; border-bottom-color: {c['accent']}; }}
    ''')
    return colors
