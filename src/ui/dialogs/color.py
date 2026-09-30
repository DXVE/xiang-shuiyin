"""取色弹窗: 当前颜色 + 预设色 + 我的颜色。"""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QColorDialog, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget)
from .. import theme
from ..dialog import csdify, dlg_button, go_frameless
from .base import (_CHIP_GAP, _CHIP_H, _CHIP_W, _COLS_PRESET, _COLS_USER, _CUR_W, _DLG_PAD,
                   _DLG_W, _message, _prep_dialog)
from ..widgets import _ColorChip, _swatch_check, _swatch_qss


# 色块选中态勾号的素材边长（逻辑像素）。
_CHIP_CHECK = 14


# 预设色板(灰阶 / 莫兰迪 / 浅色清新, 每行一个色系)
PRESET_COLORS = [
    '#FFFFFF', '#F0F0F0', '#D6D6D6', '#B0B0B0', '#808080', '#404040', '#000000',   # 灰阶
    '#6F9C7D', '#7C98A6', '#B0676B', '#B8895E', '#C9A227',                          # 莫兰迪
    '#F7E8D0', '#A8D0E6', '#F5D6D6', '#D5E8D4', '#DCE4EE', '#FFF3CD',               # 浅色清新
]
MAX_USER_COLORS = 10


class ColorDialog(QDialog):
    # 取色弹窗(点参数行的色块打开): 当前颜色 + 预设颜色 + 我的颜色 三张卡片
    # 点色块即时应用(弹窗不关, 可边点边看效果); 收藏色存 settings.json 的 custom_colors(核心截到 10 个)

    picked = Signal(str)

    def __init__(self, state, current, parent=None):
        super().__init__(parent)
        go_frameless(self)                 # 必须在窗口被创建之前(见 dialog.py)
        self.setWindowTitle(self.tr("选择颜色"))
        self.setMinimumWidth(_DLG_W)
        self._state = state
        self._current = current or "#FFFFFF"
        self._chips = []                      # 所有色块按钮, 用来刷选中态

        v = QVBoxLayout(self)
        v.setContentsMargins(_DLG_PAD, _DLG_PAD, _DLG_PAD, _DLG_PAD)
        v.setSpacing(theme.GAP_CARD)

        v.addWidget(self._current_card())
        v.addWidget(self._preset_card())
        v.addWidget(self._custom_card())

        acts = QHBoxLayout()
        acts.setSpacing(theme.GAP_CTRL)
        b = dlg_button(self.tr("保存当前颜色"), self._remember)
        b.setToolTip(self.tr("将当前颜色保存到「我的颜色」，下次可直接使用"))
        acts.addWidget(b)
        acts.addStretch(1)
        acts.addWidget(dlg_button(self.tr("关闭"), self.accept, primary=True))
        v.addLayout(acts)

