# -*- coding: utf-8 -*-
"""主题: 全局 QSS、调色板与卡片投影。

配色:
    --accent:#6F9C7D  --accent2:#5C8870  --bg:#F3F0EA  --card:#FDFCF9
    --line:#E6E0D6    --text:#0f172a     --muted:#7A756C

QSS 覆盖不到、需各自实现的部分:
  - 卡片投影 → QGraphicsDropShadowEffect(见 apply_shadow)
  - 动画/过渡 → QPropertyAnimation
  - 预览区叠加层(放大镜/间距标注) → PreviewBox 内用 QPainter 自绘
"""

import os

from core import app as core      # 资源目录只有一处定义(见 app.ASSETS_DIR: 冻结后靠 _MEIPASS 定位)

ACCENT = "#6F9C7D"
ACCENT2 = "#5C8870"
BG = "#F3F0EA"
CARD = "#FDFCF9"
LINE = "#E6E0D6"
TEXT = "#0f172a"
MUTED = "#7A756C"
# 「空 / 无」这类占位语义的中性灰(比边框色 LINE 深一档): 预览状态标识的「无预览」圆点使用
EMPTY_SLOT = "#C6BEB0"
# 空位格子的底色: 比窗口底色(BG)略浅、比卡片色(CARD)略深, 用于「我的颜色」的空位格
EMPTY_SLOT_BG = "#F1EDE5"
# 滚动条 handle 色: 比背景色与边框色都深一档, 便于定位; 仅在可滚动时显示
# (判定见 window._wire_scrollbar 的 emptyRange 属性)
SCROLL_HANDLE = "#C6BEB0"

# 图标目录取自核心: 冻结(PyInstaller)后 __file__ 不可靠, app.ICONS_DIR 已处理 _MEIPASS 层
_ASSETS = core.ICONS_DIR


def _asset(name):
    """QSS 的 url() 需要绝对路径(相对路径按进程 CWD 解析, 打包后会失效)"""
    return os.path.join(_ASSETS, name).replace("\\", "/")


# ---------- 视觉节奏 ----------
# 圆角与间距统一在此定义, 不在各处散写数字
RADIUS_CARD = 12      # 卡片、页签外层、取色板
RADIUS_CTRL = 8       # 输入框、下拉、普通按钮、胶囊
RADIUS_SMALL = 6      # 数值框两侧的 −/+ 与迷你按钮
CTRL_H = 28           # 按钮/输入类控件的统一高度

# ---- 全窗同一条左对齐线: 顶栏 / 页签行 / 卡片 / 底栏的内容都从这条线开始 ----
# 只有两段: 根布局 + 左列。左列的 10px 同时充当卡片投影的外扩余量(见 apply_shadow),
# 不额外叠加, 否则左侧会空出一大条。
INSET_ROOT = 8                           # 根布局(顶栏/主体/底栏)的横向内边距
INSET_COL = 10                           # 左列横向内边距(= 页面留白兼投影余量)
INSET_BAR = INSET_COL                    # 顶栏/底栏在根布局之内, 与左列内容对齐
PAD_CARD = 12         # 卡片内边距
GAP_CARD = 10         # 卡片与卡片之间
GAP_ROW = 4           # 卡片内相邻行之间
GAP_CTRL = 8          # 同一行内控件之间

ARROW_DOWN = _asset("arrow_down.svg")
# 开关(胶囊)素材: 28x16, 滑块在左=关、在右=开。
# 两个状态是两张静态图: QSS 的 image 不支持插值, 因此切换时滑块直接跳变, 无滑动动画;
# 需要滑动动画须改为自绘控件。
SWITCH_OFF = _asset("switch_off.svg")
SWITCH_ON = _asset("switch_on.svg")
SWITCH_OFF_HOVER = _asset("switch_off_hover.svg")
SWITCH_ON_HOVER = _asset("switch_on_hover.svg")
MENU_CHECK = _asset("menu_check.svg")   # 可勾选菜单项的勾号素材

