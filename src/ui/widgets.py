"""通用控件: 参数行、卡片、预览框、色块与胶囊开关等。"""
from PIL import Image
from PySide6.QtCore import (QCoreApplication, QPointF, QRectF, QSize, QT_TRANSLATE_NOOP, QTimer,
                              QUrl, Qt, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QIcon, QImage, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)
from . import theme
from .dialog import dlg_button
from .dialogs.base import _open_file_dialog
from .render import _pil_to_qimage
from .winconst import _PV_FADE_MS
import logging
import math
import os
import time


# ---------- 悬停链路诊断开关 ----------
# 置环境变量 XSY_HOVER_TRACE=1 时，将悬停→间距标注链路的各环节写入日志
# （事件是否到达 / 光标命中的参数行 / 高亮键 / 绘制时的 geom 与 img）。默认关闭。
_HOVER_TRACE = bool(os.environ.get("XSY_HOVER_TRACE"))


def _hover_trace(msg):
    if _HOVER_TRACE:
        logging.info("[悬停] " + msg)


# 数值框 ± 步进按钮的素材边长（逻辑像素）: 与原先的 12px 粗体字形同量级。
_STEP_ICON = 14
# 行尾 ↺: 固定宽度与图标边长(逻辑像素)。始终占位, 显隐只换图标
_RESET_W = 22
_RESET_ICON = 14


_FONT_FILTER = "字体 (*.ttf *.otf *.ttc);;所有文件 (*)"


_LABEL_W = 128
# 百分比类参数内部是 0~1 的小数; 声明 decimals=1 的行再放大 10 倍(整数走「十分位」, 1.5% -> 15)
_PCT_SCALE = 100


def _display_range(kind, lo, hi, decimals=0):
    # 参数区间 -> 数值框/滑块的整数显示区间
    # pct 按百分比换算(0.04 -> 4), decimals=1 再 ×10(0.015 -> 15); 下界向上取整(0.001 -> 1, 0 间距无意义)
    if kind != "pct":
        return int(lo), int(hi)
    mult = _PCT_SCALE * (10 ** int(decimals or 0))
    return math.ceil(lo * mult - 1e-9), int(round(hi * mult))


# 放大镜尺寸、倍数与圆角
MAG_W, MAG_H, MAG_ZOOM, MAG_RADIUS = 240, 160, 3, 12
MAG_MARGIN = 12      # 离预览框右上角的距离
# 预览图占预览框面积的上限: 贴合矩形按 √0.95 等比收缩, 四边各留约 1.3% 余量(大于圆角咬合深度, 四角不会被裁掉)
_PREVIEW_FIT_AREA = 0.95
_PREVIEW_FIT_SCALE = math.sqrt(_PREVIEW_FIT_AREA)


# 标识当前预览图是否由当前参数渲染而来。各状态圆点色取自 PRESET_COLORS，不另造色号。
PV_CHIP_DOT = {
    "none": theme.EMPTY_SLOT,  # 未选图：暗中性灰
    "rendering": "#7C98A6",
    "synced": theme.ACCENT,
    "stale": "#B8895E",
    "failed": "#B0676B",
}
PV_CHIP_H = 20          # 胶囊高度，必须定高（见 PreviewStateChip 类注释）


# 文案表。中文字面量必须包在 QT_TRANSLATE_NOOP 中：lupdate 无法解析变量取值，否则漏译。
# context 须与消费它的类名（PreviewStateChip）一致。
_PV_CHIP_TEXT = {
    "none": QT_TRANSLATE_NOOP("PreviewStateChip", "无预览"),
    "rendering": QT_TRANSLATE_NOOP("PreviewStateChip", "渲染中…"),
    "synced": QT_TRANSLATE_NOOP("PreviewStateChip", "已按当前参数渲染"),
    "stale": QT_TRANSLATE_NOOP("PreviewStateChip", "参数已改，待渲染"),
    "failed": QT_TRANSLATE_NOOP("PreviewStateChip", "渲染失败，未更新"),
}
_PV_CHIP_TIP = {
    "none": QT_TRANSLATE_NOOP("PreviewStateChip", "未选择图片"),
    "rendering": QT_TRANSLATE_NOOP("PreviewStateChip", "正在渲染，完成后自动更新"),
    "synced": QT_TRANSLATE_NOOP("PreviewStateChip", "预览已与当前参数一致（缩略档渲染，细节请用放大镜查看）"),
    "stale": QT_TRANSLATE_NOOP("PreviewStateChip", "预览尚未更新到当前参数，渲染完成后自动更新"),
    "failed": QT_TRANSLATE_NOOP("PreviewStateChip", "上次预览渲染失败，当前显示的是上一张成功的预览"),
}


# 滑块与下拉框不响应滚轮(ignore() 上抛父级滚动区), 避免滑过时误改数值; 数值框保留滚轮调节
# 下拉框只在收起状态拦滚轮, 展开后的列表是独立控件、照常可滚
class NoWheelSlider(QSlider):
    # 滚轮不调数值的滑块(滚轮让给父级滚动区)。

    def wheelEvent(self, e):        # noqa: N802 - Qt 命名
        e.ignore()


