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

    但**在跑的那一轮不会被中断**: 它照常渲完、照常上屏(此时参数可能已经又改过)。于是
    "请求→上屏"会等于"等上一轮跑完 + 自己这一轮", 前一段常被误读成渲染变慢 ——
    所以这里统计 _arrived_during(渲染期间又新增了几次请求), 由界面写进日志。

    回传的 resp 里另外带两样东西给"预览状态标识"用(见 window.PreviewStateChip):
    `token`(这一帧对应哪一组参数, 原样带回)与 `queued`(回传这一刻队列里是否已排着更新的
    请求)。有了它们, 界面才能回答"屏幕上这张图是不是当前参数渲出来的"。
    """

    rendered = Signal(dict)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._state = state
        self._cv = threading.Condition()
        self._pending = None          # (req, 发起时刻)
        self._stopping = False
        self._inflight = False        # 是否正在渲染(用于统计"渲染期间又来了几次请求")
        self._arrived_during = 0

    def request(self, req):
        with self._cv:
            if self._inflight:
                self._arrived_during += 1
            self._pending = (req, time.perf_counter())
            self._cv.notify()

    def busy(self):
        """现在有没有活(在跑或排队)。界面用它把状态标识立刻切成「渲染中」。

        可以直接在 GUI 线程同步调用: 渲染期间本类**不持有** _cv(render 是在锁外做的),
        所以这里最多等一瞬, 不会把界面卡住。
        """
        with self._cv:
            return self._inflight or self._pending is not None

    def stop(self):
        with self._cv:
            self._stopping = True
            self._cv.notify()

    def run(self):
        # 线程名进日志(%(threadName)s): QThread 的 Python 线程名默认是 Dummy-N, 一点信息都没有
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
                self._arrived_during = 0        # 本轮从零开始数
            # 排队 = 从"用户发起"到"真正开渲"的等待(只可能是等上一轮跑完), 与渲染耗时分开记
            resp = self._render(req, queue_ms=(time.perf_counter() - t0) * 1000)
            with self._cv:
                # 这两个数**必须在回传这一刻取**, 不能在本轮开始时取:
                #   以前 arrived 是每轮开始时读的, 读到的其实是**上一轮**渲染期间的到达数
                #   (滞后一整轮) —— 而"屏上这帧过没过期"要看的就是这一轮的到达情况与回传时的
                #   队列, 用滞后一轮的数字会把**最新那一帧**标成过期(停手后不再有新请求来冲掉,
                #   于是那个错误标签会一直挂着)。
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
            # PIL->QImage 的内存拷贝: 预览档一张约 10~25ms。与渲染耗时分开记, 免得混成一个数
            resp["qimg_ms"] = (time.perf_counter() - t_conv) * 1000
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
        threading.current_thread().name = "事件线程"
        while not self._stopping:
            q = self._state.events
            try:
                ev = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(ev, dict):
                self.event.emit(ev)
