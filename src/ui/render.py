# -*- coding: utf-8 -*-
"""后台渲染线程: 预览与生成均在本线程执行, 主线程只做界面"""
import queue
import threading
import time
import traceback

from PIL import Image
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QImage


def _pil_to_qimage(im):
    # PIL 图转 QImage(内存直传, 不落盘)
    if im.mode != "RGB":
        im = im.convert("RGB")
    data = im.tobytes()
    return QImage(data, im.width, im.height, im.width * 3,
                  QImage.Format.Format_RGB888).copy()


class PreviewWorker(QThread):
    # 预览渲染器: 请求只覆盖待渲染槽(latest-wins), 线程每轮取最新一份

    rendered = Signal(dict)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._state = state
        self._cv = threading.Condition()
        self._pending = None          # (req, 发起时刻)
        self._stopping = False
        self._inflight = False        # 是否正在渲染(用于统计渲染期间新增的请求数)
        self._arrived_during = 0
        self._seq = 0                 # 请求代号: 每来一个新请求 +1, 在跑的那轮据此判自己是否过期
        self._cancelled = 0           # 累计被更新的请求作废的轮数(诊断用: 拖动时它应该接近请求数)

    def request(self, req):
        with self._cv:
            self._seq += 1
            if self._inflight:
                self._arrived_during += 1
            self._pending = (req, time.perf_counter())
            self._cv.notify()

    def _stale_check(self, seq):
        # 返回本轮是否已过期的判据: 读 _seq 不加锁(GIL 内原子), 避免与 request() 争锁
        return lambda: self._seq != seq

    def busy(self):
        # 当前是否有活(在渲染或已排队); 可在 GUI 线程同步调用, 渲染在锁外执行
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
                self._arrived_during = 0
                my_seq = self._seq              # 本轮代号: 期间来了更新的请求就作废
            # 排队 = 从发起请求到真正开渲的等待(只可能来自等上一轮跑完), 与渲染耗时分开统计
            resp = self._render(req, queue_ms=(time.perf_counter() - t0) * 1000,
                                cancel_check=self._stale_check(my_seq))
            with self._cv:
                # 这两个值在回传时刻读取: 本轮开始时读到的是上一轮的到达数, 会把最新一帧标成过期
                arrived = self._arrived_during
                queued = self._pending is not None
                self._arrived_during = 0
                self._inflight = False
            if resp.get("cancelled"):
                # 作废的一轮不回帧(屏上留上一张), 待渲染槽里已有更新的请求, 下一轮立刻开渲
                with self._cv:
                    self._cancelled += 1
                continue
            resp["_req_t"] = t0
            resp["token"] = req.get("token")    # 身份标识: 成功与失败两条路径都要带
            resp["arrived_during"] = arrived
            resp["queued"] = queued
            self.rendered.emit(resp)

    def _render(self, req, queue_ms=0.0, cancel_check=None):
        t0 = time.perf_counter()
        try:
            resp = self._state.make_preview(req, cancel_check=cancel_check)
        except Exception as e:                     # noqa: BLE001 - 任何异常都要回给界面
            traceback.print_exc()
            return {"ok": False, "error": f"预览失败: {e}", "ms": 0, "queue_ms": queue_ms}
        resp = dict(resp or {})
        resp["ms"] = (time.perf_counter() - t0) * 1000
        resp["queue_ms"] = queue_ms
        pil = resp.get("image")
        if resp.get("ok") and pil is not None:
            t_conv = time.perf_counter()
            # 整幅画布仍要一份 QImage: 放大镜按像素从它重采样, 窗口尺寸变化时也由它回落重采样
            resp["source"] = _pil_to_qimage(pil)
            resp["canvas"] = pil.size
            conv_ms = (time.perf_counter() - t_conv) * 1000
            box = req.get("display")      # (可用区设备宽, 设备高, dpr): 界面预览框请求时的上屏尺寸
            if box:
                # 与界面 _fit_dev 同一算法(同一个 QSize.scaled), 保证界面拿到就能 1:1 直贴
                target = QSize(pil.width, pil.height).scaled(
                    QSize(int(box[0]), int(box[1])), Qt.AspectRatioMode.KeepAspectRatio)
                t_scale = time.perf_counter()
                small = pil.resize((target.width(), target.height()), Image.LANCZOS)
                resp["scale_ms"] = (time.perf_counter() - t_scale) * 1000
                t_conv = time.perf_counter()
                resp["image"] = _pil_to_qimage(small)
                conv_ms += (time.perf_counter() - t_conv) * 1000
                resp["display"] = (target.width(), target.height())
                resp["display_dpr"] = float(box[2])
            else:
                resp["image"] = resp["source"]
            # PIL->QImage 的两次内存拷贝(整幅 + 上屏小图): 与渲染、缩放各自分开统计
            resp["qimg_ms"] = conv_ms
        return resp


class EventWatcher(QThread):
    # 把核心的事件队列转成 Qt 信号(生成进度/完成); 队列每轮更换, 不能缓存一次性句柄

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