class NoWheelComboBox(QComboBox):
    # 滚轮不换选项的下拉框(只限收起状态; 展开的列表照常可滚)。

    def wheelEvent(self, e):        # noqa: N802 - Qt 命名
        e.ignore()


class ParamRow(QWidget):
    # 一行参数控件。按 kind 组装, 对外统一的 changed(key, value) / set_value() / value()。

    changed = Signal(str, object)
    colorClicked = Signal()          # 点「颜色管理」: 打开预设与收藏弹窗(取色弹窗由主窗提供, 需读取用户保存的颜色)
    pickColorClicked = Signal()    # 点色块: 弹出取色面板(自绘, 拖动即生效; 不经过「颜色管理」弹窗)
    resetClicked = Signal()          # 点行尾 ↺: 把本行参数恢复默认值(含镜像行)

    def __init__(self, spec, value, parent=None):
        super().__init__(parent)
        self.key = spec["key"]
        self.kind = spec["kind"]
        self._spec = spec
        self._value = value
        self._updating = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(theme.GAP_CTRL)

        if self.kind == "bool":
            self.chk = QCheckBox(self.tr(spec["label"]))
            self.chk.setCursor(Qt.PointingHandCursor)
            self.chk.toggled.connect(self._from_check)
            lay.addWidget(self.chk)
            if spec["label"].strip():
