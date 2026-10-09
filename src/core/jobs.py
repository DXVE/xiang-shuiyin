"""任务与状态: 渲染线程 RenderWorker、应用状态 AppState(设置/预设/映射表/生成与预览调度)。"""
from PIL import Image
from .cache import _LAYER_CACHE_TLS, _THUMB_SAVED_MS, _THUMB_STATS, cache_saved_ms
from .config import (DEFAULT_LOGO_DIR, FONT_SETTING_MAP, LOW_RES_THRESHOLD, MAX_PRESET_SLOTS,
                     PRESETS_DIR, PRESETS_FILE, PREVIEW_DIR, PREVIEW_FAST_MAX_SIDE,
                     PREVIEW_MAX_SIDE)
from .exif import _inject_model_map, get_exif_data_from_path
from .params import _format_manual_exif
from .paths import (_default_font_path, _load_json, _repair_font_paths, _save_json, ensure_dir,
                    load_model_map, load_settings, preset_path, save_model_map, save_settings)
from .pipeline import PreviewCancelled, _oriented_height, _unique_output_path, process_single
from datetime import datetime
import logging
import os
import queue
import shutil
import threading
import time


class RenderWorker(threading.Thread):
    def __init__(self, mode, params, events, stop_event,
                 input_path=None, output_path=None, original_name=None,
                 file_paths=None, raw_model=None, custom_model=None,
                 save_map_flag=False, manual_exif=None, batch_out_dir=None):
        super().__init__(daemon=True)
        self.name = "生成线程"        # 进日志的 %(threadName)s: 与预览/界面线程区分开(日志格式里带线程名)
        self.mode = mode
        self.params = params
        self.events = events
        self.stop_event = stop_event
        self.input_path = input_path
        self.output_path = output_path
        self.batch_out_dir = batch_out_dir or None
        self.original_name = original_name
        self.file_paths = file_paths or []
        self.raw_model = raw_model
        self.custom_model = custom_model
        self.save_map_flag = save_map_flag
        self.manual_exif = manual_exif
        self.logo_dir = params.get("logo_dir", "")

    def run(self):
        try:
            if self.mode == "single":
                self._process_single()
            else:
                _LAYER_CACHE_TLS.off = True
                self._process_batch()
        except Exception as e:
            logging.error(f"处理异常: {e}")
            self.events.put({"type": "log", "msg": f"处理出错: {e}", "level": "ERROR"})
            self.events.put({"type": "finished", "message": str(e), "success": False})

    def _process_single(self):
        _inject_model_map(self.params, self.raw_model, self.custom_model, ensure_map=True)
        if self.raw_model and self.custom_model and self.custom_model != self.raw_model and self.save_map_flag:
            model_map = load_model_map(self.logo_dir)
            model_map[self.raw_model] = self.custom_model
            save_model_map(self.logo_dir, model_map)

        logging.info(f"单张处理: {self.input_path}")
        self.events.put({"type": "log", "msg": "处理中...", "level": "INFO"})
        t0 = time.monotonic()
        try:
            success, final_path = process_single(self.input_path, self.output_path,
                                                 manual_exif=self.manual_exif, **self.params)
        except Exception as e:
            logging.error(f"单张处理失败: {e}")
            self.events.put({"type": "log", "msg": f"处理失败: {e}", "level": "ERROR"})
            self.events.put({"type": "finished", "message": str(e), "success": False})
            return
        if success:
            one_ms = (time.monotonic() - t0) * 1000
            logging.info(f"单张完成: {final_path} (耗时 {one_ms:.0f}ms)")
            self.events.put({"type": "log", "msg": f"已完成: {self.original_name}", "level": "INFO"})
            self.events.put({"type": "finished", "message": f"已完成: {os.path.basename(final_path)}",
                             "success": True, "download_path": final_path, "ms": one_ms, "count": 1,
                             "download_name": os.path.basename(final_path),   # PNG 回退时与真实扩展名一致
                             "out_dir": os.path.dirname(os.path.abspath(final_path))})
        else:
            self.events.put({"type": "finished", "message": "处理失败", "success": False})

    def _process_batch(self):
        files = self.file_paths  # [{path, name}, ...]
        if not files:
            self.events.put({"type": "log", "msg": "没有可处理的图片", "level": "WARNING"})
            self.events.put({"type": "finished", "message": "没有可处理的图片", "success": False})
            return

        # 与单张对称的兜底: model_map 缺失时批量会抛 TypeError(单张由 _inject_model_map 注入)
        self.params.setdefault("model_map", {})

        out_dir = self.batch_out_dir or PREVIEW_DIR   # 批量子目录(导出文件夹/批量水印_<时间戳>)
        total = len(files)
        success = 0
        errors = []
        used_names = set()   # 输出名去重: 不同子目录同名图片不互相覆盖
        t_batch = time.monotonic()
        logging.info(f"批量处理: 共 {total} 个文件 -> {out_dir}")
        if self.manual_exif:
            # 批量使用与照片无关的参数(该设置一次作用于整批), 日志记明本次忽略了照片 EXIF
            logging.info("批量: 使用自定义水印信息(忽略每张照片的 EXIF)")
        for idx, item in enumerate(files, 1):
            if self.stop_event.is_set():
                logging.info(f"用户取消: 已完成 {success}/{total}")
                self.events.put({"type": "finished",
                                 "message": f"用户取消：已完成 {success}/{total}", "success": False,
                                 "out_dir": out_dir})   # 与成功/失败分支一致, 界面可打开已导出目录
                return
            fpath = item.get("path", "")
            fname = item.get("name", os.path.basename(fpath))
            stem = os.path.splitext(fname)[0]
            out_name = f"{stem}_watermarked.jpg"
            dup = 2
            while out_name in used_names:
                out_name = f"{stem}_watermarked_{dup}.jpg"
                dup += 1
            used_names.add(out_name)
            out_path = os.path.join(out_dir, out_name)
            logging.info(f"[{idx}/{total}] 处理: {fname} -> {out_name}")
            self.events.put({"type": "progress", "done": idx, "total": total, "name": fname})
            t_one = time.monotonic()
            try:
                process_single(fpath, out_path, manual_exif=self.manual_exif, **self.params)
                success += 1
                # 每张的耗时: 便于定位是单张偏大还是整体退化
                logging.info(f"[{idx}/{total}] 完成: {fname} (耗时 {(time.monotonic() - t_one) * 1000:.0f}ms)")
            except Exception as e:
                err_msg = f"{fname} -> {e}"
                logging.warning(f"处理失败 {fpath}: {e}")
                errors.append(err_msg)
        batch_ms = (time.monotonic() - t_batch) * 1000
        avg = f", 平均 {batch_ms / success:.0f}ms" if success else ""
        result_msg = f"批量处理完成：{success}/{total} -> {out_dir}"
        logging.info(f"批量总耗时 {batch_ms:.0f}ms (成功 {success}/{total}{avg})")
        if errors:
            fail_detail = "\n失败详情："
            for err in errors[:5]:
                fail_detail += f"\n  {err}"
            if len(errors) > 5:
                fail_detail += f"\n  ... 及其他 {len(errors) - 5} 个文件"
            result_msg += fail_detail
        logging.info(result_msg)
        self.events.put({"type": "log", "msg": result_msg, "level": "INFO" if success == total else "WARNING"})
        self.events.put({"type": "finished", "message": result_msg, "success": success == total,
                         "out_dir": out_dir, "ms": batch_ms, "count": success})


