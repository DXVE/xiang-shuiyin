"""取色面板: 明度/饱和度方片 + 色相条 + 色号, 改动即时上屏, 无确定按钮。

在色块下方弹出: 点面板外或回车收起(保留当前色), Esc 回到打开前的颜色。
"""
from PySide6.QtCore import QEventLoop, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLineEdit,
                               QVBoxLayout, QWidget)

from .. import theme

_SV_W, _SV_H = 208, 140     # 明度/饱和度方片(逻辑像素)
_HUE_W = 20                 # 色相条宽度
_PAD = 12                   # 卡片内边距
_GAP = 10                   # 控件间距
_R = 6                      # 取色圈半径
_SHADOW = 8                 # 卡片投影在外层窗口里占的余量
_RADIUS = theme.RADIUS_SMALL


def _rounded_base(w, h, dpr, fill):
    # 生成一张按设备像素比出图、四角已按圆角裁掉的底图(避免 Qt 再缩放一次, 也避免直角溢出圆角)
    img = QImage(max(1, round(w * dpr)), max(1, round(h * dpr)),
                 QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), _RADIUS, _RADIUS)
    p.setClipPath(path)
    fill(p, w, h)
    p.end()
    return img


def _sv_image(hue, w, h, dpr):
    # 指定色相的方片底图: 横向 白→纯色, 纵向 透明→黑(标准两段叠法)
    def fill(p, bw, bh):
        g = QLinearGradient(0, 0, bw, 0)
        g.setColorAt(0.0, QColor(255, 255, 255))
        g.setColorAt(1.0, QColor.fromHsv(hue, 255, 255))
        p.fillRect(QRectF(0, 0, bw, bh), g)
        g2 = QLinearGradient(0, 0, 0, bh)
        g2.setColorAt(0.0, QColor(0, 0, 0, 0))
        g2.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(QRectF(0, 0, bw, bh), g2)

    return _rounded_base(w, h, dpr, fill)


def _hue_image(w, h, dpr):
    # 色相条底图: 顶到底 0°→359°, 七个等距控制点(红黄绿青蓝品红红)
    def fill(p, bw, bh):
        g = QLinearGradient(0, 0, 0, bh)
        for i in range(7):
            g.setColorAt(i / 6, QColor.fromHsv(round(359 * i / 6), 255, 255))
        p.fillRect(QRectF(0, 0, bw, bh), g)

    return _rounded_base(w, h, dpr, fill)


def _ring_cursor(p, x, y, light):
    # 取色圈: 内圈随底色反色, 外圈与之相反, 保证白区黑区都看得见
    inner = QColor(255, 255, 255) if not light else QColor(24, 24, 24)
    outer = QColor(24, 24, 24, 200) if not light else QColor(255, 255, 255, 230)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(outer, 3))
    p.drawEllipse(QPoint(round(x), round(y)), _R + 1, _R + 1)
    p.setPen(QPen(inner, 2))
    p.drawEllipse(QPoint(round(x), round(y)), _R, _R)


class _SVBox(QWidget):
    # 明度/饱和度方片: 横轴饱和度, 纵轴明度; 拖动即发信号, 方向键微调
    changed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_SV_W, _SV_H)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(self.tr("饱和度与明度"))
        self._hue, self._s, self._v = 200, 255, 255
        self._base = None
        self._dpr = 1.0

    def set_hsv(self, h, s, v):
        if h != self._hue:
            self._hue = h
            self._base = None
        self._s, self._v = s, v
        self.update()

    def set_dpr(self, dpr):
        # 屏幕像素比在 showEvent 才可靠: 变了就重出底图, 否则高 DPI 下会被放大发虚
        if abs(dpr - self._dpr) > 0.01:
            self._dpr = dpr
            self._base = None
            self.update()

    def _emit_at(self, pos):
        s = max(0, min(255, round(pos.x() / max(1, self.width() - 1) * 255)))
        v = max(0, min(255, round((1 - pos.y() / max(1, self.height() - 1)) * 255)))
        self._s, self._v = s, v
        self.changed.emit(s, v)
        self.update()

    def _nudge(self, dx, dy):
        self._s = max(0, min(255, self._s + dx))
        self._v = max(0, min(255, self._v + dy))
        self.changed.emit(self._s, self._v)
        self.update()

    def mousePressEvent(self, e):        # noqa: N802 - Qt 命名
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._emit_at(e.position().toPoint())

    def mouseMoveEvent(self, e):         # noqa: N802 - Qt 命名
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._emit_at(e.position().toPoint())

    def keyPressEvent(self, e):          # noqa: N802 - Qt 命名
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                 Qt.Key.Key_Up: (0, step), Qt.Key.Key_Down: (0, -step)}
        if e.key() in moves:
            self._nudge(*moves[e.key()])
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e):            # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._base is None:           # 底图只随色相与像素比变, 拖动时复用
            self._base = _sv_image(self._hue, self.width(), self.height(), self._dpr)
        p.drawImage(0, 0, self._base)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.drawRoundedRect(r, _RADIUS, _RADIUS)
        bx = self._s / 255 * (self.width() - 1)
        by = (1 - self._v / 255) * (self.height() - 1)
        # 画出来的圈往内夹一个半径: 选到极值(四角/边缘)时圈不会被圆角裁掉一半
        bx = max(_R + 1, min(bx, self.width() - _R - 2))
        by = max(_R + 1, min(by, self.height() - _R - 2))
        light = QColor.fromHsv(self._hue, self._s, self._v).lightnessF() > 0.62
        _ring_cursor(p, bx, by, light)


