"""「预设方案」弹窗: 竖向列出槽位, 右键执行加载 / 保存 / 导出 / 删除 / 导入。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QMenu, QVBoxLayout,
                               QWidget)
from core.config import PRESETS_DIR
from .. import theme
from ..dialog import csdify, dlg_button, dlg_button_row, go_frameless
from ..widgets import ElidedLabel
from ..winconst import _PRESET_SLOTS
from .base import _open_and_report, _prep_dialog, ask_fields

_SLOT_LIST_H = 220   # 槽位列表高度: 装不下 10 个槽位时滚动


class PresetDialog(QDialog):
    # 预设方案弹窗: 槽位竖排列表, 保存时弹子弹窗输入方案名; 加载 / 保存 / 导出 / 删除 / 导入全在右键菜单里
    # 每次打开都重新读盘(手工放入 presets5.json 无需重启即可见); 控件在本类、逻辑在 MainWindow

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        go_frameless(self)
        self.setWindowTitle(self.tr("预设方案"))
        self.setMinimumWidth(560)     # 与 webview 版弹窗同宽: 路径行不被过度省略

        def path_block(label, path, opener):
            # 目录行: 标签单独占一行(固定位置的长路径与「打开目录」并排时不再被挤窄)
            box = QWidget()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(0, 2, 0, 2)
            bv.setSpacing(4)
            lab = QLabel(label)
            lab.setObjectName("fieldLabel")
            bv.addWidget(lab)
            inner = QWidget()
            h = QHBoxLayout(inner)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(theme.GAP_CTRL)
            el = ElidedLabel(path)
            el.setObjectName("muted")
            el.setToolTip(path)
            h.addWidget(el, 1)
            h.addWidget(dlg_button(self.tr("打开目录"), opener))
            bv.addWidget(inner)
            return box

        v = QVBoxLayout(self)

        v.addWidget(path_block(
            self.tr("预设目录"), PRESETS_DIR,
            lambda: _open_and_report(win, PRESETS_DIR, self.tr("预设目录"))))

        title = QLabel(self.tr("预设方案（{n} 个槽位，一槽一文件）").format(n=_PRESET_SLOTS))
        title.setObjectName("groupTitle")
        v.addWidget(title)

        hint = QLabel(self.tr("右键槽位可执行 加载 / 保存 / 导出 / 导入 / 删除"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.list = QListWidget()
        self.list.setObjectName("presetList")
        self.list.setFixedHeight(_SLOT_LIST_H)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._slot_menu)
        v.addWidget(self.list)

        v.addStretch(1)
        v.addLayout(dlg_button_row(dlg_button(self.tr("关闭"), self.reject, primary=True)))
        csdify(self, self.tr("预设方案"), _prep_dialog)
        self.reload()

    def _slot_menu(self, pos):
        # 右键槽位: 先选中该槽位(后续动作读 current_slot), 再按占位与否给菜单
        item = self.list.itemAt(pos)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole) or ""
        if not key:
            return
        self.select_slot(key)
        rec = (getattr(self.win, "_preset_data", {}) or {}).get(key)
        occupied = isinstance(rec, dict)
        win = self.win
        menu = QMenu(self)
        if occupied:
            menu.addAction(self.tr("加载"), lambda: win._preset_load(self))
        menu.addAction(self.tr("保存"), lambda: win._preset_save(self))
        if occupied:
            menu.addAction(self.tr("导出"), lambda: win._export_preset(self))
            menu.addAction(self.tr("删除"), lambda: win._preset_delete(self))
        menu.addAction(self.tr("导入"), lambda: win._import_preset(self))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def current_slot(self):
        item = self.list.currentItem()
        return (item.data(Qt.ItemDataRole.UserRole) if item is not None else "") or ""

    def select_slot(self, key):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.list.setCurrentRow(i)
                self.list.scrollToItem(self.list.item(i))
                return

    def ask_name(self, initial=""):
        # 保存时弹出子弹窗输入方案名; 取消返回 None(空串由调用方按“名称必填”处理)
        vals = ask_fields(self, self.tr("保存预设"), [
            {"key": "name", "label": self.tr("方案名"), "value": initial,
             "placeholder": self.tr("为方案命名")}],
            ok_text=self.tr("保存"))
        return vals["name"] if vals else None

    def reload(self):
        self.win._reload_presets(self)
