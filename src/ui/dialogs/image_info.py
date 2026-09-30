"""「EXIF 信息」只读弹窗: 如实报照片自身读数, 不推算成品。"""
from PIL import Image as _Image
from PySide6.QtCore import QT_TRANSLATE_NOOP
from PySide6.QtWidgets import QDialog, QGridLayout, QLabel, QVBoxLayout
from core import app as core
from datetime import datetime
from .. import theme
from ..dialog import csdify, dlg_button, dlg_button_row, go_frameless
from .base import _prep_dialog
from ..widgets import ElidedLabel, _LABEL_W
import os


class ImageInfoDialog(QDialog):
    # 图片信息: 如实报告该图读到的内容(文件信息只读文件头 + 照片自带的 EXIF), 不推算成品会印成什么

    def __init__(self, path, parent=None):
        super().__init__(parent)
        go_frameless(self)
        self.setWindowTitle(self.tr("图片信息"))
        self.resize(540, 420)

        info = self._collect(path, parent)
        photo = info["photo"]

        v = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.GAP_CTRL)
        grid.setVerticalSpacing(6)
        grid.setColumnMinimumWidth(0, _LABEL_W)
        r = [0]

        def group(title, span=1):
            lab = QLabel(title)
            lab.setObjectName("cardTitle")
            if r[0]:
                grid.setRowMinimumHeight(r[0], 10)
                r[0] += 1
            grid.addWidget(lab, r[0], 0, 1, span)
            r[0] += 1

        def row(label, value, elide=False):
            lab = QLabel(self.tr(label))
            lab.setObjectName("fieldLabel")
            lab.setWordWrap(True)
            grid.addWidget(lab, r[0], 0)
            if elide:
                w1 = ElidedLabel(str(value or "—"))
                w1.setObjectName("muted")
            else:
                w1 = QLabel(str(value or "—"))
            grid.addWidget(w1, r[0], 1)
            r[0] += 1

        group(self.tr("文件"))
        row(self.tr("路径"), path, elide=True)
        kb = info["size_kb"]
        row(self.tr("大小"), ("%.1f MB" % (kb / 1024.0)) if kb >= 1024 else (f"{kb:,.0f} KB" if kb else "—"))
        row(self.tr("像素"), f"{info['w']} × {info['h']}" if info["w"] else "—")
        row(self.tr("格式"), (info["ext"] or "—").lstrip(".").upper())
        row(self.tr("修改时间"), info["mtime"])
        group(self.tr("照片 EXIF 信息"))
        for label, key in ((QT_TRANSLATE_NOOP("ImageInfoDialog", "品牌"), "make"),
                           (QT_TRANSLATE_NOOP("ImageInfoDialog", "型号"), "model"),
                           (QT_TRANSLATE_NOOP("ImageInfoDialog", "快门"), "exposure"),
                           (QT_TRANSLATE_NOOP("ImageInfoDialog", "光圈"), "fnumber"),
                           ("ISO", "iso")):
            row(label, photo[key])

        v.addLayout(grid)
        v.addStretch(1)
        v.addLayout(dlg_button_row(dlg_button(self.tr("关闭"), self.accept, primary=True)))
        csdify(self, self.tr("图片信息"), _prep_dialog)

    @staticmethod
    def _collect(path, parent):
        # 把要显示的原始数据一次读齐(文件信息 + EXIF), 失败项留空不抛
        out = {"size_kb": 0.0, "mtime": "—", "w": 0, "h": 0, "ext": "", "photo": {}}
        try:
            stat = os.stat(path)
            out["size_kb"] = stat.st_size / 1024.0
            out["mtime"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass
        try:
            out["ext"] = (parent.state._sniff_image_type(path) if parent
                          else "") or os.path.splitext(path)[1]
        except Exception:                       # noqa: BLE001
            out["ext"] = os.path.splitext(path)[1]
        try:
            from PIL import Image as _Image
            with _Image.open(path) as im:       # 只读文件头, 不解码
                out["w"], out["h"] = im.size
        except Exception:                       # noqa: BLE001 - 读不出尺寸不影响看其它项
            pass
        try:
            ex = core.get_exif_data_from_path(path) or {}
        except Exception:                       # noqa: BLE001
            ex = {}
        out["photo"] = {
            "make": (ex.get("make") or "").strip(),
            "model": (ex.get("model") or "").strip(),
            "exposure": core.exposure_to_str(ex.get("exposure_time"), ex.get("exposure_num"),
                                            ex.get("exposure_den")),
            "fnumber": core.aperture_to_str(ex.get("fnumber")),
            "iso": core.iso_to_str(ex.get("iso")),
        }
        return out
