# -*- coding: utf-8 -*-
"""会话统计: 累计本次运行中预览/生成链路的耗时与缓存命中, 供界面显示与退出时落一条汇总。

统计只做加法, 分位数仅在界面刷新与退出时计算; 样本用有界 deque(内存固定), 不随会话时长增长。
采集可能发生在预览线程/批量线程/GUI 线程, 因此全部加锁。不在任何热路径上做统计(逐帧绘制、
逐层缓存查找都不进入本模块)。
"""
import threading

from PySide6.QtCore import QCoreApplication, QT_TRANSLATE_NOOP
from collections import deque

_SAMPLES = 200      # 每类耗时最多保留最近 200 个样本: 计算 p50/p95 足够, 内存固定在几 KB


def _t(text):
    """取译文(中文是源语言)。使用时才调用 —— import 期翻译器尚未安装(见 ui/i18n.py)。"""
    return QCoreApplication.translate("stats", text)


def _pct(vals, q):
    """分位数(最近邻取法, 不做插值): vals 需已排序"""
    if not vals:
        return 0.0
    i = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
    return vals[i]


def _fmt_deque(vals):
    """(p50, p95, max) —— 只在刷新/退出时调用"""
    if not vals:
        return 0.0, 0.0, 0.0
    s = sorted(vals)
    return _pct(s, 0.5), _pct(s, 0.95), s[-1]


