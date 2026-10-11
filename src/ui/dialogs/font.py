"""「字体文件」弹窗: 四个字重槽位各自选择与重置。"""
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
from core.params import default_params
from ..dialog import csdify, dlg_button, dlg_button_row, go_frameless
from .base import _prep_dialog
from ..specs import PARAM_SPECS
from ..widgets import ParamRow


class FontDialog(QDialog):
    # 「字体文件」管理弹窗: 四个字重槽位各自选择与重置, 改动即时生效(撤销 / 写设置 / 重渲预览三条链路)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        go_frameless(self)                         # 须在窗口创建之前调用(见 dialog.py)
        self.setWindowTitle(self.tr("字体文件"))
        self.setMinimumWidth(560)

        v = QVBoxLayout(self)
        tip = QLabel(self.tr("四种字重各一个字体文件（常规 / 粗体 / 斜体 / 粗斜体）；改完立即生效。"))
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self._rows = {}
        for s in [s for s in PARAM_SPECS if s.get("dialog") == "fonts"]:
            init = parent._params.get(s["key"]) if parent is not None else None
            row = ParamRow(s, init)
            if parent is not None:
                row.changed.connect(parent._on_param)
            self._rows[s["key"]] = row
            v.addWidget(row)

        v.addSpacing(6)
        v.addLayout(dlg_button_row(
            dlg_button(self.tr("恢复默认字体"), self._reset, primary=True),
            dlg_button(self.tr("关闭"), self.accept)))
        csdify(self, self.tr("字体文件"), _prep_dialog)

    def _reset(self):
        # 四个路径恢复出厂默认, 整批计入撤销的一步
        # 须直接改 _params 并回填控件: 走 _on_param 会自开撤销事务, 把整批一步拆成四步
        win = self.parent()
        defaults = default_params()
        keys = list(self._rows)

        def _do():
            for key in keys:
                if key not in defaults:
                    continue
                if win is not None:
                    win._params[key] = defaults[key]
                self._rows[key].set_value(defaults[key])
            if win is not None and hasattr(win, "_touch"):
                win._touch()

        step = getattr(win, "_undo_step", None) if win is not None else None
        if step is None:                           # 无主窗口时只处理自身
            _do()
        else:
            step("__reset_fonts__", self.tr("重置「字体文件」"), _do)
        notify = getattr(win, "_notify", None) if win is not None else None
        if notify is not None:
            notify(self.tr("已重置「{title}」为默认值").format(title=self.tr("字体文件")))