# ---------- 界面图标(全部 16x16 SVG, 单色 #7A756C) ----------
# 图标统一采用 SVG: 由 Qt 按目标尺寸 x devicePixelRatio 栅格化, 各缩放下均清晰;
# 置灰态由 QIcon 的 Disabled 模式自动变淡, 无需单独素材。
# 约束: QIcon 不读取 QSS 的 `color`, 悬停时图标不会随文字一起变深。
MENU = _asset("menu.svg")      # 顶栏菜单
UNDO = _asset("undo.svg")      # 撤销
REDO = _asset("redo.svg")      # 重做
INFO = _asset("info.svg")      # 图片信息/EXIF
RESET = _asset("reset.svg")    # 重置本组 / 刷新预览效果
# 折叠区块的开合指示: 展开复用上方资源段的 ARROW_DOWN(与下拉框箭头同一张图), 收起用右向箭头。
ARROW_RIGHT = _asset("arrow_right.svg")

QSS = f"""
/* ---------- 基础 ----------
   背景只给顶层容器; 不得使用 QWidget 通配背景, 否则子容器会各自刷一层窗口底色。 */
QWidget {{
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 12px;
}}
QMainWindow, QDialog {{ background: {BG}; }}
/* 滚动区与其 viewport 透明, 露出页签的卡片底色 */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---------- 页签: 分段控件 ---------- */
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

/* ---------- 自绘标题栏弹窗的圆角底板(见 ui/dialog.py) ----------
   透明顶层窗口上 QSS 的 background 不绘制, 面板由垫在内容下面的子控件绘制。 */
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
    font-size: 13px;
    font-weight: bold;
    color: #334155;
    background: transparent;
}}
/* 卡片内部的功能组小标题: 比卡名低一档, 与组内的参数行同列起排。 */
QLabel#groupTitle {{
    font-size: 12px;
    font-weight: bold;
    color: #475569;
    background: transparent;
}}
/* 功能组之间的分割线(1px, 与卡片描边同色)。 */
QFrame#divider {{
    background: {LINE};
    border: none;
}}
QLabel#hint {{ color: {MUTED}; font-size: 11px; background: transparent; }}
QLabel#fieldLabel {{ background: transparent; }}
QLabel#muted {{ color: {MUTED}; background: transparent; }}
/* ---------- 「关于」弹窗 ----------
   字号必须写在此处: 全局 `QWidget {{ font-size: 12px }}` 优先于控件自身的 setFont。
   QSS 不支持字距(letter-spacing), 字距由代码 setFont 设置, 且须在弹窗抛光之后设置。 */
QLabel#aboutName {{ font-size: 22px; font-weight: 600; color: #2f4a38; background: transparent; }}
QLabel#aboutSub {{ font-size: 13px; color: {MUTED}; background: transparent; }}
QLabel#aboutVer {{ font-size: 13px; font-weight: bold; color: #334155; background: transparent; }}
QLabel#aboutInfo {{ font-size: 12px; color: {MUTED}; background: transparent; }}
QLabel#aboutNote {{ font-size: 11px; color: {MUTED}; background: transparent; }}

/* ---------- 按钮 ----------
   按钮无描边, 形状由圆角与底色构成; 中性按钮底色取 #EAE3D3, 在卡片与顶栏底色上均可辨识。 */
QPushButton {{
    background: #EAE3D3;
    border: none;
    border-radius: {RADIUS_CTRL}px;
    padding: 0 14px;          /* 竖向不设 padding: 高度由 CTRL_H 定, 文字由按钮自身居中 */
    color: {TEXT};
}}
/* 悬停与焦点同档, 按下再深一档: 两态不一致会使按钮停留在另一种样式上。 */
QPushButton:hover, QPushButton:focus {{ background: #DFD7C4; }}
QPushButton:pressed {{ background: #D4CBB6; }}
QPushButton:disabled {{ color: #b9b3a8; background: #F2EEE5; }}
/* 可勾选按钮作 pill 使用(处理模式的「单张图片 / 批量目录」) */
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
/* 入口按钮(顶栏「打开图片」): 以实心底色区别于同排的中性小按钮, 不加粗(加粗保留给 #primary)。
   底色 #c28b5e 配白字对比度 2.94:1, 低于 AA(4.5); 如需达标可压到 #9d6b42(4.55:1)。 */
QPushButton#entry {{ background: #c28b5e; color: #fff; }}
QPushButton#entry:hover, QPushButton#entry:focus {{ background: #b57f54; }}
QPushButton#entry:pressed {{ background: #a9754a; }}
/* :disabled 必须单独声明: #entry 带 ID, 特异性高于 QPushButton:disabled。 */
QPushButton#entry:disabled {{ background: #F2EEE5; color: #b9b3a8; }}
QPushButton#mini {{ padding: 0 8px; font-size: 11px; border-radius: {RADIUS_SMALL}px; color: {MUTED}; }}
/* 迷你按钮(重置本组 / 复制 / ☰ / EXIF信息): 悬停与按下态继承普通按钮, 不单独声明。 */
QPushButton#mini:hover, QPushButton#mini:focus {{ color: {TEXT}; }}
/* 带菜单的按钮(QPushButton.setMenu)右下角由 Qt 绘制下拉三角; 小图标按钮不需要, 予以关闭。 */
QPushButton#mini::menu-indicator {{ image: none; width: 0px; height: 0px; }}
/* 幽灵按钮: 无描边、无底色, 仅在悬停/按下时显示色块, 用于次要动作。
   :disabled 必须单独声明: #ghost 带 ID, 特异性高于 QPushButton:disabled, 否则置灰后的
   底色与字色会被上方规则覆盖。 */
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

/* ---------- 输入控件 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #fff;
    border: 1px solid {LINE};
    border-radius: {RADIUS_CTRL}px;
    padding: 3px 6px;
    selection-background-color: {ACCENT};
}}
QComboBox {{ padding-right: 22px; }}
/* 焦点态以底色变化表达, 不使用描边: 1 逻辑像素的边框在非整数缩放比下无法四边等宽
   (边框宽度取整会落在设备像素网格之外); 填充无宽度, 不存在取整问题。
   只读框的规则在下方单独覆盖: 其选择器特异性更高, 否则会被 [readOnly] 的白底覆盖。 */
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ background: #EEF2EC; }}
QLineEdit[readOnly="true"] {{ background: #faf8f4; color: {MUTED}; }}
QLineEdit[readOnly="true"]:focus {{ background: #EEF2EC; }}

/* 数值框使用 −/+ 步进按钮。
   QSpinBox 自带的上下箭头在 QSS 下不绘制, 且 Qt 不支持以 border 绘制三角形,
   因此只能使用图片素材或独立按钮。 */
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

/* ---------- 勾选开关(胶囊) ----------
   指示器由 SVG 素材提供, 以便同时控制轨道、滑块与悬停态。
   形态: 28x16 胶囊, 滑块在左=关、在右=开。所有勾选框统一使用这一套尺寸
   (参数开关、水印开关网格、批量目录、手动信息), 不在别处另写 ::indicator 的宽高。 */
QCheckBox {{ background: transparent; spacing: 7px; }}
QCheckBox::indicator {{ width: 28px; height: 16px; }}
QCheckBox::indicator:unchecked {{ image: url({SWITCH_OFF}); }}
QCheckBox::indicator:unchecked:hover {{ image: url({SWITCH_OFF_HOVER}); }}
QCheckBox::indicator:checked {{ image: url({SWITCH_ON}); }}
QCheckBox::indicator:checked:hover {{ image: url({SWITCH_ON_HOVER}); }}
/* 网格格位无文字标签, 需将 7px 文字间距归零, 否则开关左偏 3.5px 而与列头不对齐。 */
QCheckBox#cell {{ spacing: 0px; }}

/* ---------- 滚动条 ----------
   左列滚动条常驻(见 window._build_tab), 以保证卡片宽度不跳; 是否可见由滚动范围决定
   (window._wire_scrollbar 设置动态属性 emptyRange 并重新 polish):
     范围 0  -> handle 透明
     可滚动  -> 14px 轨道 / 手柄两侧各留 2px => 10px 圆头手柄; 悬停与按下使用主色。
   手柄尺寸由两个值控制: QScrollBar 的 width = 占位总宽(须与 window._SB_W 同步);
   QScrollBar::handle 的 margin: 0 Npx = 两侧缝隙, 手柄宽 = width - 2N。
   占位宽度始终保留(不用 AsNeeded), 因此卡片宽度不会跳。 */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 14px; }}
QScrollBar::handle:vertical {{ background: {SCROLL_HANDLE}; border-radius: 5px; min-height: 30px;
                               margin: 0 2px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {ACCENT}; }}
QScrollBar::handle:vertical[emptyRange="true"] {{ background: transparent; }}
/* add-page/sub-page 必须显式声明为透明: 否则仍由 Fusion 默认样式绘制, 会画出 1px 槽边框。 */
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

/* ---------- 分隔条 ---------- */
QSplitter::handle {{ background: transparent; width: 8px; }}

/* ---------- 菜单(顶栏 ☰) ----------
   覆盖原生样式以匹配全局配色; item 的 padding 必须显式给出, 否则左侧按默认图标宽度留白。
   此处不声明 border-radius: 菜单是弹出窗口, Qt 以背景色铺满整个矩形, 圆角对底色无效
   (需要圆角须让弹出窗口半透明并自绘)。 */
QMenu {{
    background: {CARD};
    border: 1px solid {LINE};
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 26px 6px 12px;      /* 右侧留给子菜单箭头 */
    border-radius: {RADIUS_SMALL}px;
    color: {TEXT};
}}
QMenu::item:selected {{ background: #EFF3EF; color: {TEXT}; }}   /* 与页签悬停同一档 */
QMenu::item:disabled {{ color: #b9b3a8; }}
/* 可勾选菜单项的勾号(「语言」子菜单): 使用自有素材替换 Fusion 默认图形。
   尺寸与默认指示器槽一致, 不改变文字位置。 */
QMenu::indicator {{ width: 14px; height: 14px; }}
QMenu::indicator:checked {{ image: url({MENU_CHECK}); }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 6px 8px; }}

/* ---------- 提示框 ----------
   深色底 + 白字。不声明 border-radius: 弹出窗口上 Qt 以背景色铺满整个矩形, 圆角对底色无效
   (需真圆角只能自绘提示框)。 */
QToolTip {{
    background: #334155;
    color: #fff;
    border: none;
    padding: 4px 8px;
}}
"""


def apply_shadow(widget, blur=12, dy=2, alpha=40):
    """卡片投影。使用暖灰而非纯黑: 米色底上纯黑会发灰显脏。

    约束:
      * 模糊半径不得大于卡片圆角(RADIUS_CARD): 超过后模糊会把圆角"糊平", 投影外缘近似直角;
      * 卡片左右必须留出投影余量(见 window._build_tab 的横向内边距), 否则投影会在滚动区
        视口边界被裁掉, 只剩上下两条, 同样近似直角。
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

    Windows 默认使用 full hinting, 笔画被吸附到像素网格, 中文小字号边缘偏硬;
    PreferNoHinting 配合 PreferAntialias 可获得更平滑的小字号观感。
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
    """把主色写入调色板: 列表选中、焦点框等原生绘制细节取自 Highlight, QSS 覆盖不到。
    (复选框指示器不在此列, 必须提供 SVG 素材)"""
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