# 标签为空(字体/水印开关网格的格位)时不加弹性空白: 那些格位靠 grid 的 AlignCenter 居中, 加 stretch 会把开关推偏
                lay.addStretch(1)
        else:
            lab = QLabel(self.tr(spec["label"]))
            lab.setObjectName("fieldLabel")
            lab.setFixedWidth(_LABEL_W)
            lab.setWordWrap(True)
            lay.addWidget(lab)
            hint = spec.get("hint")
            if hint:
                lab.setToolTip(self.tr(hint))

            if self.kind == "choice":
                self.combo = NoWheelComboBox()
                for val, text in spec["args"]:
                    self.combo.addItem(self.tr(text), val)
                self.combo.currentIndexChanged.connect(self._from_choice)
                lay.addWidget(self.combo, 1)

            elif self.kind == "color":
                # 双入口: 色块本身是按钮, 点它直接弹取色面板(改一次颜色只要一步); 行尾「颜色管理」进预设与收藏
                # 两段的宽度由主窗给: 色块按拉伸吃掉滑块列, 管理入口与步进器组同宽(见 _unify_spins), 故本行与数值行左右都对齐
                self.swatch = QPushButton()
                self.swatch.setCursor(Qt.PointingHandCursor)
                self.swatch.setToolTip(self.tr("点击选择颜色"))
                self.swatch.clicked.connect(self.pickColorClicked.emit)
                lay.addWidget(self.swatch, 1)
                self.color_btn = QPushButton(self.tr("颜色管理"))
                self.color_btn.setObjectName("mini")
                self.color_btn.setCursor(Qt.PointingHandCursor)
                self.color_btn.clicked.connect(self.colorClicked.emit)
                lay.addWidget(self.color_btn)

            elif self.kind == "path":
                self.edit = QLineEdit()
                self.edit.setReadOnly(True)
                self.edit.setToolTip(str(value or ""))
                self.edit.setFixedHeight(theme.CTRL_H)   # 与「选择字体」按钮同高, 整行对齐
                lay.addWidget(self.edit, 1)
                b = dlg_button(self.tr("选择字体"), self._pick_font)
                self._path_btn = b          # 宽度在 _unify_spins() 里与数值框对齐
                lay.addWidget(b)

            else:                                   # pct / int —— 整数显示; pct 可声明 decimals 走小数档
                lo, hi = spec["args"]
                # decimals=1 的行(边距类): 显示保留 1 位小数, ± 按钮按 spec 的 step 走(当前 0.5)
                self._decimals = int(spec.get("decimals", 0) or 0) if self.kind == "pct" else 0
                self._step = float(spec.get("step", 1.0) or 1.0)
                self._disp_lo, self._disp_hi = _display_range(self.kind, lo, hi, self._decimals)
                self.slider = NoWheelSlider(Qt.Horizontal)
                self.slider.setRange(self._disp_lo, self._disp_hi)
                # 滑块与方向键的步长: 小数档为 0.1%(显示整数即十分位), 其余 1(1% / 1px)
                self.slider.setSingleStep(1)
                if self._decimals:
                    # 只有小数档用 QDoubleSpinBox: 其余参数都是整数, 混两套只会让宽度统一更难算。
                    # ± 按钮调 stepDown/stepUp, 走的就是 singleStep, 因此按钮步进 = step。
                    self.spin = QDoubleSpinBox()
                    self.spin.setDecimals(self._decimals)
                    self.spin.setRange(self._disp_lo / 10, self._disp_hi / 10)
                    self.spin.setSingleStep(self._step)
                else:
                    self.spin = QSpinBox()
                    self.spin.setRange(self._disp_lo, self._disp_hi)
                    self.spin.setSingleStep(self._step)
                self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
                # 宽度与对齐不在此处确定: 由 MainWindow._unify_spins() 按全表最宽取值统一计算
                if hint:
                    self.spin.setToolTip(hint)
                self.slider.valueChanged.connect(self._from_slider)
                self.spin.valueChanged.connect(self._from_spin)
                minus = QPushButton()
                plus = QPushButton()
                _iconify(minus, theme.STEP_MINUS, _STEP_ICON)
                _iconify(plus, theme.STEP_PLUS, _STEP_ICON)
                for b, slot in ((minus, self.spin.stepDown), (plus, self.spin.stepUp)):
                    b.setObjectName("step")
                    b.setCursor(Qt.PointingHandCursor)
                    b.clicked.connect(slot)
                step = QWidget()
                self._step_box = step
                self._step_w = max(minus.sizeHint().width(), plus.sizeHint().width())
                sl = QHBoxLayout(step)
                sl.setContentsMargins(0, 0, 0, 0)
                sl.setSpacing(3)
                sl.addWidget(minus)
                sl.addWidget(self.spin)
                sl.addWidget(plus)
                lay.addWidget(self.slider, 1)
                lay.addWidget(step)

        # 行尾 ↺: 恢复本行参数为默认值; path 行在弹窗里, 不给; 格位另行共用一个(见 no_reset)
        self.reset_btn = None
        if self.kind != "path" and not spec.get("no_reset"):
            self.reset_btn = _reset_button(self.resetClicked.emit)
            lay.addWidget(self.reset_btn)

        self.set_value(value)

    def set_value(self, v):
        self._updating = True
        self._value = v
        if self.kind == "bool":
            self.chk.setChecked(bool(v))
        elif self.kind == "choice":
            i = self.combo.findData(v)
            if i >= 0:
                self.combo.setCurrentIndex(i)
        elif self.kind == "color":
            self._paint_swatch(v)
        elif self.kind == "path":
            self.edit.setText(str(v or ""))
            self.edit.setToolTip(str(v or ""))
        else:
            lo, hi = self._spec["args"]
            v = max(lo, min(hi, float(v)))
            disp = max(self._disp_lo, min(self._disp_hi, self._value_to_disp(v)))
            self.slider.setValue(disp)
            self.spin.setValue(self._slider_to_spin(disp))
        self._updating = False

    def value(self):
        return self._value

    # pct 类内部是 0~1 的小数, 差一个 ×100(小数档再 ×10, 见 _PCT_SCALE); int 类两者相同。
    def _disp_to_value(self, disp):
        if self.kind != "pct":
            return int(disp)
        return disp / (_PCT_SCALE * (10 ** self._decimals))

    def _value_to_disp(self, value):
        if self.kind != "pct":
            return int(value)
        return int(round(float(value) * _PCT_SCALE * (10 ** self._decimals)))

    # 小数档: 滑块的显示整数是"十分位", 数值框是带小数的百分数 —— 两者差 10 倍
    def _slider_to_spin(self, disp):
        return disp / 10 if self._decimals else disp

    def _spin_to_slider(self, val):
        return int(round(val * 10)) if self._decimals else int(val)

    def _paint_swatch(self, color):
        # 色块外观: 底色为颜色本身, 色号用对比色写在上面(浅底黑字 / 深底白字)
        # 与取色弹窗的色块共用 _swatch_qss; 悬停/按下态须在本控件样式表里写掉背景, 否则会落到主题那条 hover
        self.swatch.setText(str(color))
        self.swatch.setStyleSheet(_swatch_qss(color, radius=theme.RADIUS_CTRL))

    # _updating 守卫: set_value() 会反过来让控件发信号, 不挡住就会把"回填"误当成用户改动
    def _from_check(self, v):
        if self._updating:
            return
        self.changed.emit(self.key, bool(v))

    def _from_choice(self, _i):
        if self._updating:
            return
        self.changed.emit(self.key, self.combo.currentData())

    def _from_slider(self, v):
        if self._updating:
            return
        self._updating = True
        self.spin.setValue(self._slider_to_spin(v))      # 小数档差 10 倍, 其余直接同步
        self._updating = False
        self.changed.emit(self.key, self._disp_to_value(v))

    def _from_spin(self, v):
        if self._updating:
            return
        disp = self._spin_to_slider(v)
        self._updating = True
        self.slider.setValue(disp)
        self._updating = False
        self.changed.emit(self.key, self._disp_to_value(disp))

    def _hover_keys(self):
        # 本行要顺带高亮的间距段: 镜像行高亮上下两段, 其余就是它自己。
        return list(self._spec.get("mirror") or [self.key])

    def _pick_font(self):
        path = _open_file_dialog(self, self.tr("选择字体文件"),
                                 os.path.dirname(self._value or ""), _FONT_FILTER)
        if path:
            self.set_value(path)
            self.changed.emit(self.key, path)

    def spec_label(self):
        # 本行标签(在 ParamRow 语境翻译): 撤销步名等取它; 主窗自己 tr 会落到别的语境, 取不到译文
        return self.tr(self._spec["label"])


