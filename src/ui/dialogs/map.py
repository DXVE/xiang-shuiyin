"""「型号映射」弹窗: 添加 / 修改走子弹窗, 右键列表项修改 / 删除, 改动即时写盘。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QListWidget, QMenu, QMessageBox, QVBoxLayout
from core.config import DEFAULT_LOGO_DIR
from core.paths import load_model_map, save_model_map
from ..dialog import csdify, dlg_button, dlg_button_row, go_frameless
from .base import _message, _prep_dialog, ask_fields


class MapDialog(QDialog):
    # 型号映射表编辑弹窗: 添加 / 修改走子弹窗(原始 + 显示), 右键列表项修改 / 删除; 映射表按 Logo 目录存 model_map.json

    def __init__(self, state, parent=None):
        super().__init__(parent)
        go_frameless(self)                 # 须在窗口创建之前调用(见 dialog.py)
        self.setWindowTitle(self.tr("型号映射管理"))
        self.resize(560, 460)
        self.setMinimumWidth(560)     # 与 webview 版弹窗同宽: 表单与列表不被挤窄
        self._data = {}
        self.changed = False               # 本次弹窗是否真的增删改过映射表: 主窗据此决定关窗后要不要补一次重渲
        try:
            self._logo_dir = (parent._params.get("logo_dir") if parent else "") or DEFAULT_LOGO_DIR
        except Exception:
            self._logo_dir = DEFAULT_LOGO_DIR

        v = QVBoxLayout(self)
        tip = QLabel(self.tr("识别到的原始型号 --> 水印上显示的型号。"
                             "点「添加」新增；右键列表项可修改或删除（删除前会确认），改动立即保存。"))
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self._list = QListWidget()
        self._list.setObjectName("mapList")
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._item_menu)
        v.addWidget(self._list, 1)

        v.addLayout(dlg_button_row(
            dlg_button(self.tr("添加"), self._add_entry, primary=True),
            dlg_button(self.tr("关闭"), self.accept)))

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
                self._list.setCurrentRow(i)        # 选中该条: 标记刚编辑的项
                self._list.scrollToItem(self._list.item(i))
                break

    @staticmethod
    def _item_key(item):
        # 从列表项文本「原始   -->   显示」中取出原始型号(分隔符须与拼串处一致)
        return item.text().split("   -->   ")[0]

    def _item_menu(self, pos):
        # 右键列表项: 先选中该项, 再给 修改 / 删除 两项菜单(与预设弹窗同一套交互)
        item = self._list.itemAt(pos)
        if item is None:
            return
        raw = self._item_key(item)
        self._list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(self.tr("修改"), lambda: self._edit_entry(raw))
        menu.addAction(self.tr("删除"), lambda: self._remove_entry(raw))
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _ask_entry(self, raw="", nice=""):
        # 弹出子弹窗编辑一条映射: 确定返回 (原始型号, 显示型号), 取消返回 None
        vals = ask_fields(self, self.tr("型号映射"), [
            {"key": "raw", "label": self.tr("原始型号"), "value": raw,
             "placeholder": self.tr("如 ILCE-7M4")},
            {"key": "nice", "label": self.tr("显示型号"), "value": nice,
             "placeholder": self.tr("如 Sony A7M4")}])
        return (vals["raw"], vals["nice"]) if vals else None

    def _add_entry(self):
        # 「添加」: 子弹窗输入一条; 原始型号已存在时视为修改(名字与入口一致)
        res = self._ask_entry()
        if not res:
            return
        raw, nice = res
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

    def _edit_entry(self, raw):
        # 右键「修改」: 子弹窗预填该条, 确定后写回(改了原始型号就是改名)
        if raw not in self._data:
            return
        res = self._ask_entry(raw, self._data.get(raw) or "")
        if not res:
            return
        new_raw, nice = res
        if not new_raw:
            _message(self, QMessageBox.Icon.Information, self.tr("提示"), self.tr("请先填写「原始型号」"))
            return

        def _do():
            if new_raw != raw:
                del self._data[raw]
            self._data[new_raw] = nice
            self._persist()
            self._push_to_window()
            self._reload(select=new_raw)

        self._apply_change(self.tr("修改映射「{raw}」").format(raw=raw), _do)

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
        # 同时置 changed: 只有真改过, 主窗关窗后才有必要重渲
        win = self.parent()
        begin = getattr(win, "_begin_undo", None) if win is not None else None
        commit = getattr(win, "_commit_undo", None) if win is not None else None
        if begin is None or commit is None:          # 无主窗口时退化为直接执行
            fn()
            self.changed = True
            return
        begin("__model_map__", label)
        try:
            fn()
            self.changed = True
        finally:
            commit()

    def _remove_entry(self, raw):
        # 右键「删除」: 确认后删掉该条(即时写盘)
        if raw not in self._data:
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