class _HueStrip(QWidget):
    # 色相条: 顶部 0°, 底部 359°; 拖动即发信号, 方向键微调
    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_HUE_W, _SV_H)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(self.tr("色相"))
        self._hue = 200
        self._base = None
        self._dpr = 1.0

    def set_hue(self, h):
        if h != self._hue:
            self._hue = h
            self.update()

    def set_dpr(self, dpr):
        if abs(dpr - self._dpr) > 0.01:
            self._dpr = dpr
            self._base = None
            self.update()

    def _emit_at(self, pos):
        self._set(max(0, min(359, round(pos.y() / max(1, self.height() - 1) * 359))))

    def _set(self, h):
        self._hue = h
        self.changed.emit(h)
        self.update()

    def mousePressEvent(self, e):        # noqa: N802 - Qt 命名
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._emit_at(e.position().toPoint())

    def mouseMoveEvent(self, e):         # noqa: N802 - Qt 命名
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._emit_at(e.position().toPoint())

    def keyPressEvent(self, e):          # noqa: N802 - Qt 命名
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._set(max(0, min(359, self._hue + (step if e.key() == Qt.Key.Key_Up else -step))))
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e):            # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._base is None:
            self._base = _hue_image(self.width(), self.height(), self._dpr)
        p.drawImage(0, 0, self._base)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.drawRoundedRect(r, _RADIUS, _RADIUS)
        y = self._hue / 359 * (self.height() - 1)
        y = max(3, min(y, self.height() - 4))       # 夹住指示器: 0° 与 359° 时三条线都画得下
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))     # 上下各一条深线: 黄/青段的白线才有边界
        p.drawLine(QPoint(1, round(y) - 2), QPoint(self.width() - 2, round(y) - 2))
        p.drawLine(QPoint(1, round(y) + 2), QPoint(self.width() - 2, round(y) + 2))
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawLine(QPoint(1, round(y)), QPoint(self.width() - 2, round(y)))


class _Swatch(QWidget):
    # 当前颜色色块: 自绘(不设样式表), 拖动取色时每帧刷新也不会触发重新 polish
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, theme.CTRL_H)
        self._color = QColor("#FFFFFF")
        self.setAccessibleName(self.tr("当前颜色"))

    def set_color(self, text):
        c = QColor(text)
        if c.isValid() and c.name() != self._color.name():
            self._color = c
            self.update()

    def paintEvent(self, _e):            # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setBrush(self._color)
        p.setPen(QPen(QColor(theme.LINE), 2))
        p.drawRoundedRect(r, theme.RADIUS_SMALL, theme.RADIUS_SMALL)


