# -*- coding: utf-8 -*-
"""主题: 全局 QSS、调色板与卡片投影。"""

import os

from core.config import FONT_SETTING_MAP, ICONS_DIR
from core.paths import _default_font_path

ACCENT = "#6F9C7D"
ACCENT2 = "#5C8870"
BG = "#F3F0EA"
CARD = "#FDFCF9"
LINE = "#E6E0D6"
TEXT = "#0f172a"
MUTED = "#7A756C"
EMPTY_SLOT = "#C6BEB0"        # 占位语义的中性灰: 预览状态标识的「无预览」圆点使用
EMPTY_SLOT_BG = "#F1EDE5"     # 「我的颜色」空位格的底色: 比窗口底色 BG 浅、比卡片色 CARD 深
SCROLL_HANDLE = "#C6BEB0"     # 滚动条 handle 色: 仅在可滚动时显示(见 window._wire_scrollbar)

_ASSETS = ICONS_DIR


# 素材文件名拼成绝对路径, 供 QSS 的 url() 使用
def _asset(name):
    return os.path.join(_ASSETS, name).replace("\\", "/")


# 圆角与间距统一在此定义, 不在各处散写数字
RADIUS_CARD = 12      # 卡片、页签外层、取色板
RADIUS_CTRL = 8       # 输入框、下拉、普通按钮、胶囊
RADIUS_SMALL = 6      # 数值框两侧的 −/+ 与迷你按钮
CTRL_H = 28           # 按钮/输入类控件的统一高度

INSET_ROOT = 8                           # 根布局(顶栏/主体/底栏)的横向内边距
INSET_COL = 10                           # 左列横向内边距(= 页面留白兼投影余量)
INSET_BAR = INSET_COL                    # 顶栏/底栏在根布局之内, 与左列内容对齐
PAD_CARD = 14         # 卡片内边距
GAP_CARD = 12         # 卡片与卡片之间
GAP_ROW = 8           # 卡片内相邻行之间
GAP_CTRL = 8          # 同一行内控件之间

ARROW_DOWN = _asset("arrow_down.svg")
# 开关(胶囊)素材: 28x16, 滑块在左=关、在右=开; 所有勾选框共用这一套 ::indicator 尺寸
# 两态是两张静态图: QSS 的 image 不支持插值, 无滑动动画(要动画须改为自绘控件)
SWITCH_OFF = _asset("switch_off.svg")
SWITCH_ON = _asset("switch_on.svg")
SWITCH_OFF_HOVER = _asset("switch_off_hover.svg")
SWITCH_ON_HOVER = _asset("switch_on_hover.svg")
MENU_CHECK = _asset("menu_check.svg")   # 可勾选菜单项的勾号素材

# 界面图标: 全部 16x16 SVG, 单色 #7A756C
# 置灰由 QIcon 的 Disabled 态自动变淡, 无需单独素材; QIcon 不读取 QSS 的 color, 悬停时图标不随文字变深
MENU = _asset("menu.svg")      # 顶栏菜单
UNDO = _asset("undo.svg")      # 撤销
REDO = _asset("redo.svg")      # 重做
INFO = _asset("info.svg")      # 图片信息/EXIF
RESET = _asset("reset.svg")    # 刷新预览效果
RESET_SOFT = _asset("reset_soft.svg")   # 参数行行尾 ↺(每行一个, 用更浅的填充)
# 折叠区块的开合指示: 展开复用上方资源段的 ARROW_DOWN(与下拉框箭头同一张图), 收起用右向箭头
ARROW_RIGHT = _asset("arrow_right.svg")
# 自绘标题栏弹窗的关闭按钮
CLOSE = _asset("close.svg")
# 数值框的 ± 步进按钮: 成对出素材
STEP_MINUS = _asset("step_minus.svg")
STEP_PLUS = _asset("step_plus.svg")
# 色块选中态的勾号: 浅底与深底各一份, 勾形与 MENU_CHECK 相同(全程序只有一种勾)
CHIP_CHECK_DARK = _asset("chip_check_dark.svg")
CHIP_CHECK_LIGHT = _asset("chip_check_light.svg")

# 字体族优先级: 内置字体在前, 其后各平台常见中文字体, 最后 sans-serif(Qt 按列表逐字回退)
FONT_FAMILIES = ("Sarasa Gothic SC", "Microsoft YaHei UI", "Microsoft YaHei",
                 "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC",
                 "Source Han Sans SC", "WenQuanYi Micro Hei", "Segoe UI")