# 初始焦点收到对话框自身, 不落在子控件上: 落到色块或色号框会先按无焦点绘制、随后补上焦点态, 表现为打开时闪一下
# 该轮不得有子控件持有焦点; 焦点在对话框上时 Tab 仍照常进入各控件
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

        self._sync_current()
        csdify(self, self.tr("选择颜色"), _prep_dialog)

    # ---- 取色弹窗自身的构造 ----
    def _card(self, title, note=""):
        # 分组卡片: 与主窗 Card 同一套外观, 但没有「重置本组」
        # 返回 (frame, body, note_label); note 是标题右侧的补充说明(如容量), 用 hint 色
        frame = QFrame()
        frame.setObjectName("card")
        theme.apply_shadow(frame)
        v = QVBoxLayout(frame)
        v.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD - 2, theme.PAD_CARD, theme.PAD_CARD)
        v.setSpacing(theme.GAP_ROW)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(title)
        lab.setObjectName("cardTitle")
        head.addWidget(lab)
        head.addStretch(1)
        note_lab = QLabel(note)
        note_lab.setObjectName("hint")
        head.addWidget(note_lab)
        v.addLayout(head)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(theme.GAP_ROW)
        v.addLayout(body)
        return frame, body, note_lab

    def _current_card(self):
        # 当前颜色: 一格色块(点它开系统取色器) + 一个色号输入框; 色块只表达颜色, 色号只由输入框显示与编辑
        frame, body, _ = self._card(self.tr("当前颜色"))
        row = QHBoxLayout()
        row.setSpacing(theme.GAP_CTRL)
        self._cur_chip = QPushButton()
        self._cur_chip.setFixedSize(_CUR_W, theme.CTRL_H)
        self._cur_chip.setCursor(Qt.PointingHandCursor)
        self._cur_chip.setAccessibleName(self.tr("当前颜色"))   # 按钮内无文字, 需提供读屏名称
        self._cur_chip.setToolTip(self.tr("点击打开系统取色器（可拖动取色、调透明度）"))
        self._cur_chip.clicked.connect(self._native)
        row.addWidget(self._cur_chip)
        self._hex = QLineEdit()
        self._hex.setFixedHeight(theme.CTRL_H)
        self._hex.setMaxLength(9)
        self._hex.setAccessibleName(self.tr("色号"))   # 卡片标题不能作为该输入框的标签, 读屏名称另设
        self._hex.setToolTip(self.tr("直接输入色号（如 #6F9C7D），回车生效"))
        self._hex.editingFinished.connect(self._apply_hex)
        row.addWidget(self._hex, 1)
        body.addLayout(row)

        hint = QLabel(self.tr("点击色块打开取色器，也可直接输入色号"))
        hint.setObjectName("hint")
        body.addWidget(hint)
        return frame

    def _preset_card(self):
        frame, body, _ = self._card(self.tr("预设颜色"))
        body.addWidget(self._grid(PRESET_COLORS, _COLS_PRESET))
        return frame

    def _custom_card(self):
        frame, body, note = self._card(self.tr("我的颜色"))
        self._custom_note = note
        # `_reload_custom` 重建色板时的锚点: 必须取卡片 body, 用对话框顶层布局 indexOf 会返回 -1
        self._custom_body = body
        self._custom_box = self._grid(self._custom(), _COLS_USER,
                                      total=MAX_USER_COLORS, editable=True)
        body.addWidget(self._custom_box)
        tip = QLabel(self.tr("点击色块即应用；双击修改颜色，右键删除"))
        tip.setObjectName("hint")
        body.addWidget(tip)
        self._sync_custom_note()
        return frame

    def _sync_custom_note(self):
        # 在卡片标题右侧刷新「已保存 n / 共 total」计数
        self._custom_note.setText(self.tr("已保存 {n} / {total}").format(n=len(self._custom()), total=MAX_USER_COLORS))

    def _custom(self):
        try:
            return list((self._state.get_custom_colors() or {}).get("colors") or [])
        except Exception:                     # noqa: BLE001 - 读不到就当没有收藏色
            return []

    def _grid(self, colors, cols, total=0, editable=False):
        # 色块网格: total>0 时固定画满 total 个位置, 未存色的位置为浅底空位格
        # 末列留一条伸缩位: 色块定宽, 拉宽弹窗时保持左对齐而不被均匀拉开
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(_CHIP_GAP)
        for i in range(max(len(colors), total)):
            g.addWidget(self._chip(colors[i] if i < len(colors) else None,
                                   i if editable else -1, editable),
                        i // cols, i % cols)
        g.setColumnStretch(cols, 1)
        return box

    def _chip(self, color, index=-1, editable=False):
        if color is None:                     # 空位格: 浅实底、无描边、不可点
            lab = QLabel()
            lab.setFixedSize(_CHIP_W, _CHIP_H)
            lab.setAccessibleName(self.tr("未使用"))
            lab.setToolTip(self.tr("未使用"))
            lab.setStyleSheet("background:%s; border:none; border-radius:%dpx;"
                              % (theme.EMPTY_SLOT_BG, theme.RADIUS_SMALL))
            return lab
        b = _ColorChip(color, index)
        b.setFixedSize(_CHIP_W, _CHIP_H)
        b.setCursor(Qt.PointingHandCursor)
        b.setAccessibleName(self.tr("颜色 {color}").format(color=color))   # 色块只绘制颜色, 读屏需靠名字辨认
        b.setToolTip(color + (self.tr("　双击修改颜色 · 右键删除") if editable
                                     else self.tr("　点击应用")))
        self._paint_chip(b, color)
        b.activated.connect(self._choose)
        if editable:
            b.edited.connect(self._edit_saved)
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda _pos, i=index, col=color, btn=b: self._chip_menu(i, col, btn))
        self._chips.append(b)
        return b

    def _paint_chip(self, btn, color):
        # 色块 = 底色 + 选中态(勾号素材与强调色描边); 新建与刷新统一走此处, 避免两处状态不一致
        # 勾号用素材而非字形: 界面字体是内置子集, 不含 ✓(U+2713)
        selected = color.lower() == self._current.lower()
        btn.setText("")
        btn.setIcon(QIcon(_swatch_check(color)) if selected else QIcon())
        btn.setIconSize(QSize(_CHIP_CHECK, _CHIP_CHECK))
        btn.setStyleSheet(_swatch_qss(color, selected=selected))

    def _sync_current(self):
        # 把「当前颜色」格与色号框刷成 self._current, 并重刷所有色块的选中态
        self._cur_chip.setStyleSheet(
            _swatch_qss(self._current, radius=theme.RADIUS_CTRL))
        self._hex.setText(self._current)
        for b in self._chips:
            self._paint_chip(b, b.color)

    def _choose(self, color):
        self._current = color
        self._sync_current()
        self.picked.emit(color)

    def _apply_hex(self):
        # 色号输入框: 回车 / 失焦时生效; 输入不合法则回退为当前色号, 不修改参数
        txt = self._hex.text().strip()
        col = QColor(txt if txt.startswith("#") else "#" + txt)
        if not txt or not col.isValid() or col.name().lower() == QColor(self._current).name().lower():
            self._sync_current()
            return
        self._choose(col.name())

    def _pick_native(self, initial=None):
        # 开系统取色器, 返回选中的色号(取消返回 None)
        # 自行构造对话框并经 _prep_dialog 固定位置: QColorDialog.getColor() 这类静态函数显示后 Qt 会再调一次位置
        dlg = QColorDialog(QColor(initial or self._current), self)
        dlg.setWindowTitle(self.tr("选择颜色"))
        _prep_dialog(dlg)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        c = dlg.currentColor()
        return c.name() if c.isValid() else None

    def _native(self):
        c = self._pick_native()
        if c:
            self._choose(c)

    def _save_custom(self, arr):
        try:
            self._state.save_custom_colors(arr)      # 上限由核心截断到 MAX_USER_COLORS
        except Exception as e:                       # noqa: BLE001 - 存不上不该把弹窗弄崩
            _message(self, QMessageBox.Icon.Warning, self.tr("保存失败"), str(e))
            return False
        self._reload_custom()
        return True

    def _remember(self):
        cur = self._current
        arr = [c for c in self._custom() if c.lower() != cur.lower()]
        arr.append(cur)                  # 重复项移到末尾: 该列按最近保存排序
        if len(arr) > MAX_USER_COLORS:
            # 超出上限时先挤掉最早项: 核心按 `[:10]` 截断, 直接传 11 项会把刚保存的那项截掉
            arr = arr[len(arr) - MAX_USER_COLORS:]
        self._save_custom(arr)

    def _remove(self, color):
        # 从「我的颜色」中删除指定色号
        self._save_custom([c for c in self._custom() if c.lower() != color.lower()])

    def _edit_saved(self, index):
        # 把「我的颜色」第 index 项替换为新选的颜色(双击色块或右键菜单进入)
        arr = self._custom()
        if not 0 <= index < len(arr):
            return
        c = self._pick_native(arr[index])
        if not c:
            return
        arr[index] = c
        if self._save_custom(arr):
            self._choose(c)

    def _chip_menu(self, index, color, btn):
        menu = QMenu(self)
        menu.addAction(self.tr("修改该位置的颜色…")).triggered.connect(lambda: self._edit_saved(index))
        menu.addAction(self.tr("删除该颜色")).triggered.connect(lambda: self._remove(color))
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _reload_custom(self):
        # 就地重建「我的颜色」色板, 保持原位置
        # 定位须用卡片自己的 body 布局(_custom_body): 用对话框顶层布局 indexOf 会返回 -1, insertWidget(-1) 会把新色板追加到弹窗末尾
        body = self._custom_body
        idx = body.indexOf(self._custom_box)
        # 旧色块需同步从选中态列表摘除: 它们随后被销毁, 保留会让 _sync_current 访问已析构对象
        self._chips = [c for c in self._chips if not self._custom_box.isAncestorOf(c)]
        box = self._grid(self._custom(), _COLS_USER, total=MAX_USER_COLORS, editable=True)
        body.removeWidget(self._custom_box)
        self._custom_box.setParent(None)
        self._custom_box.deleteLater()
        self._custom_box = box
        body.insertWidget(idx, box)
        self._sync_custom_note()      # 同步容量计数, 否则停留在旧值
