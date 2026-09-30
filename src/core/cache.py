"""缓存: 分层渲染缓存与蒙版缓存的通用包装、命中统计与收益核算(全部缓存状态只在本模块)。"""
from .config import _LAYER_CACHE_BUDGET, _LAYER_CACHE_MAX_PX, _MASK_CACHE_MAX, _MASK_CACHE_MAX_PX
import collections
import os
import threading
import time


# 圆角/圆形 mask 缓存: mask 仅由(尺寸, 半径)决定, 与图像内容无关; 超采样会构建 4 倍边长的 L 图再缩回, 故按(尺寸, 半径)复用
# putalpha() 会拷贝 alpha 数据, 缓存对象不被输出图持有, 可安全复用
_MASK_CACHE = {}
_MASK_CACHE_LOCK = threading.Lock()
                                  # 命中/未命中计数: 仅在写缓存统计日志时读取一次
_MASK_STATS = {"hit": 0, "miss": 0}
_MASK_SAVED_MS = [0.0]            # 命中省下的构建耗时累计(毫秒), 供界面统计显示


def _cached_mask(key, build):
    with _MASK_CACHE_LOCK:
        ent = _MASK_CACHE.get(key)
    if ent is not None:
        _MASK_STATS["hit"] += 1
        _MASK_SAVED_MS[0] += ent[1]
        return ent[0]
    t0 = time.perf_counter()
    m = build()
    build_ms = (time.perf_counter() - t0) * 1000
    _MASK_STATS["miss"] += 1
    if key[1] * key[2] <= _MASK_CACHE_MAX_PX:
        with _MASK_CACHE_LOCK:
            if len(_MASK_CACHE) >= _MASK_CACHE_MAX:
                _MASK_CACHE.clear()
            _MASK_CACHE[key] = (m, build_ms)
    return m


# 服务"同一张图反复调参"的交互路径: 间距/文字/颜色变化时, 图片外观层与背景层可复用
# 键必须覆盖影响该层的全部参数, 否则会静默复用上次结果(改了参数预览不变); 键用精确尺寸, 与关闭缓存时逐像素一致
_LAYER_CACHE = collections.OrderedDict()   # LRU: 末尾最新(淘汰从头部取, 需要 popitem(last=False))
_LAYER_CACHE_LOCK = threading.Lock()
_LAYER_CACHE_BYTES = [0]            # 当前占用(字节)
_LAYER_CACHE_STATS = {"hit": 0, "miss": 0, "skip": 0}   # 观测用: 命中/未命中/未入缓存(层太大)
_LAYER_CACHE_SAVED_MS = [0.0]       # 命中省下的净耗时(毫秒): 该条目上次构建耗时 - 命中本身的副本开销
# 按线程关闭分层缓存: 批量中每张图只渲染一次, 缓存无收益且会挤掉交互预览的层
_LAYER_CACHE_TLS = threading.local()
# 预览源缩略图缓存(psrc)的命中计数
_THUMB_STATS = {"hit": 0, "miss": 0}
_THUMB_SAVED_MS = [0.0]             # 命中省下的生成耗时(读原图 + 缩略 + 落盘)累计(毫秒)


def cache_saved_ms():
    # 缓存命中省下的时间(毫秒, 估算值), 供界面统计显示
    # 计数为无锁自增(GIL 下不会撕裂), 读数是近似值
    layer = _LAYER_CACHE_SAVED_MS[0]
    mask = _MASK_SAVED_MS[0]
    thumb = _THUMB_SAVED_MS[0]
    return {"层": layer, "蒙版": mask, "缩略图": thumb, "合计": layer + mask + thumb}


def _cache_stats_line():
    # 本次渲染期间的缓存使用情况: 增量(相对上次落日志)、累计值与占用
    # 仅在写"渲染分段"日志时调用一次, 不在查找热路径上
    with _LAYER_CACHE_LOCK:
        cur = dict(_LAYER_CACHE_STATS)
        used_mb = _LAYER_CACHE_BYTES[0] / 1e6
    prev = _CACHE_STATS_SNAPSHOT
    dh, dm = cur["hit"] - prev[0], cur["miss"] - prev[1]
    _CACHE_STATS_SNAPSHOT[0], _CACHE_STATS_SNAPSHOT[1] = cur["hit"], cur["miss"]
    return (f" | 层缓存 +{dh}命中/+{dm}未命中 (累计 {cur['hit']}/{cur['miss']}, 占用 {used_mb:.0f}MB)"
            f" 蒙版 {_MASK_STATS['hit']}/{_MASK_STATS['miss']}"
            f" 缩略图 {_THUMB_STATS['hit']}/{_THUMB_STATS['miss']}")


_CACHE_STATS_SNAPSHOT = [0, 0]      # [层缓存累计命中, 累计未命中] 上一次落日志时的值(算增量用)


def _layer_bytes(im):
    # 估算一张 PIL 图的字节数(用于预算淘汰)
    try:
        w, h = im.size
        ch = len(im.getbands())
        n = getattr(im, "n_frames", 1)
        return w * h * ch * n
    except Exception:
        return 0


def _layer_cached(key, build, mutable=False):
    # 取缓存层: 未命中时调用 build() 构造并按预算缓存; key 为 None 时不使用缓存
    # mutable=True 表示调用方会就地改图(如把背景当画布继续绘制), 命中时必须在锁内返回副本,
    # 否则其它线程在此期间写入同一键会污染缓存; 超过单层像素上限的层不入缓存(仍会构造并返回)
    if key is None or getattr(_LAYER_CACHE_TLS, "off", False):
        _LAYER_CACHE_STATS["skip"] += 1
        return build()
    with _LAYER_CACHE_LOCK:
        ent = _LAYER_CACHE.get(key)
        if ent is not None:
            _LAYER_CACHE[key] = _LAYER_CACHE.pop(key)   # LRU: 移到末尾
            _LAYER_CACHE_STATS["hit"] += 1
            t0 = time.perf_counter()
            val = ent[0].copy() if mutable else ent[0]
            _LAYER_CACHE_SAVED_MS[0] += ent[2] - (time.perf_counter() - t0) * 1000
            return val
    _LAYER_CACHE_STATS["miss"] += 1
    t0 = time.perf_counter()
    val = build()
    build_ms = (time.perf_counter() - t0) * 1000
    try:
        px = val.size[0] * val.size[1]
    except Exception:
        px = 0
    if px and px <= _LAYER_CACHE_MAX_PX:
        with _LAYER_CACHE_LOCK:
            _LAYER_CACHE[key] = (val, _layer_bytes(val), build_ms)
            _LAYER_CACHE_BYTES[0] += _LAYER_CACHE[key][1]
            while _LAYER_CACHE_BYTES[0] > _LAYER_CACHE_BUDGET and len(_LAYER_CACHE) > 1:
                _, (_v, nb, _ms) = _LAYER_CACHE.popitem(last=False)
                _LAYER_CACHE_BYTES[0] -= nb
        # 入缓存后返回副本给会改图的调用方(缓存里那份要保持干净)
        return val.copy() if mutable else val
    return val


def _src_key(input_path):
    # 输入图的内容标识(路径 + mtime + size); 取不到时返回 None, 调用方据此跳过缓存
    try:
        st = os.stat(input_path)
        return (os.path.normcase(os.path.abspath(input_path)), st.st_mtime_ns, st.st_size)
    except Exception:
        return None