_QSS_FAMILIES = ", ".join(f'"{name}"' for name in FONT_FAMILIES) + ", sans-serif"

QSS = f"""
QWidget {{
    color: {TEXT};
    font-family: {_QSS_FAMILIES};
    font-size: 12px;
}}
QMainWindow, QDialog {{ background: {BG}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

QFrame#segbar {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: {RADIUS_CARD}px;
}}
QPushButton#seg {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_CTRL}px;
    padding: 0 18px;
    color: {TEXT};
}}
QPushButton#seg:hover {{ background: #EFF3EF; }}
QPushButton#seg:checked {{
    background: {ACCENT};
    color: #fff;
    font-weight: bold;
}}
QPushButton#seg:checked:hover {{ background: {ACCENT2}; }}

QFrame#csdPanel {{
    background: {BG};
    border: 1px solid {LINE};
    border-radius: {RADIUS_CARD}px;
}}

QFrame#card {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#pickerCard {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: {RADIUS_CARD}px;
}}
QLabel#cardTitle {{
    font-size: 13px;
    font-weight: bold;
    color: #334155;
    background: transparent;
}}
QLabel#groupTitle {{
    font-size: 12px;
    font-weight: bold;
    color: #475569;
    background: transparent;
}}
QFrame#divider {{
    background: {LINE};
    border: none;
}}
QLabel#hint {{ color: {MUTED}; font-size: 11px; background: transparent; }}
QLabel#fieldLabel {{ background: transparent; }}
QLabel#muted {{ color: {MUTED}; background: transparent; }}
QLabel#aboutName {{ font-size: 22px; font-weight: 600; color: #2f4a38; background: transparent; }}
QLabel#aboutSub {{ font-size: 13px; color: {MUTED}; background: transparent; }}
QLabel#aboutVer {{ font-size: 13px; font-weight: bold; color: #334155; background: transparent; }}
QLabel#aboutInfo {{ font-size: 12px; color: {MUTED}; background: transparent; }}
QLabel#aboutNote {{ font-size: 11px; color: {MUTED}; background: transparent; }}

QPushButton {{
    background: #EAE3D3;
    border: none;
    border-radius: {RADIUS_CTRL}px;
    padding: 0 14px;
    color: {TEXT};
}}
QPushButton:hover, QPushButton:focus {{ background: #DFD7C4; }}
QPushButton:pressed {{ background: #D4CBB6; }}
QPushButton:disabled {{ color: #b9b3a8; background: #F2EEE5; }}
QPushButton:checked {{
    background: {ACCENT};
    color: #fff;
    font-weight: bold;
}}
QPushButton:checked:hover, QPushButton:checked:focus {{ background: {ACCENT2}; }}
QPushButton#primary {{
    background: {ACCENT};
    color: #fff;
    font-weight: bold;
    padding: 0 20px;
}}
QPushButton#primary:hover, QPushButton#primary:focus {{ background: {ACCENT2}; }}
QPushButton#primary:disabled {{ background: #b8c9bd; color: #f0f0f0; }}
QPushButton#entry {{ background: #c28b5e; color: #fff; }}
QPushButton#entry:hover, QPushButton#entry:focus {{ background: #b57f54; }}
QPushButton#entry:pressed {{ background: #a9754a; }}
QPushButton#entry:disabled {{ background: #F2EEE5; color: #b9b3a8; }}
QPushButton#mini {{ padding: 0 8px; font-size: 11px; border-radius: {RADIUS_SMALL}px; color: {MUTED}; }}
QPushButton#mini:hover, QPushButton#mini:focus {{ color: {TEXT}; }}
QPushButton#mini::menu-indicator {{ image: none; width: 0px; height: 0px; }}
QPushButton#ghost {{
    background: transparent;
    border: none;
    padding: 0 8px;
    font-size: 11px;
    border-radius: {RADIUS_SMALL}px;
    color: {MUTED};
}}
QPushButton#ghost:hover, QPushButton#ghost:focus {{ background: #EEF2EC; color: {TEXT}; }}
QPushButton#ghost:pressed {{ background: #E2E9E1; }}
QPushButton#ghost:disabled {{ background: transparent; color: #b9b3a8; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #fff;
    border: 1px solid {LINE};
    border-radius: {RADIUS_CTRL}px;
    padding: 3px 6px;
    selection-background-color: {ACCENT};
}}
QComboBox {{ padding-right: 22px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ background: #EEF2EC; }}
QLineEdit[readOnly="true"] {{ background: #faf8f4; color: {MUTED}; }}
QLineEdit[readOnly="true"]:focus {{ background: #EEF2EC; }}

QPushButton#step {{
    min-width: 24px;
    max-width: 24px;
    padding: 0;
    border-radius: {RADIUS_SMALL}px;
    font-weight: bold;
    color: {MUTED};
}}

QComboBox::drop-down {{ border: none; width: 20px; background: transparent; }}
QComboBox::down-arrow {{ image: url({ARROW_DOWN}); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{
    background: #fff;
    border: 1px solid {LINE};
    selection-background-color: {ACCENT};
    selection-color: #fff;
}}

QSlider::groove:horizontal {{
    height: 6px;
    background: {LINE};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: #fff;
    border: 2px solid {ACCENT};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}

QCheckBox {{ background: transparent; spacing: 7px; }}
QCheckBox::indicator {{ width: 28px; height: 16px; }}
QCheckBox::indicator:unchecked {{ image: url({SWITCH_OFF}); }}
QCheckBox::indicator:unchecked:hover {{ image: url({SWITCH_OFF_HOVER}); }}
QCheckBox::indicator:checked {{ image: url({SWITCH_ON}); }}
QCheckBox::indicator:checked:hover {{ image: url({SWITCH_ON_HOVER}); }}
QCheckBox#cell {{ spacing: 0px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 14px; }}
QScrollBar::handle:vertical {{ background: {SCROLL_HANDLE}; border-radius: 5px; min-height: 30px;
                               margin: 0 2px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {ACCENT}; }}
QScrollBar::handle:vertical[emptyRange="true"] {{ background: transparent; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical,
QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal {{
    background: none; border: none; width: 0; height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 14px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL_HANDLE}; border-radius: 5px; min-width: 30px;
                                 margin: 2px 0; }}
QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {{ background: {ACCENT}; }}
QScrollBar::handle:horizontal[emptyRange="true"] {{ background: transparent; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QProgressBar {{
    background: {LINE};
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

QSplitter::handle {{ background: transparent; width: 8px; }}

QMenu {{
    background: {CARD};
    border: 1px solid {LINE};
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 26px 6px 12px;
    border-radius: {RADIUS_SMALL}px;
    color: {TEXT};
}}
QMenu::item:selected {{ background: #EFF3EF; color: {TEXT}; }}
QMenu::item:disabled {{ color: #b9b3a8; }}
QMenu::indicator {{ width: 14px; height: 14px; }}
QMenu::indicator:checked {{ image: url({MENU_CHECK}); }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 6px 8px; }}

QToolTip {{
    background: #334155;
    color: #fff;
    border: none;
    padding: 4px 8px;
}}
"""


