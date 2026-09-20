# -*- coding: utf-8 -*-
"""主题: 全局 QSS 与调色板。

全局配色:
    --accent:#6F9C7D  --accent2:#5C8870  --bg:#F3F0EA  --card:#FDFCF9
    --line:#E6E0D6    --text:#0f172a     --muted:#7A756C

QSS 能覆盖的部分: 底色/边框/圆角/字体/悬停态/滑块与滚动条。
覆盖不了、需要各自实现的部分(后续按需补):
  - 卡片投影 → QGraphicsDropShadowEffect
  - 预览区叠加层(放大镜/间距标注) → 已在 PreviewBox 里用 QPainter 自绘
"""

import os

ACCENT = "#6F9C7D"
ACCENT2 = "#5C8870"
BG = "#F3F0EA"
CARD = "#FDFCF9"
LINE = "#E6E0D6"
TEXT = "#0f172a"
MUTED = "#7A756C"
EMPTY_SLOT = "#C6BEB0"   # 空位虚线框: 比边框色(LINE)深一档, 否则在卡片上看不出有多少个空位
WARN_BG = "#FDF6E3"

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _asset(name):
    """QSS 的 url() 需要绝对路径(相对路径按进程 CWD 解析, 打包后会失效)"""
    return os.path.join(_ASSETS, name).replace("\\", "/")


# ---------- 视觉节奏 ----------
# 圆角与间距统一在这里定义, 别在各处散写数字 —— 之前 12/8/6 与 10/8/4 混着用, 看着发乱
RADIUS_CARD = 12      # 卡片、页签外层、取色板
RADIUS_CTRL = 8       # 输入框、下拉、普通按钮、胶囊
RADIUS_SMALL = 6      # 数值框两侧的 −/+ 与迷你按钮
CTRL_H = 28           # 按钮/输入类控件的统一高度(原来普通 29 / 迷你 22 / 步进 23 三种, 看着乱)

# ---- 全窗同一条左对齐线: 顶栏 / 页签行 / 卡片 / 底栏 / 状态栏的内容都从这条线开始 ----
# 只有两段: 根布局 + 左列。左列那 10px 同时充当卡片投影的外扩余量(见 apply_shadow),
# 不要再单独叠一层 —— 叠起来左边会空出一大条。
INSET_ROOT = 8                           # 根布局(顶栏/主体/底栏)的横向内边距
INSET_COL = 10                           # 左列横向内边距(= 页面留白兼投影余量)
INSET_BAR = INSET_COL                    # 顶栏/底栏在根布局之内, 与左列内容对齐
INSET_STATUS = INSET_ROOT + INSET_BAR    # 状态栏在根布局之外(贴窗口边), 要把根布局那段也算上
PAD_CARD = 12         # 卡片内边距
GAP_CARD = 10         # 卡片与卡片之间
GAP_ROW = 4           # 卡片内相邻行之间
GAP_CTRL = 8          # 同一行内控件之间

ARROW_DOWN = _asset("arrow_down.svg")
ARROW_UP = _asset("arrow_up.svg")
CHECK_OFF = _asset("check_off.svg")
CHECK_ON = _asset("check_on.svg")
CHECK_OFF_HOVER = _asset("check_off_hover.svg")
CHECK_ON_HOVER = _asset("check_on_hover.svg")

