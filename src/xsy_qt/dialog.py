# -*- coding: utf-8 -*-
"""自绘标题栏弹窗(无边框 + 自己画标题栏), 供 window.py 里的弹窗复用。

**为什么需要它**(实测, 2026-09-20, Qt 6.11.2 / Windows 11 26100):
弹窗第一次显示时, DWM 会把它画的"非客户区(标题栏+边框)"与客户区内容**合成得不同步**,
肉眼就是一两帧的错位/漂移, 而且会把窗口框闪在屏幕左上角。逐项排除后:
  * 只有"让 DWM 不画边框"才消除 —— `DWMWA_NCRENDERING_POLICY=DISABLED` 有效但会退回
    Win7 经典边框(观感不可接受); `FramelessWindowHint` 也有效, 但窗口就没有标题栏了。
  * Qt 每次 show 都会重建原生窗口, 所以"等渲染完成再显示"这类做法无效(实测: 把窗口先
    显示在屏幕外、或先全透明再恢复不透明度, 都仍然错位 —— 触发点是"第一次可见"本身)。
  * `_q_titlebar`(Qt 平台插件为每个顶层窗口建的辅助窗, 位于屏幕原点、从不可见)只是同时刻
    出现的一个标记物, 不是看到的东西。
结论: 无边框 + 自绘标题栏 —— 既没有 DWM 画的边框可错位, 观感又能沿用项目自己的卡片语言。

**实现要点(都踩过坑)**:
  * `go_frameless()` 必须在**窗口被创建之前**调用。否则 Qt 沿用建窗时算出的边框尺寸,
    之后 `move()` 会整体偏一个标题栏高度(实测 31px)。
  * 透明顶层窗口上 QSS 的 `background` 不画, 所以圆角底板是"垫在内容下面的子控件"
    (`QFrame#csdPanel`, 见 theme.QSS), 标题行同理做成窗口的子控件, 两者都不参与布局。
  * 不动原有布局里的任何控件(只改内边距): 早先"把布局项搬进外壳"的写法会踩 PySide 的
    QLayoutItem 所有权陷阱 —— `takeAt` 拿到的 item 归 Python, GC 时把布局仍在用的
    QSpacerItem 一起删掉, 之后随便一次 GC 就 abort(实测 exit 127)。
"""
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from . import theme

DLG_PAD = theme.RADIUS_CARD    # 窗口四周留给投影的余量(= 卡片投影的模糊半径, 免得被裁)
TITLE_H = theme.CTRL_H         # 自绘标题行的高度(与其它控件取齐)


def go_frameless(dlg):
    """在窗口被创建**之前**设好无边框 + 透明背景(顺序很关键, 见模块开头)。"""
    dlg.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class _TitleBar(QWidget):
    """自绘标题行: 空白处按住可拖动整个窗口。

    用 `QWindow.startSystemMove()` 走系统实现 —— 顺带拿到跨屏/贴边这类系统行为,
    也免得自己按 DPI 换算鼠标位移。
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
            except Exception:                      # noqa: BLE001 - 拖不动就退回默认处理
                pass
        super().mousePressEvent(e)


class _ChromePlacer(QObject):
    """尺寸变化时把"底板"和"标题行"摆到正确位置(两者都是窗口的子控件, 不参与布局)"""

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
    """把已建好的 QDialog 改成"无边框 + 自绘标题栏"(见模块开头的长说明)。

    `recenter(dlg)` 由调用方传入(通常是 window._prep_dialog) —— 尺寸变了要重新定死
    尺寸与位置; 传函数而不是 import, 是为了让本模块只单向依赖 theme。
    """
    if getattr(dlg, "_csd_done", False):           # 别重复包装(会多出一条标题栏)
        return None
    dlg._csd_done = True

    dlg.setObjectName("csdDialog")
    # 窗口自身透明: 否则主题里那条 QDialog{background:BG} 会把圆角糊成方块
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
    close = QPushButton("✕", bar)
    close.setObjectName("mini")
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
    # 保住引用(它们没有父布局, 只挂在窗口上)
    dlg._csd_panel, dlg._csd_bar, dlg._csd_placer = panel, bar, placer

    recenter(dlg)
    return panel


# 标准按钮的文案: **用的时候才取**, 不能在 import 期建表 —— 那时翻译器还没装(见 i18n.install)
_BTN_TEXT = {
    QMessageBox.StandardButton.Ok: "确定",
    QMessageBox.StandardButton.Yes: "是",
    QMessageBox.StandardButton.No: "否",
}


# 弹窗按钮的最小宽度: 「是 / 否 / 关闭」这种两字标签以前会因 #primary 的横向 padding 更大
# (0 20px vs 0 14px)而宽窄不一(用户报了"自绘弹窗按钮不等宽")。给一个下限, 短标签就一样宽了。
DLG_BTN_MIN_W = 76


def dlg_button(text, slot=None, primary=False, parent=None):
    """自绘弹窗里统一的按钮。

    以前每个弹窗各写各的: 映射弹窗那三个按钮没设高度、关闭也不是主色, 与取色/图片信息弹窗对不上
    (用户报"这两个按钮的样式和其他自绘弹窗不一致")。现在所有自绘弹窗的按钮都从这一个函数出,
    样式(高度 CTRL_H / 主按钮 #primary / 手型光标)才真统一; 标题栏那个 ✕ 不走这里(它是弹窗壳的一部分)。
    """
    b = QPushButton(text, parent)
    if primary:
        b.setObjectName("primary")
    b.setFixedHeight(theme.CTRL_H)
    b.setMinimumWidth(DLG_BTN_MIN_W)          # 短标签(是/否/关闭)与长标签都不会参差
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot is not None:
        b.clicked.connect(slot)
    return b


def dlg_button_row(*buttons):
    """把按钮摆成弹窗底部那一行: 右对齐、间距统一。"""
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
    """自绘标题栏的消息框, 替代 QMessageBox —— 与本项目的其它弹窗保持同一套设计语言。

    返回值遵守原 `_message` 的约定: 返回被点中的 `QMessageBox.StandardButton`
    (调用方仍在 `!= QMessageBox.StandardButton.Yes` 这样比较, 一行都不用改)。
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