def _fx_divider():
    # 「描边与投影」卡内相邻功能组之间的分割线(自带上下留白, 否则会贴住上一组最后一行)
    # 用 QFrame 而非 QWidget: 样式表里的 background 只有 QFrame 这类部件会直接绘制
    holder = QWidget()
    v = QVBoxLayout(holder)
    v.setContentsMargins(0, 9, 0, 3)
    v.setSpacing(0)
    line = QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    v.addWidget(line)
    return holder


class _CardHead(QWidget):
    # 可折叠卡片的标题行: 整条可点, 与点左端的开合按钮等价(行内子控件自己吃鼠标事件)

    clicked = Signal()

    def mouseReleaseEvent(self, e):        # noqa: N802 - Qt 命名
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class Card(QFrame):
    # 卡片: 标题 + 内容区
    # collapsible=True 时标题行整条可点、内容区默认收起: 收放只改显示、不碰参数值、状态不落盘

    def __init__(self, title, parent=None, collapsible=False):
        super().__init__(parent)
        self.setObjectName("card")
        self.title_display = self.tr(title)   # 卡名显示文本(标题标签直接取它, 不从控件反查)
        self.collapsible = collapsible
        self.collapsed = bool(collapsible)
        self.body_host = None        # 可折叠卡的内容宿主(布局本身没有可见性, 收起要落在 widget 上)
        theme.apply_shadow(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD - 2, theme.PAD_CARD, theme.PAD_CARD)
        v.setSpacing(theme.GAP_ROW)

        if collapsible:
            self._head = _CardHead(self)
            self._head.clicked.connect(self.toggle)
            head = QHBoxLayout(self._head)
        else:
            self._head = None
            head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self._toggle_btn = None
        if collapsible:
            # 开合按钮: 负担指示与点击两件事。用 #ghost(既有四档之一)而不是新增按钮分量。
            self._toggle_btn = QPushButton()
            self._toggle_btn.setObjectName("ghost")
            self._toggle_btn.setFixedWidth(24)
            self._toggle_btn.setCursor(Qt.PointingHandCursor)
            self._toggle_btn.clicked.connect(self.toggle)
            head.addWidget(self._toggle_btn)
        t = QLabel(self.title_display)        # 卡片名来自 PARAM_SPECS 或手写字面量
        t.setObjectName("cardTitle")
        head.addWidget(t)
        head.addStretch(1)
        if collapsible:
            v.addWidget(self._head)
        else:
            v.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(theme.GAP_ROW - 2)
        if collapsible:
            # 内容宿主: 收起即隐藏它。可见性由构建方(主窗)按 _vis_rule 登记, 本类不直接 setVisible。
            self.body_host = QWidget(self)
            self.body_host.setLayout(self.body)
            v.addWidget(self.body_host)
            self._sync_collapse_ui()
        else:
            v.addLayout(self.body)

    def toggle(self):
        # 收起 / 展开内容区: 只改状态, 随后由主窗走统一显隐出口(_sync_visibility)
        # 自己 setVisible 会先贴出旧尺寸的离屏投影(表现为跳一下再闪出内容); 折叠不进撤销、不触发预览
        if not self.collapsible:
            return
        self.collapsed = not self.collapsed
        self._sync_collapse_ui()
        sync = getattr(self.window(), "_sync_visibility", None)
        if sync is not None:
            sync()

    def _sync_collapse_ui(self):
        # 开合指示: 收起=右向箭头, 展开=下向箭头; 提示语同状态一起切。
        if not self.collapsible:
            return
        _iconify(self._toggle_btn, theme.ARROW_RIGHT if self.collapsed else theme.ARROW_DOWN, 12)
        tip = self.tr("展开本组") if self.collapsed else self.tr("收起本组")
        self._toggle_btn.setToolTip(tip)
        self._head.setToolTip(tip)
        self._head.setCursor(Qt.PointingHandCursor)


def _qimage_to_pil(img: QImage):
    # QImage -> PIL(仅回落路径用: 窗口尺寸/dpr 与请求时不符时现场重采样)
    q = img.convertToFormat(QImage.Format.Format_RGB888)
    return Image.frombytes("RGB", (q.width(), q.height()), q.constBits().tobytes(),
                           "raw", "RGB", q.bytesPerLine())


class PreviewStateChip(QFrame):
    # 预览标题条中的状态标识: 一个圆点 + 一句话(胶囊底), 紧随「效果预览」
    # 回答「屏上这帧是否由当前参数渲染」; 四条判据的顺序见 MainWindow._preview_state
    # 定高 20px(PV_CHIP_H): 同行按钮高 28px, 不显式定高会被拉成按钮外观

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(PV_CHIP_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 1, 8, 1)
        lay.setSpacing(5)
        self._dot = QLabel(self)
        self._dot.setFixedSize(6, 6)
        self._text = QLabel(self)
        self._text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._dot)
        lay.addWidget(self._text)
        self._state = ""
        self.set_state("none")

    def set_state(self, state, tip=""):
        # 切换状态。tip 给的是「为什么是这个状态」的一句话(渲染失败时带上原因)。
        if state not in PV_CHIP_DOT:
            state = "none"
        self._state = state
        dot = PV_CHIP_DOT[state]
        self._text.setText(self.tr(_PV_CHIP_TEXT[state]))
        self._dot.setStyleSheet(f"background: {dot}; border-radius: 3px;")
        self._text.setStyleSheet("color: %s; font-size: 11px; background: transparent;" % dot)
        # 淡底用状态色自身调透明度得到: 同一色系, 不另造颜色。
        self.setStyleSheet("background: %s; border: none; border-radius: 9px;"
                           % _rgba(dot, 0.13))
        self.setToolTip(tip or self.tr(_PV_CHIP_TIP[state]))
        self.adjustSize()

    def state(self):
        return self._state


