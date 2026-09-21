# -*- coding: utf-8 -*-
"""会话统计: 累计本次运行里预览/生成链路的耗时与缓存命中, 供界面显示与退出时落一条汇总。

为什么要有它: 单条日志只说明"这一次多慢", 判断不了"是不是整体在退化"、"缓存到底有没有用"。
统计本身极轻: 只做加法, 分位数只在**这两处**算 —— 界面刷新(每次预览一次)与退出。
样本用有界 deque(固定内存), 不随会话时长增长; 采集在预览线程/批量线程/GUI 线程都可能发生,
所以全部走一把锁。**不在任何热路径上**做统计(逐帧绘制、逐层缓存查找都不进这里)。
"""
import threading
from collections import deque

_SAMPLES = 200      # 每类耗时最多留最近 200 个样本: 算 p50/p95 足够, 内存固定在几 KB


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
        self._core = core            # 用来读核心的三个缓存计数器(只读)
        self.previews = 0            # 预览成功次数
        self.failed = 0              # 预览失败次数
        self.arrived_total = 0       # "渲染期间又到达"的请求总数
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

    def add_preview_failed(self):
        with self._lock:
            self.failed += 1

    def add_display_scale(self, ms):
        with self._lock:
            self.scales.append(ms)

    def add_generated(self, ms, count=1):
        with self._lock:
            self.generated += int(count or 1)
            self.gen_ms += float(ms or 0.0)

    # ---------- 汇总 ----------
    def cache_rates(self):
        """三个缓存的命中率与层缓存占用。读核心模块级计数器, 加锁保证同一时刻的读数一致。"""
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
        """compact=True 给状态栏一行; 否则给 tooltip / 退出日志用的多行全文"""
        with self._lock:
            n = self.previews
            renders = list(self.renders)
            queues = list(self.queues)
            convs = list(self.convs)
            scales = list(self.scales)
            e2es = list(self.e2es)
            arrived = self.arrived_total
            failed = self.failed
            gen, gen_ms = self.generated, self.gen_ms
        r50, r95, rmax = _fmt_deque(renders)
        q50, q95, qmax = _fmt_deque(queues)
        c50, _c95, _cmax = _fmt_deque(convs)
        s50, _s95, _smax = _fmt_deque(scales)
        e50, e95, emax = _fmt_deque(e2es)
        rates = self.cache_rates()
        layer_hit, layer_miss, layer_pct = rates["层"]

        if compact:
            if not n:
                return "本次会话: 尚无预览"
            gen_part = f" · 生成 {gen}" if gen else ""
            return (f"预览 {n} · 渲染 p50 {r50:.0f}/p95 {r95:.0f}ms"
                    f" · 层缓存 {layer_pct:.0f}% · 队列 {arrived}{gen_part}")

        lines = [f"本次会话: 预览 {n} 次" + (f", 失败 {failed} 次" if failed else "")]
        if n:
            lines.append(f"  渲染      p50 {r50:.0f}ms / p95 {r95:.0f}ms / max {rmax:.0f}ms")
            lines.append(f"  排队      p50 {q50:.0f}ms / p95 {q95:.0f}ms / max {qmax:.0f}ms"
                         f"   (渲染期间又到达 {arrived} 次请求)")
            lines.append(f"  转图      p50 {c50:.0f}ms")
            lines.append(f"  上屏缩放  p50 {s50:.0f}ms")
            lines.append(f"  请求→就绪  p50 {e50:.0f}ms / p95 {e95:.0f}ms / max {emax:.0f}ms")
        lines.append(f"  层缓存    命中 {layer_hit} / 未命中 {layer_miss} ({layer_pct:.0f}%)"
                     f"  占用 {rates['占用MB']:.0f}MB")
        mh, mm, mp = rates["蒙版"]
        th, tm, tp = rates["缩略图"]
        lines.append(f"  蒙版缓存  {mh}/{mm} ({mp:.0f}%)    缩略图缓存 {th}/{tm} ({tp:.0f}%)")
        if gen:
            lines.append(f"  生成      {gen} 张, 累计 {gen_ms / 1000:.1f}s, 平均 {gen_ms / gen:.0f}ms")
        return "\n".join(lines)

    def summary_oneline(self):
        """退出日志用的一行版(与多行版同源, 但按一行排版写, 不做换行符替换那种糊弄)"""
        with self._lock:
            n, failed, arrived = self.previews, self.failed, self.arrived_total
            renders, queues, convs = list(self.renders), list(self.queues), list(self.convs)
            scales, e2es = list(self.scales), list(self.e2es)
            gen, gen_ms = self.generated, self.gen_ms
        rates = self.cache_rates()
        layer_hit, layer_miss, layer_pct = rates["层"]
        parts = [f"预览 {n} 次" + (f"(失败 {failed})" if failed else "")]
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
                      f"请求→就绪 p50 {e50:.0f}/p95 {e95:.0f}/max {emax:.0f}ms",
                      f"渲染期间又到达 {arrived} 次请求"]
        mh, mm, _mp = rates["蒙版"]
        th, tm, _tp = rates["缩略图"]
        parts += [f"层缓存 {layer_hit}/{layer_miss} ({layer_pct:.0f}%, {rates['占用MB']:.0f}MB)",
                  f"蒙版 {mh}/{mm}", f"缩略图 {th}/{tm}"]
        if gen:
            parts.append(f"生成 {gen} 张, 累计 {gen_ms / 1000:.1f}s, 平均 {gen_ms / gen:.0f}ms")
        return "本次会话: " + " | ".join(parts)
