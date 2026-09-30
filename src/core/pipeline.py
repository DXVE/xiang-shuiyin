"""渲染流水线: process_single 单张成品的完整渲染与落盘, 及其量化、尺寸与输出辅助。"""
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from .cache import _cache_stats_line, _layer_cached, _src_key
from .config import BLUR_WORK_MIN_SIDE, LOW_RES_THRESHOLD, SLOW_RENDER_MS, _BLUR_WORK_STEP
from .draw import (_circle_vertices, _load_font, _rounded_rect_vertices, _shadow_offset,
                   _strip_orientation, draw_text, get_text_height, round_corners,
                   round_image_circle)
from .exif import (aperture_to_str, exposure_to_str, find_logo, get_exif_data_from_path,
                   iso_to_str, load_logo, prettify_model)
import logging
import os
import time


def _quantize_work(v):
    # 把工作图边长对齐到 _BLUR_WORK_STEP 网格(不足一格按一格算)
    return max(_BLUR_WORK_STEP, int(round(v / _BLUR_WORK_STEP)) * _BLUR_WORK_STEP)


def _oriented_height(im):
    # 按 EXIF Orientation 返回"像素转正后"的高度(不做像素运算, 只读方向标记)
    try:
        w, h = im.size
        return h if im.getexif().get(0x0112) not in (5, 6, 7, 8) else w
    except Exception:                     # noqa: BLE001 - 读不到方向标记时按未旋转处理
        return im.size[1]


# 计算一组水印行的总高度(含行距): 每行为 (类型, 内容, 字体, 高度), 高度取第4项
def _sec_height(sec, line_spacing):
    if not sec:
        return 0
    return sum(row[-1] for row in sec) + line_spacing * (len(sec) - 1)


# 按扩展名保存成品图: JPEG 优先携带原 EXIF(已去 Orientation), 保存失败逐级回退 PNG
def _save_output(canvas, output_path, original_exif):
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)

    ext = os.path.splitext(output_path)[1].lower()
    if ext in ('.jpg', '.jpeg'):
        try:
            canvas.save(output_path, format='JPEG', quality=100, subsampling=0,
                        exif=_strip_orientation(original_exif) if original_exif else None)
        except Exception:
            try:
                canvas.save(output_path, format='JPEG', quality=100, subsampling=0)
            except Exception:
                png_path = os.path.splitext(output_path)[0] + ".png"
                canvas.save(png_path, format='PNG', compress_level=0)
                output_path = png_path
    else:
        canvas.save(output_path, format='PNG', compress_level=0)
    return True, output_path


# 生成不覆盖已存在成品的唯一输出路径(单张模式用)
def _unique_output_path(out_root, stem, suffix="_watermarked.jpg"):
    out_path = os.path.join(out_root, f"{stem}{suffix}")
    if not os.path.exists(out_path):
        return out_path
    dup = 2
    while True:
        candidate = os.path.join(out_root, f"{stem}_watermarked_{dup}.jpg")
        if not os.path.exists(candidate):
            return candidate
        dup += 1


class PreviewCancelled(Exception):
    """预览这一轮已被更新的请求取代, 在阶段边界抛出(见 process_single 的 cancel_check)。"""


