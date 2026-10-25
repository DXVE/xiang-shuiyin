"""参数表与内核参数契约: 页签、卡片、控件与默认值的唯一数据源。"""
from PySide6.QtCore import QT_TRANSLATE_NOOP
from core.pipeline import process_single
import inspect


# process_single 实际接受的形参名 —— backend_params() 据此过滤 UI 侧参数
_BACKEND_KEYS = set(inspect.signature(process_single).parameters)


# canvas_ratio_mode 是 UI 侧概念，后端只接受数值型 canvas_ratio
_RATIOS = {'1:1': 1, '3:2': 3 / 2, '2:3': 2 / 3, '4:3': 4 / 3, '3:4': 3 / 4,
           '16:9': 16 / 9, '9:16': 9 / 16, '21:9': 21 / 9, '9:21': 9 / 21}
# 数值型 canvas_ratio → 档位的反查表，migrate_preset() 据此还原档位。
# 浮点相等成立: 数值由 _RATIOS 同一批字面量算出, JSON 往返精确
_RATIO_MODES = {v: k for k, v in _RATIOS.items()}


def migrate_preset(p):
    # 把预设迁成当前口径, 返回副本, 不修改传入对象
    # 仅含数值 canvas_ratio 的记录经 _RATIO_MODES 反查补上档位; 反查失败时取 original
    out = dict(p)
    if "canvas_ratio_mode" not in out and "canvas_ratio" in out:
        try:
            num = float(out.get("canvas_ratio") or 0)
        except (TypeError, ValueError):
            num = 0.0
        out["canvas_ratio_mode"] = "original" if num <= 0 else _RATIO_MODES.get(num, "original")
    return out


def backend_params(params):
    # 把 UI 参数转换成 process_single 接受的形参(按内核签名过滤 UI 专有键)
    p = {k: v for k, v in params.items() if k in _BACKEND_KEYS}
    mode = params.get("canvas_ratio_mode", "original")
    p["canvas_ratio"] = 0 if mode == "original" else _RATIOS.get(mode, 0)
    p.setdefault("model_map", {})
    return p


