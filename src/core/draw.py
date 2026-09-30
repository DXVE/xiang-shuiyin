"""绘制与形状: 文字绘制与原语、字体加载、方向剥离、圆角与圆形蒙版的构建与应用。"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from .cache import _cached_mask
from .paths import _default_font_path
import math
import os


# 计算给定字体下文本的像素高度
def get_text_height(font, text):
    tmp = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(tmp)
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


# 在图像上绘制带可选描边/投影的文字。
# shadow_angle 采用时钟系(0°=正上方, 顺时针增大), ox 正为右, oy 正为下。
def _shadow_offset(offset, angle):
    a = math.radians(angle)
    return int(offset * math.sin(a)), int(-offset * math.cos(a))


def draw_text(draw, canvas, xy, text, font, fill,
              stroke_enable=False, stroke_color="#ffffff", stroke_width=1,
              shadow_enable=False, shadow_color="#000000", shadow_offset=1,
              shadow_blur=2, shadow_alpha=0.5, shadow_angle=135,
              shadow_clip_bottom=None):
    # 使用 Pillow 原生描边参数(单次调用), 避免逐像素偏移绘制。
    use_stroke = stroke_enable and stroke_width > 0
    if shadow_enable and shadow_offset >= 0:
        # 文字投影: 文字 mask 沿角度方向平移后作为阴影(实心影 + 柔和边缘)。
        # 文字笔画细, 不做四周晕开, 否则笔画间隙会被填满; blur 仅柔化阴影边缘, offset 控制位移幅度。
        sw = stroke_width if use_stroke else 0
        bbox = draw.textbbox(xy, text, font=font, stroke_width=sw)
        ox, oy = _shadow_offset(shadow_offset, shadow_angle)
        cw, ch = canvas.size
        # shadow_blur 最小按 2px 处理: 否则阴影为硬边, 大字号行底部会出现明显截断感。
        eff_blur = max(shadow_blur, 2)
        # 高斯模糊可见范围约为半径的 3 倍: 阴影(含晕开)须完整落在画布内,
        # 空间不足时偏移向画布内收(可为负), 避免渐变在边缘硬切。
        spread = eff_blur * 3 + 8
        if ox > 0:
            ox = min(ox, cw - bbox[2] - spread)
        elif ox < 0:
            ox = max(ox, -(bbox[0] - spread))
        if oy > 0:
            oy = min(oy, ch - bbox[3] - spread)
        elif oy < 0:
            oy = max(oy, -(bbox[1] - spread))
        # shadow_clip_bottom 给定时, 阴影底部(含晕开)不超过该边界, 避免投到下一行文字上被遮挡。
        if shadow_clip_bottom is not None:
            oy = min(oy, shadow_clip_bottom - bbox[3] - eff_blur * 3 - 4)
        # pad 需覆盖高斯模糊可见范围(约 4 倍半径)与文字边缘, 否则渐变尾部会被 mask 边界硬切;
        # mask 区域仅覆盖阴影内容(文字 bbox + 偏移 + 晕开)。
        pad = eff_blur * 4 + 8
        sx0, sy0 = max(0, min(bbox[0], bbox[0] + ox) - pad), max(0, min(bbox[1], bbox[1] + oy) - pad)
        sx1, sy1 = min(cw, max(bbox[2], bbox[2] + ox) + pad), min(ch, max(bbox[3], bbox[3] + oy) + pad)
        if sx1 > sx0 and sy1 > sy0:
            mw, mh = sx1 - sx0, sy1 - sy0
            mask = Image.new("L", (mw, mh), 0)
            ImageDraw.Draw(mask).text((bbox[0] - sx0, bbox[1] - sy0), text, font=font, fill=255,
                                      stroke_width=sw, stroke_fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(eff_blur))
            sh_mask = Image.new("L", (mw, mh), 0)
            sh_mask.paste(mask, (ox, oy))
            sh = Image.new("RGBA", (mw, mh), shadow_color)
            sh.putalpha(sh_mask.point(lambda v: int(v * shadow_alpha)))
            canvas.paste(sh, (sx0, sy0), sh)
    draw.text(xy, text, fill=fill, font=font,
              stroke_width=stroke_width if use_stroke else 0,
              stroke_fill=stroke_color if use_stroke else None)


# 从 EXIF 中删除 Orientation 标记: 像素已 exif_transpose 物理转正, 保留该标记会被查看器二次旋转
def _strip_orientation(exif_bytes):
    if not exif_bytes:
        return exif_bytes
    try:
        ex = Image.Exif()
        ex.load(exif_bytes)
        ex.pop(0x0112, None)
        return ex.tobytes()
    except Exception:
        return exif_bytes


# 圆角矩形多边形路径: 每象限采样 n_per_quad 段, 直边由相邻端点连接。
# n_super=2 为四分之一圆弧, >2 为超椭圆(拐角曲率渐变, 与直边平滑衔接)。
def _rounded_rect_vertices(w, h, radius, n_per_quad=32, n_super=2.5):
    import math
    r = min(max(radius, 0.0), w / 2.0, h / 2.0)
    pts = []
    # 顺时针: 右上/右下/左下/左上四段, 圆心分别对应四角
    corners = [(w - r, r), (w - r, h - r), (r, h - r), (r, r)]
    a0s = [-math.pi / 2, 0.0, math.pi / 2, math.pi]   # 每段起始角
    for (cx, cy), a0 in zip(corners, a0s):
        for k in range(n_per_quad + 1):
            a = a0 + math.pi / 2 * k / n_per_quad
            c, s = math.cos(a), math.sin(a)
            # 超椭圆参数化 x=r·sign(c)|c|^(2/n), y=r·sign(s)|s|^(2/n); n=2 退化为圆弧
            x = r * math.copysign(abs(c) ** (2.0 / n_super), c)
            y = r * math.copysign(abs(s) ** (2.0 / n_super), s)
            # float→int 四舍五入(ImageDraw 内部按截断转 int, 会整体内偏 0.5px)
            pts.append((int(cx + x + 0.5), int(cy + y + 0.5)))
    return pts


# 整圆路径(圆心居中, 半径 radius), 用于圆形裁剪档
def _circle_vertices(w, h, radius, n_seg=128):
    import math
    cx, cy = w / 2.0, h / 2.0
    pts = []
    for k in range(n_seg + 1):
        a = math.pi * 2 * k / n_seg - math.pi / 2   # 自顶部顺时针
        pts.append((int(cx + radius * math.cos(a) + 0.5), int(cy + radius * math.sin(a) + 0.5)))
    return pts


def _build_round_mask(w, h, radius):
    ss = 4  # 超采样倍数
    # L 单通道 mask 每像素 1 字节, 上限放宽到 1.6 亿像素(约 160MB):
    # 24MP 以上大图(如 6000x4000)可保持 ss>=2, 边缘经 LANCZOS 缩小后平滑。
    while ss > 1 and (w * ss) * (h * ss) > 160_000_000:
        ss -= 1
    mw, mh = w * ss, h * ss
    mask = Image.new("L", (mw, mh), 0)
    ImageDraw.Draw(mask).polygon(_rounded_rect_vertices(mw, mh, radius * ss), fill=255)
    if ss > 1:
        mask = mask.resize((w, h), Image.Resampling.LANCZOS)
    return mask


def _build_circle_mask(w, h, d):
    ss = 4
    while ss > 1 and (w * ss) * (h * ss) > 160_000_000:
        ss -= 1
    mw, mh = w * ss, h * ss
    mask = Image.new("L", (mw, mh), 0)
    ImageDraw.Draw(mask).polygon(_circle_vertices(mw, mh, d * ss / 2.0), fill=255)
    if ss > 1:
        mask = mask.resize((w, h), Image.Resampling.LANCZOS)
    return mask


# 为图像添加圆角(生成 alpha 通道)
# 超采样(放大绘制后缩小, 缩小插值产生平滑边缘) + 超椭圆圆角路径(拐角曲率渐变, 视觉圆润)
def round_corners(image, radius):
    w, h = image.size
    mask = _cached_mask(("round", w, h, radius), lambda: _build_round_mask(w, h, radius))
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out


# 整图圆形(圆形档): 以短边为直径的内切圆裁剪, 长方形图也裁成正圆(长边超出部分透明)
def round_image_circle(image):
    w, h = image.size
    d = min(w, h)
    mask = _cached_mask(("circle", w, h, d), lambda: _build_circle_mask(w, h, d))
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out


def _load_font(size, bold, italic,
               font_path, font_bold_path, font_italic_path, font_bold_italic_path):
    paths = []
    if bold and italic:
        if font_bold_italic_path and os.path.isfile(font_bold_italic_path):
            paths.append(font_bold_italic_path)
    if bold and font_bold_path and os.path.isfile(font_bold_path):
        paths.append(font_bold_path)
    if italic and font_italic_path and os.path.isfile(font_italic_path):
        paths.append(font_italic_path)
    if font_path and os.path.isfile(font_path):
        paths.append(font_path)
    # 兜底: 传入路径失效时使用当前内置/系统字体,
    # 避免回退到 ImageFont.load_default() 的位图字体导致字号缩放失效
    if not paths:
        if bold and italic:
            p = _default_font_path("SarasaGothicSC-BoldItalic.ttf")
            if p:
                paths.append(p)
        if bold:
            p = _default_font_path("SarasaGothicSC-Bold.ttf")
            if p:
                paths.append(p)
        if italic:
            p = _default_font_path("SarasaGothicSC-Italic.ttf")
            if p:
                paths.append(p)
        p = _default_font_path("SarasaGothicSC-Regular.ttf")
        if p:
            paths.append(p)
    if not paths:
        return ImageFont.load_default()
    for p in paths:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            continue
    return ImageFont.load_default()
