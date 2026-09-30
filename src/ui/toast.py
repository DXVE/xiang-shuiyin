"""吐司提示: 一次性反馈浮层, 单例不堆叠且不接收鼠标事件。"""
from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QParallelAnimationGroup, QPointF,
                            QPropertyAnimation, QTimer, Qt)
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QVBoxLayout, QWidget


TOAST_MS = 3200
TOAST_FADE_MS = 350
TOAST_TOP = 14           # 距窗口内容顶部的距离
TOAST_SLIDE = 16         # 出场 / 收起的位移
TOAST_MIN_W, TOAST_MAX_W = 240, 560
TOAST_PAD_X = 20         # 盒子左右内边距
TOAST_CHROME = TOAST_PAD_X * 2 + 2   # 文字宽度之外的固定开销: 左右内边距 + 两侧 1px 描边


class Toast(QWidget):
    # 顶部居中显示的一次性提示(已保存 / 已导出 / 失败 …)

    def __init__(self, parent):
        super().__init__(parent)
        # 每个子控件都要设该属性(不向下传递, 否则命中测试会落到未设置的那一层)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._box = QFrame(self)
        self._box.setObjectName("toastBox")
        self._box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._box.setStyleSheet(
            "QFrame#toastBox { background: rgba(15,23,42,0.94); border: none;"
            " border-radius: 10px; }")
        bl = QVBoxLayout(self._box)
        bl.setContentsMargins(TOAST_PAD_X, 12, TOAST_PAD_X, 12)
        self._label = QLabel(self._box)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color:#e2e8f0; font-size:13px; background:transparent;")
        bl.addWidget(self._label)
        lay.addWidget(self._box)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim_pos = QPropertyAnimation(self, b"pos", self)
        self._anim_op = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim = QParallelAnimationGroup(self)
        self._anim.addAnimation(self._anim_pos)
        self._anim.addAnimation(self._anim_op)
        for a in (self._anim_pos, self._anim_op):
            a.setDuration(TOAST_FADE_MS)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TOAST_MS)
        self._timer.timeout.connect(self._fade_out)
        self._hiding = False  # 当前动画方向: 出场 / 收起(重排时按方向改终点)
        self.hide()

    def target_pos(self):
        # 它该待的位置: 水平居中、离内容顶部 TOAST_TOP
        p = self.parentWidget()
        if p is None:
            return QPointF(0, TOAST_TOP)
        return QPointF(max(0, (p.width() - self.width()) // 2), TOAST_TOP)

    def show_message(self, text):
        # 显示一条: 已显示的直接换文字并重置计时
        text = str(text)
        self._label.setText(text)
            # QLabel+WordWrap 按 sizeHint 定尺会在最小宽度处截断, 宽度与行数须自行计算
        fm = self._label.fontMetrics()
        w = min(TOAST_MAX_W - TOAST_CHROME,
                max(TOAST_MIN_W - TOAST_CHROME, fm.horizontalAdvance(text)))
        self._label.setFixedWidth(w)
        self._label.setFixedHeight(self._label.heightForWidth(w))
        self._box.adjustSize()
        self.adjustSize()
        self._hiding = False
        end = self.target_pos()
        was_visible = self.isVisible()
        if not was_visible:
            self.move(int(end.x()), int(end.y()) - TOAST_SLIDE)
            self.show()
        else:
            self.move(int(end.x()), int(end.y()))                 # 换文字不重放动画, 只把位置摆正
        self.raise_()
        self._animate(end, 1.0)
        self._timer.start()

    def reflow(self):
        # 父控件尺寸变化时重算目标位置
        # 动画运行期间必须改终点而非只 move(): 否则下一 tick 会把控件拉回旧位置
        if not self.isVisible():
            return
        end = self.target_pos()
        if self._hiding:
            end = QPointF(end.x(), end.y() - TOAST_SLIDE)
        if self._anim.state() == QAbstractAnimation.State.Running:
            # 重启动画, 不改运行中那段动画的终点(setEndValue 在运行中改终点会把控件移到 (0,0))
            self._animate(end, 0.0 if self._hiding else 1.0)
        else:
            self.move(int(end.x()), int(end.y()))

    def _animate(self, pos, opacity):
        self._anim.stop()
        self._anim_pos.setStartValue(QPointF(self.x(), self.y()))
        self._anim_pos.setEndValue(pos)
        self._anim_op.setStartValue(self._effect.opacity())
        self._anim_op.setEndValue(opacity)
        self._anim.start()

    def _fade_out(self):
        # 收起: 透明度归零 + 往上退回 TOAST_SLIDE(与出场反向)
        if not self.isVisible():
            return
        self._hiding = True
        end = self.target_pos()
        self._animate(QPointF(end.x(), end.y() - TOAST_SLIDE), 0.0)

    def _on_anim_finished(self):
        # 动画结束时收尾: 仅在透明度收到 0 时隐藏
        if self._effect.opacity() <= 0.01:
            self.hide()