class SessionStats:
    """一次运行(一个主窗口)的累计统计。线程安全。"""

    def __init__(self, core):
        self._lock = threading.Lock()
        self._core = core            # 用于读取核心的三个缓存计数器(只读)
        self.previews = 0            # 预览成功次数
        self.arrived_total = 0       # "渲染期间新增"的请求总数
        self.renders = deque(maxlen=_SAMPLES)     # 纯渲染耗时
        self.queues = deque(maxlen=_SAMPLES)      # 排队(等上一轮)
        self.convs = deque(maxlen=_SAMPLES)       # PIL->QImage
        self.scales = deque(maxlen=_SAMPLES)      # 上屏缩放(GUI 线程)
        self.e2es = deque(maxlen=_SAMPLES)        # 请求->就绪
        self.generated = 0           # 生成张数(单张 + 批量)
        self.gen_ms = 0.0            # 生成累计耗时

    # ---------- 采集 ----------
    def add_preview(self, ms=0.0, queue_ms=0.0, qimg_ms=0.0, e2e_ms=0.0, arrived=0):
        with self._lock:
            self.previews += 1
            self.renders.append(ms)
            self.queues.append(queue_ms)
            self.convs.append(qimg_ms)
            self.e2es.append(e2e_ms)
            self.arrived_total += int(arrived or 0)

    def add_display_scale(self, ms):
        with self._lock:
            self.scales.append(ms)

    def add_generated(self, ms, count=1):
        with self._lock:
            self.generated += int(count or 1)
            self.gen_ms += float(ms or 0.0)

    # ---------- 汇总 ----------
    def cache_rates(self):
        """三个缓存的命中率与层缓存占用。读取核心模块级计数器, 加锁保证同一时刻读数一致。"""
        c = self._core
        out = {}
        for name, stats in (("层", getattr(c, "_LAYER_CACHE_STATS", {})),
                            ("蒙版", getattr(c, "_MASK_STATS", {})),
                            ("缩略图", getattr(c, "_THUMB_STATS", {}))):
            hit, miss = int(stats.get("hit", 0)), int(stats.get("miss", 0))
            total = hit + miss
            out[name] = (hit, miss, (hit * 100.0 / total) if total else 0.0)
        try:
            out["占用MB"] = c._LAYER_CACHE_BYTES[0] / 1e6
        except Exception:
            out["占用MB"] = 0.0
        return out

    def summary(self, compact=False):
        """compact=True 返回单行摘要(「关于」弹窗用); 否则返回 tooltip 与退出日志用的多行全文。"""
        with self._lock:
            n = self.previews
            renders = list(self.renders)
            queues = list(self.queues)
            convs = list(self.convs)
            scales = list(self.scales)
            e2es = list(self.e2es)
            arrived = self.arrived_total
            gen, gen_ms = self.generated, self.gen_ms
        r50, r95, rmax = _fmt_deque(renders)
        q50, q95, qmax = _fmt_deque(queues)
        c50, _c95, _cmax = _fmt_deque(convs)
        s50, _s95, _smax = _fmt_deque(scales)
        e50, e95, emax = _fmt_deque(e2es)
        rates = self.cache_rates()
        layer_hit, layer_miss, layer_pct = rates["层"]

        if compact:
            # 紧凑版只返回值本身, 前缀由调用方给出(「关于」弹窗写作「本次会话统计：<此句>」)
            if not n:
                return _t(QT_TRANSLATE_NOOP("stats", "暂无预览"))
            gen_part = _t(QT_TRANSLATE_NOOP("stats", " · 生成 {gen}")).format(gen=gen) if gen else ""
            return (_t(QT_TRANSLATE_NOOP("stats", "预览 {n} · 渲染 p50 {r50:.0f}/p95 {r95:.0f}ms")).format(
                        n=n, r50=r50, r95=r95)
                    + _t(QT_TRANSLATE_NOOP("stats", " · 层缓存 {pct:.0f}% · 队列 {arrived}{gen_part}")).format(
                        pct=layer_pct, arrived=arrived, gen_part=gen_part))

        lines = [_t(QT_TRANSLATE_NOOP("stats", "本次会话：预览 {n} 次")).format(n=n)]
        if n:
            lines.append(_t(QT_TRANSLATE_NOOP("stats", "  渲染      p50 {r50:.0f}ms / p95 {r95:.0f}ms / max {rmax:.0f}ms"))
                         .format(r50=r50, r95=r95, rmax=rmax))
            lines.append(_t(QT_TRANSLATE_NOOP("stats", "  排队      p50 {q50:.0f}ms / p95 {q95:.0f}ms / max {qmax:.0f}ms"))
                         .format(q50=q50, q95=q95, qmax=qmax)
                         + _t(QT_TRANSLATE_NOOP("stats", "   （渲染期间新增 {n} 次请求）")).format(n=arrived))
            lines.append(_t(QT_TRANSLATE_NOOP("stats", "  转图      p50 {c50:.0f}ms")).format(c50=c50))
            lines.append(_t(QT_TRANSLATE_NOOP("stats", "  上屏缩放  p50 {s50:.0f}ms")).format(s50=s50))
            lines.append(_t(QT_TRANSLATE_NOOP("stats", "  请求 ~ 就绪  p50 {e50:.0f}ms / p95 {e95:.0f}ms / max {emax:.0f}ms"))
                         .format(e50=e50, e95=e95, emax=emax))
        lines.append(_t(QT_TRANSLATE_NOOP("stats", "  层缓存    命中 {hit} / 未命中 {miss} ({pct:.0f}%)")).format(
                         hit=layer_hit, miss=layer_miss, pct=layer_pct)
                     + _t(QT_TRANSLATE_NOOP("stats", "  占用 {mb:.0f}MB")).format(mb=rates["占用MB"]))
        mh, mm, mp = rates["蒙版"]
        th, tm, tp = rates["缩略图"]
        lines.append(_t(QT_TRANSLATE_NOOP("stats", "  蒙版缓存  {mh}/{mm} ({mp:.0f}%)    缩略图缓存 {th}/{tm} ({tp:.0f}%)"))
                     .format(mh=mh, mm=mm, mp=mp, th=th, tm=tm, tp=tp))
        if gen:
            lines.append(_t(QT_TRANSLATE_NOOP("stats", "  生成      {n} 张, 累计 {s:.1f}s, 平均 {avg:.0f}ms"))
                         .format(n=gen, s=gen_ms / 1000, avg=gen_ms / gen))
        return "\n".join(lines)

    def summary_oneline(self):
        """退出日志用的一行版(与多行版同源, 按一行排版, 不做换行符替换)。"""
        with self._lock:
            n, arrived = self.previews, self.arrived_total
            renders, queues, convs = list(self.renders), list(self.queues), list(self.convs)
            scales, e2es = list(self.scales), list(self.e2es)
            gen, gen_ms = self.generated, self.gen_ms
        rates = self.cache_rates()
        layer_hit, layer_miss, layer_pct = rates["层"]
        parts = [f"预览 {n} 次"]
        if n:
            r50, r95, rmax = _fmt_deque(renders)
            q50, _q95, qmax = _fmt_deque(queues)
            c50, _c95, _cmax = _fmt_deque(convs)
            s50, _s95, _smax = _fmt_deque(scales)
            e50, e95, emax = _fmt_deque(e2es)
            parts += [f"渲染 p50 {r50:.0f}/p95 {r95:.0f}/max {rmax:.0f}ms",
                      f"排队 p50 {q50:.0f}/max {qmax:.0f}ms",
                      f"转图 p50 {c50:.0f}ms",
                      f"上屏缩放 p50 {s50:.0f}ms",
                      f"请求-->就绪 p50 {e50:.0f}/p95 {e95:.0f}/max {emax:.0f}ms",
                      f"渲染期间新增 {arrived} 次请求"]
        mh, mm, _mp = rates["蒙版"]
        th, tm, _tp = rates["缩略图"]
        parts += [f"层缓存 {layer_hit}/{layer_miss} ({layer_pct:.0f}%, {rates['占用MB']:.0f}MB)",
                  f"蒙版 {mh}/{mm}", f"缩略图 {th}/{tm}"]
        if gen:
            parts.append(f"生成 {gen} 张, 累计 {gen_ms / 1000:.1f}s, 平均 {gen_ms / gen:.0f}ms")
        return "本次会话: " + " | ".join(parts)
