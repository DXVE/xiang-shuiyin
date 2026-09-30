"""「型号映射」弹窗: 增 / 改 / 删即时写盘。"""
from PySide6.QtCore import QT_TRANSLATE_NOOP
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QVBoxLayout)
from core.config import DEFAULT_LOGO_DIR
from core.paths import load_model_map, save_model_map
from ..dialog import csdify, dlg_button, dlg_button_row, go_frameless
from .base import _message, _prep_dialog


class MapDialog(QDialog):
    # 型号映射表编辑弹窗: 列表 + 增删改; 映射表按 Logo 目录存 model_map.json

    def __init__(self, state, parent=None):
        super().__init__(parent)
        go_frameless(self)                 # 须在窗口创建之前调用(见 dialog.py)
        self.setWindowTitle(self.tr("型号映射管理"))
        self.resize(560, 460)
        self._data = {}
        try:
            self._logo_dir = (parent._params.get("logo_dir") if parent else "") or DEFAULT_LOGO_DIR
        except Exception:
            self._logo_dir = DEFAULT_LOGO_DIR

        v = QVBoxLayout(self)
        tip = QLabel(self.tr("识别到的原始型号 --> 水印上显示的型号。"
                             "在列表里选中一条即可修改或删除（删除前会先确认）。"))
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        v.addWidget(self._list, 1)

        form = QHBoxLayout()
        form.setSpacing(6)
        form.addWidget(QLabel(self.tr("原始")))
        self._raw = QLineEdit()
        self._raw.setPlaceholderText(self.tr("如 ILCE-7M4"))
        form.addWidget(self._raw, 1)
        form.addWidget(QLabel(self.tr("显示")))
        self._nice = QLineEdit()
        self._nice.setPlaceholderText(self.tr("如 Sony A7M4"))
        form.addWidget(self._nice, 1)
        v.addLayout(form)

        # `QT_TRANSLATE_NOOP` 必须包在字面量上(lupdate 看不穿变量), 否则整条漏译
        v.addLayout(dlg_button_row(
            dlg_button(self.tr(QT_TRANSLATE_NOOP("MapDialog", "删除选中")), self._remove),
            dlg_button(self.tr(QT_TRANSLATE_NOOP("MapDialog", "添加 / 修改")), self._add),
            dlg_button(self.tr("关闭"), self.accept, primary=True)))

        self._list.currentItemChanged.connect(self._on_select)
        try:
            self._data = dict(load_model_map(self._logo_dir) or {})
        except Exception:                           # noqa: BLE001 - 读不出就当空表
            self._data = {}
        self._reload()
        csdify(self, self.tr("型号映射管理"), _prep_dialog)

    def _reload(self, select=None):
        # 把内存中的映射表画进列表; select = 要选中的「原始型号」
        # 此处不读盘: 每次 load_model_map() 都会用文件内容覆盖刚写入的条目; 读盘只在 __init__, 写盘只在 _persist
        self._list.clear()
        for k in sorted(self._data):
            self._list.addItem(f"{k}   -->   {self._data[k]}")
        if select is None:
            return
        for i in range(self._list.count()):
            if self._item_key(self._list.item(i)) == select:
                self._list.setCurrentRow(i)        # 会连带触发 _on_select, 表单同步为该条
                self._list.scrollToItem(self._list.item(i))
                break

    @staticmethod
    def _item_key(item):
        # 从列表项文本「原始   -->   显示」中取出原始型号(分隔符须与拼串处一致)
        return item.text().split("   -->   ")[0]

    def _on_select(self, cur, _prev):
        if cur is None:
            return
        key = self._item_key(cur)
        self._raw.setText(key)
        self._nice.setText(self._data.get(key, ""))

    def _push_to_window(self):
        # 把表同步给主窗口的 _params["model_map"](撤销快照取自该字段), 顺带刷新「水印将显示」一行
        win = self.parent()
        if win is None or not hasattr(win, "_params"):
            return
        win._params["model_map"] = dict(self._data)
        upd = getattr(win, "_update_effective_model", None)
        if upd is not None:
            upd()

    def _apply_change(self, label, fn):
        # 执行一次改动并单独记一步撤销(增 / 改 / 删各一步, 步名进入撤销按钮 tooltip)
        win = self.parent()
        begin = getattr(win, "_begin_undo", None) if win is not None else None
        commit = getattr(win, "_commit_undo", None) if win is not None else None
        if begin is None or commit is None:          # 无主窗口时退化为直接执行
            fn()
            return
        begin("__model_map__", label)
        try:
            fn()
        finally:
            commit()

    def _add(self):
        raw = self._raw.text().strip()
        nice = self._nice.text().strip()
        if not raw:
            _message(self, QMessageBox.Icon.Information, self.tr("提示"), self.tr("请先填写「原始型号」"))
            return
        existed = raw in self._data
        label = (self.tr("修改映射「{raw}」") if existed
                 else self.tr("新增映射「{raw}」")).format(raw=raw)

        def _do():
            self._data[raw] = nice      # 允许显示名为空
            self._persist()             # 先落盘(失败会回滚 _data), 再同步给窗口
            self._push_to_window()
            self._reload(select=raw)    # 选中该条以标记刚编辑的项(见 _reload)

        self._apply_change(label, _do)

    def _remove(self):
        # 删除表单中的那条映射(执行前确认: 该动作即时写盘, 且按钮紧邻「添加 / 修改」)
        raw = self._raw.text().strip()
        if raw not in self._data:
            _message(self, QMessageBox.Icon.Information, self.tr("提示"),
                     self.tr("「原始型号」里现在这条不在映射表里，请先在列表里选中要删除的条目。"))
            return
        nice = self._data.get(raw) or ""
        if _message(self, QMessageBox.Icon.Question, self.tr("删除型号映射"),
                    self.tr("确定删除「{raw}」-->「{nice}」这条映射？").format(
                        raw=raw, nice=nice or self.tr("（显示为空）")),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        def _do():
            del self._data[raw]
            self._persist()
            self._push_to_window()
            self._reload()
            self._raw.clear()
            self._nice.clear()

        self._apply_change(self.tr("删除映射「{raw}」").format(raw=raw), _do)

    def _persist(self):
        # 立刻写盘(增 / 改 / 删均即时生效); 写失败时提示并把内存回滚为磁盘内容
        try:
            save_model_map(self._logo_dir, self._data)
        except Exception as e:                      # noqa: BLE001
            _message(self, QMessageBox.Icon.Warning, self.tr("保存失败"), str(e))
            try:
                self._data = dict(load_model_map(self._logo_dir) or {})
            except Exception:                       # noqa: BLE001
                self._data = {}
            self._reload()