# 单张图片水印处理: 生成带 EXIF 信息水印的最终图片
def process_single(input_path, output_path, logo_dir,
                   font_path, font_bold_path, font_italic_path, font_bold_italic_path,
                   bg_color, text_color, bg_mode, blur_radius, corner_radius,
                   gap_image_top, gap_image_bottom, gap_top, gap_bottom, line_gap,
                   scale_logo, scale_model, scale_params,
                   bold_logo, italic_logo, bold_model, italic_model,
                   bold_params, italic_params,
                   canvas_ratio, image_margin, model_map,
                   corner_circle=False,
                   show_brand=True, show_model=True, show_params=True,
                   min_font_size=20,
                   low_res_enhance=True,   # 内部参数: 低清增强(2 倍超采样)
                   enable_image_stroke=False,
                   image_stroke_color="#ffffff",
                   image_stroke_width=5,
                   enable_text_stroke=False,
                   text_stroke_color="#ffffff",
                   text_stroke_width=1,
                   enable_image_shadow=True,
                   image_shadow_color="#000000",
                   image_shadow_offset=25,
                   image_shadow_blur=30,
                   image_shadow_alpha=0.35,
                   image_shadow_angle=135,
                   enable_text_shadow=False,
                   text_shadow_color="#000000",
                   text_shadow_offset=3,
                   text_shadow_blur=5,
                   text_shadow_alpha=0.2,
                   text_shadow_angle=135,
                   bg_darken_strength=0.0,
                   manual_exif=None,
                   layout_mode="unified",   # 水印布局模式
                   geom_out=None,    # 可选: 传入 dict, 填充间距段几何坐标(画布像素), 供预览叠加标注
                   image_out=None,   # 可选: 传入 list, 直接拿到画布 PIL 图而不落盘(调用方要内存图像时用)
                   stage_out=None,   # 可选: 传入 list, 回传本次渲染分段结果(供界面显示, 免读日志)
                   stage_log=True,   # 内部参数: 是否记录渲染分段日志
                   cancel_check=None):  # 可选: 返回 True 表示"这轮已过期", 在阶段边界中止
    _t0 = time.monotonic()
    t_prev = _t0
    stages = []   # ["预处理 12ms", ...] 按序累积

    def _check_cancel(tag):
        # 过期就抛 PreviewCancelled(只在预览路径触发)
        if cancel_check is not None and cancel_check():
            raise PreviewCancelled(tag)

    def _stage(name):
        nonlocal t_prev
        _check_cancel(name)
        now = time.monotonic()
        stages.append(f"{name} {(now - t_prev) * 1000:.0f}ms")
        t_prev = now

    def _flush_stage_log(tag):
        # 把分段结果写成一条日志(渲染末尾调用一次)
        if not stage_log:
            return
        sig = (f"画布{canvas_w}x{canvas_h} {layout_mode} 比例{canvas_ratio:.4g}" if canvas_ratio > 0
               else f"画布{canvas_w}x{canvas_h} {layout_mode} 比例原图") + (
               f" bg={bg_mode}/r{blur_radius}/d{bg_darken_strength} 圆角{corner_radius}{'圆' if corner_circle else ''}"
               f" 边距{image_margin} 间距{gap_image_top}/{gap_image_bottom}/{gap_top}/{gap_bottom}/{line_gap}"
               f" 字{min_font_size_in} 缩{scale_logo}/{scale_model}/{scale_params}"
               f" 描边{int(enable_image_stroke)} 影{int(enable_image_shadow)}o{image_shadow_offset}"
               f"b{image_shadow_blur}a{image_shadow_alpha} 字描{int(enable_text_stroke)} 字影{int(enable_text_shadow)}")
        total_ms = (time.monotonic() - _t0) * 1000
        if stage_out is not None:
            # 回给调用方: 界面把它放进"上一次渲染"的 tooltip
            stage_out.append({"tag": tag, "stages": list(stages), "total_ms": total_ms, "sig": sig})
        if image_out is not None and total_ms < SLOW_RENDER_MS:
            return
        logging.info(f"[{tag}] 渲染分段: " + ", ".join(stages)
                     + f" | 合计 {total_ms:.0f}ms | {sig}{_cache_stats_line()}")

    # 低清增强会放大该值, 参数指纹记录的是用户输入的原值
    min_font_size_in = min_font_size
    # 显式关闭原图句柄, 避免批量处理时句柄与内存堆积(convert()/resize() 生成新对象, 不受 close() 影响)
    # src_key: 本次输入图的内容标识, 分层缓存键均以它开头
    # 预览档下 input_path 为 psrc 缩略图(文件名含原图 mtime 与 size), 可直接作为原图标识
    src_key = _src_key(input_path)
    original_exif = b""
    _original = Image.open(input_path)
    try:
        original_exif = _original.info.get("exif", b"")
        original = _original
        # 透明输入(RGBA/LA/P): 透明区合成画布底色, 避免 convert("RGB") 丢弃 alpha 后露出黑/白杂色
        if original.mode in ("RGBA", "LA", "P"):
            original = original.convert("RGBA")
            bg = Image.new("RGBA", original.size, bg_color + "FF")
            original = Image.alpha_composite(bg, original)
        original = original.convert("RGB")
        # 按 EXIF Orientation 物理转正像素, 否则竖拍照片的水印方向会随查看器旋转
        original = ImageOps.exif_transpose(original)
        img_w, img_h = original.size

        render_scale = 1
        if low_res_enhance and img_h < LOW_RES_THRESHOLD:
            render_scale = 2
            min_font_size = int(min_font_size * render_scale)
            img_w *= render_scale
            img_h *= render_scale
            original_large = original.resize((img_w, img_h), Image.Resampling.LANCZOS)
        else:
            original_large = original
    finally:
        _original.close()
    _stage("预处理(解码/转正/增强)")

    exif = get_exif_data_from_path(input_path)
    _stage("EXIF读取")

    # 防御性钳制: 防止直接调用或异常输入产生极端画布尺寸
    if canvas_ratio > 0:
        canvas_ratio = min(max(canvas_ratio, 0.05), 20.0)

    margin_ratio = max(0.005, min(0.05, image_margin))
    margin_x = int(min(img_w, img_h) * margin_ratio)
    top_margin = margin_x
    base_canvas_w = img_w + 2 * margin_x

    temp_base = img_h * 0.2
    base_model_size = int(temp_base / 5.5)
    base_param_size = int(temp_base / 5.5)
    font_size_brand = max(min_font_size, int(base_param_size * scale_logo))
    font_size_model = max(min_font_size, int(base_model_size * scale_model))
    font_size_params = max(min_font_size, int(base_param_size * scale_params))

    font_brand = _load_font(font_size_brand, bold_logo, italic_logo,
                            font_path, font_bold_path, font_italic_path, font_bold_italic_path)
    font_model = _load_font(font_size_model, bold_model, italic_model,
                            font_path, font_bold_path, font_italic_path, font_bold_italic_path)
    font_params = _load_font(font_size_params, bold_params, italic_params,
                             font_path, font_bold_path, font_italic_path, font_bold_italic_path)

    lines = []

    if show_brand:
        make = (manual_exif.get("make") if manual_exif else None) or exif.get("make")
        brand_text = make if make else "UNKNOWN"
        if make:
            logo_file = find_logo(logo_dir, make)
            if logo_file:
                logo_max_h = int(temp_base * 0.45 * scale_logo)
                limg = load_logo(logo_file, max_height=logo_max_h)
                if limg:
                    lines.append(("logo", limg, None, limg.height))
                else:
                    h = get_text_height(font_brand, brand_text)
                    lines.append(("text", brand_text, font_brand, h))
            else:
                h = get_text_height(font_brand, brand_text)
                lines.append(("text", brand_text, font_brand, h))
        else:
            h = get_text_height(font_brand, brand_text)
            lines.append(("text", brand_text, font_brand, h))

    if show_model:
        # 优先级: 自定义水印信息 > 临时美化/映射表 > EXIF原型号
        manual_model = (manual_exif.get("model") if manual_exif else None)
        if manual_model:
            model_disp = manual_model
        else:
            model_raw = exif.get("model")
            model_disp = prettify_model(model_raw, model_map) if model_raw else "UNKNOWN MODEL"
        h = get_text_height(font_model, model_disp)
        lines.append(("text", model_disp, font_model, h))

    if show_params:
        if manual_exif and any([manual_exif.get(k) for k in ("exposure", "fnumber", "iso")]):
            params = "    ".join(filter(None, [
                manual_exif.get("exposure", ""),
                manual_exif.get("fnumber", ""),
                manual_exif.get("iso", ""),
            ]))
            if not params:
                params = "NO EXIF DATA"
        else:
            exp_str = exposure_to_str(
                exif.get("exposure_time"),
                exif.get("exposure_num"),
                exif.get("exposure_den")
            )

            ap_str = aperture_to_str(exif.get("fnumber"))
            iso_str = iso_to_str(exif.get("iso"))
            params = "    ".join(filter(None, [exp_str, ap_str, iso_str]))
            if not params:
                params = "NO EXIF DATA"
        h = get_text_height(font_params, params)
        lines.append(("text", params, font_params, h))

    gap_image_top_px = int(img_h * gap_image_top)
    gap_image_bottom_px = int(img_h * gap_image_bottom)
    gap_top_px = int(img_h * gap_top)
    bottom_gap_px = int(img_h * gap_bottom)

    if layout_mode == "split_top_bottom":
        # split_top_bottom: 品牌/logo 在上方, 型号与参数在下方
        if show_brand:
            above_lines = lines[:1] if lines else []
            below_lines = lines[1:]
        else:
            above_lines = []
            below_lines = lines
    else:
        above_lines = []
        below_lines = lines

    if not lines:
        min_height = top_margin + img_h + top_margin
        if canvas_ratio <= 0:
            canvas_w = base_canvas_w
            canvas_h = min_height
        else:
            ideal_h = int(base_canvas_w / canvas_ratio)
            if ideal_h < min_height:
                canvas_w = int(min_height * canvas_ratio)
                canvas_h = min_height
            else:
                canvas_w = base_canvas_w
                canvas_h = ideal_h
    else:
        line_spacing = max(0, int(img_h * line_gap))

        if layout_mode == "split_top_bottom":
            # 上下分离(镜像+居中): 上下中带等高 side_h = max(上带, 下带), 图片始终垂直居中
            above_content_h = _sec_height(above_lines, line_spacing)
            below_content_h = _sec_height(below_lines, line_spacing)
            side_h = max(gap_top_px + above_content_h + gap_image_top_px,
                         gap_image_bottom_px + below_content_h + bottom_gap_px)
            water_area_h = 2 * side_h
            min_height = 2 * margin_x + 2 * side_h + img_h
        elif layout_mode == "split_left_right":
            left_section = lines[:1] if show_brand else []
            right_section = lines[1:] if show_brand else lines
            left_h = _sec_height(left_section, line_spacing)
            right_h = _sec_height(right_section, line_spacing)
            side_h = max(left_h, right_h)
            water_area_h = gap_image_bottom_px + side_h + bottom_gap_px   # 水印与图片间距=gap_image_bottom, 与底部=gap_bottom
            min_height = top_margin + img_h + water_area_h
        else:
            total_text_h = _sec_height(lines, line_spacing)
            water_area_h = gap_image_bottom_px + total_text_h + bottom_gap_px
            min_height = top_margin + img_h + water_area_h

        if canvas_ratio <= 0:
            canvas_w = base_canvas_w
            canvas_h = min_height
        else:
            ideal_h = int(base_canvas_w / canvas_ratio)
            if ideal_h < min_height:
                if layout_mode == "split_top_bottom":
                    content_h = 2 * side_h + img_h
                else:
                    content_h = img_h + water_area_h
                min_height = content_h + 2 * margin_x
                canvas_w = int(min_height * canvas_ratio)
                canvas_h = min_height
            else:
                canvas_w = base_canvas_w
                canvas_h = ideal_h
                if canvas_h < min_height:
                    canvas_h = min_height

    MAX_DIM = 20000
    if canvas_w > MAX_DIM or canvas_h > MAX_DIM:
        scale_factor = min(MAX_DIM / canvas_w, MAX_DIM / canvas_h)
        canvas_w = int(canvas_w * scale_factor)
        canvas_h = int(canvas_h * scale_factor)
        margin_x = int(margin_x * scale_factor)
        top_margin = margin_x

    # 垂直居中
    _u_off = 0   # 统一下方: 比例画布高于内容时, 整块(图+水印)上移量, 供 paste/绘制共用
    if layout_mode == "split_top_bottom" and lines:
        # 等高中带: 图片上缘 = 画布中心 - 图高/2, 上下边带等高 → 图片画布垂直居中;
        # 比例画布更高时多余高度均分到两侧(上带起点 = 中心 - 图高/2 - side_h ≥ margin_x)
        above_content_h = _sec_height(above_lines, line_spacing)
        below_content_h = _sec_height(below_lines, line_spacing)
        side_h = max(gap_top_px + above_content_h + gap_image_top_px,
                     gap_image_bottom_px + below_content_h + bottom_gap_px)
        top_margin = (canvas_h - img_h) // 2 - side_h
    elif lines:
        content_bottom = top_margin + img_h + water_area_h
        extra = canvas_h - content_bottom
        if extra > 0:
            _u_off = extra // 2   # 统一下方: 记录上移量(paste 走底部锚定, 不读 top_margin)
            top_margin += _u_off  # 左右分离借 top_margin 居中(统一下方此变量对定位无效, 兼容保留)
    else:
        content_bottom = top_margin + img_h + top_margin
        extra = canvas_h - content_bottom
        if extra > 0:
            top_margin += extra // 2

    # 确保图片适配画布(极端比例如 10:1 或 1:4 时图片可能超出画布)
    avail_w = canvas_w - 2 * margin_x
    if layout_mode == "split_top_bottom" and lines:
        avail_h = canvas_h - 2 * margin_x - 2 * side_h
    elif layout_mode == "split_left_right" and lines:
        avail_h = canvas_h - top_margin - water_area_h
    elif lines:
        avail_h = img_h   # 统一下方: 图片高度固定, 仅按宽度缩放
    else:
        avail_h = canvas_h - top_margin - top_margin
    scale = min(avail_w / img_w, avail_h / img_h, 1.0)
    if scale < 1.0:
        img_w = int(img_w * scale)
        img_h = int(img_h * scale)
        original_large = original_large.resize((img_w, img_h), Image.Resampling.LANCZOS)
        gap_image_top_px = int(img_h * gap_image_top)
        gap_image_bottom_px = int(img_h * gap_image_bottom)
        gap_top_px = int(img_h * gap_top)
        bottom_gap_px = int(img_h * gap_bottom)
        line_spacing = max(0, int(img_h * line_gap))
        if lines and layout_mode == "split_top_bottom":
            side_h = max(gap_top_px + _sec_height(above_lines, line_spacing) + gap_image_top_px,
                         gap_image_bottom_px + _sec_height(below_lines, line_spacing) + bottom_gap_px)
            water_area_h = 2 * side_h
        elif lines and layout_mode != "split_top_bottom":
            total_text_h = _sec_height(lines, line_spacing)
    _stage("字体/几何")

    def _build_background():
        # 背景层: 模糊模式为工作图放大后的毛玻璃, 纯色模式为纯色图
        if bg_mode != "blur":
            return Image.new("RGB", (canvas_w, canvas_h), bg_color)

        wk_w = max(BLUR_WORK_MIN_SIDE, _quantize_work(canvas_w // 4))
        wk_h = max(BLUR_WORK_MIN_SIDE, _quantize_work(canvas_h // 4))
        wk_blur = max(1, round(blur_radius * (wk_w / canvas_w)))

        def _build_work_image():
            # 模糊工作图(降采样 → 增强 → 模糊 → 噪声 → 暖白蒙版)
            x = original_large.resize((wk_w, wk_h), Image.Resampling.LANCZOS)
            x = ImageEnhance.Color(x).enhance(1.35)
            x = ImageEnhance.Contrast(x).enhance(1.08)
            x = ImageEnhance.Brightness(x).enhance(1.10)
            x = x.filter(ImageFilter.GaussianBlur(wk_blur))
            # 噪声抖动: 小图生成同样保留随机高频, 放大后仍能打破色阶带/磨砂质感
            noise = Image.effect_noise((wk_w, wk_h), 3).convert("RGB")
            x = Image.blend(x, noise, 0.02)
            # 暖白半透明蒙版(雾白玻璃)
            x = Image.blend(x, Image.new("RGB", (wk_w, wk_h), (250, 248, 245)), 0.22)
            return x

        _check_cancel("背景层")
        work = _layer_cached(("bg_work", src_key, wk_w, wk_h, wk_blur), _build_work_image)
        _check_cancel("背景层")
        return work.resize((canvas_w, canvas_h), Image.Resampling.BILINEAR)

    bg = _layer_cached(
        ("bg", src_key, canvas_w, canvas_h, bg_mode, blur_radius, bg_color) if bg_mode == "blur" else None,
        _build_background, mutable=True)
    if bg_mode == "blur" and bg_darken_strength > 0:
        canvas = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), bg_darken_strength)
    else:
        canvas = bg
    _stage("背景层")

    paste_x = (canvas_w - img_w) // 2
    paste_y = top_margin
    if layout_mode == "split_top_bottom" and lines:
        paste_y = top_margin + side_h   # 图片上缘 = 上带底(图片画布垂直居中)
    elif layout_mode == "split_left_right":
        pass  # 左右分离保持 paste_y = top_margin
    elif lines:
        # 统一下方: 默认图片+水印整体贴底(gap_image_bottom/gap_bottom 精确对应);
        # 画布高于内容时整块上移 _u_off 垂直居中(与左右/上下布局视觉一致)
        water_bottom = canvas_h - bottom_gap_px
        paste_y = water_bottom - total_text_h - gap_image_bottom_px - img_h - _u_off
        if paste_y < 0:
            paste_y = 0

    # 图片外观层(圆角/正圆): 只依赖图片尺寸、圆角与圆形开关, 调间距/文字/背景时缓存键不变
    # display_img 同时是阴影层的形状来源
    display_img = original_large
    circle_mode = corner_circle   # 圆形开关: 整图以短边为直径裁成正圆(长边超出部分透明)
    if circle_mode:
        display_img = _layer_cached(("img_round", src_key, img_w, img_h, "circle"),
                                    lambda: round_image_circle(original_large))
    elif corner_radius > 0:
        display_img = _layer_cached(("img_round", src_key, img_w, img_h, "round", corner_radius),
                                    lambda: round_corners(original_large, corner_radius))
    _stage("图片外观(圆角/正圆)")
    # 图片投影(水墨晕开): 图片形状模糊后作为 alpha, 阴影自内容边缘向外渐变晕开
    # 阴影层须带 padding 外扩区, 否则 Pillow 模糊不超出图像边界, 无法形成向外晕开渐变(blur 只影响圆角边缘)
    if enable_image_shadow and image_shadow_offset > 0:
        ox, oy = _shadow_offset(image_shadow_offset, image_shadow_angle)
        pad = image_shadow_blur * 2 + 4   # 晕开外扩区
        cw, ch = canvas.size
        # 限制偏移使阴影完整落在画布内: 图片贴边时向画布内收缩, 避免硬切截断
        if ox > 0:
            ox = min(ox, max(cw - (paste_x + img_w) - pad - 4, 0))
        elif ox < 0:
            ox = max(ox, -(paste_x - pad - 4))
        if oy > 0:
            oy = min(oy, max(ch - (paste_y + img_h) - pad - 4, 0))
        elif oy < 0:
            oy = max(oy, -(paste_y - pad - 4))
        shape = display_img.split()[3] if (circle_mode or corner_radius > 0) else Image.new("L", (img_w, img_h), 255)
        sw_, sh_ = img_w + pad * 2, img_h + pad * 2

        def _build_shadow():
            # 形状模糊 + 着色(与画布无关, 偏移钳制由调用方按画布做)
            shape_p = Image.new("L", (sw_, sh_), 0)
            shape_p.paste(shape, (pad, pad))
            blurred = (shape_p if image_shadow_blur <= 0
                       else shape_p.filter(ImageFilter.GaussianBlur(image_shadow_blur)))
            s = Image.new("RGBA", (sw_, sh_), image_shadow_color)
            s.putalpha(blurred.point(lambda v: int(v * image_shadow_alpha)))
            return s

        sh = _layer_cached(
            ("img_shadow", src_key, img_w, img_h, pad, image_shadow_blur,
             image_shadow_color, image_shadow_alpha, circle_mode, corner_radius),
            _build_shadow)
        canvas.paste(sh, (paste_x + ox - pad, paste_y + oy - pad), sh)
    _stage("图片阴影层")
    canvas.paste(display_img, (paste_x, paste_y), display_img if (circle_mode or corner_radius > 0) else None)

    # 图片内描边
    # Pillow ImageDraw 的 outline 直绘无抗锯齿, 硬边叠加在已抗锯齿的圆角图片上会形成双重锯齿
    # 圆角时描边改用超采样: 放大画 L 通道 mask 后 LANCZOS 缩回, 边缘过渡与图片圆角一致
    if enable_image_stroke and image_stroke_width > 0:
        rect = (paste_x, paste_y, paste_x + img_w, paste_y + img_h)
        if circle_mode or corner_radius > 0:
            def _build_stroke():
                # 描边层(4x 超采样多边形 + LANCZOS 缩回, 单通道 mask 上色, 与画布无关)
                ss = 4
                while ss > 1 and ((img_w + image_stroke_width) * ss) * ((img_h + image_stroke_width) * ss) > 160_000_000:
                    ss -= 1
                out_w, out_h = img_w + image_stroke_width, img_h + image_stroke_width
                if circle_mode:
                    # 圆形描边: 内切圆向内外各扩 W/2 的圆环(圆心居中)
                    mw, mh = out_w * ss, out_h * ss
                    rr = min(img_w, img_h) * ss / 2.0
                    half = image_stroke_width * ss / 2.0
                    edge = Image.new("L", (mw, mh), 0)
                    d = ImageDraw.Draw(edge)
                    d.polygon(_circle_vertices(mw, mh, rr + half), fill=255)
                    d.polygon(_circle_vertices(mw, mh, max(0.0, rr - half)), fill=0)
                else:
                    # 描边 = 图片边缘向内外各扩 W/2: 外缘圆角矩形(向外扩 W, 半径 R+W/2)填充 255,
                    # 挖空内缘圆角矩形(向内缩 W, 半径 R-W/2); 内缘须用缩小后的尺寸, 只改半径会让直边处完全重叠被挖空
                    # (圆角>0 时描边消失); 大圆角退化为椭圆时内外缘长短半轴各差 W/2, 描边带仍存在
                    sw_, sh_ = out_w * ss, out_h * ss
                    iw_, ih_ = max(2, (img_w - image_stroke_width) * ss), max(2, (img_h - image_stroke_width) * ss)
                    half = image_stroke_width * ss / 2.0
                    r_out = (corner_radius + image_stroke_width / 2.0) * ss
                    r_in = max(0.0, (corner_radius - image_stroke_width / 2.0) * ss)
                    edge = Image.new("L", (sw_, sh_), 0)
                    d = ImageDraw.Draw(edge)
                    d.polygon(_rounded_rect_vertices(sw_, sh_, r_out), fill=255)
                    ox, oy = (sw_ - iw_) // 2, (sh_ - ih_) // 2
                    d.polygon([(x + ox, y + oy) for x, y in _rounded_rect_vertices(iw_, ih_, r_in)], fill=0)
                # 图层含外扩 W/2(外缘在图片外), resize/paste 用外扩尺寸并偏移 W/2
                if ss > 1:
                    edge = edge.resize((out_w, out_h), Image.Resampling.LANCZOS)
                lay = Image.new("RGBA", (out_w, out_h), image_stroke_color)
                lay.putalpha(edge)
                return lay

            stroke_layer = _layer_cached(
                ("img_stroke", src_key, img_w, img_h, image_stroke_width, image_stroke_color,
                 circle_mode, corner_radius),
                _build_stroke)
            canvas.paste(stroke_layer,
                         (paste_x - image_stroke_width // 2, paste_y - image_stroke_width // 2),
                         stroke_layer)
        else:
            # 直角图: ImageDraw 直绘(无抗锯齿但有硬边, 与直角图片一致), 开销可忽略
            draw = ImageDraw.Draw(canvas)
            draw.rectangle(rect, outline=image_stroke_color, width=image_stroke_width)
    _stage("图片描边层")

    _geom = {"gaps": {"image_margin": [], "gap_top": [], "gap_image_top": [],
                      "gap_image_bottom": [], "gap_bottom": [], "line_gap": []}}
    if lines:
        draw = ImageDraw.Draw(canvas)
        line_spacing = max(0, int(img_h * line_gap))
        bottom_gap_px = int(img_h * gap_bottom)
        gap_image_top_px = int(img_h * gap_image_top)
        gap_image_bottom_px = int(img_h * gap_image_bottom)
        gap_top_px = int(img_h * gap_top)   # 绘制阶段重算, 供左右/上下布局的间距使用

        def _render_line(typ, content, fnt, h, cx, cy, align="center", shadow_clip_bottom=None):
            # 渲染单行水印, align: center / left / right, 左右对齐以图片边线为基准
            if typ == "logo":
                lw = content.width
                if align == "left":
                    px = paste_x                       # 图片左缘
                elif align == "right":
                    px = paste_x + img_w - lw          # 图片右缘
                else:
                    px = cx - lw // 2
                canvas.paste(content, (px, int(cy)), content)
            else:
                bbox = draw.textbbox((0, 0), content, font=fnt)
                tw = bbox[2] - bbox[0]
                if align == "left":
                    tx = paste_x
                elif align == "right":
                    tx = paste_x + img_w - tw
                else:
                    tx = cx - tw // 2
                draw_text(draw, canvas, (tx, cy), content, fnt, text_color,
                          stroke_enable=enable_text_stroke,
                          stroke_color=text_stroke_color,
                          stroke_width=text_stroke_width,
                          shadow_enable=enable_text_shadow,
                          shadow_color=text_shadow_color,
                          shadow_offset=text_shadow_offset,
                          shadow_blur=text_shadow_blur,
                          shadow_alpha=text_shadow_alpha,
                          shadow_angle=text_shadow_angle,
                          shadow_clip_bottom=shadow_clip_bottom)

        cx = canvas_w // 2

        if layout_mode == "split_top_bottom":
            # 等高中带渲染: 上组贴图(品牌底 → 图顶 = gap_image_top), 下组贴图(图底 → 型号顶 = gap_image_bottom)
            # 矮侧/比例画布的余量落在外侧, 图片由画布几何保证垂直居中
            above_content_h = _sec_height(above_lines, line_spacing)
            below_content_h = _sec_height(below_lines, line_spacing)
            cy = paste_y - gap_image_top_px - above_content_h
            above_ys = []
            for i, (typ, content, fnt, h) in enumerate(above_lines):
                clip = cy + h + line_spacing if i < len(above_lines) - 1 else paste_y
                _render_line(typ, content, fnt, h, cx, cy, "center", clip)
                above_ys.append((cy, cy + h))
                cy += h + line_spacing
            below_y = paste_y + img_h + gap_image_bottom_px   # 图底到下方水印顶 = gap_image_bottom
            cy = below_y
            below_ys = []
            for i, (typ, content, fnt, h) in enumerate(below_lines):
                clip = cy + h + line_spacing if i < len(below_lines) - 1 else None
                _render_line(typ, content, fnt, h, cx, cy, "center", clip)
                below_ys.append((cy, cy + h))
                cy += h + line_spacing
            # 几何标注(实际渲染距离): 内侧两段=滑块值; 外侧两段含矮侧余量/画布边界
            if geom_out is not None:
                gap_top_end = above_ys[0][0] if above_ys else min(top_margin, paste_y - gap_image_top_px)
                _geom["gaps"]["gap_top"].append((0, gap_top_end))
                gap_image_top_start = above_ys[-1][1] if above_ys else paste_y - gap_image_top_px
                _geom["gaps"]["gap_image_top"].append((gap_image_top_start, paste_y))
                _geom["gaps"]["gap_image_bottom"].append((paste_y + img_h, below_y))
                if below_ys:
                    _geom["gaps"]["gap_bottom"].append((below_ys[-1][1], canvas_h))
                    for (y0, _), (y1, _) in zip(below_ys, below_ys[1:]):
                        _geom["gaps"]["line_gap"].append((y0, y1))

        elif layout_mode == "split_left_right":
            # 下方: 品牌靠左, 型号+参数靠右
            left_section = lines[:1] if show_brand else []
            right_section = lines[1:] if show_brand else lines
            left_h = _sec_height(left_section, line_spacing)
            right_h = _sec_height(right_section, line_spacing)
            bottom_h = max(left_h, right_h)
            base_y = paste_y + img_h + gap_image_bottom_px   # 水印与图片间距 = gap_image_bottom

            cy = base_y + (bottom_h - left_h) // 2
            for i, (typ, content, fnt, h) in enumerate(left_section):
                clip = cy + h + line_spacing if i < len(left_section) - 1 else None
                _render_line(typ, content, fnt, h, cx, cy, "left", clip)
                cy += h + line_spacing

            cy = base_y + (bottom_h - right_h) // 2
            right_ys = []
            for i, (typ, content, fnt, h) in enumerate(right_section):
                clip = cy + h + line_spacing if i < len(right_section) - 1 else None
                _render_line(typ, content, fnt, h, cx, cy, "right", clip)
                right_ys.append((cy, cy + h))
                cy += h + line_spacing
            # 几何标注: gap_image_bottom=图底→水印区顶; gap_bottom=水印区底→画布底
            if geom_out is not None:
                _geom["gaps"]["gap_image_bottom"].append((paste_y + img_h, base_y))
                _geom["gaps"]["gap_bottom"].append((base_y + bottom_h, canvas_h))
                for (y0, _), (y1, _) in zip(right_ys, right_ys[1:]):
                    _geom["gaps"]["line_gap"].append((y0, y1))

        else:
            # 统一下方: 水印区贴画布底, 预留 descender/描边安全区(避免 bottom_gap=0 时笔画贴边被切)
            fnt_last = lines[-1][2]
            desc = fnt_last.getmetrics()[1] if fnt_last else 0
            bottom_safe = desc + (text_stroke_width if enable_text_stroke else 0)
            water_start_y = canvas_h - bottom_gap_px - bottom_safe - _sec_height(lines, line_spacing) - _u_off
            cy = water_start_y
            all_ys = []
            for i, (typ, content, fnt, h) in enumerate(lines):
                # 阴影 clip 到下一行上边界, 避免阴影投到下一行文字上被遮挡(看起来截断)
                clip = cy + h + line_spacing if i < len(lines) - 1 else None
                _render_line(typ, content, fnt, h, cx, cy, "center", clip)
                all_ys.append((cy, cy + h))
                cy += h + line_spacing
            # 几何标注: gap_image_bottom=图底→水印区顶; gap_bottom=水印区底→画布底
            if geom_out is not None:
                _geom["gaps"]["gap_image_bottom"].append((paste_y + img_h, water_start_y))
                if all_ys:
                    # gap_bottom 只记用户设的 bottom_gap_px, 画布底预留的 bottom_safe(descender/描边安全区)不计入
                    _geom["gaps"]["gap_bottom"].append((all_ys[-1][1], all_ys[-1][1] + bottom_gap_px))
                    for (y0, _), (y1, _) in zip(all_ys, all_ys[1:]):
                        _geom["gaps"]["line_gap"].append((y0, y1))
    _stage("水印文字绘制")

    if render_scale > 1:
        canvas = canvas.resize((round(canvas_w / render_scale),
                                round(canvas_h / render_scale)),
                               Image.Resampling.LANCZOS)
        _stage("低清缩回")

    # 收集几何坐标(供预览叠加间距标注): _geom 记录的是 render_scale 放大前坐标,
    # 需按最终画布尺寸归一化(canvas 可能被 MAX_DIM 或 render_scale 缩放)
    if geom_out is not None and any(_geom["gaps"].values()):
        fx = canvas.size[0] / canvas_w
        fy = canvas.size[1] / canvas_h
        geom_out["canvas"] = canvas.size
        geom_out["img"] = (round(paste_x * fx), round(paste_y * fy),
                           round((paste_x + img_w) * fx), round((paste_y + img_h) * fy))
        geom_out["gaps"] = {
            k: [(round(y0 * fy), round(y1 * fy)) for (y0, y1) in v]
            for k, v in _geom["gaps"].items()
        }

    if image_out is not None:
        # 调用方直接取内存图像: 跳过编码与写盘
        image_out.append(canvas)
        _flush_stage_log("预览")
        return True, None
    ret = _save_output(canvas, output_path, original_exif)
    _stage("编码保存")
    _flush_stage_log("成品")
    return ret
