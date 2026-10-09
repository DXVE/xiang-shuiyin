"""菜单动作与弹窗入口: 关于、日志与数据目录、预设 / Logo / 取色 / 映射 / 字体。"""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox
from core.config import DATA_DIR, DEFAULT_LOGO_DIR, LOG_DIR
from core.exif import prettify_model
from .dialogs.about import AboutDialog
from .dialogs.base import _message, _open_and_report
from .dialogs.color import ColorDialog, pick_native_color
from .dialogs.font import FontDialog
from .dialogs.image_info import ImageInfoDialog
from .dialogs.map import MapDialog
from .dialogs.preset import PresetDialog
import logging
import os


class ActionMixin:

    def _sync_lang_menu(self):
        # 刷这一行(文字里的可选语言码)与子菜单的勾号
        # 文字从 i18n.LANGS 现算; 勾号跟着 self._lang —— 切语言要重启才生效, 但设置是当场存下的
        if not hasattr(self, "_lang_menu"):
            return
        self._lang_menu.menuAction().setText(
            self.tr("语言") + "  " + "/".join(b for _c, b, _a in self._lang_acts))
        for c, _b, act in self._lang_acts:
            act.setChecked(c == self._lang)

    def _set_lang(self, code):
        # 切语言: 写进设置, 提示用户自己关掉重开才生效
        # 不做热切换也不自动重启: 参数面板文案是建窗时 tr() 出来的, 热切换得整窗重建(批量 / 预览线程在跑时风险高)
        if code == self._lang:
            return
        self._lang = code
        self._sync_lang_menu()          # 菜单当场反映新选的语言(界面文案要重启才变, 见类注释)
        try:
            self.state.settings["lang"] = code
            self._save_settings_now()               # 合并式保存: 只多写一个 lang 键
        except Exception as e:                      # noqa: BLE001 - 存不上不影响这次运行
            logging.warning(f"[i18n] 保存语言设置失败: {e}")
        _message(self, QMessageBox.Icon.Information, self.tr("语言"),
                 self.tr("界面语言已保存，请关闭程序后重新打开生效。"))

    def _show_about(self):
        # 关于: 品牌区 + 版本/环境 + 数据目录 + 分发许可
        AboutDialog(self).exec()

    def _open_log_dir(self):
        _open_and_report(self, LOG_DIR, "日志目录")

    def _open_data_dir(self):
        _open_and_report(self, DATA_DIR, "数据目录")

    def _show_image_info(self):
        # 「图片信息」弹窗: 文件信息 + 照片自己的 EXIF(如实报告, 不推算最后会印成什么)
        # 要知水印里会显示什么, 看「识别型号」那一行的 tooltip
        path = getattr(self, "_preview_src", "")
        if not path or not os.path.isfile(path):
            self._notify(self.tr("请先选择图片"))
            return
        dlg = ImageInfoDialog(path, self)
        dlg.exec()

    def _update_effective_model(self):
        # 刷新「识别型号」那一行(以及水印里会显示什么的说明)
        # self._effective_model = 水印最终会印的名字, 与 _inject_model_map + prettify_model 同一套规则: 临时美化名 > 映射表 > 原始型号
        raw = self._raw_model
        if self._custom_model and self._custom_model != raw:
            name = self._custom_model
        else:
            try:
                name = prettify_model(raw, self._params.get("model_map") or {})
            except Exception:
                name = raw
        self._effective_model = name or ""
        self._model_lab.setText(raw or "—")
        tips = [self.tr("品牌：{brand}").format(brand=self._raw_brand)] if self._raw_brand else []
        if name:
            tips.append(self.tr("水印将显示：{name}").format(name=name))
        self._model_lab.setToolTip("\n".join(tips))
        # 「复制原始型号」: 未识别到型号(未选图 / 图中无 EXIF)时置灰
        self._copy_btn.setEnabled(bool(raw))
        self._copy_btn.setToolTip(self.tr("复制识别到的型号到剪贴板") if raw
                                  else self.tr("未识别到型号（请先选择图片）"))

    def _open_preset_dialog(self):
        # 打开「预设方案」弹窗(构造即读盘)
        dlg = PresetDialog(self)
        dlg.exec()

    def _open_logo_dir(self):
        # 「打开目录」: 在资源管理器中打开 Logo 目录(该目录不会自动创建, 首次点击时先建再开)
        path = self._params.get("logo_dir") or DEFAULT_LOGO_DIR
        try:
            os.makedirs(path, exist_ok=True)        # 已存在时不报错
        except Exception as e:                      # noqa: BLE001 - 建不出来就退化成"报一句"
            self._notify(self.tr("Logo 目录不可用：{err}").format(err=e))
            logging.warning(f"[目录] 创建 Logo 目录失败: {path} ({e})")
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self._notify(self.tr("已打开 Logo 目录：{path}").format(path=path))
        else:
            self._notify(self.tr("无法打开 Logo 目录：{path}").format(path=path))

    def _pick_native_color(self, row):
        # 点色块: 直接开系统取色器, 改一次颜色只要一步(不必先进「颜色管理」再点一次色块)
        # 取色器标题用本行标签, 一眼看出在改哪种颜色
        c = pick_native_color(self, row.value(), row.color_pick_title())
        if c:
            row.set_value(c)
            self._on_param(row.key, c)

    def _open_color_dialog(self, row):
        # 点「颜色管理」开弹窗(预设色 + 我的颜色); 弹窗内点色块即时应用, 由 _on_param 一并负责保存与预览
        # 关闭后不再补一次渲染: 没有改动也重渲会让"开窗看一眼"白付一整帧
        dlg = ColorDialog(self.state, row.value(), self)
        dlg.picked.connect(lambda c, r=row: (r.set_value(c), self._on_param(r.key, c)))
        dlg.exec()

    def _open_font_dialog(self):
        # ☰ →「字体文件…」: 字体文件收进弹窗管理; 弹窗内每次改文件都走 _on_param, 已是即时生效
        FontDialog(self.state, self).exec()

    def _open_map_dialog(self):
        dlg = MapDialog(self.state, self)
        dlg.exec()
        if not dlg.changed:
            # 一行没改就关窗: 内存与文件本来就一致, 也不必重渲(开窗看一眼不该白付一整帧)
            return
        # 撤销由弹窗逐步记录(见 MapDialog._apply_change), 此处收尾: 对齐内存与文件(写盘失败时以文件为准)并重渲预览
        self._reload_model_map()
        self._touch()

    def _copy_raw_model(self):
        if self._raw_model:
            QApplication.clipboard().setText(self._raw_model)
            self._notify(self.tr("已复制原始型号到剪贴板"))
        else:
            # 该按钮在未识别到型号时已置灰, 此处兜底提示避免静默无响应
            self._notify(self.tr("未识别到型号（请先选择图片）"))

    def _on_custom_model(self, text):
        self._custom_model = text.strip()
        self._update_effective_model()
        self.request_preview()
