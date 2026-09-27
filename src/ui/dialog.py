# -*- coding: utf-8 -*-
"""自绘标题栏弹窗(无边框 + 自绘标题行), 供 window.py 复用。

Qt 6.11 + Windows 11 下, DWM 绘制的非客户区(标题栏与边框)与客户区内容在弹窗首次显示时
合成不同步, 会出现一两帧错位/漂移。因此采用无边框 + 自绘标题栏: 既没有 DWM 边框可错位,
观感也与项目卡片语言一致。

实现约束:
  * `go_frameless()` 必须在窗口被创建**之前**调用, 否则 Qt 沿用建窗时算出的边框尺寸, 之后
    `move()` 会整体偏一个标题栏高度(约 31px);
  * 透明顶层窗口上 QSS 的 `background` 不绘制, 因此圆角底板与标题行做成窗口的子控件
    (`QFrame#csdPanel`, 见 theme.QSS), 两者都不参与布局;
  * 不移动原有布局中的任何控件(仅调整内边距): 搬运布局项会触发 PySide 的 QLayoutItem
    所有权问题 —— `takeAt` 取出的 item 归 Python 所有, GC 时会删除布局仍在使用的
    QSpacerItem, 之后任意一次 GC 都会导致进程 abort。
"""
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from . import theme

DLG_PAD = theme.RADIUS_CARD    # 窗口四周留给投影的余量(= 卡片投影的模糊半径, 避免被裁)
TITLE_H = theme.CTRL_H         # 自绘标题行的高度(与其它控件取齐)
CLOSE_ICON = 12                # 关闭按钮素材的边长: #mini 的内边距两侧各 8px, 恰好放下 12px


def go_frameless(dlg):
    """在窗口被创建之前设置无边框与透明背景(顺序要求见模块说明)。"""
    dlg.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class _TitleBar(QWidget):
    """自绘标题行: 空白处按住可拖动整个窗口。

    用 `QWindow.startSystemMove()` 走系统实现, 可顺带获得跨屏/贴边等系统行为, 也无需自行
    按 DPI 换算鼠标位移。
    """

    def __init__(self, dlg, parent=None):
        super().__init__(parent)
        self._dlg = dlg
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, e):                  # noqa: N802 - Qt 命名
        if e.button() == Qt.MouseButton.LeftButton:
            try:
                wh = self._dlg.windowHandle()
                if wh is not None and wh.startSystemMove():
                    return
            except Exception:                      # noqa: BLE001 - 拖不动时退回默认处理
                pass
        super().mousePressEvent(e)


class _ChromePlacer(QObject):
    """尺寸变化时把底板与标题行摆到正确位置(两者都是窗口的子控件, 不参与布局)"""

    def __init__(self, dlg, panel, bar, left, right, top):
        super().__init__(dlg)
        self._dlg, self._panel, self._bar = dlg, panel, bar
        self._left, self._right, self._top = left, right, top

    def eventFilter(self, obj, ev):                # noqa: N802 - Qt 命名
        if ev.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest):
            self.place()
        return False

    def place(self):
        try:
            d = self._dlg
            w, h = d.width(), d.height()
            self._panel.setGeometry(DLG_PAD, DLG_PAD,
                                    max(0, w - 2 * DLG_PAD), max(0, h - 2 * DLG_PAD))
            self._bar.setGeometry(self._left, self._top,
                                  max(0, w - self._left - self._right), TITLE_H)
        except RuntimeError:                       # 控件已析构
            pass


