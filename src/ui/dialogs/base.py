"""弹窗公共部分: 无边框壳、显示前准备、按钮工厂、表单弹窗、文件/目录选择框与弹窗度量常量。"""
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QVBoxLayout)
from .. import theme
from ..dialog import MessageDialog, csdify, dlg_button, dlg_button_row, go_frameless
import logging
import os


_COLS_PRESET = 6   # 预设色每行几个(18 色 = 3 行)
_COLS_USER = _COLS_PRESET   # 与预设色同列数: 两组色板列宽一致, 上下对齐


def _prep_dialog(dlg):
    # 对话框显示前定死尺寸与位置, 并离屏预演一次完整显示流程
    # ensurePolished() 不可省: 未上样式表时 sizeHint 偏小, 显示后布局定稿会再撑大一次
    dlg.ensurePolished()
    dlg.adjustSize()
    p = dlg.parentWidget()
    if p is not None:
        host = p.window()
        c = host.frameGeometry().center()
        x = c.x() - dlg.width() // 2
        y = c.y() - dlg.height() // 2
# 父窗跨屏或贴边时会把对话框推出可用区, 需拉回(否则 Qt 会重新摆位一次越界窗口, 表现为先闪一下再跳)
        scr = host.screen()
        if scr is not None:
            a = scr.availableGeometry()
            x = max(a.left(), min(x, a.right() - dlg.width()))
            y = max(a.top(), min(y, a.bottom() - dlg.height()))
        dlg.move(x, y)

    primed = dlg.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dlg.show()
    dlg.hide()
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, primed)


def _message(parent, icon, title, text,
             buttons=QMessageBox.StandardButton.Ok, default=None):
    # 消息框: 返回被点中的标准按钮(与 QMessageBox.exec() 的返回值口径一致)
    box = MessageDialog(parent, icon, title, text, buttons, default, recenter=_prep_dialog)
    box.exec()
    return box.result_button


def _save_file_dialog(parent, caption, directory="", default_name="", name_filter=""):
    # 另存为一个文件; 取消返回空串
    dlg = QFileDialog(parent, caption)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dlg.setFileMode(QFileDialog.FileMode.AnyFile)
    if directory:
        dlg.setDirectory(directory)
    if default_name:
        dlg.selectFile(default_name)
    if name_filter:
        dlg.setNameFilter(name_filter)
    dlg.setDefaultSuffix("json")            # 用户不打扩展名时补上, 否则存成无类型文件
    _prep_dialog(dlg)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    files = dlg.selectedFiles()
    return files[0] if files else ""


def _open_and_report(win, path, what):
    # 打开一个文件或目录, 结果用吐司提示(失败只提示并记日志, 不弹窗)
    if not os.path.exists(path):
        win._notify(f"{what}不存在：{path}")
        return False
    try:
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception as e:                      # noqa: BLE001
        ok = False
        logging.warning(f"[打开] {what} 失败 {path}: {e}")
    win._notify(f"已打开{what}：{path}" if ok else f"打不开，路径是：{path}")
    return bool(ok)


def _open_file_dialog(parent, caption, directory="", name_filter=""):
    # 选单个文件; 取消返回空串
    dlg = QFileDialog(parent, caption)
    dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    if directory:
        dlg.setDirectory(directory)
    if name_filter:
        dlg.setNameFilter(name_filter)
    _prep_dialog(dlg)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    files = dlg.selectedFiles()
    return files[0] if files else ""


def _open_dir_dialog(parent, caption, directory=""):
    # 选目录; 取消返回空串
    dlg = QFileDialog(parent, caption)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    if directory:
        dlg.setDirectory(directory)
    _prep_dialog(dlg)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    files = dlg.selectedFiles()
    return files[0] if files else ""


class _FormDialog(QDialog):
    # 通用表单弹窗: 若干「标签 + 单行输入」, 确定返回 {key: 去空白后的文本}, 取消返回 None

    def __init__(self, parent, title, fields, ok_text=None):
        super().__init__(parent)
        go_frameless(self)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self._values = None
        v = QVBoxLayout(self)
        self._edits = []
        for f in fields:
            row = QHBoxLayout()
            row.setSpacing(theme.GAP_CTRL)
            lab = QLabel(f.get("label", ""))
            lab.setObjectName("fieldLabel")
            lab.setWordWrap(True)
            row.addWidget(lab)
            e = QLineEdit(str(f.get("value", "")))
            if f.get("placeholder"):
                e.setPlaceholderText(f["placeholder"])
            row.addWidget(e, 1)
            v.addLayout(row)
            self._edits.append((f["key"], e))
        v.addSpacing(6)
        v.addLayout(dlg_button_row(
            dlg_button(ok_text or self.tr("确定"), self._ok, primary=True),
            dlg_button(self.tr("取消"), self.reject)))
        csdify(self, title, _prep_dialog)
        if self._edits:
            self._edits[0][1].setFocus()

    def _ok(self):
        # 收集各输入框文本(去首尾空白); 非空校验由调用方按字段语义决定
        self._values = {k: e.text().strip() for k, e in self._edits}
        self.accept()

    def values(self):
        return self._values


def ask_fields(parent, title, fields, ok_text=None):
    # 弹出表单并等待: 取消返回 None, 否则返回 {key: 文本}
    dlg = _FormDialog(parent, title, fields, ok_text)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()


_CHIP_W = theme.CTRL_H + 20          # 色块尺寸: 宽于控件高度, 便于点击与辨识
_CHIP_H = theme.CTRL_H
_CHIP_GAP = theme.GAP_CTRL
# 弹窗四周留白: 兼作卡片投影的外扩余量(投影贴边会被裁成直角)
_DLG_PAD = 12
# 弹窗最小宽 = 一行色块 + 卡片内边距 + 弹窗留白, 使色板恰好铺满卡片宽度
_DLG_W = (_COLS_PRESET * _CHIP_W + (_COLS_PRESET - 1) * _CHIP_GAP
          + 2 * theme.PAD_CARD + 2 * _DLG_PAD)
# 「当前颜色」色块: 与色板同高但更宽, 是这张卡的焦点而非网格里的一格
_CUR_W = theme.CTRL_H * 2 + 8
