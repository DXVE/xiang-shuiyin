"""「关于」弹窗。"""
from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
from core import __version__ as app_version
from core.config import BASE_DIR, ICONS_DIR
from .. import theme
from ..dialog import csdify, dlg_button, dlg_button_row, go_frameless
from .base import _DLG_PAD, _prep_dialog
from ..widgets import _IconLink
import os
import platform


# 项目仓库地址(同时用作 GitHub 图标的 tooltip)
REPO_URL = "https://github.com/your-name/ShuiYin-QT6"
_ABOUT_ICON = 20            # 「关于」弹窗里 GitHub 标识的显示边长(逻辑像素)


def _about_logo(size, dpr=1.0):
    # 关于框顶部的 logo: 按设备像素缩放, 避免 Qt 再缩放一次; 素材缺失时返回 None
    path = os.path.join(ICONS_DIR, "exif-watermark-logo-1024.png")
    pm = QPixmap(path)
    if pm.isNull():
        return None
    px = max(1, int(round(size * dpr)))
    pm = pm.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                   Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


def _about_label(text, obj_name, spacing=0.0):
    # 关于框里的一行文字: 字号/字重/颜色由 QSS 提供, 本函数只登记 QSS 表达不了的字距与「可选中」意图
    lab = QLabel(text)
    lab.setObjectName(obj_name)
    lab.setProperty("letterSpacing", float(spacing))
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lab


class AboutDialog(QDialog):
    # 关于弹窗: 顶部 logo + 程序名 + 一句话 + 版本与环境 + 数据目录 + 分发许可

    def __init__(self, parent=None):
        super().__init__(parent)
        go_frameless(self)
        self.setWindowTitle(self.tr("关于"))
        import platform

        from PySide6 import __version__ as pyside_version

        from core import __version__ as app_version

        v = QVBoxLayout(self)
        v.setContentsMargins(_DLG_PAD + 8, _DLG_PAD + 8, _DLG_PAD + 8, _DLG_PAD)
        v.setSpacing(0)

        logo = _about_logo(88, self.devicePixelRatioF())
        if logo is not None:
            lb = QLabel()
            lb.setPixmap(logo)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(lb)
            v.addSpacing(18)

        v.addWidget(_about_label(self.tr("「镶 · 水印」 水印处理工具"), "aboutName", spacing=6.0))
        v.addSpacing(6)
        v.addWidget(_about_label(self.tr("一帧入画　一镶成框"), "aboutSub", spacing=2.0))
        v.addSpacing(18)
        v.addWidget(_about_label(f"v{app_version}", "aboutVer"))
        v.addSpacing(14)
        # 环境两行: 版本号/系统名是数据, 只有模板串需要翻译
        v.addWidget(_about_label(self.tr("Python {py} · PySide6 {pyside}").format(
            py=platform.python_version(), pyside=pyside_version), "aboutInfo"))
        v.addSpacing(2)
        build = platform.version().rsplit(".", 1)[-1]
        v.addWidget(_about_label(f"{platform.system()} {platform.release()} · {build}",
                                 "aboutInfo"))
        v.addSpacing(8)
        v.addWidget(_about_label(self.tr("数据目录：{path}").format(path=BASE_DIR),
                                 "aboutInfo"))
        v.addSpacing(4)
        v.addWidget(_about_label(self.tr("本次会话统计：{stats}").format(
            stats=self._stats_text(parent)), "aboutInfo"))
        v.addSpacing(16)
        note = _about_label(self.tr(
            "分发时请遵守 PySide6 的 LGPLv3 与内置字体的 SIL OFL 1.1"
            "（详见仓库 THIRD_PARTY_LICENSES.md 与 assets/fonts/OFL.txt）。"),
            "aboutNote")
        note.setWordWrap(True)
        note.setMaximumWidth(420)
        v.addWidget(note)
        v.addSpacing(14)
# 仓库链接用按设备像素出图的标签: 富文本 <a><img> 会把 SVG 交给 QTextDocument 再光栅化一次而发虚
        v.addWidget(_IconLink(theme._asset("github.svg"), _ABOUT_ICON, REPO_URL,
                              self.tr("项目仓库（GitHub）"), self))
        v.addSpacing(14)
        v.addLayout(dlg_button_row(dlg_button(self.tr("关闭"), self.accept, primary=True)))
        for lab in self.findChildren(QLabel):
            if lab.text():
                lab.setTextInteractionFlags(lab.textInteractionFlags()
                                            | Qt.TextInteractionFlag.TextSelectableByMouse)
        csdify(self, self.tr("关于"), _prep_dialog)
        for lab in self.findChildren(QLabel):
            sp = lab.property("letterSpacing")
            if sp:
                f = lab.font()
                f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(sp))
                lab.setFont(f)

    @staticmethod
    def _stats_text(parent):
        # 会话统计文本; 挂在主窗口上, 取不到时返回空串
        try:
            return parent._stats.summary(compact=True)
        except Exception:                            # noqa: BLE001
            return ""