# 卡片投影: 用暖灰而非纯黑(米色底上纯黑会发灰显脏)
def apply_shadow(widget, blur=12, dy=2, alpha=40):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(62, 52, 38, alpha))
    widget.setGraphicsEffect(eff)


# 把随包分发的内置字体注册给 Qt, 返回成功注册的字体 id 列表
def load_bundled_fonts():
    from PySide6.QtGui import QFontDatabase

    ids = []
    for _key, fname in FONT_SETTING_MAP:
        path = _default_font_path(fname)
        if not path:
            continue
        fid = QFontDatabase.addApplicationFont(path)
        if fid >= 0:
            ids.append(fid)
    return ids


# 装配界面字体: 注册内置字体, 按 FONT_FAMILIES 定字体族
def apply_font(app):
    from PySide6.QtGui import QFont

    load_bundled_fonts()
    f = QFont()
    # 传整个列表: 内置字体是子集, 子集外的字符由列表里其余字体逐字回退
    f.setFamilies(list(FONT_FAMILIES))
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(f)


# 把主色写入调色板: 列表选中、焦点框等原生绘制细节取自 Highlight(QSS 覆盖不到)
def apply_palette(app):
    from PySide6.QtGui import QColor, QPalette

    pal = app.palette()
    accent = QColor(ACCENT)
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