def _rgba(hex_color, alpha):
    # #RRGGBB + 透明度 -> rgba(...)。用于状态标识的淡底。
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


class PreviewBox(QWidget):
    # 预览显示区: 等比缩放居中; 自绘以提供放大镜与间距几何的绘制入口
    # 上屏缩放已由渲染线程完成, 本控件只 QPixmap.fromImage 贴图
    # 窗口拖动/换屏(尺寸或 dpr 与请求不符)才回落 _scale_for_display 现场重采样

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pix = None         # 已缩到上屏尺寸的位图(设备像素, 不设 dpr: 贴图按设备几何算)
        self._src = None         # 整幅画布 QImage: 放大镜源 + 回落路径的重采样源
        self._canvas = None      # 整幅画布尺寸(px), 算等比尺寸用
        self._shown_for = None   # _pix 对应的可用区设备尺寸 + dpr: 相符才直接贴
        self._pil = None         # 回落路径用(按需从 _src 转换, 见 _scale_for_display)
        self._scaled = None
        self._scaled_for = None
        self._hint = self.tr("请拖入图片，或点击「打开图片」")
        self._geom = None      # 间距段几何(画布像素坐标), 由 make_preview 的 resp.geom 回传
        self._hl = set()       # 当前要高亮的间距段键
        self._mag_pos = None   # 放大镜: 光标在控件内的位置(None=不显示)
        self._prev_pm = None   # 沉底作淡入的那一帧位图, 收尾必须清掉; 为空表示从底色浮出(首次预览)
        self._prev_box = None  # 底层帧上一次的显示矩形(与 _prev_pm 配对)
        self._last_pm = None   # 最近一次真正画出的位图与矩形: 新帧据此沉底
        self._last_box = None
        self._fade_active = False
        self._fade_t0 = 0.0
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)
        self._fade_timer.timeout.connect(self._fade_tick)
        self.setMouseTracking(True)

    def avail_dev(self):
        # 当前预览框能给出的上屏设备像素尺寸(渲染线程按同一算法缩图, 见 PreviewWorker)。
        dpr = self.devicePixelRatioF()
        avail = QSize(max(1, int(self.width() * _PREVIEW_FIT_SCALE)),
                      max(1, int(self.height() * _PREVIEW_FIT_SCALE)))
        return QSize(max(1, round(avail.width() * dpr)), max(1, round(avail.height() * dpr)))

    def set_image(self, image: QImage, source=None, canvas=None, shown=None, shown_dpr=None):
        # 接收一帧: image 已是上屏尺寸, source 是整幅画布(放大镜与回落路径用)
        # shown 为渲染线程缩图时用的可用区设备尺寸; 与本控件当前尺寸一致才 1:1 直贴
        self._begin_fade()
        self._pix = QPixmap.fromImage(image)
        self._src = source if source is not None else image
        self._canvas = tuple(canvas) if canvas else (image.width(), image.height())
        self._shown_for = (tuple(shown), shown_dpr) if shown else None
        self._pil = None
        self._scaled = None
        self._scaled_for = None
        self.update()

    def _begin_fade(self):
        # 起淡入: 每次换帧都走同一条过渡, 不按间隔或宽高比分岔(否则同类操作有时淡入有时硬切)
        # 唯一例外是没有可信的上一帧(首帧尚未画出/窗口刚变过尺寸), 那时只能原子替换
        if self._fade_active:
            # 淡入还没结束又来一帧: 沿用底层与进度, 只把最上面那帧换掉
            # 重开一次会让画面往回跳(刚混进来那帧被撤掉), 连续调参时每帧都会这样
            self.update()
            return
        if self._last_pm is None and self._pix is not None:
            return
        # 框里本来就是空的时候(首次预览)没有上一帧, 底就是背景色, 等于让画面从底色里浮出来
        self._prev_pm, self._prev_box = (None, None) if self._pix is None else (self._last_pm, self._last_box)
        self._fade_t0 = time.perf_counter()
        self._fade_active = True
        self._fade_timer.start()

    def _stop_fade(self):
        # 收起底层: 清空与淡入结束都要走这里, 漏一处旧帧就会留在屏上成重影
        self._fade_timer.stop()
        self._fade_active = False
        self._prev_pm = None
        self._prev_box = None

    def _fade_progress(self):
        # 淡入进度 0~1
        return min(1.0, (time.perf_counter() - self._fade_t0) * 1000 / _PV_FADE_MS)

    def _fade_tick(self):
        if self._fade_progress() >= 1.0:
            self._stop_fade()
        self.update()

    def _fit_dev(self):
        # 按整幅画布的宽高比, 在本控件可用区内等比取出的设备像素尺寸。
        cw, ch = self._canvas or (1, 1)
        return QSize(cw, ch).scaled(self.avail_dev(), Qt.AspectRatioMode.KeepAspectRatio)

    def _scale_for_display(self, target_dev):
        # 回落路径: 控件尺寸/dpr 与请求时不符时, 从整幅画布现场重采样
        # 用 PIL 的 LANCZOS 而非 QPixmap.scaled(Smooth): 后者是盒式滤波(平滑但不锐), LANCZOS 带负瓣、缩小同时略锐化; 只在拖动或换屏时执行
        dpr = self.devicePixelRatioF()
        t0 = time.perf_counter()
        if self._pil is None:
            self._pil = _qimage_to_pil(self._src)      # 整幅画布转 PIL: 仅回落时付一次
        im = self._pil.resize((target_dev.width(), target_dev.height()), Image.LANCZOS)
        pm = QPixmap.fromImage(_pil_to_qimage(im))
        scale_ms = (time.perf_counter() - t0) * 1000
        logging.info(f"[上屏] 回落重采样 {self._pil.size[0]}x{self._pil.size[1]}"
                     f"-->{target_dev.width()}x{target_dev.height()} dpr={dpr:g} "
                     f"耗时 {scale_ms:.0f}ms (PIL LANCZOS)")
        return pm

    def set_geo(self, geom):
        # 间距段几何(画布像素): {canvas:(w,h), img:(x0,y0,x1,y1), gaps:{键:[(y0,y1),…]}}
        self._geom = geom or None
        self.update()

    def set_highlight(self, keys):
        keys = set(keys or ())
        _hover_trace("set_highlight(%s) 旧=%s" % (sorted(keys), sorted(self._hl)))
        if keys != self._hl:
            self._hl = keys
            self.update()

    def set_hint(self, text):
        self._hint = text
        self.update()

    def mouseMoveEvent(self, e):
        self._mag_pos = e.position()
        if self._pix is not None:
            self.update()

    def leaveEvent(self, _e):
        if self._mag_pos is not None:
            self._mag_pos = None
            self.update()

    def resizeEvent(self, e):      # noqa: N802 - Qt 命名
        # 尺寸一变, 上一帧的位图与矩形都失效: 收起底层并忘掉它, 之后一帧走原子替换
        self._stop_fade()
        self._last_pm = None
        self._last_box = None
        super().resizeEvent(e)

    def clear(self):
        self._stop_fade()
        self._pix = None
        self._src = None
        self._canvas = None
        self._shown_for = None
        self._pil = None
        self._scaled = None
        self._scaled_for = None
        self._last_pm = None
        self._last_box = None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 深色底取**与卡片同一档圆角**, 并裁剪到这条圆角路径上(图片/标注/放大镜一并被裁)。
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), theme.RADIUS_CARD, theme.RADIUS_CARD)
        p.setClipPath(path)
        p.fillPath(path, QColor("#333333"))
        if self._pix is None:
            p.setPen(QColor("#9aa0a6"))
            p.drawText(self.rect(), Qt.AlignCenter, self._hint)
            return