class ColorPickerPopup(QFrame):
    # 取色面板本体: 面板内任何改动都发 picked, 由调用方即时上屏
    picked = Signal(str)          # 变化中的色号(#rrggbb)
    finished = Signal(bool)       # 收起: True=保留(点面板外/回车), False=取消(Esc)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        # 圆角卡片画在子控件上: 顶层窗口设了半透明后 QSS 背景不生效(见 wiki §6.6)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._hue, self._s, self._v = 200, 255, 255
        self._cancel = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(_SHADOW, _SHADOW, _SHADOW, _SHADOW)
        card = QFrame()
        card.setObjectName("pickerCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(60, 55, 48, 70))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        v.setSpacing(_GAP)
        row = QHBoxLayout()
        row.setSpacing(_GAP)
        self._sv = _SVBox()
        self._huebar = _HueStrip()
        row.addWidget(self._sv)
        row.addWidget(self._huebar)
        v.addLayout(row)

        bottom = QHBoxLayout()
        bottom.setSpacing(_GAP)
        self._swatch = _Swatch()
        bottom.addWidget(self._swatch)
        self._hex = QLineEdit()
        self._hex.setMaxLength(9)
        self._hex.setPlaceholderText("#RRGGBB")
        self._hex.setAccessibleName(self.tr("色号"))
        self._hex.returnPressed.connect(self._commit_and_close)
        self._hex.editingFinished.connect(self._from_hex)
        bottom.addWidget(self._hex, 1)
        v.addLayout(bottom)

        self._sv.changed.connect(self._from_sv)
        self._huebar.changed.connect(self._from_hue)

    # ---- 外部接口 ----
    def set_color(self, text):
        c = QColor(text or "#FFFFFF")
        if not c.isValid():
            c = QColor("#FFFFFF")
        h = max(0, c.hue()) if c.hue() >= 0 else 0
        self._hue, self._s, self._v = h, c.saturation(), c.value()
        self._sync()
        # 色号框回填原色号: HSV 往返带 8 位量化误差, 直接显示换算结果会让"打开又关掉"也微改颜色
        self._hex.setText(c.name())

    def show_under(self, anchor):
        # 贴在锚点下方; 越界时上移/左移, 保证整块面板(含投影余量)可见
        self.ensurePolished()
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        scr = anchor.screen()
        if scr is not None:
            a = scr.availableGeometry()
            x = max(a.left(), min(g.x(), a.right() - self.width()))
            y = g.y() if g.y() + self.height() <= a.bottom() else \
                anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4
            y = max(a.top(), min(y, a.bottom() - self.height()))
            g = QPoint(x, y)
        self.move(g)
        self.show()

    # ---- 内部 ----
    def _color(self):
        return QColor.fromHsv(self._hue, self._s, self._v)

    def _sync(self):
        self._sv.set_hsv(self._hue, self._s, self._v)
        self._huebar.set_hue(self._hue)
        txt = self._color().name()
        if self._hex.text() != txt:
            self._hex.setText(txt)
        self._swatch.set_color(txt)

    def showEvent(self, e):              # noqa: N802 - Qt 命名
        # 像素比在窗口显示后才可靠, 这里同步一次并让两个自绘控件重出底图
        super().showEvent(e)
        dpr = self.devicePixelRatioF() or 1.0
        self._sv.set_dpr(dpr)
        self._huebar.set_dpr(dpr)
        self._sv.setFocus(Qt.FocusReason.PopupFocusReason)

    def _from_sv(self, s, v):
        self._s, self._v = s, v
        self._sync()
        self.picked.emit(self._color().name())

    def _from_hue(self, h):
        self._hue = h
        self._sync()
        self.picked.emit(self._color().name())

    def _from_hex(self):
        txt = self._hex.text().strip()
        c = QColor(txt if txt.startswith("#") else "#" + txt)
        if not c.isValid():
            self._sync()
            return
        self.set_color(c.name())
        self.picked.emit(c.name())

    def _commit_and_close(self):
        self._from_hex()
        self.close()

    def keyPressEvent(self, e):          # noqa: N802 - Qt 命名
        if e.key() == Qt.Key.Key_Escape:
            self._cancel = True
            self.close()
            return
        super().keyPressEvent(e)

    def hideEvent(self, e):              # noqa: N802 - Qt 命名
        self.finished.emit(not self._cancel)
        super().hideEvent(e)


def pick_color(anchor, initial, on_change=None):
    # 在 anchor 下方弹出取色面板, 返回最终色号; 取消(Esc)返回 None
    # on_change: 面板内每次变化都回调, 调用方据此即时上屏
    popup = ColorPickerPopup(anchor)
    popup.set_color(initial)
    out = []
    loop = QEventLoop()

    def done(keep):
        # finished 只发一次: 收起时记录结果并退出嵌套事件循环
        if out:
            return
        out.append(popup._color().name() if keep else None)
        loop.quit()

    touched = []

    def on_pick(c):
        touched.append(True)
        if callable(on_change):
            on_change(c)

    popup.picked.connect(on_pick)
    popup.finished.connect(done)
    popup.show_under(anchor)
    loop.exec()
    popup.deleteLater()
    return out[0] if out and touched else None     # 没动过就当没这回事: 不落地、不记撤销