def csdify(dlg, title, recenter):
    """把已建好的 QDialog 改为无边框 + 自绘标题栏(见模块说明)。

    `recenter(dlg)` 由调用方传入(通常是 `window._prep_dialog`): 尺寸变化后需重新定死尺寸与
    位置。传函数而非在模块内 import, 以使本模块只单向依赖 theme。
    """
    if getattr(dlg, "_csd_done", False):           # 避免重复包装(会多出一条标题栏)
        return None
    dlg._csd_done = True

    dlg.setObjectName("csdDialog")
    # 窗口自身必须透明: 否则 QDialog 的窗口底色会把圆角填成方块
    dlg.setStyleSheet("QDialog#csdDialog { background: transparent; }")

    root = dlg.layout()
    m = root.contentsMargins()
    left = max(m.left(), theme.INSET_COL) + DLG_PAD
    right = max(m.right(), theme.INSET_COL) + DLG_PAD
    bottom = max(m.bottom(), theme.INSET_COL) + DLG_PAD
    top = DLG_PAD + theme.GAP_ROW
    root.setContentsMargins(left, top + TITLE_H + theme.GAP_ROW, right, bottom)

    panel = QFrame(dlg)                            # 圆角底板: 垫在内容下面
    panel.setObjectName("csdPanel")
    theme.apply_shadow(panel)
    panel.lower()

    bar = _TitleBar(dlg, dlg)                      # 自绘标题行
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(theme.GAP_CTRL)
    lab = QLabel(title, bar)
    lab.setObjectName("cardTitle")
    lay.addWidget(lab)
    lay.addStretch(1)
    # 关闭按钮用素材而不是文字符号 ✕(U+2715): 界面字体是内置子集, 不含该码位。
    # 此处不借用 window._iconify(会与 window 形成循环导入), 素材仍取自 theme 的「界面图标」段。
    close = QPushButton(bar)
    close.setObjectName("mini")
    close.setIcon(QIcon(theme.CLOSE))
    close.setIconSize(QSize(CLOSE_ICON, CLOSE_ICON))
    close.setFixedSize(TITLE_H, TITLE_H)
    close.setCursor(Qt.CursorShape.PointingHandCursor)
    close.setToolTip(QCoreApplication.translate("dialog", "关闭"))
    close.setAccessibleName(QCoreApplication.translate("dialog", "关闭"))
    close.clicked.connect(dlg.reject)
    lay.addWidget(close)

    placer = _ChromePlacer(dlg, panel, bar, left, right, top)
    dlg.installEventFilter(placer)
    panel.show()
    bar.show()
    placer.place()
    # 保留引用(它们没有父布局, 只挂在窗口上)
    dlg._csd_panel, dlg._csd_bar, dlg._csd_placer = panel, bar, placer

    recenter(dlg)
    return panel


# 标准按钮的文案: 使用时才取译文, 不能在 import 期建表 —— 那时翻译器尚未安装(见 i18n.install)
_BTN_TEXT = {
    QMessageBox.StandardButton.Ok: "确定",
    QMessageBox.StandardButton.Yes: "是",
    QMessageBox.StandardButton.No: "否",
}


# 弹窗按钮的最小宽度: 短标签(是/否/关闭)不会因 #primary 的横向内边距更大而宽窄不一
DLG_BTN_MIN_W = 76


def dlg_button(text, slot=None, primary=False, parent=None):
    """自绘弹窗统一使用的按钮。

    样式(高度 CTRL_H / 主按钮 #primary / 手型光标)集中于此, 保证各弹窗一致; 标题栏的 ✕
    不走本函数(它是弹窗壳 `csdify` 的一部分)。
    """
    b = QPushButton(text, parent)
    if primary:
        b.setObjectName("primary")
    b.setFixedHeight(theme.CTRL_H)
    b.setMinimumWidth(DLG_BTN_MIN_W)          # 短标签与长标签宽度不参差
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot is not None:
        b.clicked.connect(slot)
    return b


def dlg_button_row(*buttons):
    """把按钮排成弹窗底部那一行: 右对齐、统一间距。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.GAP_CTRL)
    row.addStretch(1)
    for b in buttons:
        row.addWidget(b)
    return row


def _btn_text(btn):
    return QCoreApplication.translate("dialog", _BTN_TEXT[btn])


class MessageDialog(QDialog):
    """自绘标题栏的消息框, 替代 QMessageBox, 与其它自绘弹窗保持同一套设计语言。

    返回值遵守原 `_message` 的约定: 返回被点中的 `QMessageBox.StandardButton`,
    调用方仍以 `!= QMessageBox.StandardButton.Yes` 比较。
    """

    def __init__(self, parent, icon, title, text,
                 buttons=QMessageBox.StandardButton.Ok, default=None, recenter=None):
        super().__init__(parent)
        go_frameless(self)                         # 必须在建窗之前
        self.result_button = default or QMessageBox.StandardButton.Ok
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD)
        root.setSpacing(theme.GAP_CARD)
        msg = QLabel(str(text))
        msg.setWordWrap(True)
        msg.setMinimumWidth(280)
        root.addWidget(msg)
        row = QHBoxLayout()
        row.setSpacing(theme.GAP_CTRL)
        row.addStretch(1)
        for b in (QMessageBox.StandardButton.Ok, QMessageBox.StandardButton.Yes,
                  QMessageBox.StandardButton.No):
            if not (buttons & b):
                continue
            btn = dlg_button(_btn_text(b), primary=(b == self.result_button))
            btn.clicked.connect(lambda _c, bb=b: self._pick(bb))
            row.addWidget(btn)
        root.addLayout(row)
        if recenter is not None:
            csdify(self, title or QCoreApplication.translate("dialog", "提示"), recenter)

    def _pick(self, b):
        self.result_button = b
        self.accept()