# SmoothPixmapTransform 是兜底: 目标矩形按设备像素对齐时是 1:1 拷贝, 不对齐(拖动窗口的小数位置)时开着最多一次极轻的重采样, 关着会变成最近邻
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        dpr = self.devicePixelRatioF()
        dev = self._fit_dev()
        # 渲染线程正是按"这个可用区尺寸"缩的图: 相符就直接贴, 不相符(窗口刚被拖动/换屏)才回落
        if self._shown_for == ((dev.width(), dev.height()), dpr):
            pm = self._pix
        else:
            # dpr 进缓存键: 移到另一块缩放率不同的屏上必须重算(否则会沿用旧 dpr 的位图)
            key = (dev, dpr)
            if self._scaled is None or self._scaled_for != key:
                self._scaled = self._scale_for_display(dev)
                self._scaled_for = key
            pm = self._scaled
        # 原点取整到**设备像素**: 位图不设 dpr, 由这里按设备几何给出目标矩形, 与屏幕 1:1。
        ox = round((self.width() * dpr - pm.width()) / 2) / dpr
        oy = round((self.height() * dpr - pm.height()) / 2) / dpr
        box = QRectF(ox, oy, pm.width() / dpr, pm.height() / dpr)
        if self._fade_active:
            # 淡入: 底层按原样铺上, 新帧叠着淡入 —— 两层加起来始终铺满, 中途不会透出深色底(透出来就是"变暗")
            # 底层为空(首次预览)时框里只有底色, 等于让画面从底色里浮出来
            t = self._fade_progress()
            if self._prev_pm is not None:
                self._draw_frame(p, self._prev_pm, self._prev_box)
            p.setOpacity(t)
            self._draw_frame(p, pm, box)
            p.setOpacity(1.0)
        else:
            self._draw_frame(p, pm, box)
        # 记下这一帧实际画出的位图与矩形: 下一帧据此沉底
        self._last_pm, self._last_box = pm, box
        self._paint_geom(p, box)
        self._paint_magnifier(p, box)

    def _draw_frame(self, p, pm, box):
        # 贴一帧: 位图不设 dpr, 目标矩形由调用方按设备几何给出
        p.drawPixmap(box, pm, QRectF(0, 0, pm.width(), pm.height()))

    def _paint_magnifier(self, p, target):
        # 放大镜: 固定预览框右上角, 内容随光标平移(光标所指的源点居中)
        # 按像素宽度从固有源重采样(不是把已缩小的位图再放大); 源是整幅画布的 QImage, 只取光标附近一小块, 不为它另留整幅 QPixmap
        pos = self._mag_pos
        src = self._src
        if pos is None or src is None or not target.contains(pos):
            return
        sw, sh = src.width(), src.height()
        dw, dh = target.width(), target.height()
        if dw <= 0 or dh <= 0 or sw <= 0 or sh <= 0:
            return
        src_w = min(float(sw), MAG_W * sw / (dw * MAG_ZOOM))
        src_h = min(float(sh), MAG_H * sh / (dh * MAG_ZOOM))
        cx = (pos.x() - target.left()) / dw * sw
        cy = (pos.y() - target.top()) / dh * sh
        # 贴边时夹住源矩形(而不是让 drawImage 拿到越界的源区)
        x0 = max(0.0, min(cx - src_w / 2, sw - src_w))
        y0 = max(0.0, min(cy - src_h / 2, sh - src_h))
        box = QRectF(self.width() - MAG_W - MAG_MARGIN, MAG_MARGIN, MAG_W, MAG_H)
        p.save()
