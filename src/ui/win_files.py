"""文件与拖放: 选图、输出目录、批量目录、拖放事件与模式切换。"""
from core.jobs import _list_images_in_dir
from .dialogs.base import _open_dir_dialog, _open_file_dialog
import os


class FilesMixin:

    def _set_mode(self, mode):
        self._mode = mode
        for k, b in self._mode_btns.items():
            b.setChecked(k == mode)
        self._sync_files_ui()
        # 「单张 / 批量」那四行的显隐走统一出口(冻结重绘 + 容器当场定高度), 见 _vis_rule
        self._sync_visibility()
        if self._sync_preview_source():
            self.request_preview()

    def _sync_files_ui(self):
        # 「文件与美化」页签里随处理模式变化的文案; 只刷文案, 显隐走 _vis_rule + _sync_visibility
        single = self._mode == "single"
        if not single and self._batch_files:
            self._batch_count.setText(self.tr("已载入 {n} 张（预览与调参取第一张）")
                                      .format(n=len(self._batch_files)))
        elif not single:
            self._batch_count.setText(self.tr("尚未载入图片"))

    def _on_recursive(self, on):
        self._batch_recursive = bool(on)

    def _pick_batch_dir(self):
        folder = _open_dir_dialog(self, self.tr("选择图片文件夹"), self._batch_dir or "")
        if folder:
            self._load_batch_dir(folder)

    def _load_batch_dir(self, folder):
        cand = _list_images_in_dir(folder, recursive=self._batch_recursive)
        images = [p for p in cand if self.state._sniff_image_type(p)]
        if not images:
            self._notify(self.tr("所选目录里没有受支持的图片")
                         + (self.tr("（含子目录）") if self._batch_recursive
                            else self.tr("（可勾选「包含子文件夹」）")))
            return
        self.load_batch([{"path": p, "name": os.path.basename(p)} for p in images], folder)

    def load_batch(self, items, folder=""):
        self._batch_files = items
        self._batch_dir = folder or os.path.dirname(items[0]["path"])
        self._batch_val.setText(self._batch_dir)
        self._batch_val.setToolTip(self._batch_dir)
        self._set_mode("batch")
        self._notify(self.tr("已载入 {n} 张图片，可开始生成").format(n=len(items)))

    def _manual_exif(self):
        # 启用时返回手动信息, 否则返回 None
        return dict(self._manual) if self._manual_on else None

    def _pick_image(self):
        path = _open_file_dialog(
            self, self.tr("选择图片"), "",
            self.tr("图片 (*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff *.ico);;所有文件 (*)"))
        if path:
            self.set_input(path)

    def _pick_output(self):
        d = _open_dir_dialog(self, self.tr("选择导出文件夹"), self._output_dir or "")
        if d:
            self._set_output_dir(d, manual=True)

    def _set_output_dir(self, path, manual):
        self._output_dir = path
        self._out_label.setText(("" if manual else self.tr("（跟随原图） ")) + path)
        self._out_label.setToolTip(path)

    def set_input(self, path):
        if not os.path.isfile(path):
            self._notify(self.tr("请拖入图片文件"))
            return
        if not self.state._sniff_image_type(path):
            self._notify(self.tr("不支持的图片格式"))
            return
        self._input_path = path
        self._input_name = os.path.basename(path)
        self._input_label.setText(self._input_name)
        self._input_label.setToolTip(path)
        self._input_val.setText(path)
        self._input_val.setToolTip(path)
        self._set_output_dir(
            os.path.join(os.path.dirname(os.path.abspath(path)), "watermarked"), manual=False)
        try:
            ex = self.state.read_exif(path) or {}
            self._raw_model = (ex.get("model") or "").strip()
            brand = (ex.get("make") or "").strip()
        except Exception:
            self._raw_model, brand = "", ""
        self._raw_brand = brand
        self._update_effective_model()
        if self._mode != "single":
            self._set_mode("single")            # 内部按需重渲
        else:
            self._sync_preview_source()
            self.request_preview()
        self._notify(self.tr("已选择：{name}").format(name=self._input_name))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self._preview.set_hint(self.tr("松开鼠标以载入"))

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self._preview.set_hint(self.tr("请拖入图片，或点击「打开图片」"))

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.acceptProposedAction()
        self._preview.set_hint(self.tr("请拖入图片，或点击「打开图片」"))
        if not paths:
            self._notify(self.tr("未收到文件"))
            return
        # 拖入目录 = 批量目录; 拖入多张 = 批量列表; 单张 = 单张
        folders = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if not os.path.isdir(p)]
        if folders:
            self._load_batch_dir(folders[0])
            return
        images = [p for p in files if self.state._sniff_image_type(p)]
        if not images:
            self._notify(self.tr("不支持的图片格式"))
            return
        if len(images) == 1:
            self.set_input(images[0])
        else:
            self.load_batch([{"path": p, "name": os.path.basename(p)} for p in images])