# 预览档位对应的输入缩放比: None 表示不缩(原图本身短于该档位), 档位为 0 时按全量加载
def _preview_scale_for(max_side, long_side):
    if max_side <= 0:
        logging.info(f"预览: 全量加载 {long_side}px(不缩略, 单次渲染会明显变慢)")
        return None
    scale = max_side / long_side
    return scale if scale < 1.0 else None


# 摘取渲染线程给的过期判据: 判据本身抛错时按"未过期"处理, 不因此中断回帧
def _cancel_requested(cancel_check):
    try:
        return bool(cancel_check()) if callable(cancel_check) else False
    except Exception:
        return False


class AppState:
    def __init__(self):
        self.settings = _repair_font_paths(load_settings())   # 字体路径失效时自动回退新资源目录
        self.events = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()
        self.generate_lock = threading.Lock()   # 串行化 start_generate 检查+启动, 防 TOCTOU
        self._rendering_count = 0   # 渲染中计数(多窗口并发渲染互不干扰), >0 时新预览返回旧图
        self._render_lock = threading.Lock()   # 保护 _rendering_count 原子增减(多线程桥接下防漂移)
        self._sel_lock = threading.Lock()
        # 预览缩略图缓存: {(原文件绝对路径, mtime_ns, size): (src 路径, 原图高度)}
        self._thumb_cache = {}
        self._thumb_lock = threading.Lock()
        self._thumb_gen_lock = threading.Lock()   # 串行化缩略图生成(预热线程 vs 预览请求)
        # 本次会话使用过的输出目录: 供 _allowed_open_dir 判断「打开所在目录」是否放行
        self.user_dirs = set()
        ensure_dir(PREVIEW_DIR)
        self.exit_reason = None  # 退出原因, 供退出日志打印

    # 输入路径校验: 只要求存在且为普通文件
    # Qt 同进程内不存在"不可信调用方"这一威胁模型, 因此它不是安全边界: 据此加白名单会让新功能(如"最近打开")静默失效
    def _is_allowed_input(self, p):
        try:
            return bool(p) and os.path.isfile(p)
        except Exception:
            return False

    # 按文件头魔数识别图片真实类型, 返回扩展名('.jpg'/'.webp' 等)或 None(非受支持图片)
    def _sniff_image_type(self, p):
        if not p or not os.path.isfile(p):
            return None
        try:
            with open(p, "rb") as f:
                head = f.read(16)
        except Exception:
            return None
        if head.startswith(b"\xff\xd8\xff"):
            return ".jpg"                # JPEG
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"                # PNG
        if head.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"                # GIF
        if head.startswith(b"BM"):
            return ".bmp"                # BMP
        if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
            return ".webp"               # WEBP
        if head.startswith((b"II*\x00", b"MM\x00*")):
            return ".tif"                # TIFF
        if head.startswith(b"\x00\x00\x01\x00"):
            return ".ico"                # ICO
        return None

    # 校验 open_dir 目标是否在白名单内(导出目录/PREVIEW_DIR), 返回规范化绝对路径或 None
    def _allowed_open_dir(self, p):
        if not p:
            return None
        try:
            absp = os.path.abspath(p)
            target = absp if os.path.isdir(absp) else os.path.dirname(absp)
        except Exception:
            return None
        roots = [PREVIEW_DIR, DEFAULT_LOGO_DIR]
        out_dir = (self.settings.get("output_dir") or "").strip()
        if out_dir:
            roots.append(out_dir)   # 历史遗留值(现已不再持久化 output_dir)
        with self._sel_lock:
            roots.extend(self.user_dirs)   # 本次会话实际用过的导出目录(见 start_generate)
        target_nc = os.path.normcase(target)
        for base in roots:
            if not base:
                continue
            base_nc = os.path.normcase(os.path.abspath(base))
            # 目标 == 根目录 或 目标在根目录的子目录内
            if target_nc == base_nc or target_nc.startswith(base_nc + os.sep):
                return target
        return None

    def read_exif(self, path):
        if not path or not os.path.isfile(path):
            return {"make": None, "model": None}
        try:
            exif = get_exif_data_from_path(path)
        except Exception:
            exif = {"make": None, "model": None}
        return {"make": exif.get("make"), "model": exif.get("model")}

    def save_model_map(self, logo_dir, map_dict):
        save_model_map(logo_dir or DEFAULT_LOGO_DIR, map_dict or {})
        return {"ok": True}

    def get_custom_colors(self):
        return {"colors": self.settings.get("custom_colors") or []}

    def save_custom_colors(self, colors):
        arr = [str(c) for c in (colors or [])][:10]
        self.settings["custom_colors"] = arr
        save_settings(self.settings)
        return {"ok": True, "colors": arr}

    # 读 10 个槽位, 返回 {槽位字符串: 记录}(仅含有内容的槽)
    def load_presets(self):
        self._migrate_presets()
        out = {}
        for slot in range(1, MAX_PRESET_SLOTS + 1):
            fpath = preset_path(slot)
            if not os.path.isfile(fpath):
                continue
            rec = _load_json(fpath, check_file=True)
            if not isinstance(rec, dict):
                continue
            rec.setdefault("_preset_name", f"方案 {slot}")
            for fkey, fname in FONT_SETTING_MAP:        # 字体路径失效 -> 只在内存里回落
                cur = rec.get(fkey)
                if not cur or not os.path.isfile(cur):
                    newp = _default_font_path(fname)
                    if newp:
                        rec[fkey] = newp
            out[str(slot)] = rec
        return out

    # 把一份参数存进槽位(同名/同槽覆盖由界面先确认)
    def preset_save(self, slot, params, name):
        try:
            n = int(slot)
        except (TypeError, ValueError):
            return {"ok": False, "error": "槽位无效"}
        if not 1 <= n <= MAX_PRESET_SLOTS:
            return {"ok": False, "error": f"槽位超出 1~{MAX_PRESET_SLOTS}"}
        rec = dict(params or {})
        rec["_preset_name"] = (name or "").strip() or f"方案 {n}"
        _save_json(preset_path(n), rec, ensure_path=PRESETS_DIR)
        return {"ok": True}

    def preset_delete(self, slot):
        fpath = preset_path(slot)
        try:
            if os.path.isfile(fpath):
                os.remove(fpath)
        except (OSError, TypeError, ValueError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    # 把两种历史格式的预设迁入槽位文件, 随后把源文件改名(保留数据, 避免已删除的预设被再次迁入)
    def _migrate_presets(self):
        if os.path.isfile(PRESETS_FILE):
            old = _load_json(PRESETS_FILE)
            moved = 0
            if isinstance(old, dict):
                for key in sorted(old, key=lambda k: str(k)):
                    rec = old[key]
                    if not isinstance(rec, dict):
                        continue
                    try:
                        self.preset_save(str(key), rec, rec.get("_preset_name") or f"方案 {key}")
                        moved += 1
                    except Exception as e:              # noqa: BLE001 - 单个迁不动不挡其它
                        logging.warning(f"预设迁移失败(槽位 {key}): {e}")
            try:
                os.replace(PRESETS_FILE, PRESETS_FILE + ".migrated")
                logging.info(f"预设已迁入槽位文件: {moved} 个; 老文件改名 "
                             f"{os.path.basename(PRESETS_FILE)}.migrated")
            except OSError as e:
                logging.warning(f"老预设文件改名失败(下次启动会再迁一次): {e}")
        # 按名字命名的旧预设文件: 按名字排序填入空槽, 随后移入子目录(只填空槽以免覆盖现有数据,
        # 放不下的保留在 _migrated/ 中而不删除)
        try:
            stray = [f for f in sorted(os.listdir(PRESETS_DIR))
                     if f.lower().endswith(".json") and not f.lower().startswith("presets")]
        except OSError:
            return
        if not stray:
            return
        free = [i for i in range(1, MAX_PRESET_SLOTS + 1) if not os.path.isfile(preset_path(i))]
        park = os.path.join(PRESETS_DIR, "_migrated")
        for i, fname in zip(free, stray):
            src = os.path.join(PRESETS_DIR, fname)
            rec = _load_json(src, check_file=True)
            if not isinstance(rec, dict):
                continue
            try:
                self.preset_save(str(i), rec,
                                 rec.get("_preset_name") or os.path.splitext(fname)[0])
                os.makedirs(park, exist_ok=True)
                os.replace(src, os.path.join(park, fname))
                logging.info(f"预设已迁入空槽位 {i}: {fname}")
            except Exception as e:                      # noqa: BLE001
                logging.warning(f"预设迁移失败({fname}): {e}")
        left = [f for f in stray if os.path.isfile(os.path.join(PRESETS_DIR, f))]
        if left:
            logging.warning(f"槽位已满, {len(left)} 个旧预设留在 {park} 里未参与列表: {left[:5]}")

    def start_generate(self, req):
        self.generate_lock.acquire()   # 串行化 检查+创建+启动, 消除并发请求的 TOCTOU
        try:
            if self.worker and self.worker.is_alive():
                return {"ok": False, "error": "已有任务正在处理，请先等待完成或停止"}
            mode = req.get("mode", "single")
            params = req.get("params") or {}
            manual_exif = _format_manual_exif(req.get("manual_exif"))
            self.stop_event = threading.Event()
            self.events = queue.Queue()
            # 导出目录: 以请求值为先(界面未保存的改动即时生效), 否则取持久化设置, 最后回退到临时目录
            out_root = ((req.get("output_dir") or self.settings.get("output_dir") or "").strip()
                        or PREVIEW_DIR)
            # 记录本次会话用过的导出目录: output_dir 不再持久化, 「打开所在目录」的白名单依赖它
            with self._sel_lock:
                self.user_dirs.add(os.path.normcase(os.path.abspath(out_root)))
            if mode == "single":
                in_p = (req.get("input_path") or "").strip()
                original_name = (req.get("original_name") or os.path.basename(in_p)).strip()
                if not in_p or not os.path.isfile(in_p) or not self._is_allowed_input(in_p):
                    return {"ok": False, "error": "请先选择一张输入图片"}
                try:
                    os.makedirs(out_root, exist_ok=True)
                except Exception as e:
                    return {"ok": False, "error": f"导出目录不可写：{out_root}（{e}）"}
                stem = os.path.splitext(original_name)[0]
                out_p = _unique_output_path(out_root, stem)
                out_name = os.path.basename(out_p)
                self.worker = RenderWorker(
                    "single", params, self.events, self.stop_event,
                    input_path=in_p, output_path=out_p, original_name=out_name,
                    raw_model=req.get("raw_model"), custom_model=req.get("custom_model"),
                    save_map_flag=bool(req.get("save_map_flag")), manual_exif=manual_exif)
            else:
                file_paths = req.get("file_paths") or []
                if not file_paths:
                    return {"ok": False, "error": "请先选择要批量处理的图片"}
                # 与单张模式相同: 仅接受通过校验的原文件路径(见 _is_allowed_input)
                validated = []
                for item in file_paths:
                    if not isinstance(item, dict):
                        return {"ok": False, "error": "批量文件格式错误"}
                    p = (item.get("path") or "").strip()
                    if not p or not os.path.isfile(p) or not self._is_allowed_input(p):
                        return {"ok": False, "error": "批量文件路径无效（请重新选择或拖入）"}
                    validated.append({"path": p, "name": item.get("name") or os.path.basename(p)})
                file_paths = validated
                # 批量在导出文件夹下建独立子目录(不打包 zip), 与单张成品隔离
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")   # 毫秒级, 防同一秒两次批量撞同一子目录
                batch_out = os.path.join(out_root, f"批量水印_{ts}")
                try:
                    os.makedirs(batch_out, exist_ok=True)
                except Exception as e:
                    return {"ok": False, "error": f"无法创建导出子目录: {e}"}
                self.worker = RenderWorker("batch", params, self.events, self.stop_event,
                                              file_paths=file_paths, batch_out_dir=batch_out,
                                              manual_exif=manual_exif)
            self.worker.start()
            return {"ok": True}
        finally:
            self.generate_lock.release()

    def stop(self):
        self.stop_event.set()
        return {"ok": True}

    # 把已打开的图缩略到 max_side 并保存为 psrc_<tag>.jpg, 返回 (src 路径, EXIF 转正后高度)
    def _write_preview_src(self, im, tag="", max_side=PREVIEW_MAX_SIDE):
        exif = im.info.get("exif", b"")
        src_h = _oriented_height(im)
        if max_side > 0:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        prefix = "psrc" if max_side <= 0 or max_side >= PREVIEW_MAX_SIDE else "psrcf"
        src = os.path.join(PREVIEW_DIR, f"{prefix}_{tag}.jpg" if tag else f"{prefix}_gen.jpg")
        save_kw = {"format": "JPEG", "quality": 90}
        if exif:
            save_kw["exif"] = exif
        im.convert("RGB").save(src, **save_kw)
        return src, src_h

    # 返回该原图对应的预览缩略图 (src 路径, EXIF 转正后高度); 命中 (原文件, mtime, size, max_side) 缓存则不重读原图
    def _preview_src_for(self, input_path, src_image=None, max_side=PREVIEW_MAX_SIDE):
        try:
            st = os.stat(input_path)
            key = (os.path.normcase(os.path.abspath(input_path)), st.st_mtime_ns, st.st_size, max_side)
            tag = f"{int(st.st_mtime)}_{st.st_size}"
        except Exception:
            key, tag = None, ""
        if key is not None:
            with self._thumb_lock:
                ent = self._thumb_cache.get(key)
            if ent and os.path.isfile(ent[0]):
                _THUMB_STATS["hit"] += 1
                _THUMB_SAVED_MS[0] += ent[2]        # 省下的是"读原图 + 缩略 + 落盘"那一次
                logging.info(f"预览: 命中缩略图缓存 {os.path.basename(ent[0])}")
                return ent[0], ent[1]
        with self._thumb_gen_lock:
            if key is not None:   # 等锁期间可能已被预热线程生成好
                with self._thumb_lock:
                    ent = self._thumb_cache.get(key)
                if ent and os.path.isfile(ent[0]):
                    _THUMB_STATS["hit"] += 1
                    _THUMB_SAVED_MS[0] += ent[2]
                    logging.info(f"预览: 命中缩略图缓存(等锁后) {os.path.basename(ent[0])}")
                    return ent[0], ent[1]
            t0 = time.perf_counter()
            if src_image is not None:
                src, src_h = self._write_preview_src(src_image, tag, max_side)
            else:
                with Image.open(input_path) as im:
                    src, src_h = self._write_preview_src(im, tag, max_side)
            gen_ms = (time.perf_counter() - t0) * 1000
            _THUMB_STATS["miss"] += 1
            if key is not None:
                with self._thumb_lock:
                    if len(self._thumb_cache) > 8:   # 预览源通常就一张, 留少量最近项即可
                        self._thumb_cache.clear()
                    self._thumb_cache[key] = (src, src_h, gen_ms)
        logging.info(f"预览: 生成缩略图 {os.path.basename(src)} (src_h={src_h})")
        return src, src_h


    # 该原图在指定档位的缩略图是否已生成: 只查缓存不触发生成, 与 _preview_src_for 共用同一套键
    def _preview_src_cached(self, input_path, max_side):
        try:
            st = os.stat(input_path)
            key = (os.path.normcase(os.path.abspath(input_path)), st.st_mtime_ns, st.st_size, max_side)
        except Exception:
            return False
        with self._thumb_lock:
            ent = self._thumb_cache.get(key)
        return bool(ent and os.path.isfile(ent[0]))

    # 先导帧条件: 首次看这张大图(全档缩略图尚未生成)才先渲一帧小图, 之后每帧调参都不再付这份开销
    def _want_pilot(self, on_fast, input_path, long_side):
        if not callable(on_fast) or long_side <= PREVIEW_FAST_MAX_SIDE:
            return False
        return not self._preview_src_cached(input_path, PREVIEW_MAX_SIDE)

    # 一档预览渲染: 缩略到 max_side 后渲染, 返回 (ok, resp); resp 的 image 是整幅画布的 PIL 图(不落盘), 由渲染线程转 QImage 并缩到上屏尺寸
    def _render_tier(self, input_path, params, req, src_image, max_side, preview_scale, cancel_check):
        geom_out, image_out, stage_out = {}, [], []
        t0 = time.perf_counter()
        try:
            success, _ = self._render_preview(
                input_path, params, req.get("manual_exif"),
                req.get("raw_model"), req.get("custom_model"),
                None, preview_scale=preview_scale,
                geom_out=geom_out, src_image=src_image, image_out=image_out,
                stage_out=stage_out, max_side=max_side,
                cancel_check=cancel_check)
        except PreviewCancelled:
            raise
        except Exception as e:
            logging.error(f"预览生成失败: {e}")
            return False, {"ok": False, "error": f"预览生成失败: {e}"}
        if not success or not image_out:
            return False, {"ok": False, "error": "预览生成失败"}
        resp = {"ok": True, "image": image_out[0],
                # 全量档(max_side=0)返回真实画布边长, 避免调用方与日志读到 0
                "max_side": max_side or max(image_out[0].size),
                # 本档从开渲到出图的耗时: 全档会被渲染线程的请求级耗时覆盖, 先导帧则据此报告即时上屏的代价
                "ms": (time.perf_counter() - t0) * 1000}
        if geom_out:
            resp["geom"] = geom_out   # 间距段坐标, 预览图叠加标注用
        if stage_out:
            resp["stage"] = stage_out[0]   # 渲染分段: 界面显示 + 请求级日志复用, 不必读日志文件
        return True, resp


    # 统一渲染入口: preview_scale 给定时先缩略输入图, geom_out 收集间距段几何, src_image 复用调用方已打开的句柄, stage_out 回传渲染分段
    def _render_preview(self, input_path, params, manual_exif, raw_model, custom_model,
                        out_path, preview_scale=None, pv="", geom_out=None, src_image=None,
                        image_out=None, stage_out=None, max_side=PREVIEW_MAX_SIDE,
                        cancel_check=None):
        params = dict(params)
        params["low_res_enhance"] = False
        if preview_scale:
            src, src_h = self._preview_src_for(input_path, src_image, max_side)
            input_path = src
            # 预览相对成品的渲染宽度比: 所有绝对像素参数按此等比缩放, 使预览观感与成品一致
            # (圆角/模糊半径/字号下限/描边宽都是像素绝对值, 不缩放则预览小图上显得更大更粗)。
            # scale2 = 成品那一侧的 2x 系数(成品恒为开), 按 EXIF 转正后的高度判定(见 _oriented_height)。
            scale2 = 2 if src_h < LOW_RES_THRESHOLD else 1
            ratio = preview_scale / scale2
            params["min_font_size"] = max(8, int(params.get("min_font_size", 20) * ratio))
            params["corner_radius"] = max(0, int(params.get("corner_radius", 0) * ratio))
            params["image_stroke_width"] = max(0, int(params.get("image_stroke_width", 5) * ratio))
            params["text_stroke_width"] = max(0, int(params.get("text_stroke_width", 1) * ratio))
            params["blur_radius"] = max(0, int(params.get("blur_radius", 10) * ratio))
            params["image_shadow_offset"] = max(0, int(params.get("image_shadow_offset", 25) * ratio))
            params["image_shadow_blur"] = max(0, int(params.get("image_shadow_blur", 30) * ratio))
            params["text_shadow_offset"] = max(0, int(params.get("text_shadow_offset", 3) * ratio))
            params["text_shadow_blur"] = max(0, int(params.get("text_shadow_blur", 5) * ratio))
            # 阴影方向是角度, 与分辨率无关, 不缩放
        # 与生成一致: 注入临时型号美化映射(仅本次生效, 不写映射表)
        params = _inject_model_map(params, raw_model, custom_model, copy_on_inject=True)
        return process_single(input_path, out_path,
                              manual_exif=_format_manual_exif(manual_exif),
                              geom_out=geom_out, image_out=image_out, stage_out=stage_out,
                              cancel_check=cancel_check,
                              **params)

    # 实时预览: 输入图缩略到 PREVIEW_MAX_SIDE 后渲染, 画布 PIL 图经 image_out 回给调用方(不落盘)
    # on_fast: 可选回调, 传入时先渲一帧低分辨率画面交它即时上屏(仅首次看这张大图, 见 _want_pilot)
    def make_preview(self, req, cancel_check=None, on_fast=None):
        input_path = (req.get("input_path") or "").strip()
        if not input_path or not os.path.isfile(input_path) or not self._is_allowed_input(input_path):
            return {"ok": False, "error": "请先选择一张输入图片"}
        params = req.get("params") or {}
        with self._render_lock:
            self._rendering_count += 1
        # 本帧靠缓存命中省下的时间: 取渲染前后的差值(读的是已有计数器, 不是新加的计时点)
        saved_before = cache_saved_ms()["合计"]
        try:
            # 只打开一次原图: 尺寸计算与缩略图生成共用同一句柄
            with Image.open(input_path) as im:
                long_side = max(im.size) or 1
                if self._want_pilot(on_fast, input_path, long_side):
                    # 先导帧缩"解码后原图的副本": 两档共用这一次解码(24MP 图上省约 450ms),
                    # 副本不可省 —— _write_preview_src 用 thumbnail() 原地缩小, 借用原句柄会把随后的全档缩略图也缩成小档
                    im.load()
                    ok_p, pilot = self._render_tier(
                        input_path, params, req, im.copy(), PREVIEW_FAST_MAX_SIDE,
                        _preview_scale_for(PREVIEW_FAST_MAX_SIDE, long_side), cancel_check)
                    if ok_p:
                        pilot["fast"] = True
                        on_fast(pilot)
                    if _cancel_requested(cancel_check):
                        # 先导帧期间已被更新的请求取代: 全分辨率帧不必再渲
                        return {"ok": False, "cancelled": True}
                ok, resp = self._render_tier(
                    input_path, params, req, im, PREVIEW_MAX_SIDE,
                    _preview_scale_for(PREVIEW_MAX_SIDE, long_side), cancel_check)
        except PreviewCancelled as e:
            # 本轮已过期: 不回帧、不当失败, 线程随即去渲待渲染槽里的最新一份
            logging.info(f"预览: 上一轮在「{e}」处让位给更新的请求")
            return {"ok": False, "cancelled": True}
        except Exception as e:
            logging.error(f"预览生成失败: {e}")
            return {"ok": False, "error": f"预览生成失败: {e}"}
        finally:
            with self._render_lock:
                self._rendering_count -= 1
        if not ok:
            return resp
        # 这一帧的缓存收益: 与本帧渲染耗时并列显示, 才有"对照"的意义(累计值只会一直变大)
        # 口径: 命中次数 × 各条目上次构建耗时。生成线程同时在跑时, 它的命中会算进来, 略有偏差。
        resp["cache_hit_ms"] = max(0.0, cache_saved_ms()["合计"] - saved_before)
        return resp

    def cleanup(self):
        try:
            if os.path.isdir(PREVIEW_DIR):
                shutil.rmtree(PREVIEW_DIR, ignore_errors=True)
        except Exception:
            pass


# 列出目录内的候选文件, recursive=True 时连子目录一起走
def _list_images_in_dir(folder, recursive=False):
    out = []
    try:
        if recursive:
            for root, _dirs, names in os.walk(folder):
                for nm in sorted(names):
                    out.append(os.path.join(root, nm))
        else:
            for nm in sorted(os.listdir(folder)):
                p = os.path.join(folder, nm)
                if os.path.isfile(p):
                    out.append(p)
    except Exception as e:
        logging.warning(f"列出目录失败 {folder}: {e}")
    return out