# QPainter 的 Antialiasing 与 SmoothPixmapTransform 默认都是关的: 前者不开圆角出锯齿, 后者不开缩放成马赛克
# 主预览路径不受此约束: pixmap.scaled(..., Smooth) 自带平滑, 且其目标为矩形不需抗锯齿
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # **不做投影**。放大镜压在深色预览底上, 白色边框已足够分辨。

        clip = QPainterPath()
        clip.addRoundedRect(box, MAG_RADIUS, MAG_RADIUS)
        p.save()
        p.setClipPath(clip)
        p.fillRect(box, QColor("#222222"))
        p.drawImage(box, src, QRectF(x0, y0, src_w, src_h))
        p.restore()

        # 白色边框: 内缩半个笔宽, 否则一半压在裁剪外被切掉
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#ffffff"), 2))
        inner = box.adjusted(1, 1, -1, -1)
        p.drawRoundedRect(inner, MAG_RADIUS - 1, MAG_RADIUS - 1)
        p.restore()

    def _paint_geom(self, p, target):
        # 在预览图上叠加「当前悬停的那个间距」: 间距段画横向色带, image_margin 填图片四周的外边距区域; 未悬停不画
        g = self._geom
        if not g or not self._hl:
            _hover_trace("绘制跳过: geom=%s 高亮=%s" % ("有" if g else "无", sorted(self._hl)))
            return
# 这里画的是横平竖直的 2px 色带与矩形边, 关掉抗锯齿保持边缘利落(paintEvent 开头那把 Antialiasing 是给圆弧用的)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        cw, ch = g.get("canvas") or (0, 0)
        if not cw or not ch:
            return
        sx = target.width() / cw
        sy = target.height() / ch
        fill = QColor(255, 184, 0, 77)
        edge = QColor(255, 160, 0, 140)
        drew = []
        for key, segs in (g.get("gaps") or {}).items():
            if key not in self._hl:
                continue
            for y0, y1 in segs:
                r = QRectF(target.left(), target.top() + y0 * sy,
                           target.width(), max(2.0, (y1 - y0) * sy))
                p.fillRect(r, fill)
                p.setPen(QPen(edge, 2))
                p.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.top()))
                p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
                drew.append(f"{key}[{y0},{y1}]")
        ig = g.get("img")
        if ig and "image_margin" in self._hl:
            x0, y0, x1, y1 = ig
            rx0 = target.left() + x0 * sx
            ry0 = target.top() + y0 * sy
            rx1 = target.left() + x1 * sx
            ry1 = target.top() + y1 * sy
# 填「图片四周那一圈外边距」而不画压在图片上的细虚线框: 后者上下两条会被预览框圆角裁掉, 左右两条又糊在照片内容里
            ring = [QRectF(target.left(), target.top(), target.width(), ry0 - target.top()),
                    QRectF(target.left(), ry1, target.width(), target.bottom() - ry1),
                    QRectF(target.left(), ry0, rx0 - target.left(), ry1 - ry0),
                    QRectF(rx1, ry0, target.right() - rx1, ry1 - ry0)]
            for rr in ring:
                if rr.width() > 0.5 and rr.height() > 0.5:
                    p.fillRect(rr, fill)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(edge, 2))
            p.drawRect(QRectF(rx0, ry0, rx1 - rx0, ry1 - ry0))
            drew.append(f"外边距四块+图片框 {x0},{y0},{x1},{y1}->屏幕({rx0:.0f},{ry0:.0f},{rx1-rx0:.0f}x{ry1-ry0:.0f})")
        elif "image_margin" in self._hl:
            drew.append("图片外边距没画: geom 里没有 img")
        _hover_trace("绘制: 高亮=%s 画了[%s]" % (sorted(self._hl), "; ".join(drew) or "无"))


