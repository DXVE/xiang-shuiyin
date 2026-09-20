# -*- coding: utf-8 -*-
"""后台渲染线程: 预览与生成都跑在这里, 主线程只做 UI。

- **不需要三件套**:
  这里参数变更只是覆盖"待渲染槽", 渲染线程每轮取最新一份 —— 连拖滑块产生的中间态
  自动合并成最后一次, 不需要防抖也不需要作废在途请求。
- **界面不冻**: 渲染期间主线程不参与, 没有 GIL 与界面线程互相拖累。
- **解码留在本线程**: QImage 可在非 GUI 线程构造并解码(QPixmap 不行), 主线程只做
  `QPixmap.fromImage` 和贴图。
"""
import queue
import threading
import time
import traceback

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


def _pil_to_qimage(im):
    """PIL 图 -> QImage，不落盘。

    直接用 tobytes() 做一次内存拷贝（约 15MB，比 JPEG 编码 + 写盘 + 读回解码便宜得多）。
    .copy() 是必须的：QImage 不持有 bytes 的所有权，不拷贝会读到已释放的内存。
    """
    if im.mode != "RGB":
        im = im.convert("RGB")
    data = im.tobytes()
    return QImage(data, im.width, im.height, im.width * 3,
                  QImage.Format.Format_RGB888).copy()


class PreviewWorker(QThread):
    """latest-wins 预览渲染器。

    request() 只覆盖待渲染槽并唤醒, 不排队 —— 所以渲染耗时 500ms 时, 用户连拖 3 秒滑块
    最多同时存在"1 个在渲染 + 1 个待渲染", 不会积压出一串过期预览。
    """

    rendered = Signal(dict)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._state = state
        self._cv = threading.Condition()
        self._pending = None          # (req, 发起时刻)
        self._stopping = False

    def request(self, req):
        with self._cv:
            self._pending = (req, time.perf_counter())
            self._cv.notify()

    def stop(self):
        with self._cv:
            self._stopping = True
            self._cv.notify()

    def run(self):
        while True:
            with self._cv:
                while self._pending is None and not self._stopping:
                    self._cv.wait()
                if self._stopping:
                    return
                req, t0 = self._pending
                self._pending = None
            resp = self._render(req)
            resp["_req_t"] = t0
            self.rendered.emit(resp)

    def _render(self, req):
        t0 = time.perf_counter()
        try:
            resp = self._state.make_preview(req)
        except Exception as e:                     # noqa: BLE001 - 任何异常都要回给界面
            traceback.print_exc()
            return {"ok": False, "error": f"预览失败: {e}", "ms": 0}
        resp = dict(resp or {})
        resp["ms"] = (time.perf_counter() - t0) * 1000
        pil = resp.get("image")
        if resp.get("ok") and pil is not None:
            resp["image"] = _pil_to_qimage(pil)
        return resp


class EventWatcher(QThread):
    """把核心的事件队列转成 Qt 信号(生成进度/完成)。

    `AppState.start_generate` 每轮会换一个新的 queue.Queue, 所以这里每轮都重新取引用,
    不能缓存一次性句柄。
    """

    event = Signal(dict)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._state = state
        self._stopping = False

    def stop(self):
        self._stopping = True

    def run(self):
        while not self._stopping:
            q = self._state.events
            try:
                ev = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(ev, dict):
                self.event.emit(ev)