# kind: choice(下拉) / pct(0~1 浮点, 显示百分比) / int(像素整数) / bool(复选) /
#       color(取色) / path(文件路径)
# only_if / only_if_not: (参数名, 允许值) —— 不满足时该行隐藏
PARAM_SPECS = [
    # ---------- 不在页签里平铺的行(收进弹窗) ----------
    # 字体路径条目的 tab=None 使其不被 _build_tab 展开; label/hint/kind 仍由本表提供,
    # 弹窗按 dialog=fonts 取用, 撤销步名亦取自该 label
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_path", label=QT_TRANSLATE_NOOP("ParamRow", "常规字体"), kind="path"),
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_bold_path", label=QT_TRANSLATE_NOOP("ParamRow", "粗体字体"), kind="path"),
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_italic_path", label=QT_TRANSLATE_NOOP("ParamRow", "斜体字体"), kind="path"),
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_bold_italic_path", label=QT_TRANSLATE_NOOP("ParamRow", "粗斜体字体"), kind="path"),


    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "最小字号设置"), key="min_font_size", label=QT_TRANSLATE_NOOP("ParamRow", "最小字号"), kind="int",
         args=(0, 200), hint=QT_TRANSLATE_NOOP("ParamRow", "0~200px，默认 20")),

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "画布比例"), key="canvas_ratio_mode", label=QT_TRANSLATE_NOOP("ParamRow", "画布比例"), kind="choice",
         args=[("original", QT_TRANSLATE_NOOP("ParamRow", "跟随原图")), ("1:1", "1:1"), ("3:2", QT_TRANSLATE_NOOP("ParamRow", "3:2（横）")), ("2:3", QT_TRANSLATE_NOOP("ParamRow", "2:3（竖）")),
               ("4:3", QT_TRANSLATE_NOOP("ParamRow", "4:3（横）")), ("3:4", QT_TRANSLATE_NOOP("ParamRow", "3:4（竖）")), ("16:9", QT_TRANSLATE_NOOP("ParamRow", "16:9（横）")),
               ("9:16", QT_TRANSLATE_NOOP("ParamRow", "9:16（竖）")), ("21:9", QT_TRANSLATE_NOOP("ParamRow", "21:9（超宽）")), ("9:21", QT_TRANSLATE_NOOP("ParamRow", "9:21（超长）"))]),

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "水印位置布局"), key="layout_mode", label=QT_TRANSLATE_NOOP("ParamRow", "布局"), kind="choice",
         args=[("unified", QT_TRANSLATE_NOOP("ParamRow", "统一下方")), ("split_top_bottom", QT_TRANSLATE_NOOP("ParamRow", "上下分离")),
               ("split_left_right", QT_TRANSLATE_NOOP("ParamRow", "下方左右分离"))]),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="bg_mode", label=QT_TRANSLATE_NOOP("ParamRow", "背景模式"), kind="choice",
         args=[("blur", QT_TRANSLATE_NOOP("ParamRow", "模糊图片")), ("color", QT_TRANSLATE_NOOP("ParamRow", "纯色"))]),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="bg_color", label=QT_TRANSLATE_NOOP("ParamRow", "背景色"), kind="color",
         only_if=("bg_mode", ("color",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="blur_radius", label=QT_TRANSLATE_NOOP("ParamRow", "模糊半径"), kind="int",
         args=(1, 1000), step=100, hint=QT_TRANSLATE_NOOP("ParamRow", "1~1000，默认 150"), only_if=("bg_mode", ("blur",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="bg_darken_strength", label=QT_TRANSLATE_NOOP("ParamRow", "背景暗化"), kind="pct",
         args=(0.0, 1.0), step=10, hint=QT_TRANSLATE_NOOP("ParamRow", "0~100%，默认 0；100=全黑（仅模糊模式）"),
         only_if=("bg_mode", ("blur",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="corner_radius", label=QT_TRANSLATE_NOOP("ParamRow", "图片圆角"), kind="int",
         args=(0, 1000), step=10, hint=QT_TRANSLATE_NOOP("ParamRow", "0~1000 圆角，默认 0")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="corner_circle", label=QT_TRANSLATE_NOOP("ParamRow", "圆形（整图正圆）"), kind="bool"),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="text_color", label=QT_TRANSLATE_NOOP("ParamRow", "文字颜色"), kind="color"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="scale_logo", label=QT_TRANSLATE_NOOP("ParamRow", "Logo 缩放"), kind="pct",
         args=(0.1, 2.0), step=10, hint=QT_TRANSLATE_NOOP("ParamRow", "10~200%，默认 70%")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="scale_model", label=QT_TRANSLATE_NOOP("ParamRow", "型号缩放"), kind="pct",
         args=(0.1, 2.0), step=10, hint=QT_TRANSLATE_NOOP("ParamRow", "10~200%，默认 50%")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="scale_params", label=QT_TRANSLATE_NOOP("ParamRow", "参数缩放"), kind="pct",
         args=(0.1, 2.0), step=10, hint=QT_TRANSLATE_NOOP("ParamRow", "10~200%，默认 30%")),

# 字体样式为网格布局(行=文字种类, 列=显示/粗体/斜体), 由 _build_font_style_card 构建; 本条目仅用于占位卡片顺序
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印开关"), key=None, special="_build_font_style_card"),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "边距"), key="image_margin", label=QT_TRANSLATE_NOOP("ParamRow", "图片与边框间距"), kind="pct",
         args=(0.005, 0.05), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.5~5%，默认 1.5%；可输 1 位小数，按钮按 0.5% 步进")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_inner", label=QT_TRANSLATE_NOOP("ParamRow", "内侧间距（水印与图片间距）"), kind="pct",
         args=(0.001, 0.20), mirror=("gap_image_top", "gap_image_bottom"), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.1~20%，上下镜像同步（默认 4%，仅上下分离）；可输 1 位小数"),
         only_if=("layout_mode", ("split_top_bottom",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_outer", label=QT_TRANSLATE_NOOP("ParamRow", "外侧间距（水印与画布边距）"), kind="pct",
         args=(0.001, 0.20), mirror=("gap_top", "gap_bottom"), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.1~20%，上下镜像同步（默认 4%，仅上下分离）；可输 1 位小数"),
         only_if=("layout_mode", ("split_top_bottom",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_image_bottom", label=QT_TRANSLATE_NOOP("ParamRow", "水印与图片间距"), kind="pct",
         args=(0.001, 0.20), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.1~20%，默认 4%；可输 1 位小数，按钮按 0.5% 步进"),
         only_if_not=("layout_mode", ("split_top_bottom",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_bottom", label=QT_TRANSLATE_NOOP("ParamRow", "水印与底部间距"), kind="pct",
         args=(0.001, 0.20), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.1~20%，默认 4%；可输 1 位小数，按钮按 0.5% 步进"),
         only_if_not=("layout_mode", ("split_top_bottom",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "边距"), key="line_gap", label=QT_TRANSLATE_NOOP("ParamRow", "水印行间距"), kind="pct",
         args=(0.0, 0.10), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0~10%，默认 2%；可输 1 位小数，按钮按 0.5% 步进")),

# 描边与投影: 四组合成一张可折叠卡片(默认折叠, 卡内按 group 分四组、组间画分割线); 本条目只占卡片顺序(key=None)
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), key=None, special="_build_fx_card"),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片描边"),
         key="enable_image_stroke", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片描边"),
         key="image_stroke_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片描边"),
         key="image_stroke_width", label=QT_TRANSLATE_NOOP("ParamRow", "宽度"), kind="int",
         args=(1, 50), hint=QT_TRANSLATE_NOOP("ParamRow", "1~50px，默认 5")),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="enable_image_shadow", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_angle", label=QT_TRANSLATE_NOOP("ParamRow", "方向"), kind="int",
         args=(0, 360), hint=QT_TRANSLATE_NOOP("ParamRow", "0~360°，默认 135")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_offset", label=QT_TRANSLATE_NOOP("ParamRow", "偏移"), kind="int",
         args=(0, 100), hint=QT_TRANSLATE_NOOP("ParamRow", "0~100px，默认 25")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_alpha", label=QT_TRANSLATE_NOOP("ParamRow", "浓度"), kind="pct",
         args=(0.05, 1.0), hint=QT_TRANSLATE_NOOP("ParamRow", "5~100%，默认 35")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_blur", label=QT_TRANSLATE_NOOP("ParamRow", "晕开范围"), kind="int",
         args=(0, 80), hint=QT_TRANSLATE_NOOP("ParamRow", "0~80px，默认 30")),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字描边"),
         key="enable_text_stroke", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字描边"),
         key="text_stroke_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字描边"),
         key="text_stroke_width", label=QT_TRANSLATE_NOOP("ParamRow", "宽度"), kind="int",
         args=(1, 50), hint=QT_TRANSLATE_NOOP("ParamRow", "1~50px，默认 1")),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="enable_text_shadow", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_angle", label=QT_TRANSLATE_NOOP("ParamRow", "方向"), kind="int",
         args=(0, 360), hint=QT_TRANSLATE_NOOP("ParamRow", "0~360°，默认 135")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_offset", label=QT_TRANSLATE_NOOP("ParamRow", "偏移"), kind="int",
         args=(0, 40), hint=QT_TRANSLATE_NOOP("ParamRow", "0~40px，默认 3")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_alpha", label=QT_TRANSLATE_NOOP("ParamRow", "浓度"), kind="pct",
         args=(0.05, 1.0), hint=QT_TRANSLATE_NOOP("ParamRow", "5~100%，默认 20")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_blur", label=QT_TRANSLATE_NOOP("ParamRow", "晕开范围"), kind="int",
         args=(0, 30), hint=QT_TRANSLATE_NOOP("ParamRow", "0~30px，默认 5")),
]