QSS = f"""
/* ---------- 基础 ----------
   背景只给顶层容器。**不要**写 QWidget 通配背景: 那会让每个子容器(步进器分组、
   参数行等)都刷一层窗口底色, 在白色卡片里表现为"控件背后多出一块印子" */
QWidget {{
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {BG}; }}
/* 滚动区与其 viewport 透明, 露出页签的卡片底色 */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---------- 页签: 分段控件() ----------
   不用 QTabWidget: 它的 tab 条与 pane 是两套几何(直角 tab + 圆角 pane, 接缝对不齐),
   且 tab 条右侧空白由样式的 Base 色填充, 会露出一块与主题不符的纯白。 */
QFrame#segbar {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: {RADIUS_CARD}px;
}}
QPushButton#seg {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_CTRL}px;
    padding: 0 18px;
    color: {TEXT};
}}
QPushButton#seg:hover {{ background: #EFF3EF; }}
QPushButton#seg:checked {{
    background: {ACCENT};
    border-color: {ACCENT2};
    color: #fff;
    font-weight: bold;
}}
QPushButton#seg:checked:hover {{ background: {ACCENT2}; }}

/* ---------- 自绘标题栏弹窗的圆角底板(见 xsy_qt/dialog.py) ----------
   无边框 + 透明背景的顶层窗口上 QSS 的 background 不画, 所以窗口的"面板"由一个
   垫在内容下面的子控件画。配色/圆角与卡片同源, 只是它扮演的是"窗口表面"的角色。 */
QFrame#csdPanel {{
    background: {BG};
    border: 1px solid {LINE};
    border-radius: {RADIUS_CARD}px;
}}

/* ---------- 卡片 ---------- */
QFrame#card {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: {RADIUS_CARD}px;
}}
QLabel#cardTitle {{
    font-size: 14px;
    font-weight: bold;
    color: #334155;
    background: transparent;
}}
QLabel#hint {{ color: {MUTED}; font-size: 12px; background: transparent; }}
QLabel#fieldLabel {{ background: transparent; }}
QLabel#muted {{ color: {MUTED}; background: transparent; }}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: {RADIUS_CTRL}px;
    padding: 0 14px;          /* 竖向不设 padding: 高度由 CTRL_H 定, 文字由按钮自身居中 */
    color: {TEXT};
}}
QPushButton:hover {{ background: #f1f5f9; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:disabled {{ color: #b9b3a8; background: #f6f3ec; border-color: {LINE}; }}
/* 可勾选按钮当 pill 用(处理模式的「单张图片 / 批量目录」) */
QPushButton:checked {{
    background: {ACCENT};
    border-color: {ACCENT2};
    color: #fff;
    font-weight: bold;
}}
QPushButton:checked:hover {{ background: {ACCENT2}; }}
QPushButton#primary {{
    background: {ACCENT};
    border-color: {ACCENT2};
    color: #fff;
    font-weight: bold;
    padding: 0 20px;
}}
QPushButton#primary:hover {{ background: {ACCENT2}; }}
QPushButton#primary:disabled {{ background: #b8c9bd; border-color: #a8b9ad; color: #f0f0f0; }}
QPushButton#mini {{ padding: 0 8px; font-size: 12px; border-radius: {RADIUS_SMALL}px; color: {MUTED}; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #fff;
    border: 1px solid {LINE};
    border-radius: {RADIUS_CTRL}px;
    padding: 3px 6px;
    selection-background-color: {ACCENT};
}}
QComboBox {{ padding-right: 22px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QLineEdit[readOnly="true"] {{ background: #faf8f4; color: {MUTED}; }}

/* 数值框用 −/+ 步进按钮()。
   不用 QSpinBox 自带的上下箭头: 在 QSS 下 Qt 既不画原生箭头, 也不支持 CSS 那种
   "用 border 画三角" 的写法(实测渲染成实心方块), 只能上图片素材 —— 按钮更省事,
   点击区域也更大。 */
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

/* ---------- 滑块 ---------- */
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

/* ---------- 复选框 ----------
   指示器用 SVG 素材自绘。原来靠 Fusion 原生绘制 + 调色板 Highlight, 结果是
   "未勾选只有一圈极淡的细框、勾选后只剩一个孤零零的对勾(方框整个不见)",
   在白卡片上根本看不出是个可勾选项。素材方案能同时控制方框、填充与对勾。 */
QCheckBox {{ background: transparent; spacing: 7px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked {{ image: url({CHECK_OFF}); }}
QCheckBox::indicator:unchecked:hover {{ image: url({CHECK_OFF_HOVER}); }}
QCheckBox::indicator:checked {{ image: url({CHECK_ON}); }}
QCheckBox::indicator:checked:hover {{ image: url({CHECK_ON_HOVER}); }}

/* ---------- 滚动条 ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #d6cfc2; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #d6cfc2; border-radius: 5px; min-width: 30px; }}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background: {LINE};
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

/* ---------- 状态栏 ---------- */
QStatusBar {{
    background: {CARD};
    border-top: 1px solid {LINE};
    color: {MUTED};
}}
QStatusBar::item {{ border: none; }}
QLabel#statusText {{ color: {MUTED}; background: transparent; }}

/* ---------- 分隔条 ---------- */
QSplitter::handle {{ background: transparent; width: 8px; }}

/* ---------- 提示框 ---------- */
QToolTip {{
    background: #334155;
    color: #fff;
    border: none;
    padding: 4px 8px;
    border-radius: 4px;
}}

/* ---------- 分组小标题(卡片内) ---------- */
QLabel#groupLabel {{
    color: {MUTED};
    font-weight: bold;
    background: transparent;
    padding-top: 4px;
}}
"""


def apply_shadow(widget, blur=12, dy=2, alpha=40):
    """卡片投影。用暖灰而不是纯黑 —— 米色底上纯黑会发灰显脏。

    模糊半径别大于卡片圆角(RADIUS_CARD=12): 超过之后模糊会把 12px 的圆角"糊平",
    投影外缘看着像直角。取 12 与圆角相当, 投影才会跟着拐角走。

    另外卡片左右必须留出投影余量(见 window._build_tab 的横向内边距) —— 否则投影
    在滚动区视口边界被裁掉, 只剩上下两条, 更像方角。

    实测: 16 张卡片时整窗重绘 6ms -> 11ms, 可接受; 且只在整窗重绘时付费,
    预览刷新只重绘 PreviewBox, 不受影响。
    """
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(62, 52, 38, alpha))
    widget.setGraphicsEffect(eff)


def apply_font(app):
    """显式指定字体族并关闭栅格化对齐。

    Windows 默认走 full hinting —— 笔画被吸附到像素网格, 中文小字号会显得"硬"。
    浏览器用的是亚像素定位 + 轻微 hinting, 观感更细腻; PreferNoHinting 更接近它。
    """
    from PySide6.QtGui import QFont, QFontDatabase

    fams = QFontDatabase.families()
    f = QFont()
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
        if name in fams:
            f.setFamily(name)
            break
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(f)


def apply_palette(app):
    """把主色写进调色板: 列表选中、焦点框等原生绘制的细节取 Highlight, QSS 覆盖不到。
    (复选框的指示器不在其中 —— 那个必须自己给 SVG 素材, 见 QSS 里的注释)"""
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
