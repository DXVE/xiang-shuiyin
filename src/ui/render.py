# -*- coding: utf-8 -*-
"""后台渲染线程: 预览与生成均在本线程执行, 主线程只做界面。

约束:
- 参数变更只覆盖"待渲染槽", 渲染线程每轮取最新一份 —— 连拖滑块产生的中间态自动合并成
  最后一次, 不需要防抖, 也不需要作废在途请求;
- 解码留在本线程(QImage 可在非 GUI 线程构造并解码, QPixmap 不行), 主线程只做
  `QPixmap.fromImage` 与贴图。
"""
import queue
import threading
import time
import traceback

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


def _pil_to_qimage(im):
    """PIL 图 -> QImage，不落盘。

    用 `tobytes()` 做一次内存拷贝(约 15MB)，比 JPEG 编码 + 写盘 + 读回解码便宜得多。
    必须调用 `.copy()`: QImage 不持有传入 bytes 的所有权，否则会读到已释放的内存。
    """
    if im.mode != "RGB":
        im = im.convert("RGB")
    data = im.tobytes()
    return QImage(data, im.width, im.height, im.width * 3,
                  QImage.Format.Format_RGB888).copy()


class PreviewWorker(QThread):
    """latest-wins 预览渲染器。

    `request()` 只覆盖待渲染槽并唤醒, 不排队 —— 渲染耗时 500ms 时连续拖动 3 秒, 最多同时
    存在"1 个在渲染 + 1 个待渲染", 不会积压出一串过期预览。

    在跑的那一轮不会被中断: 它照常渲完并上屏(此时参数可能已再次变更), 因此"请求→上屏"
    等于"等上一轮跑完 + 自己这一轮", 前一段常被误读成渲染变慢。为此在回传数据中附带:
    `token`(该帧对应的参数指纹)与 `queued`(回传时刻队列中是否已排着更新的请求),
    并统计 `_arrived_during`(渲染期间新增的请求次数)供界面写入日志。
    """

    rendered = Signal(dict)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._state = state
        self._cv = threading.Condition()
        self._pending = None          # (req, 发起时刻)
        self._stopping = False
        self._inflight = False        # 是否正在渲染(用于统计渲染期间新增的请求数)
        self._arrived_during = 0

    def request(self, req):
        with self._cv:
            if self._inflight:
                self._arrived_during += 1
            self._pending = (req, time.perf_counter())
            self._cv.notify()

    def busy(self):
        """当前是否有活(在渲染或已排队)。界面用它把状态标识立刻切成「渲染中」。

        可在 GUI 线程同步调用: 渲染在锁外执行, 渲染期间本类不持有 `_cv`, 因此最多等一瞬。
        """
        with self._cv:
            return self._inflight or self._pending is not None

    def stop(self):
        with self._cv:
            self._stopping = True
            self._cv.notify()

    def run(self):
        # 线程名写入日志的 %(threadName)s, Qt 默认名(Dummy-N)没有区分度
        threading.current_thread().name = "预览线程"
        while True:
            with self._cv:
                while self._pending is None and not self._stopping:
                    self._cv.wait()
                if self._stopping:
                    return
                req, t0 = self._pending
                self._pending = None
                self._inflight = True
                self._arrived_during = 0        # 本轮从零开始计数
            # 排队 = 从发起请求到真正开渲的等待(只可能来自等上一轮跑完), 与渲染耗时分开统计
            resp = self._render(req, queue_ms=(time.perf_counter() - t0) * 1000)
            with self._cv:
                # 这两个值必须在回传时刻读取: 在本轮开始时读取得到的是上一轮的到达数(滞后一轮),
                # 而"屏上这帧是否已过期"取决于这一轮的到达情况与回传时刻的队列, 用滞后数字会把
                # 最新那一帧标成过期(停手后没有新请求来冲掉, 该错误标签会一直挂着)。
                arrived = self._arrived_during
                queued = self._pending is not None
                self._arrived_during = 0
                self._inflight = False
            resp["_req_t"] = t0
            resp["token"] = req.get("token")    # 身份标识: 成功与失败两条路径都要带
            resp["arrived_during"] = arrived
            resp["queued"] = queued
            self.rendered.emit(resp)

    def _render(self, req, queue_ms=0.0):
        t0 = time.perf_counter()
        try:
            resp = self._state.make_preview(req)
        except Exception as e:                     # noqa: BLE001 - 任何异常都要回给界面
            traceback.print_exc()
            return {"ok": False, "error": f"预览失败: {e}", "ms": 0, "queue_ms": queue_ms}
        resp = dict(resp or {})
        resp["ms"] = (time.perf_counter() - t0) * 1000
        resp["queue_ms"] = queue_ms
        pil = resp.get("image")
        if resp.get("ok") and pil is not None:
            t_conv = time.perf_counter()
            resp["image"] = _pil_to_qimage(pil)
            # PIL->QImage 的内存拷贝, 预览档一张约 10~25ms; 与渲染耗时分开统计
            resp["qimg_ms"] = (time.perf_counter() - t_conv) * 1000
        return resp


class EventWatcher(QThread):
    """把核心的事件队列转成 Qt 信号(生成进度/完成)。

    `AppState.start_generate` 每轮会换一个新的 `queue.Queue`, 因此每轮都重新取引用,
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
        threading.current_thread().name = "事件线程"
        while not self._stopping:
            q = self._state.events
            try:
                ev = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(ev, dict):
                self.event.emit(ev)