def _reset_button(slot):
    # 行尾 ↺ 按钮(参数行与「水印开关」的行共用): 常显、恒占位
    # 不做悬停显形: 判据只能靠光标轮询(参数行不接 Enter/Leave), 落不到「水印开关」那种不在参数行内的 ↺ 上, 会错显到别的行;
    # 也不做透明度渐隐: 卡片自带投影效果, 父级有图形效果时子控件的效果不生效(实测: 子按钮整个不绘制)
    b = QPushButton()
    b.setObjectName("ghost")
    b.setFixedWidth(_RESET_W)
    _iconify(b, theme.RESET_SOFT, _RESET_ICON)
    b.setToolTip(QCoreApplication.translate("ParamRow", "恢复默认值"))
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def _iconify(btn, asset, size=16):
    # 给按钮装一个 SVG 图标(界面图标只有这一种来源, 见 theme 的「界面图标」)
    # SVG 按 目标尺寸×dpr 栅格化, 缩放不失真; 置灰态由 QIcon 的 Disabled 模式自动变淡; size 需与按钮的 iconSize 一致
    btn.setIcon(QIcon(asset))
    btn.setIconSize(QSize(size, size))


class _IconLink(QLabel):
    # 只有图标、点击打开浏览器的控件(「关于」弹窗底部的 GitHub 标识); 带手型光标、tooltip(URL)与读屏名字
    # 图标按设备像素出图(QIcon.pixmap(size, dpr))上屏 1:1; 不用富文本 <a><img> 锚点(会经 QTextDocument 再缩放而发虚)

    def __init__(self, icon_path, size, url, accessible_name, parent=None):
        super().__init__(parent)
        self._url = url
        pm = QIcon(icon_path).pixmap(QSize(size, size), self.devicePixelRatioF())
        self.setPixmap(pm)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(url)
        self.setAccessibleName(accessible_name)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            QDesktopServices.openUrl(QUrl(self._url))


def _swatch_fg(color):
    # 色块上的前景色: 浅底用黑(#000)、深底用白(#fff)
    # 色块文字与选中态勾号素材共用这一条判据, 因此只写在这里一处
    return "#000" if QColor(color).lightness() > 128 else "#fff"


def _swatch_check(color):
    # 选中态勾号的素材路径: 与 _swatch_fg 同判据, 深浅底各一份(SVG 填充色写死)
    return theme.CHIP_CHECK_DARK if _swatch_fg(color) == "#000" else theme.CHIP_CHECK_LIGHT


def _swatch_qss(color, radius=theme.RADIUS_SMALL, selected=False):
    # 色块样式(取色弹窗的色块与「当前颜色」共用此定义)
    # 悬停/按下/聚焦都必须同时写出 background: 仅写 border 会落到主题的 QPushButton:hover, 鼠标移入后色块变灰
    # 四个状态的几何完全一致(margin 2px + border 2px), 仅描边颜色变化; 悬停只换成深一档的暖灰, 不改色相与粗细
    col = QColor(color)
    fg = _swatch_fg(color)
    ring, ring_hot = ((theme.ACCENT, theme.ACCENT2) if selected else (theme.LINE, theme.MUTED))
    return ("QPushButton { background:%s; border:2px solid %s; border-radius:%dpx; margin:2px;"
            " color:%s; font-weight:bold; padding:0; }"
            "QPushButton:hover, QPushButton:focus { background:%s; border:2px solid %s; }"
            "QPushButton:pressed { background:%s; border:2px solid %s; }"
            % (col.name(), ring, radius, fg,
               col.name(), ring_hot, col.name(), ring_hot))


class _ColorChip(QPushButton):
    # 取色弹窗里的一个色块: QPushButton 只有 clicked、没有 doubleClicked, 故自行转发鼠标双击事件(单击仍走 clicked)

    activated = Signal(str)
    edited = Signal(int)

    def __init__(self, color, index=-1, parent=None):
        super().__init__(parent)
        self.color = color
        self.index = index                    # 「我的颜色」里的序号; -1 = 预设色, 不可改
        self.clicked.connect(lambda: self.activated.emit(self.color))

    def mouseDoubleClickEvent(self, e):       # noqa: N802 - Qt 命名
        if self.index >= 0:
            self.edited.emit(self.index)
        else:
            super().mouseDoubleClickEvent(e)


class ElidedLabel(QLabel):
    # 超长文本(路径之类)的标签: 按控件宽度在中间省略
    # QLabel 自身不省略, 长路径会顶大 minimumSizeHint 并撑破布局

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(70)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)   # 允许选中以复制完整路径
        self.setToolTip(text)

    def setText(self, text):            # noqa: N802 - Qt 命名
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        fm = self.fontMetrics()
        p.drawText(self.rect(), int(self.alignment()),
                   fm.elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width()))
