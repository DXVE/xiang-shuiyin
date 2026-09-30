"""参数: 内核默认参数表与手动 EXIF 信息的格式化。"""
from .config import DEFAULT_LOGO_DIR
from .paths import _default_font_path


# 通用模板: 去空白 -> 空串返空 -> 已含前缀/后缀则原样返回 -> 否则自动补上
def _fmt_wrap(v, prefix="", suffix="", check=None):
    v = v.strip()
    if not v:
        return ""
    return v if (check and check(v)) else f"{prefix}{v}{suffix}"


def _fmt_iso(v):
    return _fmt_wrap(v, prefix="ISO", check=lambda s: s.upper().startswith("ISO"))


def _fmt_fnumber(v):
    return _fmt_wrap(v, prefix="f/",
                     check=lambda s: s.startswith("f/") or s.startswith("F/"))


def _fmt_exposure(v):
    return _fmt_wrap(v, suffix="s", check=lambda s: s.endswith("s"))


def _format_manual_exif(manual_exif):
    if not manual_exif:
        return None
    return {
        "make": (manual_exif.get("make") or "").strip(),
        "model": (manual_exif.get("model") or "").strip(),
        "exposure": _fmt_exposure(manual_exif.get("exposure") or ""),
        "fnumber": _fmt_fnumber(manual_exif.get("fnumber") or ""),
        "iso": _fmt_iso(manual_exif.get("iso") or ""),
    }


def default_params():
    return {
        "logo_dir": DEFAULT_LOGO_DIR,
        "font_path": _default_font_path("SarasaGothicSC-Regular.ttf"),
        "font_bold_path": _default_font_path("SarasaGothicSC-Bold.ttf"),
        "font_italic_path": _default_font_path("SarasaGothicSC-Italic.ttf"),
        "font_bold_italic_path": _default_font_path("SarasaGothicSC-BoldItalic.ttf"),
        "canvas_ratio_mode": "original",
        "gap_image_top": 0.04,
        "gap_image_bottom": 0.04,
        "gap_top": 0.04,
        "gap_bottom": 0.04,
        "line_gap": 0.02,
        "image_margin": 0.015,
        "show_brand": True,
        "show_model": True,
        "show_params": True,
        "bg_color": "#FFFFFF",
        "text_color": "#383a42",
        "bg_mode": "color",
        "blur_radius": 150,
        "corner_radius": 0,
        "corner_circle": False,
        "scale_logo": 0.7,
        "scale_model": 0.5,
        "scale_params": 0.3,
        "bold_logo": True,
        "italic_logo": False,
        "bold_model": False,
        "italic_model": True,
        "bold_params": False,
        "italic_params": True,
        "min_font_size": 20,
        "enable_image_stroke": False,
        "image_stroke_color": "#ffffff",
        "image_stroke_width": 5,
        "enable_text_stroke": False,
        "text_stroke_color": "#ffffff",
        "text_stroke_width": 1,
        "enable_image_shadow": True,
        "image_shadow_color": "#000000",
        "image_shadow_offset": 25,
        "image_shadow_blur": 30,
        "image_shadow_alpha": 0.35,
        "image_shadow_angle": 135,
        "enable_text_shadow": False,
        "text_shadow_color": "#000000",
        "text_shadow_offset": 3,
        "text_shadow_blur": 5,
        "text_shadow_alpha": 0.2,
        "text_shadow_angle": 135,
        "bg_darken_strength": 0,
        "layout_mode": "unified",
    }
