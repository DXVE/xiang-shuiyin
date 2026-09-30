"""预览链路: 载荷指纹、请求、回帧与状态标识、取样图切换。"""
from . import i18n
from .specs import backend_params
from .widgets import _PV_CHIP_TIP
import hashlib
import json
import logging
import time
from .winconst import _SLOW_RENDER_MS


class PreviewMixin:

    def _desired_preview_source(self):
        if self._mode == "single":
            return self._input_path
        return self._batch_files[0]["path"] if self._batch_files else ""

    def _sync_preview_source(self):
        # 预览取样图: 单张模式取选中的图, 批量模式取批量列表第一张, 两者都无时沿用上一次的源
        # 返回 True 表示取样图已变化、需要重渲
        want = self._desired_preview_source() or getattr(self, "_preview_src", "")
        if want == getattr(self, "_preview_src", ""):
            return False
        self._preview_src = want
        self._sync_refresh_btn()        # 「刷新预览效果」只在有图可预览时可点
        self._sync_info_btn()           # 「ℹ 图片信息」同理
        if not want:
            self._preview.clear()
            self._preview.set_hint(self.tr("请拖入图片，或点击「打开图片」"))
        return True

    def _refresh_preview_clicked(self):
        # 「刷新预览效果」: 自动预览之外的手动入口, 用于渲染失败后重试或对同一组参数再渲染一次
        if not getattr(self, "_preview_src", ""):
            self._notify(self.tr("请先选择图片（也可直接拖入图片或文件夹）"))
            return
        self._manual_preview = True
        self._refresh_btn.setEnabled(False)
        self.request_preview()

    def _finish_manual_preview(self, ok):
        # 手动刷新的收尾: 复位按钮 + 给一句结果(仅手动刷新才提示)
        if not getattr(self, "_manual_preview", False):
            return
        self._manual_preview = False
        self._sync_refresh_btn()
        if ok:
            self._notify(self.tr("预览已生成"))

    def _sync_refresh_btn(self):
        # 无可预览图像时将「刷新预览效果」置灰, 并以 tooltip 说明
        if not hasattr(self, "_refresh_btn"):
            return
        has = bool(getattr(self, "_preview_src", ""))
        self._refresh_btn.setEnabled(has)
        self._refresh_btn.setToolTip(self.tr("按当前参数重新渲染预览") if has
                                     else self.tr("请先选择图片后再刷新预览"))

    def _sync_info_btn(self):
        # 没图可看时将「图片信息」置灰(与「刷新预览效果」同一口径)
        if not hasattr(self, "_info_btn"):
            return
        self._info_btn.setEnabled(bool(getattr(self, "_preview_src", "")))

    def _preview_payload(self):
        # 预览请求的载荷: 发请求与计算指纹共用同一份, 避免两处定义漂移
        return {
            "input_path": self._preview_src,
            "params": backend_params(self._params),
            "raw_model": self._raw_model,
            "custom_model": self._custom_model,
            "manual_exif": self._manual_exif(),
            # 直接要内存图像: 核心不落盘, 省掉 JPEG 编码/写盘/读回解码
            "inline_image": True,
        }

    def _preview_token(self):
        # 当前参数对应的指纹(短哈希); 与屏上那帧的指纹相等即表示屏上这帧由当前参数渲染
        raw = json.dumps(self._preview_payload(), sort_keys=True, ensure_ascii=False,
                         default=repr)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def _preview_state(self, queued_hint=False):
        # 状态标识的判据(顺序即优先级)
        # queued_hint: 回传帧带来的"回传那一刻队列里是否已排着更新的请求", 只在 _on_rendered 里传
        if not getattr(self, "_preview_src", ""):
            return "none"
        # 建窗过程中(渲染线程尚未启动)也可能进入此分支, 因此不直接取属性
        worker = getattr(self, "_preview_worker", None)
        if queued_hint or (worker is not None and worker.busy()):
            return "rendering"
        if self._preview_err is not None:
            # 失败优先于「指纹相等」与「从未成功渲染」
            return "failed"
        if self._shown_token is None:
            return "none"           # 一张都还没渲成过, 且没有失败
        if self._shown_token != self._preview_token():
            return "stale"
        return "synced"

    def _sync_chip(self, queued_hint=False):
        # 把状态标识刷成当前判据的结果(每次请求发出、每帧上屏/失败、取样图变更时各调一次)
        if not hasattr(self, "_state_chip"):
            return
        st = self._preview_state(queued_hint)
        tip = ""
        if st == "failed" and self._preview_err:
            tip = self.tr(_PV_CHIP_TIP["failed"]) + "\n" + i18n.tr_msg(str(self._preview_err))
        self._state_chip.set_state(st, tip)

    def request_preview(self):
        # 请求一次预览: 画布按核心的缩略档 PREVIEW_MAX_SIDE 渲染, 高清细节查看依赖放大镜
        if not getattr(self, "_preview_src", ""):
            self._sync_chip()
            return
        req = self._preview_payload()
        # 身份标识随请求走, 由渲染线程原样回传: 界面据此知道"屏上这帧是哪一组参数渲的"
        req["token"] = self._preview_token()
        # 上屏尺寸随请求走(不参与指纹): 渲染线程据此把画布缩到预览框要的尺寸
        dev = self._preview.avail_dev()
        req["display"] = (dev.width(), dev.height(), self._preview.devicePixelRatioF())
        self._preview_worker.request(req)
        self._sync_chip()

    def _on_rendered(self, resp):
        if not resp.get("ok"):
            err = i18n.tr_msg(str(resp.get("error") or ""))
            self._preview_err = err
            self._finish_manual_preview(False)          # 手动刷新失败也要把按钮放回可点(重试入口)
            self._notify(self.tr("预览失败：") + err)
            logging.warning(f"[预览] 渲染失败: {err}")
            self._sync_chip()
            return
        img = resp.get("image")
        if img is not None:
            self._preview.set_image(img, source=resp.get("source"), canvas=resp.get("canvas"),
                                    shown=resp.get("display"), shown_dpr=resp.get("display_dpr"))
        self._shown_token = resp.get("token")
        self._preview_err = None
        self._preview.set_geo(resp.get("geom"))
        e2e = (time.perf_counter() - resp.get("_req_t", time.perf_counter())) * 1000
        hit_ms = float(resp.get("cache_hit_ms") or 0.0)
        self._perf.setText(self.tr("渲染 {ms:.0f} ms · 请求 > 显示 {e2e:.0f} ms · 缓存命中 -{hit:.0f}ms")
                          .format(ms=resp.get("ms", 0), e2e=e2e, hit=hit_ms))
        self._finish_manual_preview(True)

        # 记录预览链路的排队、渲染、转图与端到端耗时
        ms = resp.get("ms", 0.0)
        queue_ms = resp.get("queue_ms", 0.0)
        qimg_ms = resp.get("qimg_ms", 0.0)
        scale_ms = resp.get("scale_ms", 0.0)
        arrived = resp.get("arrived_during", 0)
        # 画布尺寸取整幅画布(resp["canvas"]): resp["image"] 已是上屏尺寸的小图
        cw, ch = resp.get("canvas") or ((img.width(), img.height()) if img is not None else ("-", "-"))
        size = f"{cw}x{ch}"
        parts = [f"[预览] 渲染 {ms:.0f}ms (排队 {queue_ms:.0f}ms, 缩图 {scale_ms:.0f}ms, 转图 {qimg_ms:.0f}ms)",
                 f"画布 {size}",
                 f"档 {resp.get('max_side', '-')}",
                 f"请求 > 就绪 {e2e:.0f}ms"]
        if arrived:
            # 排队时间往往就是它造成的: 在跑的那一轮不会被中断, 新请求要等它跑完
            parts.append(f"渲染期间新增 {arrived} 次请求(上一轮不可中断, 排队主要来自这里)")
        line = " | ".join(parts)
        if ms + queue_ms >= _SLOW_RENDER_MS:
            logging.warning(line + f" [偏慢: 渲染 {ms:.0f}ms]")
        else:
            logging.info(line)

        # 会话统计: 与界面上的 _stats 标签同一份数据源(转图/上屏的明细只在上面那行日志里)
        self._stats.add_preview(ms=ms, queue_ms=queue_ms, e2e_ms=e2e, arrived=arrived)
        # 上一次渲染的分段耗时挂到 tooltip: 参数改完不用去翻日志就知道时间花在哪一段
        st = resp.get("stage") or {}
        if st.get("stages"):
            self._perf.setToolTip(
                self.tr("渲染分段({tag}) 合计 {total:.0f}ms").format(
                    tag=i18n.tr_msg(st.get("tag", "")), total=st.get("total_ms", 0))
                + "\n" + "\n".join("· " + i18n.tr_msg(s) for s in st["stages"])
                + "\n\n" + self.tr("参数指纹:") + "\n" + str(st.get("sig", "")))
        else:
            self._perf.setToolTip("")
        # 状态标识放最后刷: 它要看的是"这一刻"的判据(回传时队列里有没有已经排上的新请求)
        self._sync_chip(queued_hint=bool(resp.get("queued")))
