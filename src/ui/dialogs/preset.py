"""「预设方案」弹窗: 槽位选择与命名。"""
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget
from core.config import PRESETS_DIR
from .. import theme
from ..dialog import csdify, dlg_button, dlg_button_row, go_frameless
from .base import _open_and_report, _prep_dialog
from ..widgets import ElidedLabel, NoWheelComboBox, _LABEL_W


class PresetDialog(QDialog):
    # 预设方案弹窗: 槽位 / 名称 / 加载·保存·删除, 外加 导入·导出 与 目录入口
    # 每次打开都重新读盘(手工放入 presets5.json 无需重启即可见); 控件在本类、逻辑在 MainWindow

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        go_frameless(self)
        self.setWindowTitle(self.tr("预设方案"))
        self.resize(560, 250)

        def row(label, widgets):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 2, 0, 2)
            h.setSpacing(theme.GAP_CTRL)
            lab = QLabel(self.tr(label))
            lab.setObjectName("fieldLabel")
            lab.setFixedWidth(_LABEL_W)
            lab.setWordWrap(True)
            h.addWidget(lab)
            for i, sub in enumerate(widgets):
                h.addWidget(sub, 1 if i == 0 else 0)
            return w

        v = QVBoxLayout(self)
        tip = QLabel(self.tr("一个槽位一个文件（presets1..10.json）；导入将保存到当前选中的槽位。"))
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self.combo = NoWheelComboBox()
        v.addWidget(row("槽位", [self.combo]))

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self.tr("方案名（保存时必填）"))
        v.addWidget(row("名称", [
            self.name_edit,
            dlg_button(self.tr("加载"), lambda: win._preset_load(self)),
            dlg_button(self.tr("保存"), lambda: win._preset_save(self)),
            dlg_button(self.tr("删除"), lambda: win._preset_delete(self))]))

        self.dir_label = ElidedLabel(PRESETS_DIR)
        self.dir_label.setObjectName("muted")
        self.dir_label.setToolTip(PRESETS_DIR)
        v.addWidget(row("目录", [
            self.dir_label,
            dlg_button(self.tr("打开目录"),
                       lambda: _open_and_report(win, PRESETS_DIR, self.tr("预设目录")))]))
        v.addStretch(1)
        v.addLayout(dlg_button_row(
            dlg_button(self.tr("导入…"), lambda: win._import_preset(self)),
            dlg_button(self.tr("导出…"), lambda: win._export_preset(self)),
            dlg_button(self.tr("关闭"), self.reject, primary=True)))
        csdify(self, self.tr("预设方案"), _prep_dialog)
        self.reload()

    def current_slot(self):
        return self.combo.currentData() or ""

    def name_text(self):
        return self.name_edit.text().strip()

    def set_name(self, text):
        self.name_edit.setText(text)

    def reload(self):
        self.win._reload_presets(self)
