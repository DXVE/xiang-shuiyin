"""生命周期与交互同步: 显示 / 缩放 / 关闭、分栏比例、悬停心跳与手填字段。"""
from PySide6.QtCore import QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication
from .widgets import ParamRow, _hover_trace
import logging
from .winconst import _SPLIT_LEFT, _WIN_EDGE, _WIN_H_RATIO, _WIN_W_RATIO


class EventMixin:

    def _fit_to_screen(self):
        # 按屏幕可用区(已扣任务栏)的百分比定初始尺寸并居中(留边 _WIN_EDGE px)
        # 只影响初始值: 不做尺寸持久化, 每次启动按当前屏幕重算
        scr = self.screen() or QApplication.primaryScreen()
        if scr is not None:
            a = scr.availableGeometry()
            sw, sh = a.width(), a.height()
        else:
            sw, sh = 1536, 816          # 拿不到屏幕时按 1080p@125% 的可用区兜底
        w = max(320, min(int(sw * _WIN_W_RATIO), sw - _WIN_EDGE))
        h = max(240, min(int(sh * _WIN_H_RATIO), sh - _WIN_EDGE))
        self.resize(w, h)
        self.move(a.center().x() - w // 2, a.center().y() - h // 2)

    def _apply_split_ratio(self):
        # 按 _SPLIT_LEFT 重排左右列; 用户手动拖过分隔条后本次会话不再自动分配
        # 分母用 sum(sizes()): 它是分隔条实际可分配的宽度, 与 handle 宽度无关; 窗口极窄时 QSplitter 会钳制到各列最小值
        sp = getattr(self, "_split", None)
        if sp is None or self._split_user_set:
            return
        total = sum(sp.sizes())
        if total <= 0:                  # 首次布局还没走完, sizes() 没有意义
            return
        left = int(total * _SPLIT_LEFT)
        sp.setSizes([left, total - left])
        self._sync_bottom_lead()        # 底栏那组跟着分栏走(见 _sync_bottom_lead)

    def _on_split_moved(self, *_pos):
        self._split_user_set = True
        self._sync_bottom_lead()        # 用户拖过分隔条: 底栏那组也要跟着

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_split_ratio()
# 用户拖过分隔条后不再自动分配比例, 但「分隔条占宽 − sizes 之和」会随窗口尺寸变, 底栏那段留白得跟着重算
        self._sync_bottom_lead()
        # 吐司是绝对定位的浮层, 窗口一改尺寸就得重算(在它显示的那 3.2 秒里窗口可能被拖过)
        if getattr(self, "_toast", None) is not None and self._toast.isVisible():
            self._toast.reflow()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._fitted:
            self._fitted = True
# 首帧时才知道窗口落在哪块屏(多屏下未必是 primaryScreen): 再按那块屏的可用区收一次, 只做这一次
            scr = self.screen()
            if scr is not None:
                a = scr.availableGeometry()
                w = min(self.width(), a.width() - _WIN_EDGE)
                h = min(self.height(), a.height() - _WIN_EDGE)
                if (w, h) != (self.width(), self.height()):
                    self.resize(w, h)
            self._apply_split_ratio()
# 底栏留白: 首帧算一次, 再补一次 —— 首帧窗口尚未映射, 分栏面板的最终坐标不可用
            self._sync_bottom_lead()
            QTimer.singleShot(0, self, self._sync_bottom_lead)

    def _on_manual_toggle(self, on):
        self._manual_on = bool(on)
        for e in self._manual_edits.values():
            e.setEnabled(self._manual_on)
        self.request_preview()

    def _on_manual_field(self, key, text):
        self._manual[key] = text
        if self._manual_on:
            self.request_preview()

    def _sync_gap_hover(self):
        # 按光标位置决定预览图上高亮哪一段间距 —— 悬停高亮的唯一判据, 由 80ms 心跳定时器驱动(参数行不接 Enter/Leave)
        # 两步: ① widgetAt 沿父链找参数行; ② 兜底扫行矩形(光标在行留白处或窗口未上屏时 ① 可能为空); set_highlight 内含去重, 每拍调用不额外重绘
        gp = QCursor.pos()
        keys, hit = [], ""
        w = QApplication.widgetAt(gp)
        while w is not None and not isinstance(w, ParamRow):
            w = w.parentWidget()
        if isinstance(w, ParamRow) and w.window() is self and w.isVisible():
            keys, hit = w._hover_keys(), w.key
        else:
            for row in self._rows.values():
                if row.isVisible() and row.rect().contains(row.mapFromGlobal(gp)):
                    keys, hit = row._hover_keys(), row.key
                    break
        _hover_trace("重算: 光标=(%d,%d) -> 命中 %s" % (gp.x(), gp.y(), hit or "无"))
        self._preview.set_highlight(keys)

    def closeEvent(self, e):
        if getattr(self, "_hover_timer", None) is not None:
            self._hover_timer.stop()        # 关窗时停掉悬停心跳(它只服务界面, 没有收尾动作)
        if self._save_timer.isActive():     # 防抖还没到点就关窗: 立刻落盘, 否则最后一次改动会丢
            self._save_timer.stop()
            self._save_settings_now()
# 退出时落一条会话汇总, 分行写(每行带 [统计] 前缀)
        try:
            for line in self._stats.summary().splitlines():
                logging.info("[统计] " + line)
        except Exception as ex:             # noqa: BLE001 - 汇总失败不能挡住关窗
            logging.warning(f"[统计] 汇总失败: {ex}")
        self._preview_worker.stop()
        self._preview_worker.wait(2000)
        self._event_watcher.stop()
        self._event_watcher.wait(2000)
        self.state.cleanup()          # 只清本程序自己的 PREVIEW_DIR(见 XSY_PREVIEW_DIR)
        super().closeEvent(e)
