"""生成任务与结果事件: 启动生成、消费内核事件、一次性提示。"""
from PySide6.QtWidgets import QMessageBox
from core.paths import open_path
from . import i18n
from .dialogs.base import _message
from .specs import backend_params


class GenerateMixin:

    def start_generate(self):
        if self._generating:
            self._notify(self.tr("正在处理中，请稍候"))
            return
        if not self._output_dir:
            self._notify(self.tr("请先选择导出目录"))
            return
        req = {
            "params": backend_params(self._params),
            "manual_exif": self._manual_exif(),
            "output_dir": self._output_dir,
            "raw_model": self._raw_model,
            "custom_model": self._custom_model,
            "save_map_flag": True,
        }
        if self._mode == "single":
            if not self._input_path:
                self._notify(self.tr("请先选择图片"))
                return
            req.update(mode="single", input_path=self._input_path,
                       original_name=self._input_name or "image.jpg")
        else:
            if not self._batch_files:
                self._notify(self.tr("请先选择要批量处理的图片"))
                return
            req.update(mode="batch", file_paths=self._batch_files)
        d = self.state.start_generate(req)
        if not d.get("ok"):
            self._notify(i18n.tr_msg(d.get("error")) or self.tr("无法开始处理"))
            return
        self._generating = True
        self._gen_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._progress.setMaximum(len(self._batch_files) if self._mode == "batch" else 100)

    def _on_event(self, ev):
        t = ev.get("type")
        if t == "progress":
            total = ev.get("total") or 0
            self._progress.setVisible(True)
            self._progress.setMaximum(total or 100)
            done = ev.get("done") or 0
            self._progress.setValue(done)
            # 进度显示在进度条自身(格式 n / N); 文件名写入其 tooltip
            if total:
                self._progress.setFormat(f"{done} / {total}")
            name = ev.get("name")
            if name:
                self._progress.setToolTip(i18n.tr_msg(name))
        elif t == "log":
            self._notify(i18n.tr_msg(str(ev.get("msg") or "")))
        elif t == "finished":
            self._generating = False
            self._gen_btn.setEnabled(True)
            self._progress.setVisible(False)
            msg = i18n.tr_msg(str(ev.get("message") or ""))
            # 生成耗时由核心在 finished 事件里带回来(单张=这一张, 批量=整批), 进会话统计
            self._stats.add_generated(ev.get("ms") or 0.0, ev.get("count") or 1)
            if ev.get("success"):
                out_dir = ev.get("out_dir") or self._output_dir
                self._notify(self.tr("已完成：{msg}").format(msg=i18n.tr_msg(msg)))
                if out_dir:
                    open_path(out_dir)     # 完成后打开输出目录
            else:
                _message(self, QMessageBox.Icon.Warning, self.tr("处理结果"),
                                         msg or self.tr("处理失败"))

    def _notify(self, text):
        # 一次性提示(吐司): 只由用户显式动作触发, 后台循环(自动预览 / 自动保存)不得调用
        if getattr(self, "_toast", None) is not None:
            self._toast.show_message(text)
