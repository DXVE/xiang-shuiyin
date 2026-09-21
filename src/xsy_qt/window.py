# -*- coding: utf-8 -*-
"""镶·水印 · 主窗口。

参数面板: 三个页签「文件与美化 / 字体与画布 / 水印与效果」,
控件与取值范围由参数表驱动 —— 加参数只改表,

本步已对齐: 三页签 · 全部参数控件 · 取色 · 字体路径 · 画布比例 · 布局 · 卡片分组重置 ·
           显隐联动 · 拖放/选图 · 实时预览 · 单张生成
未对齐( 后续): 批量 · 预设 · 型号映射弹窗 · 手动 EXIF · 取色板(预设色/收藏色) ·
              放大镜 · 间距几何标注 · 启动页/状态吐司
"""
import inspect
import json
import logging
import math
import os
import time
from datetime import datetime

from PySide6.QtCore import (QCoreApplication, QPointF, QRectF, QSize, Qt,
                            QT_TRANSLATE_NOOP, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget,
)

from PIL import Image, ImageFilter

from xiangshuiyin import app as core

from . import i18n, single, theme
from .dialog import MessageDialog, csdify, dlg_button, dlg_button_row, go_frameless
from .render import EventWatcher, PreviewWorker, _pil_to_qimage
from .stats import SessionStats

# process_single 实际接受的形参名 —— backend_params() 据此过滤 UI 侧参数
_BACKEND_KEYS = set(inspect.signature(core.process_single).parameters)

_RATIOS = {'1:1': 1, '3:2': 3 / 2, '2:3': 2 / 3, '4:3': 4 / 3, '3:4': 3 / 4,
           '16:9': 16 / 9, '9:16': 9 / 16, '21:9': 21 / 9, '9:21': 9 / 21}
# 那时 _params 里根本没有 canvas_ratio 这个键, 加载时会被 _apply_params 整条跳过(实测: 加载
# 预设后画布比例不生效), 所以加载前先迁回档位。浮点相等在这里成立: 两边都由同一批字面量算出,
# JSON 往返是精确的。
_RATIO_MODES = {v: k for k, v in _RATIOS.items()}

# ---------- 预设 ----------
_PRESET_SLOTS = 10
# 预设里"跟着机器走"的键: 都是绝对路径, 换台机器就是死链。**存槽位时保留**(同机存取要能还原
# 字体), 只在导出成文件时剥掉 —— 分享出去的那份必须是机器无关的。
_PRESET_MACHINE_KEYS = ("logo_dir", "font_path", "font_bold_path",
                        "font_italic_path", "font_bold_italic_path")
# 导出文件里的格式标记: _apply_params 会忽略未知键, 所以它不影响加载, 只用来让将来能识别。
_PRESET_FORMAT = "xiangshuiyin.preset/1"

# 撤销/重做: 只留最近 N 步; 同一控件的连续改动在停手 _UNDO_COALESCE_MS 之后算一步
# (滑块是逐像素发信号的, 不合并的话拖一次要按两百下)
_UNDO_LIMIT = 100
_UNDO_COALESCE_MS = 600


def migrate_preset(p):
    """把老格式/别版格式的预设迁成当前口径(返回副本, 不改传进来的那份)。

    眼下的迁移只有一条: 只有数值 `canvas_ratio`、没有档位 `canvas_ratio_mode` 的记录,
    用 _RATIO_MODES 反查回档位。反查不到(自由比例等)就落到"跟随原图"。
    """
    out = dict(p)
    if "canvas_ratio_mode" not in out and "canvas_ratio" in out:
        try:
            num = float(out.get("canvas_ratio") or 0)
        except (TypeError, ValueError):
            num = 0.0
        out["canvas_ratio_mode"] = "original" if num <= 0 else _RATIO_MODES.get(num, "original")
    return out


def _safe_filename(name):
    """把方案名变成能当文件名的字符串(Windows 上 \\ / : * ? " < > | 都不合法)。"""
    s = "".join("_" if c in '\\/:*?"<>|' else c for c in str(name or "")).strip(" .")
    return s or "preset"

_FONT_FILTER = "字体 (*.ttf *.otf *.ttc);;所有文件 (*)"


def backend_params(params):
    """把 UI 参数转成 `process_single` 能收的形参。

    存在这一步的原因: 后端契约不对称 —— `default_params()` 带 UI 专用的 `canvas_ratio_mode`,
    `getCanvasRatio()` 做同样转换)。直接 `**default_params()` 调 `process_single` 会 TypeError。
    """
    p = {k: v for k, v in params.items() if k in _BACKEND_KEYS}
    mode = params.get("canvas_ratio_mode", "original")
    p["canvas_ratio"] = 0 if mode == "original" else _RATIOS.get(mode, 0)
    p.setdefault("model_map", {})
    return p


TABS = [("files", QT_TRANSLATE_NOOP("MainWindow", "文件与美化")),
        ("font", QT_TRANSLATE_NOOP("MainWindow", "字体与画布")),
        ("effects", QT_TRANSLATE_NOOP("MainWindow", "水印与效果"))]

# ===== 参数控件规格表 =====
# kind: choice(下拉) / pct(0~1 浮点, 显示百分比) / int(像素整数) / bool(复选) /
#       color(取色) / path(文件路径)
# 取值范围与提示文案
PARAM_SPECS = [
    # ---------- 字体与画布 ----------
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_path", label=QT_TRANSLATE_NOOP("ParamRow", "常规字体"), kind="path"),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_bold_path", label=QT_TRANSLATE_NOOP("ParamRow", "粗体字体"), kind="path"),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_italic_path", label=QT_TRANSLATE_NOOP("ParamRow", "斜体字体"), kind="path"),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_bold_italic_path", label=QT_TRANSLATE_NOOP("ParamRow", "粗斜体字体"), kind="path"),

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "最小字号设置"), key="min_font_size", label=QT_TRANSLATE_NOOP("ParamRow", "最小字号"), kind="int",
         args=(0, 200), hint=QT_TRANSLATE_NOOP("ParamRow", "0~200px，默认 20")),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "最小字号设置"), key="low_res_enhance", label=QT_TRANSLATE_NOOP("ParamRow", "低分辨率增强"),
         kind="bool", hint=QT_TRANSLATE_NOOP("ParamRow", "图片高度<1500px 时 2 倍渲染")),

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "画布比例"), key="canvas_ratio_mode", label=QT_TRANSLATE_NOOP("ParamRow", "画布比例"), kind="choice",
         args=[("original", QT_TRANSLATE_NOOP("ParamRow", "跟随原图")), ("1:1", "1:1"), ("3:2", QT_TRANSLATE_NOOP("ParamRow", "3:2（横）")), ("2:3", QT_TRANSLATE_NOOP("ParamRow", "2:3（竖）")),
               ("4:3", QT_TRANSLATE_NOOP("ParamRow", "4:3（横）")), ("3:4", QT_TRANSLATE_NOOP("ParamRow", "3:4（竖）")), ("16:9", QT_TRANSLATE_NOOP("ParamRow", "16:9（横）")),
               ("9:16", QT_TRANSLATE_NOOP("ParamRow", "9:16（竖）")), ("21:9", QT_TRANSLATE_NOOP("ParamRow", "21:9（超宽）")), ("9:21", QT_TRANSLATE_NOOP("ParamRow", "9:21（超长）"))]),

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "水印位置布局"), key="layout_mode", label=QT_TRANSLATE_NOOP("ParamRow", "布局"), kind="choice",
         args=[("unified", QT_TRANSLATE_NOOP("ParamRow", "统一下方")), ("split_top_bottom", QT_TRANSLATE_NOOP("ParamRow", "上下分离")),
               ("split_left_right", QT_TRANSLATE_NOOP("ParamRow", "下方左右分离"))]),

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="image_margin", label=QT_TRANSLATE_NOOP("ParamRow", "图片与边框间距"), kind="pct",
         args=(0.005, 0.05), hint=QT_TRANSLATE_NOOP("ParamRow", "1~5%，默认 1.5%（框里按整数取整显示）")),
    # 上下分离是「镜像+居中」模式: 后端用等高中带保证图片垂直居中, 外侧实际渲染可大于滑块值。
    # 所以这一档只给两个镜像滑块 —— 露 4 个独立值会让人以为能调成不对称, 实际不会。
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_inner", label=QT_TRANSLATE_NOOP("ParamRow", "内侧间距（水印与图片间距）"), kind="pct",
         args=(0.001, 0.20), mirror=("gap_image_top", "gap_image_bottom"),
         hint=QT_TRANSLATE_NOOP("ParamRow", "1~20%，上下镜像同步（默认 4%，仅上下分离）"),
         only_if=("layout_mode", ("split_top_bottom",))),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_outer", label=QT_TRANSLATE_NOOP("ParamRow", "外侧间距（水印与画布边距）"), kind="pct",
         args=(0.001, 0.20), mirror=("gap_top", "gap_bottom"),
         hint=QT_TRANSLATE_NOOP("ParamRow", "1~20%，上下镜像同步（默认 4%，仅上下分离）"),
         only_if=("layout_mode", ("split_top_bottom",))),
    # 上下分离模式下的镜像行(内/外侧间距)写入, 统一下方与左右分离都不使用上方的两个间距,
    # 参数本身仍在(见 default_params), 照常传给 process_single。
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_image_bottom", label=QT_TRANSLATE_NOOP("ParamRow", "水印与图片间距"), kind="pct",
         args=(0.001, 0.20), hint=QT_TRANSLATE_NOOP("ParamRow", "1~20%，默认 4%"),
         only_if_not=("layout_mode", ("split_top_bottom",))),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_bottom", label=QT_TRANSLATE_NOOP("ParamRow", "水印与底部间距"), kind="pct",
         args=(0.001, 0.20), hint=QT_TRANSLATE_NOOP("ParamRow", "1~20%，默认 4%"),
         only_if_not=("layout_mode", ("split_top_bottom",))),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="line_gap", label=QT_TRANSLATE_NOOP("ParamRow", "水印行间距"), kind="pct",
         args=(0.0, 0.10), hint=QT_TRANSLATE_NOOP("ParamRow", "0~10%，默认 2%")),

    # ---------- 水印与效果 ----------
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片"), key="bg_mode", label=QT_TRANSLATE_NOOP("ParamRow", "背景模式"), kind="choice",
         args=[("blur", QT_TRANSLATE_NOOP("ParamRow", "模糊图片")), ("color", QT_TRANSLATE_NOOP("ParamRow", "纯色"))]),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片"), key="bg_color", label=QT_TRANSLATE_NOOP("ParamRow", "背景色"), kind="color"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片"), key="blur_radius", label=QT_TRANSLATE_NOOP("ParamRow", "模糊半径"), kind="int",
         args=(1, 1000), hint=QT_TRANSLATE_NOOP("ParamRow", "1~1000，默认 150"), only_if=("bg_mode", ("blur",))),
    # 纯色模式不显示: 核心对纯色背景有意不做暗化(见 process_single 里的注释), 露着控件
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片"), key="bg_darken_strength", label=QT_TRANSLATE_NOOP("ParamRow", "背景暗化"), kind="pct",
         args=(0.0, 1.0), hint=QT_TRANSLATE_NOOP("ParamRow", "0~100%，默认 0；100=全黑（仅模糊模式）"),
         only_if=("bg_mode", ("blur",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片"), key="corner_radius", label=QT_TRANSLATE_NOOP("ParamRow", "图片圆角"), kind="int",
         args=(0, 1000), hint=QT_TRANSLATE_NOOP("ParamRow", "0~1000 圆角，默认 0")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片"), key="corner_circle", label=QT_TRANSLATE_NOOP("ParamRow", "圆形（整图正圆）"), kind="bool"),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片描边"), key="enable_image_stroke", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片描边"), key="image_stroke_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color",
         only_if=("enable_image_stroke", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片描边"), key="image_stroke_width", label=QT_TRANSLATE_NOOP("ParamRow", "宽度"), kind="int",
         args=(1, 50), hint=QT_TRANSLATE_NOOP("ParamRow", "1~50px，默认 5"), only_if=("enable_image_stroke", (True,))),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片投影"), key="enable_image_shadow", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片投影"), key="image_shadow_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color",
         only_if=("enable_image_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片投影"), key="image_shadow_angle", label=QT_TRANSLATE_NOOP("ParamRow", "方向"), kind="int",
         args=(0, 360), hint=QT_TRANSLATE_NOOP("ParamRow", "0~360°，默认 135"), only_if=("enable_image_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片投影"), key="image_shadow_offset", label=QT_TRANSLATE_NOOP("ParamRow", "偏移"), kind="int",
         args=(0, 100), hint=QT_TRANSLATE_NOOP("ParamRow", "0~100px，默认 25"), only_if=("enable_image_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片投影"), key="image_shadow_alpha", label=QT_TRANSLATE_NOOP("ParamRow", "浓度"), kind="pct",
         args=(0.05, 1.0), hint=QT_TRANSLATE_NOOP("ParamRow", "5~100%，默认 35"), only_if=("enable_image_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "图片投影"), key="image_shadow_blur", label=QT_TRANSLATE_NOOP("ParamRow", "晕开范围"), kind="int",
         args=(0, 80), hint=QT_TRANSLATE_NOOP("ParamRow", "0~80px，默认 30"), only_if=("enable_image_shadow", (True,))),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="text_color", label=QT_TRANSLATE_NOOP("ParamRow", "文字颜色"), kind="color"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="scale_logo", label=QT_TRANSLATE_NOOP("ParamRow", "Logo 缩放"), kind="pct",
         args=(0.1, 2.0), hint=QT_TRANSLATE_NOOP("ParamRow", "10~200%，默认 70%")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="scale_model", label=QT_TRANSLATE_NOOP("ParamRow", "型号缩放"), kind="pct",
         args=(0.1, 2.0), hint=QT_TRANSLATE_NOOP("ParamRow", "10~200%，默认 50%")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="scale_params", label=QT_TRANSLATE_NOOP("ParamRow", "参数缩放"), kind="pct",
         args=(0.1, 2.0), hint=QT_TRANSLATE_NOOP("ParamRow", "10~200%，默认 30%")),

    # 字体样式用网格布局(行=文字种类, 列=显示/粗体/斜体), 由 _build_font_style_card 构建;
    # 这条只用于占住卡片顺序
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "字体样式"), key=None, special="_build_font_style_card"),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "文字描边"), key="enable_text_stroke", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "文字描边"), key="text_stroke_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color",
         only_if=("enable_text_stroke", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "文字描边"), key="text_stroke_width", label=QT_TRANSLATE_NOOP("ParamRow", "宽度"), kind="int",
         args=(1, 50), hint=QT_TRANSLATE_NOOP("ParamRow", "1~50px，默认 1"), only_if=("enable_text_stroke", (True,))),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "文字投影"), key="enable_text_shadow", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "文字投影"), key="text_shadow_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color",
         only_if=("enable_text_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "文字投影"), key="text_shadow_angle", label=QT_TRANSLATE_NOOP("ParamRow", "方向"), kind="int",
         args=(0, 360), hint=QT_TRANSLATE_NOOP("ParamRow", "0~360°，默认 135"), only_if=("enable_text_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "文字投影"), key="text_shadow_offset", label=QT_TRANSLATE_NOOP("ParamRow", "偏移"), kind="int",
         args=(0, 40), hint=QT_TRANSLATE_NOOP("ParamRow", "0~40px，默认 3"), only_if=("enable_text_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "文字投影"), key="text_shadow_alpha", label=QT_TRANSLATE_NOOP("ParamRow", "浓度"), kind="pct",
         args=(0.05, 1.0), hint=QT_TRANSLATE_NOOP("ParamRow", "5~100%，默认 20"), only_if=("enable_text_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "文字投影"), key="text_shadow_blur", label=QT_TRANSLATE_NOOP("ParamRow", "晕开范围"), kind="int",
         args=(0, 30), hint=QT_TRANSLATE_NOOP("ParamRow", "0~30px，默认 5"), only_if=("enable_text_shadow", (True,))),
]

_LABEL_W = 128
# 百分比类参数(pct)内部存的是 0~1 的小数, 按"整数百分比"显示: 显示值 = 内部值 × 100。
# 数值框一律按整数显示、不带单位 —— 以前 pct 行显示 "4.0 %"、int 行显示 "150", 两种样子并存。
_PCT_SCALE = 100


def _display_range(kind, lo, hi):
    """参数区间 -> 数值框/滑块的整数显示区间。

    int 类原样; pct 类按百分比换算成整数(0.04 -> 4)。下界**向上取整**: gap 这类下界是
    0.001(=0.1%), 直接取整会得到 0, 而 0 间距是无意义的(image_margin 还会被核心夹回
    0.5%), 取 1 才对应"最低 1%"。
    """
    if kind != "pct":
        return int(lo), int(hi)
    return math.ceil(lo * _PCT_SCALE - 1e-9), int(round(hi * _PCT_SCALE))

# 持久化时剥掉的键, 与内核持久化口径一致:
# 输入/输出路径一律不落盘(model_map 存在 logo_dir 下的 model_map.json, 也不进 settings)
_NOT_PERSISTED = {"logo_dir", "input_path", "output_path", "batch_dir", "output_dir", "model_map"}
# 放大镜尺寸/倍数/圆角, (240×160, 3 倍, 12px 圆角)
MAG_W, MAG_H, MAG_ZOOM, MAG_RADIUS = 240, 160, 3, 12
MAG_MARGIN = 12      # 离预览框右上角的距离; 留出投影的余量
# 竖向滚动条占位宽度, 必须与 theme.QSS 里 `QScrollBar:vertical { width }` 一致(现在 14px):
# _segbar_row 按这个值给页签行留右侧占位, 两边不同步的话页签卡片会比参数卡片宽出那条差
_SB_W = 14

# 预览显示缩放: 用 PIL 的 LANCZOS 而不是 QPixmap.scaled(Smooth) —— 见 PreviewBox._scale_for_display
# 后面的 UnsharpMask 是"轻锐化"(半径1/阈值3), 数值按"过冲(白边)幅度"定, 实测(856px 宽的
# 上屏设备像素图上, 与不锐化的偏差):
#     percent  20 → 99.9 分位过冲 12/255, 最大 25      30 → 18 / 37     40 → 24 / 46
#              60 → 34 / 68                            80 → 42 / 83    100 → 50 / 93
# 60 是当初为补偿"上屏那步被 Qt 放大 1.25 倍"才加上去的; 那个损失已经修掉(见
# _scale_for_display), 锐化现在只影响观感 —— 想"预览=成品"就置 0, 觉得字还有点软就给 30,
# 看到笔画边上有白/黑描边(过冲)就往下降。
_PREVIEW_SHARPEN = 60

# ---------- 主窗口初始尺寸与左右分栏 ----------
# 初始尺寸按**屏幕可用区**(已扣任务栏)的百分比算, 不写死像素: 1080p@125% 的逻辑可用区只有
# 1536x816, 写死 860 高会把底栏与状态栏顶到屏幕外(实测就是这个问题)。留边 _WIN_EDGE px
# 保证四边不贴边、还能拖动。数值可直接调。
_WIN_W_RATIO, _WIN_H_RATIO, _WIN_EDGE = 0.8, 0.9, 100
# 左列占比(左 4 : 右 6)。窗口缩放时按此比例重排; 用户手动拖过分隔条后不再自动分配。
_SPLIT_LEFT = 0.4

# 慢调用阈值(ms): 取 800, 超过就把请求日志降级成 WARNING 并标"偏慢"。
# 渲染 + 排队一起算 —— 用户感受到的卡顿是这两者之和(排队来自"在跑的那一轮不可中断")。
_SLOW_RENDER_MS = 800

FONT_STYLE_COLS = ("显示", "粗体", "斜体")
FONT_STYLE_GROUPS = [
    ("Logo 文字", ("show_brand", "bold_logo", "italic_logo")),
    ("型号文字", ("show_model", "bold_model", "italic_model")),
    ("参数文字", ("show_params", "bold_params", "italic_params")),
]


# ---- 滚轮行为 ----
# 参数面板整个在滚动区里, 滑块又几乎占满一行宽度 —— 滚轮滑过时很容易"顺手"把数值改了,
# 而改完没有任何提示, 属于事后才发现参数不对的那类坑。所以滑块与下拉框不吃滚轮:
# 事件不处理, 由 Qt 上抛给父级(滚动区), 滚轮就变成滚动页面。
# **数值框(QSpinBox)保留滚轮调节** —— 它窄、不易误碰, 滚轮微调反而好用。
# 注意下拉框只在收起状态不吃滚轮; 展开后那个列表是另一个控件, 照常能用滚轮翻选项。
class NoWheelSlider(QSlider):
    """滚轮不调数值的滑块(滚轮让给父级滚动区)。"""

    def wheelEvent(self, e):        # noqa: N802 - Qt 命名
        e.ignore()


class NoWheelComboBox(QComboBox):
    """滚轮不换选项的下拉框(只限收起状态; 展开的列表照常可滚)。"""

    def wheelEvent(self, e):        # noqa: N802 - Qt 命名
        e.ignore()


class ParamRow(QWidget):
    """一行参数控件。按 kind 组装, 对外统一的 changed(key, value) / set_value() / value()。"""

    changed = Signal(str, object)
    hovered = Signal(list)
    colorClicked = Signal()      # 点「选择颜色」: 弹窗由窗口提供(要 state 拿用户保存的颜色)     # 悬停时给出需要在预览图上高亮的间距段键(空列表=取消)

    def __init__(self, spec, value, parent=None):
        super().__init__(parent)
        self.key = spec["key"]
        self.kind = spec["kind"]
        self._spec = spec
        self._value = value
        self._updating = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(theme.GAP_CTRL)

        # 表里的 label/hint/选项文案都保持中文原文(中文就是源语言), 显示时才 tr() ——
        # 所以参数面板那 122 处只需要在这一处与下面几处包一下, 不必逐个改表
        if self.kind == "bool":
            self.chk = QCheckBox(self.tr(spec["label"]))
            self.chk.setCursor(Qt.PointingHandCursor)
            self.chk.toggled.connect(self._from_check)
            lay.addWidget(self.chk)
            if spec["label"].strip():
                # 网格单元格给的是空格标签: 标签为空字符串时 Qt 连未勾选的方框都不画
                # (不加任何样式表也一样, 实测非白像素为 0), 所以必须留一个空格;
                # 这里按 strip() 判断, 空格标签不加弹性空白, 好让单元格按复选框大小居中
                lay.addStretch(1)
        else:
            lab = QLabel(self.tr(spec["label"]))
            lab.setObjectName("fieldLabel")
            lab.setFixedWidth(_LABEL_W)
            lab.setWordWrap(True)
            lay.addWidget(lab)
            hint = spec.get("hint")
            if hint:
                lab.setToolTip(self.tr(hint))

            if self.kind == "choice":
                self.combo = NoWheelComboBox()
                for val, text in spec["args"]:
                    self.combo.addItem(self.tr(text), val)
                self.combo.currentIndexChanged.connect(self._from_choice)
                lay.addWidget(self.combo, 1)

            elif self.kind == "color":
                # 色块**本身就是按钮**: 直接点色块开取色弹窗。
                # 以前色块是纯 QLabel(只显示色号)、右边另挂一个「选择颜色」按钮, 结果
                # 看着像输入框却点不动, 想改色只能去够右边那个按钮 —— 现在整个控件区
                # 都是可点的色块, 右边缘仍与数值行的步进器组对齐(宽度由布局拉伸给出)。
                self.swatch = QPushButton()
                self.swatch.setCursor(Qt.PointingHandCursor)
                self.swatch.setToolTip(self.tr("点击选择颜色"))
                self.swatch.clicked.connect(self.colorClicked.emit)
                lay.addWidget(self.swatch, 1)

            elif self.kind == "path":
                self.edit = QLineEdit()
                self.edit.setReadOnly(True)
                self.edit.setToolTip(str(value or ""))
                lay.addWidget(self.edit, 1)
                b = QPushButton(self.tr("选择字体"))
                self._path_btn = b          # 宽度在 _unify_spins() 里与数值框对齐
                b.clicked.connect(self._pick_font)
                lay.addWidget(b)

            else:                                   # pct / int —— 两者都按整数显示
                # 不用 QDoubleSpinBox: 全部整数, 也就没有小数位与 " %" 后缀的差别
                lo, hi = spec["args"]
                self._disp_lo, self._disp_hi = _display_range(self.kind, lo, hi)
                self.slider = NoWheelSlider(Qt.Horizontal)
                self.spin = QSpinBox()
                # 滑块与数值框用**同一套显示单位**(整数), 所以两边可以直接对拷, 不再各算一套系数
                self.slider.setRange(self._disp_lo, self._disp_hi)
                self.spin.setRange(self._disp_lo, self._disp_hi)
                self.slider.setSingleStep(1)
                self.spin.setSingleStep(1)
                # − / + 步进按钮(): 关掉 QSpinBox 自带箭头 ——
                # QSS 下 Qt 既不画原生箭头也不支持 CSS 的 border 三角写法, 只能上素材
                self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
                # 宽度与对齐不在这里定: 统一由 MainWindow._unify_spins() 按全表最宽取值算
                if hint:
                    self.spin.setToolTip(hint)
                self.slider.valueChanged.connect(self._from_slider)
                self.spin.valueChanged.connect(self._from_spin)
                minus = QPushButton("−")
                plus = QPushButton("+")
                for b, slot in ((minus, self.spin.stepDown), (plus, self.spin.stepUp)):
                    b.setObjectName("step")
                    b.setCursor(Qt.PointingHandCursor)
                    b.clicked.connect(slot)
                step = QWidget()
                self._step_box = step
                self._step_w = max(minus.sizeHint().width(), plus.sizeHint().width())
                sl = QHBoxLayout(step)
                sl.setContentsMargins(0, 0, 0, 0)
                sl.setSpacing(3)
                sl.addWidget(minus)
                sl.addWidget(self.spin)
                sl.addWidget(plus)
                lay.addWidget(self.slider, 1)
                lay.addWidget(step)

        self.set_value(value)

    # ---- 取值/设值 ----
    def set_value(self, v):
        self._updating = True
        self._value = v
        if self.kind == "bool":
            self.chk.setChecked(bool(v))
        elif self.kind == "choice":
            i = self.combo.findData(v)
            if i >= 0:
                self.combo.setCurrentIndex(i)
        elif self.kind == "color":
            self._paint_swatch(v)
        elif self.kind == "path":
            self.edit.setText(str(v or ""))
            self.edit.setToolTip(str(v or ""))
        else:
            lo, hi = self._spec["args"]
            v = max(lo, min(hi, float(v)))
            disp = max(self._disp_lo, min(self._disp_hi, self._value_to_disp(v)))
            self.slider.setValue(disp)
            self.spin.setValue(disp)
        self._updating = False

    def value(self):
        return self._value

    # ---- 显示值(整数) <-> 内部值 ----
    # pct 类内部是 0~1 的小数, 差一个 ×100; int 类两者相同。
    def _disp_to_value(self, disp):
        return disp / _PCT_SCALE if self.kind == "pct" else int(disp)

    def _value_to_disp(self, value):
        return int(round(float(value) * _PCT_SCALE)) if self.kind == "pct" else int(value)

    def _paint_swatch(self, color):
        """色块外观: 底色就是颜色本身, 色号用对比色写在上头(浅底黑字/深底白字)。

        悬停与按下必须在**本控件自己的样式表里**把背景一起写掉: 只写 border 的话会落到
        主题那条 `QPushButton:hover{background:#f1f5f9}`, 鼠标一上去色块变灰 —— 看着像
        控件坏了, 也正是"点不动"观感的来源之一。
        """
        col = QColor(color if isinstance(color, str) else "#ffffff")
        fg = "#000" if col.lightness() > 128 else "#fff"
        self.swatch.setText(str(color))
        self.swatch.setStyleSheet(
            "QPushButton { background:%s; border:1px solid %s; border-radius:%dpx; color:%s; }"
            "QPushButton:hover { background:%s; border:2px solid %s; }"
            "QPushButton:pressed { background:%s; border:2px solid %s; }"
            % (col.name(), theme.LINE, theme.RADIUS_CTRL, fg,
               col.name(), theme.ACCENT, col.name(), theme.ACCENT))

    # ---- 内部 ----
    # _updating 守卫: set_value() 会反过来让控件发信号, 不挡住就会把"回填"误当成用户改动
    def _from_check(self, v):
        if self._updating:
            return
        self.changed.emit(self.key, bool(v))

    def _from_choice(self, _i):
        if self._updating:
            return
        self.changed.emit(self.key, self.combo.currentData())

    def _from_slider(self, v):
        if self._updating:
            return
        self._updating = True
        self.spin.setValue(v)               # 两边同一套显示单位, 直接同步
        self._updating = False
        self.changed.emit(self.key, self._disp_to_value(v))

    def _from_spin(self, v):
        if self._updating:
            return
        self._updating = True
        self.slider.setValue(v)
        self._updating = False
        self.changed.emit(self.key, self._disp_to_value(v))

    def enterEvent(self, _e):
        self.hovered.emit(list(self._spec.get("mirror") or [self.key]))

    def leaveEvent(self, _e):
        self.hovered.emit([])

    def _pick_font(self):
        path = _open_file_dialog(self, self.tr("选择字体文件"),
                                 os.path.dirname(self._value or ""), _FONT_FILTER)
        if path:
            self.set_value(path)
            self.changed.emit(self.key, path)


class Card(QFrame):

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.keys = []
        theme.apply_shadow(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD - 2, theme.PAD_CARD, theme.PAD_CARD)
        v.setSpacing(theme.GAP_ROW)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        t = QLabel(self.tr(title))            # 卡片名来自 PARAM_SPECS 或手写字面量
        t.setObjectName("cardTitle")
        head.addWidget(t)
        head.addStretch(1)
        self.reset_btn = QPushButton(self.tr("↺ 重置本组"))
        self.reset_btn.setObjectName("mini")
        self.reset_btn.setToolTip(self.tr("把本卡片的参数恢复为出厂默认"))
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.reset_btn)
        v.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(theme.GAP_ROW - 2)
        v.addLayout(self.body)


def _qimage_to_pil(img: QImage):
    """QImage -> PIL(显示缩放走 PIL, 见 PreviewBox._scale_for_display)"""
    q = img.convertToFormat(QImage.Format.Format_RGB888)
    return Image.frombytes("RGB", (q.width(), q.height()), q.constBits().tobytes(),
                           "raw", "RGB", q.bytesPerLine())


class PreviewBox(QWidget):
    """预览显示区: 等比缩放居中。自绘(而非 QLabel)是为后续放大镜/间距几何标注留绘制入口
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.stats = None        # 由 MainWindow 注入的 SessionStats(记录上屏缩放耗时); None 时只记日志
        self._pix = None
        self._pil = None
        self._scaled = None
        self._scaled_for = None
        self._hint = self.tr("拖入图片，或点「打开图片」")
        self._geom = None      # 间距段几何(画布像素坐标), 由 make_preview 的 resp.geom 回传
        self._hl = set()       # 当前要高亮的间距段键
        self._mag_pos = None   # 放大镜: 光标在控件内的位置(None=不显示)
        self.setMouseTracking(True)

    def set_image(self, image: QImage):
        self._pix = QPixmap.fromImage(image)
        self._pil = _qimage_to_pil(image)      # 显示缩放用 PIL(LANCZOS), 见 _scale_for_display
        self._scaled = None
        self._scaled_for = None
        self.update()


    def _scale_for_display(self, target):
        """把固有源缩到 target(**逻辑**上屏尺寸), 带缓存。两件事缺一不可:

        1) 用 PIL 的 LANCZOS, 不用 QPixmap.scaled(SmoothTransformation): 后者降采样是面积
           平均(盒式滤波), 平滑但不"锐"; LANCZOS 带负瓣, 缩小的同时有一点锐化。两者数值差
           只有 ~1.6/255, 但差在高对比边缘(水印笔画、圆角), 肉眼就是"清楚"与"发虚"。
        2) **按设备像素缩**(target × devicePixelRatioF), 再 setDevicePixelRatio。本机是
           125% 缩放(dpr=1.25): 只按逻辑尺寸缩的话, 上屏那一步 Qt 还要把整张位图放大 1.25
           倍, 而它用的是**最近邻**(paintEvent 原本没开 SmoothPixmapTransform) —— 斜边、圆角、
           文字笔画当场起台阶。实测(同一块内容, 与"画布直接缩到设备像素"的理想值比, 满量程
           255; 脚本见 shots/preview_blit_3way.png):
               旧实现(最近邻 1.25x)     与理想差 1.160, 边缘硬而碎 —— 就是看到的"锯齿"
               旧实现 + 只补平滑提示     与理想差 1.262, 边缘强度 6.69 → 4.35 —— 变成"发虚"
               按设备像素 + dpr         与理想差 0.000(1:1 落屏, 一次重采样都不做)
           放大镜没有这一步(它从固有源按像素直接画, 只重采样一次), 所以"放大镜看着清楚、
           预览图发虚/起锯齿"是同一个原因, 不是渲染画布分辨率不够。

        之后可选一次轻锐化, 把降采样糊掉的笔锋拉回来一点。
        只在控件尺寸/画面/dpr 变化时算一次(缓存在 _scaled), 一张 4090x3526 的画布约 10~40ms。
        """
        dpr = self.devicePixelRatioF()
        dev = QSize(max(1, round(target.width() * dpr)), max(1, round(target.height() * dpr)))
        if self._pil is None:
            pm = self._pix.scaled(dev, Qt.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            return pm
        t0 = time.perf_counter()
        im = self._pil.resize((dev.width(), dev.height()), Image.LANCZOS)
        if _PREVIEW_SHARPEN > 0:
            im = im.filter(ImageFilter.UnsharpMask(radius=1.0, percent=_PREVIEW_SHARPEN,
                                                   threshold=3))
        pm = QPixmap.fromImage(_pil_to_qimage(im))
        pm.setDevicePixelRatio(dpr)      # 位图是"1.25 倍的像素", 按逻辑坐标画才是 1:1
        # 上屏缩放跑在 GUI 线程上(实测慢的时候几十毫秒), 值得单独记一条。这里只在
        # "新画面/新尺寸"时执行(结果缓存在 _scaled), 所以每次预览最多一行 —— 绝不进逐帧重绘路径。
        scale_ms = (time.perf_counter() - t0) * 1000
        src = self._pil.size
        logging.info(f"[上屏] 缩放 {src[0]}x{src[1]}→{dev.width()}x{dev.height()} "
                     f"dpr={dpr:g} 耗时 {scale_ms:.0f}ms (PIL LANCZOS+锐化{_PREVIEW_SHARPEN})")
        if self.stats is not None:
            self.stats.add_display_scale(scale_ms)
        return pm

    def set_geo(self, geom):
        """间距段几何(画布像素): {canvas:(w,h), img:(x0,y0,x1,y1), gaps:{键:[(y0,y1),…]}}"""
        self._geom = geom or None
        self.update()

    def set_highlight(self, keys):
        keys = set(keys or ())
        if keys != self._hl:
            self._hl = keys
            self.update()

    def set_hint(self, text):
        self._hint = text
        self.update()

    # ---- 放大镜 ----
    def mouseMoveEvent(self, e):
        self._mag_pos = e.position()
        if self._pix is not None:
            self.update()

    def leaveEvent(self, _e):
        if self._mag_pos is not None:
            self._mag_pos = None
            self.update()

    def clear(self):
        self._pix = None
        self._pil = None
        self._scaled = None
        self._scaled_for = None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#333333"))     # 预览底色
        if self._pix is None:
            p.setPen(QColor("#9aa0a6"))
            p.drawText(self.rect(), Qt.AlignCenter, self._hint)
            return
        # 预览位图按设备像素缩好再画(见 _scale_for_display), 这里基本都是 1:1。
        # SmoothPixmapTransform 是兜底: 缩放率非整数时 round() 会差半个像素, 开着它最多是
        # 一次极轻的重采样, 关着(Qt 默认)就变成最近邻抽样 —— 斜边/圆角立刻起锯齿。
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = self._pix.size().scaled(self.size(), Qt.KeepAspectRatio)
        # dpr 进缓存键: 拖到另一块缩放率不同的屏上要重算(否则会沿用旧 dpr 的位图)
        key = (target, self.devicePixelRatioF())
        if self._scaled is None or self._scaled_for != key:
            self._scaled = self._scale_for_display(target)
            self._scaled_for = key
        ox = (self.width() - target.width()) // 2
        oy = (self.height() - target.height()) // 2
        p.drawPixmap(ox, oy, self._scaled)
        box = QRectF(ox, oy, target.width(), target.height())
        self._paint_geom(p, box)
        self._paint_magnifier(p, box)

    def _paint_magnifier(self, p, target):
        """放大镜: 固定预览框右上角, 内容随光标平移(光标所指的源点居中)。

        按**像素宽度**从固有源重采样(不是把已缩小的位图再放大), 所以边缘不虚 ——
        光标不在图像区域内(深色留白)时不显示: 那里没有可放大的内容。
        """
        pos = self._mag_pos
        if pos is None or self._pix is None or not target.contains(pos):
            return
        src = self._pix
        sw, sh = src.width(), src.height()
        dw, dh = target.width(), target.height()
        if dw <= 0 or dh <= 0 or sw <= 0 or sh <= 0:
            return
        src_w = min(float(sw), MAG_W * sw / (dw * MAG_ZOOM))
        src_h = min(float(sh), MAG_H * sh / (dh * MAG_ZOOM))
        cx = (pos.x() - target.left()) / dw * sw
        cy = (pos.y() - target.top()) / dh * sh
        # 贴边时夹住源矩形(而不是让 drawPixmap 拿到越界的源区)
        x0 = max(0.0, min(cx - src_w / 2, sw - src_w))
        y0 = max(0.0, min(cy - src_h / 2, sh - src_h))
        box = QRectF(self.width() - MAG_W - MAG_MARGIN, MAG_MARGIN, MAG_W, MAG_H)
        p.save()
        # QPainter 的 Antialiasing 与 SmoothPixmapTransform **默认都是关的**:
        #   - 不开 SmoothPixmapTransform → drawPixmap 走最近邻, 缩放后成马赛克
        #   - 不开 Antialiasing → 圆角边框出锯齿
        # 主预览那条路径不用管: pixmap.scaled(..., Smooth) 自带平滑, 且它是矩形不用抗锯齿。
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # 柔和投影。QPainter 没有模糊, 用几层递增加粗的半透明描边近似
        p.setBrush(Qt.NoBrush)
        for w, alpha in ((12, 8), (7, 14), (3, 22)):
            p.setPen(QPen(QColor(0, 0, 0, alpha), w))
            r = box.adjusted(-w / 2, -w / 2 + 3, w / 2, w / 2 + 3)
            p.drawRoundedRect(r, MAG_RADIUS + w / 2, MAG_RADIUS + w / 2)

        # 圆角裁剪必须走 path: 用 setClipRect 会把图像内容填进方角, 再叠一圈圆角白边
        # 就成了"方角 + 白线溢出"(实测就长这样)
        clip = QPainterPath()
        clip.addRoundedRect(box, MAG_RADIUS, MAG_RADIUS)
        p.save()
        p.setClipPath(clip)
        p.fillRect(box, QColor("#222222"))
        p.drawPixmap(box, src, QRectF(x0, y0, src_w, src_h))
        p.restore()

        # 白色边框: 内缩半个笔宽, 否则一半压在裁剪外被切掉
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#ffffff"), 2))
        inner = box.adjusted(1, 1, -1, -1)
        p.drawRoundedRect(inner, MAG_RADIUS - 1, MAG_RADIUS - 1)
        p.restore()

    def _paint_geom(self, p, target):
        """在预览图上叠加「当前悬停的那个间距」的可视化。

        : 间距段画横向色带(上/下各一条 2px 描边),
        image_margin 画图片四周虚线框。未悬停时不画任何东西。
        """
        g = self._geom
        if not g or not self._hl:
            return
        cw, ch = g.get("canvas") or (0, 0)
        if not cw or not ch:
            return
        sx = target.width() / cw
        sy = target.height() / ch
        fill = QColor(255, 184, 0, 77)        # rgba(255,184,0,.30)
        edge = QColor(255, 160, 0, 140)       # rgba(255,160,0,.55)
        for key, segs in (g.get("gaps") or {}).items():
            if key not in self._hl:
                continue
            for y0, y1 in segs:
                r = QRectF(target.left(), target.top() + y0 * sy,
                           target.width(), max(2.0, (y1 - y0) * sy))
                p.fillRect(r, fill)
                p.setPen(QPen(edge, 2))
                p.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.top()))
                p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        ig = g.get("img")
        if ig and "image_margin" in self._hl:
            x0, y0, x1, y1 = ig
            r = QRectF(target.left() + x0 * sx, target.top() + y0 * sy,
                       (x1 - x0) * sx, (y1 - y0) * sy)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 160, 0, 204), 2, Qt.DashLine))
            p.drawRect(r)


PRESET_COLORS = [
    '#FFFFFF', '#F0F0F0', '#D6D6D6', '#B0B0B0', '#808080', '#404040', '#000000',   # 灰阶
    '#6F9C7D', '#7C98A6', '#B0676B', '#B8895E', '#C9A227',                          # 莫兰迪
    '#F7E8D0', '#A8D0E6', '#F5D6D6', '#D5E8D4', '#DCE4EE', '#FFF3CD',               # 浅色清新
]
_COLS_PRESET = 6   # 预设色每行几个(18 色 = 3 行)
_COLS_USER = _COLS_PRESET   # 与预设色同列数 —— 两组色板列宽一致, 上下对齐成同一条网格
MAX_USER_COLORS = 10


def _prep_dialog(dlg):
    """对话框显示前把尺寸和位置定死。

    否则 Qt 先按 sizeHint 把窗口摆在一处、等布局定稿后再居中一次 —— 肉眼就是
    "先在别处闪一下, 再跳到中间"。实测(父窗 1240x900):
        不处理          显示瞬间 (760,470) → 稳定后 (750,343)   跳 127px
        adjustSize      (760,351) → (750,343)                   跳 8px
        adjustSize+居中  (619,381) → (619,381)                  不跳

    **只对本函数处理过的对话框有效**: `move()` 会置上 WA_Moved, Qt 显示时就不再自己
    摆位。所以别再用 `QColorDialog.getColor()` / `QMessageBox.warning()` 这类静态便捷
    函数 —— 它们内部自己 new + show, 走的是"先显示、显示后再挪"的路径, 实测
    QColorDialog 显示在 (910,470) 再挪到 (617,264)、QFileDialog 甚至先在屏幕左上角
    (0,0) 闪一下再跳到中间。要用静态函数的语义, 就自己 new 出来、过一遍本函数再 exec。

    `ensurePolished()` 不能省: 没上样式表时 QLabel/QPushButton 的 sizeHint 偏小,
    量出来的尺寸偏小, 显示后布局定稿又会撑大一次 —— 尺寸一跳, 位置看着也像跳。
    """
    dlg.ensurePolished()
    dlg.adjustSize()
    p = dlg.parentWidget()
    if p is not None:
        host = p.window()
        c = host.frameGeometry().center()
        x = c.x() - dlg.width() // 2
        y = c.y() - dlg.height() // 2
        # 父窗中心若把对话框推到屏幕外(父窗跨屏/贴着屏幕边), 拉回可用区内 ——
        # 否则 Qt 会把"越界"的窗口重新摆一次, 又变成先闪一下再跳
        scr = host.screen()
        if scr is not None:
            a = scr.availableGeometry()
            x = max(a.left(), min(x, a.right() - dlg.width()))
            y = max(a.top(), min(y, a.bottom() - dlg.height()))
        dlg.move(x, y)

    # 离屏"演一遍"完整的显示流程, 再真正显示。
    #
    # 只保证位置对了还不够: 窗口被映射到屏幕的那一刻, 内容往往还没画 —— 实测抓屏可见弹窗先以
    # **一块纯白空窗口**出现(只剩标题栏), 约 30ms 后才补上卡片与色板。这就是"弹窗先闪一下再
    # 出现"。它还是个竞态, 所以表现为"只有部分颜色行会闪、且时有时无"。
    #
    # WA_DontShowOnScreen 下 show() 会照常走完整的布局与绘制(只是不映射到桌面), 于是真正 exec()
    # 时后备存储里已经有内容, 映射即可见完整界面。调用方自己设过这个属性的要还原(自检脚本会设)。
    primed = dlg.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dlg.show()
    dlg.hide()
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, primed)


# ---- 消息框 / 文件对话框 ----
# 一律走这三个函数, 不要用 QMessageBox.warning() / QMessageBox.question() /
# QFileDialog.getOpenFileName() / QFileDialog.getExistingDirectory() 这些**静态便捷函数**:
# 它们内部自己 new + show, 走的是"先显示、显示后再挪"的路径, 过了 _prep_dialog 的 `move()`
# 那一步就没了。实测(父窗 1240x900):
#     QMessageBox.warning()               Show 在 (910,470) → 4ms 后 Move 到 (888,432)
#     QFileDialog.getOpenFileName()       Show 在 (0,0)     → 立刻 Move 到 (900,461)
# 肉眼就是"先在别处闪过一个边框, 再在另一个位置出现"。下面这三个函数把顺序倒过来:
# 先 new、先定死尺寸与位置, 再 exec。
def _message(parent, icon, title, text,
             buttons=QMessageBox.StandardButton.Ok, default=None):
    """消息框。返回被点中的标准按钮(与 `QMessageBox.exec()` 的返回值口径一致)。

    这里是**自绘标题栏**的弹窗(`dialog.MessageDialog`), 不是系统消息框:
    一是 QMessageBox 带 DWM 画的边框, 首次显示会出现一两帧错位(见 dialog.py 里的说明);
    二是三种弹窗(取色/型号映射/消息框)要长成同一套设计语言。
    `icon` 参数保留是为了不动调用方, 目前不画图标(标题 + 正文已足够表达)。
    """
    box = MessageDialog(parent, icon, title, text, buttons, default, recenter=_prep_dialog)
    box.exec()
    return box.result_button


def _save_file_dialog(parent, caption, directory="", default_name="", name_filter=""):
    """另存为一个文件; 取消返回空串。"""
    dlg = QFileDialog(parent, caption)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dlg.setFileMode(QFileDialog.FileMode.AnyFile)
    if directory:
        dlg.setDirectory(directory)
    if default_name:
        dlg.selectFile(default_name)
    if name_filter:
        dlg.setNameFilter(name_filter)
    dlg.setDefaultSuffix("json")            # 用户不打扩展名时补上, 否则存成无类型文件
    _prep_dialog(dlg)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    files = dlg.selectedFiles()
    return files[0] if files else ""


def ask_slot(parent, presets, name, slots=_PRESET_SLOTS):
    """「导入到哪个槽位」—— 列出全部槽位与各自当前方案名, 由用户自己选。取消返回 ""。

    **不做成"默认第一个空槽"式的无人值守**: 第一个槽很可能已经存着东西, 默认它等于鼓励
    误覆盖。列表里把已占用的槽位连方案名一起列出来, 一眼就能挑到空的; 预选值是"第一个空槽"
    (都占满了才预选当前槽位), 兼顾顺手与安全。
    """
    dlg = QDialog(parent)
    go_frameless(dlg)
    dlg.setWindowTitle(QCoreApplication.translate("window", "导入预设"))
    v = QVBoxLayout(dlg)
    lab = QLabel(QCoreApplication.translate("window", "把方案「{name}」导入到哪个槽位？").format(name=name))
    lab.setWordWrap(True)
    v.addWidget(lab)
    combo = NoWheelComboBox()
    first_empty = -1
    for i in range(1, slots + 1):
        key = str(i)
        p = presets.get(key) or {}
        cur_name = p.get("_preset_name") if isinstance(p, dict) else None
        combo.addItem(QCoreApplication.translate("window", "槽位 {slot}：{name}").format(
            slot=i, name=cur_name or QCoreApplication.translate("window", "— 空 —")), key)
        if not cur_name and first_empty < 0:
            first_empty = combo.count() - 1
    if first_empty >= 0:
        combo.setCurrentIndex(first_empty)
    v.addWidget(combo)
    hint = QLabel(QCoreApplication.translate("window", "目标槽位已有方案时会被覆盖。"))
    hint.setObjectName("hint")
    v.addWidget(hint)
    v.addLayout(dlg_button_row(
        dlg_button(QCoreApplication.translate("window", "取消"), dlg.reject),
        dlg_button(QCoreApplication.translate("window", "导入"), dlg.accept, primary=True)))
    csdify(dlg, QCoreApplication.translate("window", "导入预设"), _prep_dialog)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    return combo.currentData() or ""


def _hamburger_icon(color, size=15, dpr=1.0):
    """画"三横线"菜单图标。

    为什么要自绘、为什么要按设备像素画(两个坑都踩过):
      ① 不直接用 ☰(U+2630): 那个码位在中文字体里没有, 靠字体回退拿到别的字体的字形,
         小字号下看着像个小箭头(v);
      ② **按 devicePixelRatio 出图**: 第一版在 15px 的位图上画完交给 Qt 放大 1.25 倍(本机
         125% 缩放), 线条被重采样得发虚、三条粗细还不匀 —— 与预览上屏那条教训是同一个
         (见 README「预览发虚」)。这里 px = size×dpr 出图 + setDevicePixelRatio, 上屏就是 1:1。
      线宽与间距都取整数设备像素(125% 下 3px 线宽 ≈ 2.4 逻辑像素), 这样三条一样粗。
    """
    from PySide6.QtGui import QIcon
    dpr = float(dpr or 1.0)
    px = max(9, int(round(size * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)   # 横平竖直的线, 不要抗锯齿
    pen = QPen(QColor(color))
    pen.setWidth(max(2, round(px / 9)))                        # 设备像素(约图标的 11%)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.setPen(pen)
    inset = round(px * 0.18)
    half = pen.width() // 2
    for frac in (0.24, 0.5, 0.76):                             # 三条等距
        y = min(px - half - 1, max(half, round(px * frac)))
        p.drawLine(QPointF(inset, y), QPointF(px - inset, y))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def _open_and_report(win, path, what):
    """打开一个文件/目录并把结果写进状态栏(打不开就说一句 + 记日志, 不弹窗)。

    与 `_open_logo_dir` 用的是同一个机制(QDesktopServices); 那条另有"目录不存在就先建出来"
    的逻辑, 所以没合到一起。
    """
    if not os.path.exists(path):
        win._status(f"{what}不存在：{path}")
        return False
    try:
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception as e:                      # noqa: BLE001
        ok = False
        logging.warning(f"[打开] {what} 失败 {path}: {e}")
    win._status(f"已打开{what}：{path}" if ok else f"打不开，路径是：{path}")
    return bool(ok)


class ImageInfoDialog(QDialog):
    """图片信息: 照片自己的 EXIF / 文件信息, 与**最终会上水印的值**并排。

    为什么要并排: 勾了「自定义水印信息」之后, 品牌、型号、拍摄参数(快门/光圈/ISO)这三组都会被
    那份手动信息覆盖(见核心 `process_single` 里 `manual_exif` 的三处用法) —— 只看 EXIF 根本
    分不清印出来的到底是哪个, 这是这个程序最容易糊涂的一处。

    文件那几项(大小/像素/格式/时间)都是**只读文件头**, 不解码整张图。
    """

    def __init__(self, path, manual, effective_model, parent=None):
        super().__init__(parent)
        go_frameless(self)
        self.setWindowTitle(self.tr("图片信息"))
        self.resize(540, 420)

        info = self._collect(path, parent)
        photo = info["photo"]
        # 当前生效值: 勾了自定义信息就用那份(与 _format_manual_exif 同一套补 `s`/`f/`/`ISO`),
        # 没勾就是照片自己的
        cur = dict(photo)
        if manual:
            cur.update({k: v for k, v in (core._format_manual_exif(manual) or {}).items()
                        if v and k in ("make", "model", "exposure", "fnumber", "iso")})
            note = self.tr("「当前生效值」= 你在「自定义水印信息」里填的那份（它覆盖照片的 EXIF）")
        else:
            note = self.tr("「当前生效值」= 照片自己的 EXIF（未启用「自定义水印信息」）")

        v = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.GAP_CTRL)
        grid.setVerticalSpacing(6)
        grid.setColumnMinimumWidth(0, _LABEL_W)
        r = [0]

        def group(title, span=1):
            lab = QLabel(title)
            lab.setObjectName("cardTitle")
            if r[0]:
                grid.setRowMinimumHeight(r[0], 10)      # 组之间留一点空
                r[0] += 1
            grid.addWidget(lab, r[0], 0, 1, span)
            r[0] += 1

        def row(label, value, value2=None, elide=False):
            lab = QLabel(self.tr(label))
            lab.setObjectName("fieldLabel")
            lab.setWordWrap(True)
            grid.addWidget(lab, r[0], 0)
            if elide:
                w1 = ElidedLabel(str(value or "—"))
                w1.setObjectName("muted")
            else:
                w1 = QLabel(str(value or "—"))
            grid.addWidget(w1, r[0], 1)
            if value2 is not None:
                grid.addWidget(QLabel(str(value2 or "—")), r[0], 2)
            r[0] += 1

        group(self.tr("文件"))
        row(self.tr("路径"), path, elide=True)
        kb = info["size_kb"]
        row(self.tr("大小"), ("%.1f MB" % (kb / 1024.0)) if kb >= 1024 else (f"{kb:,.0f} KB" if kb else "—"))
        row(self.tr("像素"), f"{info['w']} × {info['h']}" if info["w"] else "—")
        row(self.tr("格式"), (info["ext"] or "—").lstrip(".").upper())
        row(self.tr("修改时间"), info["mtime"])
        group(self.tr("水印里会用到的值"), span=3)
        h1 = QLabel(self.tr("照片里的值"))
        h2 = QLabel(self.tr("当前生效值"))
        for h in (h1, h2):
            h.setObjectName("hint")
        grid.addWidget(h1, r[0], 1)
        grid.addWidget(h2, r[0], 2)
        r[0] += 1
        for label, key in ((QT_TRANSLATE_NOOP("ImageInfoDialog", "品牌"), "make"),
                           (QT_TRANSLATE_NOOP("ImageInfoDialog", "型号"), "model"),
                           (QT_TRANSLATE_NOOP("ImageInfoDialog", "快门"), "exposure"),
                           (QT_TRANSLATE_NOOP("ImageInfoDialog", "光圈"), "fnumber"),
                           ("ISO", "iso")):
            same = photo[key] == cur[key]
            row(label, photo[key], photo[key] if same else cur[key])
        row(self.tr("水印显示（最终）"), effective_model or "—")

        v.addLayout(grid)
        tip = QLabel(note + self.tr("；「水印显示」是再按 自定义 > 映射表 > 原始 算出的最终型号。"))
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        v.addLayout(dlg_button_row(dlg_button(self.tr("关闭"), self.accept, primary=True)))
        csdify(self, self.tr("图片信息"), _prep_dialog)

    @staticmethod
    def _collect(path, parent):
        """把要显示的原始数据一次读齐(文件信息 + EXIF), 失败项留空不抛。"""
        out = {"size_kb": 0.0, "mtime": "—", "w": 0, "h": 0, "ext": "", "photo": {}}
        try:
            stat = os.stat(path)
            out["size_kb"] = stat.st_size / 1024.0
            out["mtime"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass
        try:
            out["ext"] = (parent.state._sniff_image_type(path) if parent
                          else "") or os.path.splitext(path)[1]
        except Exception:                       # noqa: BLE001
            out["ext"] = os.path.splitext(path)[1]
        try:
            from PIL import Image as _Image
            with _Image.open(path) as im:       # 只读文件头, 不解码
                out["w"], out["h"] = im.size
        except Exception:                       # noqa: BLE001 - 读不出尺寸不影响看其它项
            pass
        try:
            ex = core.get_exif_data_from_path(path) or {}
        except Exception:                       # noqa: BLE001
            ex = {}
        # 与渲染端同一套格式化函数 —— 免得这里显示的快门/光圈与真正印上去的写法不一致
        out["photo"] = {
            "make": (ex.get("make") or "").strip(),
            "model": (ex.get("model") or "").strip(),
            "exposure": core.exposure_to_str(ex.get("exposure_time"), ex.get("exposure_num"),
                                            ex.get("exposure_den")),
            "fnumber": core.aperture_to_str(ex.get("fnumber")),
            "iso": core.iso_to_str(ex.get("iso")),
        }
        return out


def _open_file_dialog(parent, caption, directory="", name_filter=""):
    """选单个文件; 取消返回空串。"""
    dlg = QFileDialog(parent, caption)
    dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    if directory:
        dlg.setDirectory(directory)
    if name_filter:
        dlg.setNameFilter(name_filter)
    _prep_dialog(dlg)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    files = dlg.selectedFiles()
    return files[0] if files else ""


def _open_dir_dialog(parent, caption, directory=""):
    """选目录; 取消返回空串。"""
    dlg = QFileDialog(parent, caption)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    if directory:
        dlg.setDirectory(directory)
    _prep_dialog(dlg)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    files = dlg.selectedFiles()
    return files[0] if files else ""


_CHIP_W = theme.CTRL_H + 20          # 色块尺寸: 比控件高度略宽, 好点也好认
_CHIP_H = theme.CTRL_H
_CHIP_GAP = theme.GAP_CTRL
# 弹窗四周留白: 既是对话框留白, 也充当卡片投影的外扩余量(贴边投影会被裁成直角)
_DLG_PAD = 12
# 弹窗最小宽 = 一行色块 + 卡片内边距 + 弹窗留白 —— 让色板正好铺满卡片宽度, 右边不留一截空档
_DLG_W = (_COLS_PRESET * _CHIP_W + (_COLS_PRESET - 1) * _CHIP_GAP
          + 2 * theme.PAD_CARD + 2 * _DLG_PAD)
# 顶部「当前颜色」的色块: 与色板同高但更宽 —— 它是这张卡的焦点, 不是网格里的一格
_CUR_W = theme.CTRL_H * 2 + 8


def _swatch_qss(color, radius=theme.RADIUS_SMALL, selected=False):
    """色块样式(取色弹窗里的色块与「当前颜色」共用这一处定义)。

    悬停 / 按下 / 聚焦都必须把 background 一起写掉 —— 只写 border 的话会落到主题那条
    `QPushButton:hover{background:#f1f5f9}`, 鼠标一上去色块就变灰, 看着像坏了。

    border 粗细只在选中态变化(色块都是定宽定高, 不会引起重排), 悬停只换颜色 ——
    粗细一变, 未选中↔选中的描边就会"跳"一下。选中另配 ✓ 字形, 不靠颜色单独表达。
    """
    col = QColor(color)
    fg = "#000" if col.lightness() > 128 else "#fff"
    if selected:
        return ("QPushButton { background:%s; border:2px solid %s; border-radius:%dpx;"
                " color:%s; font-weight:bold; padding:0; }"
                "QPushButton:hover { background:%s; border:2px solid %s; }"
                "QPushButton:pressed { background:%s; border:2px solid %s; }"
                % (col.name(), theme.ACCENT, radius, fg,
                   col.name(), theme.ACCENT2, col.name(), theme.ACCENT2))
    return ("QPushButton { background:%s; border:1px solid %s; border-radius:%dpx;"
            " color:%s; font-weight:bold; padding:0; }"
            "QPushButton:hover, QPushButton:focus { background:%s; border:1px solid %s; }"
            "QPushButton:pressed { background:%s; border:1px solid %s; }"
            % (col.name(), theme.LINE, radius, fg,
               col.name(), theme.ACCENT, col.name(), theme.ACCENT2))


class _ColorChip(QPushButton):
    """取色弹窗里的一个色块。

    QPushButton 只有 clicked、没有 doubleClicked, 而"双击这一格换成别的颜色"要的正是
    双击, 所以自己转发一次鼠标双击事件(单击照旧走 clicked)。
    """

    activated = Signal(str)
    edited = Signal(int)

    def __init__(self, color, index=-1, parent=None):
        super().__init__(parent)
        self.color = color
        self.index = index                    # 「我的颜色」里的序号; -1 = 预设色, 不可改
        self.clicked.connect(lambda: self.activated.emit(self.color))

    def mouseDoubleClickEvent(self, e):       # noqa: N802 - Qt 命名
        if self.index >= 0:
            self.edited.emit(self.index)
        else:
            super().mouseDoubleClickEvent(e)


class ColorDialog(QDialog):
    """取色对话框(点参数行的色块打开): 当前颜色 + 预设颜色 + 我的颜色。

    三组内容各占一张卡片, 与主窗的参数卡片同一套外观(白底 / 圆角 / 暖投影 / cardTitle
    + hint)。以前是平铺在窗口底色上的一串控件, 标题直接浮在米色底上 —— 和主窗放在一起
    像两个程序。

      当前颜色   一格色块(点它开系统取色器, 拖动取色/调透明度都靠它) + 一个色号输入框。
                 色号只显示一处: 色块只负责"是什么颜色", 文字交给输入框。
      预设颜色   点一下即应用; 当前用的那个带 ✓ 和强调色描边, 一眼看出选中的是哪个。
      我的颜色   点应用, **双击改这一格的颜色**, 右键删除。空位画虚线框并固定画满
                 10 格, 卡片标题右边另写「已存 n / 10」—— 虚线框只说得出"还有空",
                 说不出"占了几格"。

    点色块是**即时应用**(弹窗不关): 预览在工作线程里跑、主窗口照常重绘, 能一边点一边
    看效果, 满意了点「关闭」。收藏色存 settings.json 的 custom_colors(核心截到 10 个),
    """

    picked = Signal(str)

    def __init__(self, state, current, parent=None):
        super().__init__(parent)
        go_frameless(self)                 # 必须在窗口被创建之前(见 dialog.py)
        self.setWindowTitle(self.tr("选择颜色"))
        self.setMinimumWidth(_DLG_W)
        self._state = state
        self._current = current or "#FFFFFF"
        self._chips = []                      # 所有色块按钮, 用来刷选中态

        v = QVBoxLayout(self)
        v.setContentsMargins(_DLG_PAD, _DLG_PAD, _DLG_PAD, _DLG_PAD)
        v.setSpacing(theme.GAP_CARD)

        v.addWidget(self._current_card())
        v.addWidget(self._preset_card())
        v.addWidget(self._custom_card())

        acts = QHBoxLayout()
        acts.setSpacing(theme.GAP_CTRL)
        b = dlg_button(self.tr("保存当前颜色"), self._remember)
        b.setToolTip(self.tr("把当前颜色存进「我的颜色」，下次直接点"))
        acts.addWidget(b)
        acts.addStretch(1)                       # 右侧那个"落点"按钮靠右
        # 取色是即时应用的, "关闭"就是这次操作的落点(不是取消), 给主按钮的强调色
        acts.addWidget(dlg_button(self.tr("关闭"), self.accept, primary=True))
        v.addLayout(acts)

        # 初始焦点收到对话框自身, **不要落在子控件上**: 色块与色号框都带 :focus 描边, 而焦点是
        # 窗口激活时才生效的, 比首帧晚一两帧 —— 那两帧里控件先按"无焦点"画一遍, 随后才补上描边,
        # 肉眼就是刚打开时那个色块"闪"了一下(实测: 首帧与稳定帧在色块区域差 204 个像素,
        # 描边从 LINE #E6E0D6 变成 ACCENT #6F9C7D, 约 40ms 后才稳定)。
        # 焦点放在对话框上, 从首帧起就没有描边变化; Tab 照常进入各控件(那时才该显出描边)。
        # 注意: 不能改成"把焦点给别的控件"—— 焦点落到色号输入框上一样会闪(主题里 QLineEdit:focus
        # 也改描边色), 必须让这一轮没有子控件持有焦点。
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

        self._sync_current()
        csdify(self, self.tr("选择颜色"), _prep_dialog)     # 无边框 + 自绘标题栏

    # ---- 取色弹窗自身的构造 ----
    def _card(self, title, note=""):
        """分组卡片: 与主窗的 Card 同一套外观, 但没有「重置本组」—— 这里没有"一组参数"可重置。

        返回 (frame, body, note_label); note 是标题右侧的补充说明(如容量), 用 hint 色。
        """
        frame = QFrame()
        frame.setObjectName("card")
        theme.apply_shadow(frame)
        v = QVBoxLayout(frame)
        v.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD - 2, theme.PAD_CARD, theme.PAD_CARD)
        v.setSpacing(theme.GAP_ROW)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(title)
        lab.setObjectName("cardTitle")
        head.addWidget(lab)
        head.addStretch(1)
        note_lab = QLabel(note)
        note_lab.setObjectName("hint")
        head.addWidget(note_lab)
        v.addLayout(head)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(theme.GAP_ROW)
        v.addLayout(body)
        return frame, body, note_lab

    def _current_card(self):
        """当前颜色: 一格色块(点它开系统取色器) + 一个色号输入框。

        以前色块铺满整行、里头写着色号, 右边又是一个同样内容的输入框 —— 同一个值并排显示
        两遍; 而且那条横贯整窗的色条看着像进度条, 不像控件。现在色块收成与色板同高的一格
        (只表达颜色本身), 色号只由输入框负责显示与编辑。
        """
        frame, body, _ = self._card(self.tr("当前颜色"))
        row = QHBoxLayout()
        row.setSpacing(theme.GAP_CTRL)
        self._cur_chip = QPushButton()
        self._cur_chip.setFixedSize(_CUR_W, theme.CTRL_H)
        self._cur_chip.setCursor(Qt.PointingHandCursor)
        self._cur_chip.setAccessibleName(self.tr("当前颜色"))   # 按钮内没有文字, 读屏得有个名字
        self._cur_chip.setToolTip(self.tr("点击打开系统取色器（可拖动取色、调透明度）"))
        self._cur_chip.clicked.connect(self._native)
        row.addWidget(self._cur_chip)
        self._hex = QLineEdit()
        self._hex.setFixedHeight(theme.CTRL_H)
        self._hex.setMaxLength(9)
        self._hex.setAccessibleName(self.tr("色号"))   # 卡片标题不等于这个输入框的标签, 读屏要用它
        self._hex.setToolTip(self.tr("直接输入色号（如 #6F9C7D），回车生效"))
        self._hex.editingFinished.connect(self._apply_hex)
        row.addWidget(self._hex, 1)
        body.addLayout(row)

        hint = QLabel(self.tr("点色块开取色器，也可直接输入色号"))
        hint.setObjectName("hint")
        body.addWidget(hint)
        return frame

    def _preset_card(self):
        frame, body, _ = self._card(self.tr("预设颜色"))
        body.addWidget(self._grid(PRESET_COLORS, _COLS_PRESET))
        return frame

    def _custom_card(self):
        frame, body, note = self._card(self.tr("我的颜色"))
        self._custom_note = note
        # 重建「我的颜色」时要用的容器: `_reload_custom` 必须在这张卡片的 body 里换色板,
        # 用对话框顶层布局会 indexOf 不到(返回 -1, 变成追加到弹窗末尾)
        self._custom_body = body
        self._custom_box = self._grid(self._custom(), _COLS_USER,
                                      total=MAX_USER_COLORS, editable=True)
        body.addWidget(self._custom_box)
        tip = QLabel(self.tr("点色块即应用；双击改色，右键删除"))
        tip.setObjectName("hint")
        body.addWidget(tip)
        self._sync_custom_note()
        return frame

    def _sync_custom_note(self):
        """把「已存几个 / 共几个」写在卡片标题右边。"""
        self._custom_note.setText(self.tr("已存 {n} / {total}").format(n=len(self._custom()), total=MAX_USER_COLORS))

    def _custom(self):
        try:
            return list((self._state.get_custom_colors() or {}).get("colors") or [])
        except Exception:                     # noqa: BLE001 - 读不到就当没有收藏色
            return []

    def _grid(self, colors, cols, total=0, editable=False):
        """色块网格。`total>0` 时**固定画满 total 个位置**, 没存颜色的画虚线框 ——
        这样用户一眼能看到总共有几个位置、还剩几个, 而不是对着空荡荡一片猜。
        末列留一条伸缩位: 色块是定宽的, 弹窗被拉宽时它们保持左对齐, 不会被均匀拉开。"""
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(_CHIP_GAP)
        for i in range(max(len(colors), total)):
            g.addWidget(self._chip(colors[i] if i < len(colors) else None,
                                   i if editable else -1, editable),
                        i // cols, i % cols)
        g.setColumnStretch(cols, 1)
        return box

    def _chip(self, color, index=-1, editable=False):
        if color is None:                     # 空位: 虚线框, 不可点
            lab = QLabel()
            lab.setFixedSize(_CHIP_W, _CHIP_H)
            lab.setAccessibleName(self.tr("空位"))
            lab.setToolTip(self.tr("空位"))
            lab.setStyleSheet("background:transparent; border:1px dashed %s; border-radius:%dpx;"
                              % (theme.EMPTY_SLOT, theme.RADIUS_SMALL))
            return lab
        b = _ColorChip(color, index)
        b.setFixedSize(_CHIP_W, _CHIP_H)
        b.setCursor(Qt.PointingHandCursor)
        b.setAccessibleName(self.tr("颜色 {color}").format(color=color))   # 色块只画颜色, 读屏得靠名字才认得出
        b.setToolTip(color + (self.tr("　双击改色 · 右键删除") if editable
                                     else self.tr("　点击应用")))
        self._paint_chip(b, color)
        b.activated.connect(self._choose)
        if editable:
            b.edited.connect(self._edit_saved)
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda _pos, i=index, col=color, btn=b: self._chip_menu(i, col, btn))
        self._chips.append(b)
        return b

    # ---- 状态刷新 ----
    def _paint_chip(self, btn, color):
        """色块 = 底色 + 选中态(✓ + 强调色描边)。新建与刷新都走这里, 免得两处口径不一
        (踩过: 新建时只刷了样式没写 ✓, 刚存下的收藏色看着没被选中)。"""
        selected = color.lower() == self._current.lower()
        btn.setText("✓" if selected else "")
        btn.setStyleSheet(_swatch_qss(color, selected=selected))

    def _sync_current(self):
        """把「当前颜色」那一格 + 色号框刷成 self._current, 并重刷所有色块的选中态。"""
        self._cur_chip.setStyleSheet(
            _swatch_qss(self._current, radius=theme.RADIUS_CTRL))
        self._hex.setText(self._current)
        for b in self._chips:
            self._paint_chip(b, b.color)

    def _choose(self, color):
        self._current = color
        self._sync_current()
        self.picked.emit(color)               # 即时应用, 弹窗不关

    def _apply_hex(self):
        """色号输入框: 回车/失焦时生效。不合法就退回当前色号, 不动参数。"""
        txt = self._hex.text().strip()
        col = QColor(txt if txt.startswith("#") else "#" + txt)
        if not txt or not col.isValid() or col.name().lower() == QColor(self._current).name().lower():
            self._sync_current()
            return
        self._choose(col.name())

    # ---- 系统取色器 ----
    def _pick_native(self, initial=None):
        """开系统取色器, 返回选中的色号(取消返回 None)。

        不能再用 `QColorDialog.getColor()` 这个静态函数: 它内部自己 new + show, 显示
        之后 Qt 还会再摆一次位 —— 实测先在 (910,470) 出现、再跳到 (617,264), 就是用户
        看到的"窗口在屏幕左上角闪一下才落到界面里"。自己 new 出来、过一遍
        `_prep_dialog` 把位置定死, 就没有这一跳(和「管理所有型号映射」同一套做法)。
        """
        dlg = QColorDialog(QColor(initial or self._current), self)
        dlg.setWindowTitle(self.tr("选择颜色"))
        _prep_dialog(dlg)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        c = dlg.currentColor()
        return c.name() if c.isValid() else None

    def _native(self):
        c = self._pick_native()
        if c:
            self._choose(c)

    # ---- 「我的颜色」的增删改 ----
    def _save_custom(self, arr):
        try:
            self._state.save_custom_colors(arr)      # 上限由核心截到 MAX_USER_COLORS
        except Exception as e:                       # noqa: BLE001 - 存不上不该把弹窗弄崩
            _message(self, QMessageBox.Icon.Warning, self.tr("保存失败"), str(e))
            return False
        self._reload_custom()
        return True

    def _remember(self):
        cur = self._current
        arr = [c for c in self._custom() if c.lower() != cur.lower()]
        arr.append(cur)                  # 重复的挪到末尾(这一列按"最近保存"排序)
        if len(arr) > MAX_USER_COLORS:
            # 存满了要挤掉最早那个: 核心是 `[:10]` 截断, 直接把 11 个丢给它会把**刚存的
            # 这个**截掉 —— 用户点「保存当前颜色」看着什么都没发生
            arr = arr[len(arr) - MAX_USER_COLORS:]
        self._save_custom(arr)

    def _remove(self, color):
        """从「我的颜色」里删掉一个(上限 10 个, 存满了就得能删)。"""
        self._save_custom([c for c in self._custom() if c.lower() != color.lower()])

    def _edit_saved(self, index):
        """把「我的颜色」第 index 格换成新挑的颜色(双击色块 / 右键菜单进来)。"""
        arr = self._custom()
        if not 0 <= index < len(arr):
            return
        c = self._pick_native(arr[index])
        if not c:
            return
        arr[index] = c
        if self._save_custom(arr):
            self._choose(c)

    def _chip_menu(self, index, color, btn):
        menu = QMenu(self)
        menu.addAction(self.tr("改这一格的颜色…")).triggered.connect(lambda: self._edit_saved(index))
        menu.addAction(self.tr("删除该颜色")).triggered.connect(lambda: self._remove(color))
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _reload_custom(self):
        """换了收藏色之后就地重建「我的颜色」那一块(保持原位置)。

        必须用卡片自己的 body 布局(`_custom_body`)定位 —— 色板是挂在卡片里的, 拿对话框
        顶层布局去 indexOf 会找不到(返回 -1), insertWidget(-1) 会把新色板追加到弹窗末尾:
        卡片被掏空、色板跑到最底下, 弹窗还跟着长高。
        """
        body = self._custom_body
        idx = body.indexOf(self._custom_box)
        # 旧格子里的色块要一并从选中态列表里摘掉: 它们随后就被销毁, 留着会被
        # _sync_current 摸到已析构的 C++ 对象
        self._chips = [c for c in self._chips if not self._custom_box.isAncestorOf(c)]
        box = self._grid(self._custom(), _COLS_USER, total=MAX_USER_COLORS, editable=True)
        body.removeWidget(self._custom_box)
        # 必须 setParent(None): 单靠 removeWidget 只是从布局里摘掉, 控件仍是本窗口的子对象、
        # 仍会被画在原位置, 直到 deleteLater 真正执行 —— 表现为新旧两块叠在一起
        self._custom_box.setParent(None)
        self._custom_box.deleteLater()
        self._custom_box = box
        body.insertWidget(idx, box)
        self._sync_custom_note()      # 容量数字跟着一起变, 否则会停在旧的值上


class ElidedLabel(QLabel):
    """超长文本(路径之类)的标签: 按控件宽度在中间省略。

    QLabel 不会自动省略 —— 长路径会把 minimumSizeHint 顶大, 把卡片/布局撑出边界。
    省略中间比省略尾部有用: "C:\\Users\\...\\IMAGE-2025-01-19-1228-482D.jpg" 两头都还认得出。
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(70)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)   # 仍可选中复制完整路径
        self.setToolTip(text)

    def setText(self, text):            # noqa: N802 - Qt 命名
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        fm = self.fontMetrics()
        p.drawText(self.rect(), int(self.alignment()),
                   fm.elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width()))


class MapDialog(QDialog):
    """

    """

    def __init__(self, state, parent=None):
        super().__init__(parent)
        go_frameless(self)                 # 必须在窗口被创建之前(见 dialog.py)
        self.setWindowTitle(self.tr("型号映射管理"))
        self.resize(560, 460)
        self._data = {}
        try:
            self._logo_dir = (parent._params.get("logo_dir") if parent else "") or core.DEFAULT_LOGO_DIR
        except Exception:
            self._logo_dir = core.DEFAULT_LOGO_DIR

        v = QVBoxLayout(self)
        tip = QLabel(self.tr("识别到的原始型号 → 水印上显示的型号。左侧列表选中后可删除。"))
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        v.addWidget(self._list, 1)

        form = QHBoxLayout()
        form.setSpacing(6)
        form.addWidget(QLabel(self.tr("原始")))
        self._raw = QLineEdit()
        self._raw.setPlaceholderText(self.tr("如 ILCE-7M4"))
        form.addWidget(self._raw, 1)
        form.addWidget(QLabel(self.tr("显示")))
        self._nice = QLineEdit()
        self._nice.setPlaceholderText(self.tr("如 Sony A7M4"))
        form.addWidget(self._nice, 1)
        v.addLayout(form)

        # 只有两个动作, 且都是**即时生效**(点完立刻写盘): 所以不再有单独的「保存到映射表」——
        # 列表上一秒就变了, 再要求"保存"一次反而是误导; 也不再有「关闭」(标题栏那个 ✕ 就是关)。
        # NOOP 必须包在**字面量**上(lupdate 看不穿变量): 曾经包在 text 上, 结果整条漏译
        v.addLayout(dlg_button_row(
            dlg_button(self.tr(QT_TRANSLATE_NOOP("MapDialog", "添加 / 修改")), self._add),
            dlg_button(self.tr(QT_TRANSLATE_NOOP("MapDialog", "删除选中")), self._remove)))

        self._list.currentItemChanged.connect(self._on_select)
        try:
            self._data = dict(core.load_model_map(self._logo_dir) or {})
        except Exception:                           # noqa: BLE001 - 读不出就当空表
            self._data = {}
        self._reload()
        csdify(self, self.tr("型号映射管理"), _prep_dialog)   # 无边框 + 自绘标题栏

    def _reload(self):
        """把**内存里**这份映射表画进列表。

        **不要在这里读文件**: 以前它每调一次就 load_model_map() 一次, 于是
        「添加 / 修改」刚写进 self._data 的条目立刻被文件里的旧内容冲掉 —— 表现就是
        "点添加没反应、列表始终是空的"(文件里本来就没有)。读盘只在 __init__ 里做一次,
        写盘只在「保存到映射表」里做。
        """
        self._list.clear()
        for k in sorted(self._data):
            self._list.addItem(f"{k}   →   {self._data[k]}")

    def _on_select(self, cur, _prev):
        if cur is None:
            return
        key = cur.text().split("   →   ")[0]
        self._raw.setText(key)
        self._nice.setText(self._data.get(key, ""))

    def _add(self):
        raw = self._raw.text().strip()
        nice = self._nice.text().strip()
        if not raw:
            _message(self, QMessageBox.Icon.Information, self.tr("提示"), self.tr("请先填「原始型号」"))
            return
        self._data[raw] = nice          # 允许把显示名清空
        self._reload()
        self._persist()

    def _remove(self):
        raw = self._raw.text().strip()
        if raw in self._data:
            del self._data[raw]
            self._reload()
            self._persist()
        self._raw.clear()
        self._nice.clear()

    def _persist(self):
        """立刻写盘 —— 两个动作都是即时生效, 不再有单独的「保存到映射表」按钮。

        写失败就报一句, 并把内存回滚成磁盘上那份(否则界面显示一份、文件里另一份,
        下次开弹窗又是第三个样子)。
        """
        try:
            core.save_model_map(self._logo_dir, self._data)
        except Exception as e:                      # noqa: BLE001
            _message(self, QMessageBox.Icon.Warning, self.tr("保存失败"), str(e))
            try:
                self._data = dict(core.load_model_map(self._logo_dir) or {})
            except Exception:                       # noqa: BLE001
                self._data = {}
            self._reload()


class MainWindow(QMainWindow):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self._params = self._load_params()
        self._rows = {}
        self._cards = []
        self._input_path = ""
        self._input_name = ""
        self._raw_model = ""
        self._raw_brand = ""             # 品牌只进「识别型号」那行的 tooltip
        self._effective_model = ""       # 水印最终会印的型号(见 _update_effective_model)
        self._custom_model = ""
        self._output_dir = ""
        self._generating = False
        # 批量模式
        self._mode = "single"            # single / batch
        self._batch_files = []           # [{"path", "name"}, ...]
        self._batch_dir = ""
        self._batch_recursive = True
        self._manual_on = False
        self._manual = {"make": "", "model": "", "exposure": "", "fnumber": "", "iso": ""}
        self._manual_preview = False     # 是否是「刷新预览效果」点出来的那一轮(只它才动状态栏)
        # 撤销/重做(见 _begin_undo): 栈 + 在途事务 + 收尾定时器
        self._undo_stack, self._redo_stack, self._undo_txn = [], [], None
        # 当前界面语言(启动时 main.py 已按 settings['lang'] 装好翻译器)
        self._lang = (state.settings or {}).get("lang") or i18n.DEFAULT_LANG

        self.setWindowTitle(self.tr("镶 · 水印 (Qt 版)"))
        self.setAcceptDrops(True)
        self._split_user_set = False     # 用户是否手动拖过分隔条(拖过就不再自动分配比例)
        self._fitted = False             # 是否已按屏幕可用区定过尺寸(见 _fit_to_screen)
        self._stats = SessionStats(core)  # 会话统计: 界面右侧显示 + 退出时落一条汇总
        self._fit_to_screen()
        self._build_ui()
        self._preview.stats = self._stats     # 上屏缩放的耗时也进统计(见 PreviewBox._scale_for_display)
        self._sync_visibility()
        self._mode_btns["single"].setChecked(True)
        self._sync_files_ui()
        self._sync_refresh_btn()         # 启动时还没选图: 「刷新预览效果」先按灰
        self._sync_info_btn()            # 「ℹ 图片信息」同理
        # 也必须走一遍: 「复制原始型号」的可用态/说明文字在这里定下来 —— 少了这一步, 启动后
        # 那个按钮是 enabled 的、tooltip 还是空的(用户报"没打开图片也能点")。
        self._update_effective_model()
        self._unify_spins()
        self._unify_buttons()
        # 型号美化卡里两个按钮取同宽, 否则右边缘一宽一窄参差
        w = max(self._map_btn.sizeHint().width(), self._copy_btn.sizeHint().width())
        self._map_btn.setFixedWidth(w)
        self._copy_btn.setFixedWidth(w)
        self.set_tab(0)

        # 参数自动保存: 防抖 500ms 写 settings.json(与内核同一节奏), 关窗时兜底落盘
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_settings_now)

        # 撤销事务的"收尾": 停手 600ms (或换控件/撤销重做/窗口级动作) 就落定一步
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(_UNDO_COALESCE_MS)
        self._undo_timer.timeout.connect(self._commit_undo)
        self._undo_timer.stop()
        # 全窗第一组快捷键(以前一个都没有)
        for seq, slot in (("Ctrl+Z", self.undo),
                          ("Ctrl+Shift+Z", self.redo), ("Ctrl+Y", self.redo)):
            QShortcut(QKeySequence(seq), self).activated.connect(slot)

        self._preview_worker = PreviewWorker(state, self)
        self._preview_worker.rendered.connect(self._on_rendered)
        self._preview_worker.start()
        self._event_watcher = EventWatcher(state, self)
        self._event_watcher.event.connect(self._on_event)
        self._event_watcher.start()
        self._refresh_stats()            # 状态栏右侧先显示"尚无预览", 避免那块空白看着像坏了
        self._status(self.tr("拖入图片即可开始（放大镜/间距标注/取色板见后续步骤）"))

    # ---------- 参数 ----------
    def _load_params(self):
        p = dict(core.default_params())
        saved = self.state.settings or {}
        for k, v in saved.items():
            if k in p:                      # 只吃已知参数; 未知键留在 settings.json 里不动
                p[k] = v
        try:
            p["model_map"] = core.load_model_map(p.get("logo_dir") or core.DEFAULT_LOGO_DIR)
        except Exception:
            p["model_map"] = {}
        return p

    def _on_param(self, key, value):
        spec = self._spec_by_key.get(key) or {}
        self._begin_undo(key, spec.get("label") or key)     # 撤销: 按控件合并, 见 _begin_undo
        if spec.get("mirror"):
            # 镜像行: 一个滑块写两个底层参数(上下各一份), 保持底层行同步, 切到别的布局不残留
            for t in spec["mirror"]:
                self._params[t] = value
                if t in self._rows:
                    self._rows[t].set_value(value)
        else:
            self._params[key] = value
        # key 是某个 only_if / only_if_not 的"条件参数"(layout_mode / bg_mode / enable_*)时
        # 才需要重算显隐; 被控制的参数自己变了不影响显隐
        if key in self._controllers:
            self._sync_visibility()
        self._touch()

    # ---------- 撤销 / 重做 ----------
    # 快照式: 参数就是一整份扁平字典(~51 个标量 + model_map), 存一份几十 KB 都不到, 留 100 步
    # 也无所谓。**难点不在栈, 在合并**: 滑块是 valueChanged 逐像素发信号, 不合并的话拖一次滑块
    # 要按两百下撤销。所以按"同一个控件 + 一段空闲时间"合并成一步(见 _begin_undo)。
    def _snapshot(self):
        """取一份撤销用的快照。

        **必须连 logo_dir 与 model_map 一起取**: 「恢复默认设置」除了铺参数, 还会单独改这两样
        (先赋值 logo_dir, 再按它重读映射表) —— 只存参数表的话, 撤销"恢复默认"会留下半截状态。
        """
        s = dict(self._params)
        s["model_map"] = dict(self._params.get("model_map") or {})
        return s

    def _begin_undo(self, key, label):
        """开始/延续一次撤销事务(同一个 key 的连续改动算一步)。"""
        txn = self._undo_txn
        if txn is not None and txn["key"] == key:
            txn["label"] = label
            self._undo_timer.start()            # 还在拖: 把"收尾"往后推(松开后 600ms 才落定)
            return
        self._commit_undo()                     # 换了控件: 先把上一笔落定
        self._undo_txn = {"key": key, "label": label, "before": self._snapshot()}
        self._undo_timer.start()
        self._sync_undo_btns()

    def _commit_undo(self):
        """把进行中的事务落定入栈(超时 / 换控件 / 窗口级动作之后)。"""
        self._undo_timer.stop()
        txn, self._undo_txn = self._undo_txn, None
        if txn is None:
            return
        after = self._snapshot()
        if after == txn["before"]:              # 值绕了一圈又回来了(滑块拖回原位): 不入栈
            self._sync_undo_btns()
            return
        self._undo_stack.append({"label": txn["label"], "before": txn["before"], "after": after})
        del self._undo_stack[:-_UNDO_LIMIT]     # 只留最近 N 步
        self._redo_stack.clear()                # 有了新改动, 重做链失效
        self._sync_undo_btns()

    def _undo_step(self, key, label, fn):
        """把一个"整批动作"记成**一步**(重置本组 / 恢复默认 / 加载预设 / 映射表保存)。"""
        self._begin_undo(key, label)
        try:
            fn()
        finally:
            self._commit_undo()

    def _restore(self, snap):
        """把一份快照铺回去。

        `_apply_params` 已经会处理镜像行、显隐联动、Logo 标签, 并触发保存与预览; `model_map`
        也在 `_params` 里, 所以它会跟着回去 —— **不要**再调 `_reload_model_map()`(那是从磁盘重读,
        会把快照里的那份覆盖掉)。
        """
        self._apply_params(snap)

    def undo(self):
        self._commit_undo()                     # 在途的那笔先落定, 再撤它
        if not self._undo_stack:
            return
        e = self._undo_stack.pop()
        self._restore(e["before"])
        self._redo_stack.append(e)
        self._status(self.tr("已撤销：{label}").format(label=e["label"]))
        self._sync_undo_btns()

    def redo(self):
        self._commit_undo()
        if not self._redo_stack:
            return
        e = self._redo_stack.pop()
        self._restore(e["after"])
        self._undo_stack.append(e)
        self._status(self.tr("已重做：{label}").format(label=e["label"]))
        self._sync_undo_btns()

    def _sync_undo_btns(self):
        """按钮可用态与 tooltip 跟着栈走(栈空置灰; tooltip 带上"这一步是什么")。"""
        if not hasattr(self, "_undo_btn"):
            return
        nu = self._undo_stack[-1]["label"] if self._undo_stack else ""
        nr = self._redo_stack[-1]["label"] if self._redo_stack else ""
        can_undo = bool(self._undo_stack) or self._undo_txn is not None
        self._undo_btn.setEnabled(can_undo)
        self._redo_btn.setEnabled(bool(self._redo_stack))
        self._undo_btn.setToolTip(self.tr("撤销：{label}（Ctrl+Z）").format(label=nu) if nu
                                    else self.tr("撤销（Ctrl+Z）"))
        self._redo_btn.setToolTip(self.tr("重做：{label}（Ctrl+Shift+Z）").format(label=nr)
                                    if nr else self.tr("重做（Ctrl+Shift+Z）"))

    def _sync_visibility(self):
        """按 only_if / only_if_not 显隐参数行。

        一整批改完再放开重绘: 逐个 setVisible 会触发多次布局, 而卡片带 QGraphicsDropShadowEffect
        (渲染到离屏图), 尺寸一变就可能先贴出旧尺寸的图 —— 实测表现就是"先跳一下, 再闪现出子选项"。
        这里关掉更新期间的重绘、并在放开前强制走完一次布局。
        """
        area = self._stack.currentWidget()
        host = area.widget() if area is not None else None
        if host is not None:
            host.setUpdatesEnabled(False)
        try:
            for key, row in self._rows.items():
                cond = self._only_if.get(key)
                ncond = self._only_if_not.get(key)
                show = (cond is None or self._params.get(cond[0]) in cond[1]) and                        (ncond is None or self._params.get(ncond[0]) not in ncond[1])
                row.setVisible(show)
        finally:
            if host is not None:
                lay = host.layout()
                if lay is not None:
                    lay.activate()          # 先算完终态布局, 再放开重绘, 避免中间态被画出来
                host.setUpdatesEnabled(True)

    def _sync_mirror_rows(self, changed):
        """把"镜像行"的显示值刷成它底层参数的值。

        镜像行(内侧/外侧间距)是 UI 侧概念: 它自己不存值, 写的是 gap_image_top/bottom 这些
        参数。所以批量赋值(重置本组 / 加载预设 / 恢复默认)之后必须回填一次 —— 否则那两行
        的滑块停在旧位置上, 而后端参数已经被改成新值: 看起来"重置没生效", 再动一下滑块又会
        """
        for key, row in self._rows.items():
            m = (self._spec_by_key.get(key) or {}).get("mirror")
            if m and m[0] in changed:
                row.set_value(self._params.get(m[0]))

    def _reset_card(self, card):
        defaults = core.default_params()
        # 卡片登记的 keys 只含"有控件"的参数; 镜像行代它写的那两个参数(gap_image_top/gap_top)
        # 没有控件, 必须一起重置, 否则重置后它们停在旧值上(界面已改、内核没改)
        keys = set(card.keys)
        for key in card.keys:
            keys.update((self._spec_by_key.get(key) or {}).get("mirror") or ())
        title = card.findChild(QLabel, "cardTitle").text()

        def _do():
            changed = set()
            for key in keys:
                if key in defaults:
                    self._params[key] = defaults[key]
                    if key in self._rows:
                        self._rows[key].set_value(defaults[key])
                    changed.add(key)
            self._sync_mirror_rows(changed)
            self._sync_visibility()
            self._touch()

        self._undo_step(f"__reset_card__{title}", f"重置「{title}」", _do)
        self._status(self.tr("已重置「{title}」为默认值").format(title=title))

    # ---------- 窗口尺寸 / 分栏 ----------
    def _fit_to_screen(self):
        """按屏幕可用区(已扣任务栏)的百分比定初始尺寸并居中。

        不写死像素的理由: 1080p@125% 下逻辑可用区只有 1536x816, 而"够用"的窗口高度要 860 ——
        写死就会顶出屏幕, 底栏与状态栏落到屏幕外, 每次都要手动拉。留边 _WIN_EDGE px
        保证四边不贴边。

        只影响初始值: 之后用户怎么改窗口都不再干预(没有加尺寸持久化, 每次启动都用算出来的,
        见 README 的"已知事项")。"""
        scr = self.screen() or QApplication.primaryScreen()
        if scr is not None:
            a = scr.availableGeometry()
            sw, sh = a.width(), a.height()
        else:
            sw, sh = 1536, 816          # 拿不到屏幕时按 1080p@125% 的可用区兜底
        w = max(320, min(int(sw * _WIN_W_RATIO), sw - _WIN_EDGE))
        h = max(240, min(int(sh * _WIN_H_RATIO), sh - _WIN_EDGE))
        self.resize(w, h)
        self.move(a.center().x() - w // 2, a.center().y() - h // 2)

    def _apply_split_ratio(self):
        """把左右列按 _SPLIT_LEFT 重排(窗口缩放时保持比例); 用户手动拖过后本次会话不再干预。

        分母用 sum(sizes()): 它就是分隔条真正可分配的宽度, 与 handle 宽度(8px)无关, 不必手扣。
        窗口极窄时(最小 674 宽 -> 40% = 270 < 左列最小 293) QSplitter 会自动钳到各自最小值,
        比例优雅退让, 不会挤坏布局。"""
        sp = getattr(self, "_split", None)
        if sp is None or self._split_user_set:
            return
        total = sum(sp.sizes())
        if total <= 0:                  # 首次布局还没走完, sizes() 没有意义
            return
        left = int(total * _SPLIT_LEFT)
        sp.setSizes([left, total - left])

    def _on_split_moved(self, *_pos):
        self._split_user_set = True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_split_ratio()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._fitted:
            self._fitted = True
            # 首帧时才知道窗口最终落在哪块屏(多屏下未必是 primaryScreen): 再按那块屏的可用区
            # 收一次, 避免又出现"底部超出屏幕"。只在第一次做, 之后不动用户的调整。
            scr = self.screen()
            if scr is not None:
                a = scr.availableGeometry()
                w = min(self.width(), a.width() - _WIN_EDGE)
                h = min(self.height(), a.height() - _WIN_EDGE)
                if (w, h) != (self.width(), self.height()):
                    self.resize(w, h)
            self._apply_split_ratio()

    # ---------- 构建 ----------
    def _build_ui(self):
        # 预览控件先建: 页签里的参数行在构造时就要连它的 set_highlight
        self._preview = PreviewBox()
        self._only_if = {s["key"]: s["only_if"] for s in PARAM_SPECS if s.get("only_if")}
        self._only_if_not = {s["key"]: s["only_if_not"] for s in PARAM_SPECS if s.get("only_if_not")}
        self._spec_by_key = {s["key"]: s for s in PARAM_SPECS if s.get("key")}
        self._controllers = {c[0] for c in list(self._only_if.values()) + list(self._only_if_not.values())}

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(theme.INSET_ROOT, 10, theme.INSET_ROOT, 8)
        outer.setSpacing(8)

        outer.addWidget(self._build_topbar())

        self._split = QSplitter(Qt.Horizontal)
        split = self._split
        left = QWidget()
        lv = QVBoxLayout(left)
        # 左列不留横向内边距: 留白必须放在**视口之内**(页签行与卡片宿主)。放这里等于
        # 在滚动区外面, 卡片投影贴着视口边界照样会被裁成直角
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(theme.GAP_CARD)
        lv.addWidget(self._segbar_row())
        self._stack = QStackedWidget()
        for tab_id, _title in TABS:
            self._stack.addWidget(self._build_tab(tab_id))
        lv.addWidget(self._stack, 1)
        split.addWidget(left)
        split.addWidget(self._build_preview_pane())
        # 左右按 _SPLIT_LEFT 的比例排布(见 _apply_split_ratio)。**不要**设 setStretchFactor:
        # 那套是"初始尺寸之外多出来的空间按权重分", 左列会被冻在初始值上(改造前就是 540 恒定、
        # 右边的预览独吞所有增量), 与"按比例"是两种语义。也不在这里 setSizes: 首次布局前
        # sizes() 还没有意义, 初值由 showEvent 里的 _apply_split_ratio() 给。
        split.setChildrenCollapsible(False)   # 左列变窄后防止被拖塌
        split.splitterMoved.connect(self._on_split_moved)
        outer.addWidget(split, 1)

        outer.addWidget(self._build_bottombar())
        self._status_bar = self.statusBar()
        self._status_bar.setFixedHeight(theme.CTRL_H)   # 原来偏矮, 与按钮取齐
        # 不用 QStatusBar 自带的 showMessage: 那行字是它自己绘制的, 既不受 contentsMargins
        # 也不受 QSS padding 影响, 左缩进会跟顶栏/底栏差一截。换成自己的 QLabel 就能走布局,
        # 与其它栏落在同一条左对齐线上。
        self._status_label = QLabel("")
        self._status_label.setObjectName("statusText")
        self._status_bar.addWidget(self._status_label, 1)
        # 状态栏右侧两组数字(等宽小字, 各自带 tooltip):
        #   1) 每次渲染的耗时 —— 原来在预览框下方, 会让预览底边比左列高 21~22px("一高一低"),
        #      搬到这儿后右列只剩预览一个孩子, 两侧底边自然齐平, 预览还多出那 21px 高度;
        #   2) 本次会话的累计统计(悬停看完整分位与缓存命中率)。
        self._perf = QLabel("")
        self._perf.setStyleSheet("color:%s; font-family:Consolas,monospace; background:transparent;"
                                 % theme.MUTED)
        self._status_bar.addPermanentWidget(self._perf)
        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(
            "color:%s; font-family:Consolas,monospace; background:transparent;" % theme.MUTED)
        self._status_bar.addPermanentWidget(self._stats_label)
        self._status_bar.setContentsMargins(theme.INSET_STATUS, 0, 0, 0)
        # QStatusBar 的盒布局还会再加一点内部边距(由样式决定, 实测 2px), 首次布局后量一次补掉,
        # 否则状态文本会比顶栏/底栏多缩进那一点
        QTimer.singleShot(0, self._fix_status_inset)

    def _refresh_stats(self):
        """刷新状态栏右侧的会话统计(每次预览/生成结束时调用一次, 只做加法与一次排序)。"""
        try:
            self._stats_label.setText(self._stats.summary(compact=True))
            self._stats_label.setToolTip(self._stats.summary())
        except Exception as e:                      # 统计出问题绝不能影响主流程
            logging.warning(f"[统计] 刷新失败: {e}")

    def _build_topbar(self):
        top = QFrame()
        t = QHBoxLayout(top)
        # 左缩进对齐左列内容(与页签卡片、参数卡片同一条竖线); 右边缘仍由根布局决定
        t.setContentsMargins(theme.INSET_BAR, 0, 0, 0)
        t.setSpacing(8)
        # ☰ 菜单: 长尾功能收在这里, 不另起一条菜单栏(那会在"全平铺"的界面上多一条空栏)
        self._menu_btn = QPushButton()
        self._menu_btn.setIcon(_hamburger_icon(theme.MUTED, dpr=self.devicePixelRatioF()))
        self._menu_btn.setObjectName("mini")
        self._menu_btn.setFixedWidth(32)
        self._menu_btn.setCursor(Qt.PointingHandCursor)
        self._menu_btn.setToolTip(self.tr("菜单"))
        self._menu_btn.setAccessibleName(self.tr("菜单"))
        self._menu_btn.setMenu(self._build_menu())
        t.addWidget(self._menu_btn)
        self._open_btn = QPushButton(self.tr("打开图片"))
        self._open_btn.clicked.connect(self._pick_image)
        t.addWidget(self._open_btn)
        self._input_label = QLabel(self.tr("未选择图片"))
        self._input_label.setObjectName("muted")
        self._input_label.setMinimumWidth(160)
        t.addWidget(self._input_label, 1)
        # 「识别型号」不再放顶栏: 它和「型号美化」卡里的那一行显示的是同一件事, 两处重复
        # (用户报"功能上冲突"), 现在统一到卡片里(见 _build_model_card)。
        # ℹ 图片信息: 没图可看时置灰, 见 _sync_info_btn
        self._info_btn = QPushButton("ℹ")
        self._info_btn.setObjectName("mini")
        self._info_btn.setFixedWidth(32)
        self._info_btn.setCursor(Qt.PointingHandCursor)
        self._info_btn.setToolTip(self.tr("这张照片的 EXIF 与最终会上水印的值"))
        self._info_btn.setAccessibleName(self.tr("图片信息"))
        self._info_btn.clicked.connect(self._show_image_info)
        t.addWidget(self._info_btn)
        reset = QPushButton(self.tr("恢复默认设置"))
        reset.setObjectName("mini")
        reset.setToolTip(self.tr("把所有参数恢复为出厂默认"))
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._reset_settings)
        t.addWidget(reset)
        return top

    def _build_menu(self):
        """顶栏 ☰ 的菜单。长尾功能都收在这里 —— 界面是"全平铺"的, 不能再往顶栏堆按钮。"""
        m = QMenu(self)
        m.addAction(self.tr("关于"), self._show_about)
        m.addSeparator()
        m.addAction(self.tr("导入预设…"), self._import_preset)
        m.addAction(self.tr("导出预设…"), self._export_preset)
        m.addSeparator()
        m.setToolTipsVisible(True)       # 菜单项也带 tooltip —— 每个入口指向哪个目录一目了然
        a = m.addAction(self.tr("打开日志目录"), self._open_log_dir)
        a.setToolTip(core.LOG_DIR)       # 日志就在程序目录下(LOG_DIR == BASE_DIR)
        a = m.addAction(self.tr("打开数据目录"), self._open_data_dir)
        a.setToolTip(core.DATA_DIR)      # 数据目录是它下面的 watermark_data 子目录
        a = m.addAction(self.tr("打开 Logo 目录"), self._open_logo_dir)
        a.setToolTip(self._params.get("logo_dir") or core.DEFAULT_LOGO_DIR)
        m.addSeparator()
        lang = m.addMenu(self.tr("语言"))
        for code, label in i18n.LANGS:
            act = lang.addAction(label)
            act.setCheckable(True)
            act.setChecked(code == self._lang)
            act.triggered.connect(lambda _c, co=code: self._set_lang(co))
        return m

    def _set_lang(self, code):
        """切语言: 写进设置, 提示**自己关掉重开**才生效。

        **不做热切换, 也不自动重启**: 参数面板那 122 处文案是建窗时 tr() 出来的, 热切换得把
        整窗重建一遍(批量生成/预览线程还在跑时风险高); 自动重启则要走"先放掉单实例管道再起新
        进程"那套, 从 IDE/快捷方式等不同入口启动时未必都起得来。交给用户手动重启最稳。
        """
        if code == self._lang:
            return
        self._lang = code
        try:
            self.state.settings["lang"] = code
            self._save_settings_now()               # 合并式保存: 只多写一个 lang 键
        except Exception as e:                      # noqa: BLE001 - 存不上不影响这次运行
            logging.warning(f"[i18n] 保存语言设置失败: {e}")
        _message(self, QMessageBox.Icon.Information, self.tr("语言"),
                 self.tr("界面语言已保存，请关闭程序后重新打开生效。"))

    def _show_about(self):
        """关于: 版本 + 三个路径 + 许可提醒。版本号只在这里出现, 不做常驻显示。"""
        import platform

        from PySide6 import __version__ as pyside_version

        from xiangshuiyin import __version__ as app_version
        # 正文带插值, 所以写成"模板 + format"并配 NOOP(见 i18n 模块说明); 标题也要 tr
        _message(self, QMessageBox.Icon.Information, self.tr("关于"),
                 self.tr(QT_TRANSLATE_NOOP(
                     "MainWindow",
                     "镶 · 水印（Qt 版）  {version}\n"
                     "Python {py} · PySide6 {pyside}\n{os}\n\n"
                     "程序目录：{base}\n数据目录：{data}\n日志目录：{logs}\n预览目录：{preview}\n\n"
                     "分发时请遵守 PySide6 的 LGPLv3（详见仓库 README 与 THIRD_PARTY_LICENSES）。"))
                 .format(version=app_version, py=platform.python_version(), pyside=pyside_version,
                         os=f"{platform.system()}-{platform.release()}", base=core.BASE_DIR,
                         data=core.DATA_DIR, logs=core.LOG_DIR, preview=core.PREVIEW_DIR))

    def _open_log_dir(self):
        _open_and_report(self, core.LOG_DIR, "日志目录")

    def _open_data_dir(self):
        _open_and_report(self, core.DATA_DIR, "数据目录")

    def _show_image_info(self):
        """「图片信息」弹窗: 照片里的 EXIF 与**最终会上水印的值**并排。

        并排是有用的: 勾了「自定义水印信息」之后, 品牌/型号/拍摄参数会被那份手动信息覆盖,
        单看 EXIF 分不清"我印出来的到底是哪个"。
        """
        path = getattr(self, "_preview_src", "")
        if not path or not os.path.isfile(path):
            self._status(self.tr("先选择一张图片"))
            return
        dlg = ImageInfoDialog(path, self._manual if self._manual_on else None,
                              self._effective_model, self)
        dlg.exec()

    def _build_preview_pane(self):
        """右侧预览面板: 标题条 + 预览框。

        **不要在预览框下面加任何控件**(以前这里是预览下方一行耗时 `_perf`): 那一行会让预览
        底边比左列(含滚动条)高 21~22px, 看上去"一高一低"。耗时数字现在住状态栏右侧
        (见状态栏构造处), 右列于是填满整个面板, 两侧底边对齐。

        标题条与左列页签块**等高**(都是 42px, 算法见 _build_preview_header), 间隔也用
        GAP_CARD —— 于是两块上下沿齐平, 预览框顶边也与左列第一张卡片齐平。代价是这
        42 + 10 = 52px 从预览高度里出, 是"两列顶部协调"换来的。

        左留白用 INSET_COL: 滚动条手柄两侧的"空气"要相等, 左边是"卡片宿主右留白(INSET_COL)
        + 手柄自己的 margin 2px", 右边就得是"手柄 margin 2px + 这里的 INSET_COL"; 不加这段
        预览会紧贴分隔条, 手柄看着贴向预览一侧(实测 12.4px vs 6.0px, 加上后两侧都是 12px)。
        """
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(theme.INSET_COL, 0, 0, 0)
        rl.setSpacing(theme.GAP_CARD)       # 与左列"块与块之间"同一节奏
        rl.addWidget(self._build_preview_header())
        rl.addWidget(self._preview, 1)
        return right

    def _build_preview_header(self):
        """预览标题条: 一张圆角小块(与左列卡片同一套底/边/圆角), 里面左「效果预览」右「↺ 刷新预览效果」。

        为什么是"块"而不是一行普通文字: 左列顶部本来就是一张圆角卡片(页签行), 右列顶端裸着
        放一行字会显得两块不同源。

        **高度与左列页签块严格相等(42px)** —— 左边是 6px 内边距 + 28px 按钮 + 6px 内边距
        + 1px 边框 ×2, 这里用同一套算法(竖向 6px 内边距 + 同一个 28px 高的迷你按钮), 于是
        两块的上沿、下沿在同一条线上。这是拿预览高度换来的(42px + GAP_CARD = 52px), 是
        "两列顶部协调"的代价; 想省下来就得让标题条浮在预览之上(不占布局高度), 见 README。
        按钮与卡片里的「↺ 重置本组」同款(#mini: 11px、MUTED 色、可见边框)。
        **不挂投影**(其它卡片都有): 右列顶端没有留白余量, 投影会被裁掉。
        """
        self._preview_header = QFrame()
        self._preview_header.setObjectName("card")
        h = QHBoxLayout(self._preview_header)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(theme.GAP_CTRL)
        title = QLabel(self.tr("效果预览"))
        title.setObjectName("cardTitle")
        h.addWidget(title)
        h.addStretch(1)
        self._refresh_btn = QPushButton(self.tr("↺ 刷新预览效果"))
        self._refresh_btn.setObjectName("mini")     # 与卡片里的「↺ 重置本组」同一款
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._refresh_preview_clicked)
        h.addWidget(self._refresh_btn)
        return self._preview_header

    def _build_bottombar(self):
        bottom = QFrame()
        b = QHBoxLayout(bottom)
        b.setContentsMargins(theme.INSET_BAR, 0, 0, 0)   # 与顶栏、状态栏同一条左对齐线
        b.setSpacing(8)
        b.addWidget(QLabel(self.tr("导出到")))
        self._out_label = ElidedLabel(self.tr("(跟随原图目录)"))
        self._out_label.setObjectName("muted")
        b.addWidget(self._out_label, 1)
        pick_out = QPushButton(self.tr("选择输出目录"))
        pick_out.clicked.connect(self._pick_output)
        b.addWidget(pick_out)
        self._progress = QProgressBar()
        self._progress.setFixedWidth(200)
        self._progress.setVisible(False)
        b.addWidget(self._progress)
        self._gen_btn = QPushButton(self.tr("开始生成"))
        self._gen_btn.setObjectName("primary")
        self._gen_btn.setMinimumWidth(130)
        self._gen_btn.clicked.connect(self.start_generate)
        b.addWidget(self._gen_btn)
        return bottom

    def _build_tab(self, tab_id):
        specs = [s for s in PARAM_SPECS if s["tab"] == tab_id]
        area = QScrollArea()
        area.setWidgetResizable(True)
        # 滚动条常驻: 内容不满一屏时也保留占位, 卡片宽度不会跳(_segbar_row 按同一宽度对齐)。
        # "常驻"只保占位, 不保可见 —— 不可滚动时由 _wire_scrollbar 把 handle 画成透明,
        # 否则范围 0 会把 handle 拉满整条轨道, 变成一条从顶到底的实心长条。
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._wire_scrollbar(area.verticalScrollBar())
        self._wire_scrollbar(area.horizontalScrollBar())
        host = QWidget()
        v = QVBoxLayout(host)
        # 左右留白在视口之内 —— 同时充当卡片投影的外扩余量(贴边界会被裁成直角)
        v.setContentsMargins(theme.INSET_COL, 0, theme.INSET_COL, theme.GAP_ROW + 4)
        v.setSpacing(theme.GAP_CARD)

        if tab_id == "files":
            for builder in (self._build_mode_card, self._build_model_card,
                            self._build_manual_card, self._build_preset_card):
                v.addWidget(builder())

        card_order = []
        for s in specs:
            if s["card"] not in card_order:
                card_order.append(s["card"])
        for name in card_order:
            special = next((s.get("special") for s in specs
                            if s["card"] == name and s.get("special")), None)
            if special:
                v.addWidget(getattr(self, special)())
                continue
            card = Card(name)
            card.reset_btn.clicked.connect(lambda _c, cd=card: self._reset_card(cd))
            for s in specs:
                if s["card"] != name:
                    continue
                init = self._params.get(s["key"])
                if init is None and s.get("mirror"):
                    init = self._params.get(s["mirror"][0])
                row = ParamRow(s, init)
                row.changed.connect(self._on_param)
                row.hovered.connect(self._preview.set_highlight)
                if s["kind"] == "color":
                    row.colorClicked.connect(lambda r=row: self._open_color_dialog(r))
                self._rows[s["key"]] = row
                card.keys.append(s["key"])
                card.body.addWidget(row)
            self._cards.append(card)
            v.addWidget(card)

        v.addStretch(1)
        area.setWidget(host)
        return area

    def _wire_scrollbar(self, sb):
        """让滚动条"不可滚动时把手柄画成透明"。

        滚动条是常驻的(占位不变 -> 卡片宽度不跳), 但范围为 0 时 Qt 会把 handle 拉满整条轨道,
        看起来就是一条从顶到底的实心长条。这里按范围切一个动态属性, QSS 用
        `::handle[emptyRange="true"] { background: transparent; }` 把它画成透明 —— 平时看不见,
        一旦内容真的溢出就恢复成深色 6px 手柄(好定位)。**占位宽度始终保留**, 不是 AsNeeded,
        所以不会出现"滚动条一出现卡片就窄 10px"的跳动。
        """
        def _on_range(lo, hi, bar=sb):
            empty = hi <= lo
            if bar.property("emptyRange") == empty:
                return
            bar.setProperty("emptyRange", empty)
            bar.style().unpolish(bar)      # 改动态属性后必须重新 polish, 否则 QSS 不重算
            bar.style().polish(bar)
        sb.rangeChanged.connect(_on_range)
        _on_range(sb.minimum(), sb.maximum())   # 初始态: 首次布局前范围是 0, 先按"空"起

    def _segbar_row(self):
        """页签行: 右侧留出竖向滚动条的宽度, 让页签卡片与下方卡片左右都对齐。

        卡片的可见宽度 = 列宽 - 滚动条宽(滚动区在 stack 里), 所以页签行必须补上这段,
        否则页签卡片会比下方卡片宽出一整条滚动条。
        """
        row = QWidget()
        h = QHBoxLayout(row)
        # 与 _build_tab 的卡片宿主同一个横向留白: 两者左边缘对齐, 且都在视口之内
        h.setContentsMargins(theme.INSET_COL, 0, theme.INSET_COL, 0)
        h.setSpacing(0)
        h.addWidget(self._build_segbar(), 1)
        h.addSpacing(_SB_W)
        return row

    def _unify_buttons(self):
        """所有按钮统一高度。

        原来三种高度并存: 普通按钮 29px、#mini(重置本组/复制原始型号) 22px、#step(−/+) 23px,
        同一张卡片里并排就参差不齐。宽度仍按各自需要(#step/#mini 另有限制)。
        """
        for b in self.findChildren(QPushButton):
            b.setFixedHeight(theme.CTRL_H)

    def _unify_spins(self):
        """所有数值框统一宽度 + 数值居中。

        之前每行按"自己的最大值"算宽度, 于是 int 行(如 150)窄、pct 行(如 100.0 %)宽,
        同一张卡片里并排看就是长短不一。这里按全表最宽的可能取值算一个宽度, 全用同一个。
        """
        rows = [r for r in self._rows.values() if hasattr(r, "spin")]
        if not rows:
            return
        fm = rows[0].spin.fontMetrics()
        widest = 0
        for r in rows:
            # 取该行可能出现的最大显示值(整数), 全表按最宽的那个算 —— 以前按 "100.0 %" 这种
            # 带后缀的文本算, 现在都是纯整数, 宽度自然收窄
            widest = max(widest, fm.horizontalAdvance(str(r._disp_hi)))
        width = widest + 26
        for r in rows:
            r.spin.setFixedWidth(width)
            r.spin.setAlignment(Qt.AlignCenter)
        # 字体路径行的「选择字体」按钮: 左边缘对齐数值框、右边缘对齐右侧的 + 按钮,
        # 也就是整组宽度 = 步进按钮 + 间距 + 数值框 + 间距 + 步进按钮
        step_w = rows[0]._step_w
        grp = 2 * (step_w + 3) + width      # 3 = 步进器组内部的 spacing
        for r in self._rows.values():
            if getattr(r, "kind", "") == "path" and hasattr(r, "_path_btn"):
                r._path_btn.setFixedWidth(grp)
            # 颜色行的色块不用管: 它自己铺满控件区(拉伸), 右边缘天然落在步进器组那一列

    def _build_segbar(self):
        """页签做成「分段控件」(): 外层一张卡片, 里面三个胶囊。

        不用 QTabWidget: 它的 tab 条与 pane 是两套几何(直角 tab + 圆角 pane, 接缝处
        对不齐), 而且 tab 条右侧的空白由样式的 Base 色填充, 会露出一块与主题不符的纯白,
        在预览区左侧看起来像"多出来一块"。
        """
        wrap = QFrame()
        wrap.setObjectName("segbar")
        h = QHBoxLayout(wrap)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(4)
        self._tab_btns = []
        for i, (_tab_id, title) in enumerate(TABS):
            b = QPushButton(self.tr(title))
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c, k=i: self.set_tab(k))
            h.addWidget(b)
            self._tab_btns.append(b)
        h.addStretch(1)
        # 撤销 / 重做: 放在页签卡**内部**的右端(那里本来就有一个 addStretch) —— 卡宽与行高都不变,
        # 页签卡与下方参数卡依旧严格等宽(README 里那条不变量)
        self._undo_btn = QPushButton("↶")
        self._redo_btn = QPushButton("↷")
        for b, slot in ((self._undo_btn, self.undo), (self._redo_btn, self.redo)):
            b.setObjectName("mini")
            b.setFixedWidth(28)
            b.setEnabled(False)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            h.addWidget(b)
        self._sync_undo_btns()
        return wrap

    # 供外部(截图工具/测试)使用的页签接口, 替代原先的 QTabWidget API
    def tab_titles(self):
        return [t for _id, t in TABS]

    def set_tab(self, index):
        if not 0 <= index < len(self._tab_btns):
            return
        for i, b in enumerate(self._tab_btns):
            b.setChecked(i == index)
        self._stack.setCurrentIndex(index)

    def _build_font_style_card(self):
        """字体样式: 行=文字种类, 列=显示/粗体/斜体。

        原来做成「Logo 文字 / └ 粗体 / └ 斜体 / 型号文字 / …」的平铺复选列表,
        三层归属全靠 └ 前缀暗示, 读起来是一串没有结构的复选框。改成网格后
        """
        card = Card(QT_TRANSLATE_NOOP("Card", "字体样式"))
        card.reset_btn.clicked.connect(lambda _c, cd=card: self._reset_card(cd))
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        hsp = 22          # 网格列间距(比行内间距大些, 三列勾选框挨太紧不好点)
        grid.setHorizontalSpacing(hsp)
        grid.setVerticalSpacing(5)
        # 第 0 列宽度按"让第一列控件落在其它卡片的控件列上"反推:
        #   本卡: 12 + col0 + hsp   其它卡片: 12 + _LABEL_W + GAP_CTRL
        # 自适应的话 col0 只有约 85px, 网格里的列会比其它卡片靠左, 同一页两条缩进线不齐
        grid.setColumnMinimumWidth(0, _LABEL_W + theme.GAP_CTRL - hsp)
        for col, head in enumerate(FONT_STYLE_COLS, start=1):
            h = QLabel(head)
            h.setObjectName("hint")
            h.setAlignment(Qt.AlignCenter)
            grid.addWidget(h, 0, col)
        for r, (name, keys) in enumerate(FONT_STYLE_GROUPS, start=1):
            lab = QLabel(name)
            lab.setObjectName("fieldLabel")
            grid.addWidget(lab, r, 0)
            for c, key in enumerate(keys, start=1):
                # 标签给空格(见 ParamRow: 空标签的 QCheckBox 不画方框), 语义靠无障碍名与提示补上
                row = ParamRow(dict(key=key, label=" ", kind="bool"), self._params.get(key))
                row.hovered.connect(self._preview.set_highlight)
                row.chk.setAccessibleName(f"{name}·{FONT_STYLE_COLS[c - 1]}")
                row.chk.setToolTip(f"{name} · {FONT_STYLE_COLS[c - 1]}")
                row.changed.connect(self._on_param)
                self._rows[key] = row
                card.keys.append(key)
                grid.addWidget(row, r, c, Qt.AlignmentFlag.AlignCenter)
        grid.setColumnStretch(len(FONT_STYLE_GROUPS[0][1]) + 1, 1)
        card.body.addLayout(grid)
        self._cards.append(card)
        return card

    # ---------- 「文件与美化」页签的四张卡片 ----------
    def _row(self, label, widget, label_w=_LABEL_W):
        """一行 [定宽标签 + 控件], 返回容器。卡片里的非参数行(路径/按钮)都用它对齐。"""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(theme.GAP_CTRL)
        lab = QLabel(self.tr(label))          # 卡片里的行标签都从这儿过
        lab.setObjectName("fieldLabel")
        lab.setFixedWidth(label_w)
        lab.setWordWrap(True)
        h.addWidget(lab)
        if isinstance(widget, (list, tuple)):
            for i, sub in enumerate(widget):
                h.addWidget(sub, 1 if i == 0 else 0)
        else:
            h.addWidget(widget, 1)
        return w

    def _path_label(self, text=""):
        lab = ElidedLabel(text)
        lab.setObjectName("muted")
        return lab

    def _build_mode_card(self):
        card = Card(QT_TRANSLATE_NOOP("Card", "处理模式"))
        card.reset_btn.setVisible(False)
        row = QWidget()
        rh = QHBoxLayout(row)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(6)
        self._mode_btns = {}
        for key, text in (("single", QT_TRANSLATE_NOOP("MainWindow", "单张图片")),
                          ("batch", QT_TRANSLATE_NOOP("MainWindow", "批量目录"))):
            b = QPushButton(self.tr(text))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c, k=key: self._set_mode(k))
            self._mode_btns[key] = b
            rh.addWidget(b)
        rh.addStretch(1)
        card.body.addWidget(row)

        # 这里不再放「选择图片」按钮 —— 与顶部栏的「打开图片」功能重复, 只保留顶部那个;
        # 路径显示留着(顶栏只显示文件名, 这里能看到完整路径, 且可选中复制)
        self._input_val = self._path_label(self.tr("未选择图片"))
        self._row_single = self._row(QT_TRANSLATE_NOOP("MainWindow", "输入图片"), self._input_val)
        card.body.addWidget(self._row_single)

        self._batch_val = self._path_label(self.tr("未选择目录"))
        self._row_batch = self._row(QT_TRANSLATE_NOOP("MainWindow", "图片目录"), [
            self._batch_val, self._btn(self.tr("选择文件夹"), self._pick_batch_dir)])
        card.body.addWidget(self._row_batch)

        self._recursive_chk = QCheckBox(self.tr("包含子文件夹"))
        self._recursive_chk.setCursor(Qt.PointingHandCursor)
        self._recursive_chk.setChecked(self._batch_recursive)
        self._recursive_chk.toggled.connect(self._on_recursive)
        self._row_batch_rec = self._row("", self._recursive_chk)
        card.body.addWidget(self._row_batch_rec)

        self._batch_count = QLabel("")
        self._batch_count.setObjectName("hint")
        card.body.addWidget(self._batch_count)
        return card

    def _btn(self, text, slot, mini=False):
        b = QPushButton(text)
        if mini:
            b.setObjectName("mini")
        b.clicked.connect(slot)
        return b

    def _build_model_card(self):
        card = Card(QT_TRANSLATE_NOOP("Card", "型号美化"))
        card.reset_btn.setVisible(False)
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText(self.tr("临时美化名（本次生效）"))
        self._model_edit.setToolTip(self.tr("留空则用映射表；填了本次强制使用这个名字"))
        self._model_edit.textChanged.connect(self._on_custom_model)
        self._map_btn = self._btn(self.tr("管理所有型号映射"), self._open_map_dialog)
        card.body.addWidget(self._row(QT_TRANSLATE_NOOP("MainWindow", "美化型号"), [self._model_edit, self._map_btn]))
        # 「识别型号」= 照片里识别到的型号(与「美化型号/映射表」无关); 水印最终会印什么由
        # tooltip 给出 —— 这样"识别"与"美化后"两件事都在, 又不占两行(以前顶栏与这里各显示一份,
        # 看着像重复, 用户报过)。
        self._model_lab = ElidedLabel("—")
        # 不用 muted: 识别到的型号是"拿这张图会印什么"的依据, 以前它在顶栏是加粗深色的,
        # 搬进卡片不该降格成灰字(其余行的值仍是 muted, 那是惯例, 这一行是例外)
        self._model_lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._copy_btn = self._btn(self.tr("复制原始型号"), self._copy_raw_model)
        card.body.addWidget(self._row(QT_TRANSLATE_NOOP("MainWindow", "识别型号"),
                                      [self._model_lab, self._copy_btn]))
        return card

    def _update_effective_model(self):
        """刷新「识别型号」那一行(以及"水印里会显示什么"的说明)。

        `self._effective_model` 才是**水印最终会印的名字** —— 与渲染端 _inject_model_map +
        prettify_model 同一套规则: 临时美化名优先, 其次映射表, 最后回落原始型号。它现在只出现在
        tooltip 与「图片信息」弹窗里(那一行显示的是**识别到的**型号, 两件事不混着说)。
        """
        raw = self._raw_model
        if self._custom_model and self._custom_model != raw:
            name = self._custom_model
        else:
            try:
                name = core.prettify_model(raw, self._params.get("model_map") or {})
            except Exception:
                name = raw
        self._effective_model = name or ""
        self._model_lab.setText(raw or "—")
        tips = [self.tr("品牌：{brand}").format(brand=self._raw_brand)] if self._raw_brand else []
        if name:
            tips.append(self.tr("水印里会显示：{name}").format(name=name))
        self._model_lab.setToolTip("\n".join(tips))
        # 「复制原始型号」: 没有识别到型号(没选图 / 图里没 EXIF)时置灰 —— 以前它一直是 #mini 的
        # 灰字样式, 明明可点却看着像禁用了(用户报过), 现在改成普通按钮 + 真的按状态置灰
        self._copy_btn.setEnabled(bool(raw))
        self._copy_btn.setToolTip(self.tr("把识别到的型号复制到剪贴板") if raw
                                  else self.tr("还没有识别到型号（先选一张图片）"))

    def _build_manual_card(self):
        card = Card(QT_TRANSLATE_NOOP("Card", "自定义水印信息"))
        card.reset_btn.setVisible(False)
        self._manual_chk = QCheckBox(self.tr("启用（启用后强制使用下列信息，忽略照片 EXIF）"))
        self._manual_chk.setCursor(Qt.PointingHandCursor)
        self._manual_chk.toggled.connect(self._on_manual_toggle)
        card.body.addWidget(self._manual_chk)

        self._manual_edits = {}
        # 挤成两行多列、标签宽度还不一致, 看着乱; 竖排后标签列与其它卡片对齐, 也更好读。
        # 文案走 _row/QLineEdit, lupdate 看不到变量, 所以用 NOOP 把字面量摆出来(context 与
        # 运行时查表的 context 一致: _row 是 MainWindow.tr, 这里也是)
        specs = (("make", QT_TRANSLATE_NOOP("MainWindow", "品牌"),
                  QT_TRANSLATE_NOOP("MainWindow", "如 Canon"),
                  QT_TRANSLATE_NOOP("MainWindow", "原样输出")),
                 ("model", QT_TRANSLATE_NOOP("MainWindow", "型号"),
                  QT_TRANSLATE_NOOP("MainWindow", "如 EOS R5"),
                  QT_TRANSLATE_NOOP("MainWindow", "原样输出")),
                 ("exposure", QT_TRANSLATE_NOOP("MainWindow", "快门"),
                  QT_TRANSLATE_NOOP("MainWindow", "如 1/125"),
                  QT_TRANSLATE_NOOP("MainWindow", "自动补 s 后缀")),
                 ("fnumber", QT_TRANSLATE_NOOP("MainWindow", "光圈"),
                  QT_TRANSLATE_NOOP("MainWindow", "如 2.8"),
                  QT_TRANSLATE_NOOP("MainWindow", "自动补 f/ 前缀")),
                 ("iso", "ISO", QT_TRANSLATE_NOOP("MainWindow", "如 100"),
                  QT_TRANSLATE_NOOP("MainWindow", "自动补 ISO 前缀")))
        for key, label, ph, tip in specs:
            e = QLineEdit()
            e.setPlaceholderText(self.tr(ph))
            e.setToolTip(self.tr(tip))
            e.setEnabled(False)
            e.textChanged.connect(lambda t, k=key: self._on_manual_field(k, t))
            self._manual_edits[key] = e
            card.body.addWidget(self._row(label, e))
        return card

    def _build_preset_card(self):
        # 免得为一行 Logo 目录再占一张卡片的高度
        # Logo 目录那一行收进了 ☰ 菜单(用户: "logo目录及打开按钮, 收纳进菜单中") ——
        # 卡片名字也回到「预设方案」: 它现在只装预设了
        card = Card(QT_TRANSLATE_NOOP("Card", "预设方案"))
        card.reset_btn.setVisible(False)
        self._preset_combo = NoWheelComboBox()
        self._preset_name = QLineEdit()
        self._preset_name.setPlaceholderText(self.tr("方案名（保存时必填）"))
        card.body.addWidget(self._row(QT_TRANSLATE_NOOP("MainWindow", "槽位"), self._preset_combo))
        card.body.addWidget(self._row(QT_TRANSLATE_NOOP("MainWindow", "名称"), [
            self._preset_name,
            self._btn(self.tr("加载"), self._preset_load),
            self._btn(self.tr("保存"), self._preset_save),
            self._btn(self.tr("删除"), self._preset_delete)]))
        self._reload_presets()
        return card

    def _open_logo_dir(self):
        """

        这个目录**不会被自动创建**(ensure_dir 只管日志/字体/预览三个), 所以第一次点它时先
        """
        path = self._params.get("logo_dir") or core.DEFAULT_LOGO_DIR
        try:
            os.makedirs(path, exist_ok=True)        # 已存在时不报错
        except Exception as e:                      # noqa: BLE001 - 建不出来就退化成"报一句"
            self._status(self.tr("Logo 目录不可用：{err}").format(err=e))
            logging.warning(f"[目录] 创建 Logo 目录失败: {path} ({e})")
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self._status(self.tr("已打开 Logo 目录：{path}").format(path=path))
        else:
            self._status(self.tr("打不开，Logo 目录是：{path}").format(path=path))

    def _reload_presets(self):
        try:
            self._preset_data = self.state.load_presets() or {}
        except Exception:
            self._preset_data = {}
        cur = self._preset_combo.currentData()
        self._preset_combo.clear()
        for i in range(1, _PRESET_SLOTS + 1):
            key = str(i)
            p = self._preset_data.get(key) or {}
            name = p.get("_preset_name") if isinstance(p, dict) else None
            self._preset_combo.addItem(self.tr("槽位 {slot}：{name}").format(
                slot=i, name=name or self.tr("— 空 —")), key)
        if cur:
            j = self._preset_combo.findData(cur)
            if j >= 0:
                self._preset_combo.setCurrentIndex(j)

    def _preset_load(self):
        key = self._preset_combo.currentData()
        p = self._preset_data.get(key) if key else None
        if not isinstance(p, dict):
            self._status(self.tr("该槽位为空，无法加载"))
            return
        name = p.get("_preset_name") or key
        # 里没有 confirm), 这里先问一句, 与「恢复默认设置」「删除预设」同一个形态。
        if _message(self, QMessageBox.Icon.Question, self.tr("加载预设"),
                    self.tr("将把当前所有参数覆盖为预设「{name}」的值。继续？").format(name=name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._undo_step("__preset_load__", f"加载预设「{name}」",
                        lambda: self._apply_params(migrate_preset(p)))
        self._preset_name.setText(p.get("_preset_name") or "")
        self._status(self.tr("已加载预设：{name}").format(name=name))

    def _preset_payload(self):
        """存进预设的那份参数(与 settings 的口径**刻意不同**)。

        - 带上 `canvas_ratio_mode`: 内核只认数值 `canvas_ratio`, 而 `_params` 里根本没有这个键
          (它由 UI 的档位下拉推出来), 不存档位的话加载时会被 `_apply_params` 整条跳过 ——
          这一条是本移植漏掉的。
        - 带上 `logo_dir` / 4 个字体路径: 同一台机器上"存→取"要能还原字体, 这是常见用法。
          **分享出去才需要剥掉** —— 见 `_export_preset` 与 `_PRESET_MACHINE_KEYS`。
        - 不带 mode(单张/批量不是方案的一部分)、不带 input/output 路径(本来就不在 _params 里)。
        """
        p = dict(self._params)
        p["canvas_ratio_mode"] = self._params.get("canvas_ratio_mode", "original")
        return p

    def _preset_save(self):
        key = self._preset_combo.currentData()
        if not key:
            return
        name = self._preset_name.text().strip()
        if not name:
            self._status(self.tr("请先输入方案名再保存"))
            return
        self.state.preset_save(key, self._preset_payload(), name)
        self._reload_presets()
        self._preset_combo.setCurrentIndex(self._preset_combo.findData(key))
        self._status(self.tr("已保存到槽位 {slot}：{name}").format(slot=key, name=name))

    def _export_preset(self):
        """把当前槽位的方案导出成一个 .json(给别人用)。

        导出的是**剥掉机器路径**的那份: logo_dir 与 4 个字体路径换台机器就是死链, 带出去只会
        让对方拿到一个静默降级的方案(品牌 logo 变文字、字体变内置 Roboto)。
        """
        key = self._preset_combo.currentData()
        rec = self._preset_data.get(key) if key else None
        if not isinstance(rec, dict):
            self._status(self.tr("该槽位为空，没有可导出的方案"))
            return
        name = rec.get("_preset_name") or f"方案 {key}"
        path = _save_file_dialog(self, self.tr("导出预设"), "", f"{_safe_filename(name)}.json",
                                 self.tr("预设 (*.json)"))
        if not path:
            return
        data = {k: v for k, v in rec.items() if k not in _PRESET_MACHINE_KEYS}
        data["_format"] = _PRESET_FORMAT
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:                      # noqa: BLE001 - 写不出去就报一句, 不弹窗
            self._status(self.tr("导出失败：{err}").format(err=e))
            logging.warning(f"[预设] 导出失败 {path}: {e}")
            return
        logging.info(f"[预设] 已导出槽位 {key} -> {path} (剔除机器路径)"
                     f" 共 {len(data)} 键")
        self._status(self.tr("已导出方案「{name}」").format(name=name))

    def _import_preset(self):
        """从一个 .json 导入方案到**你指定的槽位**。

        两条刻意的取舍:
          - 目标槽位由你在弹窗里选(不默认第一个): 第一个槽往往已经存着东西;
          - 文件里的机器路径键一律忽略(不吃别人的绝对路径): 导入只吃"方案参数"。
        """
        path = _open_file_dialog(self, self.tr("导入预设"), "",
                                 self.tr("预设 (*.json);;所有文件 (*)"))
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:                      # noqa: BLE001
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"),
                     self.tr("读不了这个文件：\n{err}").format(err=e))
            return
        if not isinstance(data, dict) or not data:
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"), self.tr("这个文件里没有方案数据。"))
            return
        # 只吃"单条记录"。presets.json 那种 slot-keyed 的多方案文件要求先选一条, 这一版不做 ——
        # 与其猜, 不如明确拒绝(判据是"有没有方案参数键")
        if not any(k in data for k in ("bg_color", "canvas_ratio_mode", "canvas_ratio",
                                      "font_path", "layout_mode")):
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"),
                     self.tr("这个文件里不像是单个方案（可能是整份 presets.json）。"
                             "请用「导出预设」得到单个方案文件后再导入。"))
            return
        name = str(data.get("_preset_name") or os.path.splitext(os.path.basename(path))[0])
        payload = {k: v for k, v in data.items()
                   if k not in _PRESET_MACHINE_KEYS and k != "_format"}
        slot = ask_slot(self, self._preset_data, name)
        if not slot:
            return
        cur = self._preset_data.get(slot) or {}
        cur_name = cur.get("_preset_name") if isinstance(cur, dict) else None
        if cur_name and _message(self, QMessageBox.Icon.Question, self.tr("导入预设"),
                                 self.tr("槽位 {slot} 已有方案「{name}」，导入会覆盖它。继续？")
                                 .format(slot=slot, name=cur_name),
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.state.preset_save(slot, migrate_preset(payload), name)
        self._reload_presets()
        self._preset_combo.setCurrentIndex(self._preset_combo.findData(slot))
        logging.info(f"[预设] 已导入 {path} -> 槽位 {slot}（{'覆盖' if cur_name else '空槽'}）")
        self._status(self.tr("已导入方案「{name}」到槽位 {slot}").format(name=name, slot=slot))

    def _preset_delete(self):
        key = self._preset_combo.currentData()
        if not key or not isinstance(self._preset_data.get(key), dict):
            self._status(self.tr("该槽位为空，无需删除"))
            return
        name = self._preset_data[key].get("_preset_name") or key
        if _message(self, QMessageBox.Icon.Question, self.tr("删除预设"),
                    self.tr("确定删除「{name}」？").format(name=name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.state.preset_delete(key)
        self._reload_presets()
        self._preset_name.clear()
        self._status(self.tr("已删除槽位 {slot}：{name}").format(slot=key, name=name))

    def _apply_params(self, src):
        """把一整份参数铺回(加载预设 / 恢复默认用)。只吃已知键, 未知键忽略。

        **按参数遍历, 不是按控件遍历**: 有些参数没有控件(如 gap_image_top/gap_top, 它们
        只由上下分离下的镜像行代写), 按控件遍历会把它们漏掉 —— 表现为"加载预设后某些间距
        """
        changed = set()
        for key, val in src.items():
            if key not in self._params:            # 未知键(如预设里的 _preset_name)忽略
                continue
            self._params[key] = val
            changed.add(key)
            row = self._rows.get(key)
            if row is not None:
                row.set_value(val)
        self._sync_mirror_rows(changed)
        self._sync_visibility()
        self._touch()

    # ---------- 参数持久化 ----------
    def _collect_settings(self):
        s = {k: v for k, v in self._params.items()
             if k in _BACKEND_KEYS and k not in _NOT_PERSISTED}
        s["canvas_ratio_mode"] = self._params.get("canvas_ratio_mode", "original")
        s["mode"] = self._mode
        return s

    def _save_settings_now(self):
        try:
            # **合并**而不是整体替换: custom_colors 这类键不归参数面板管(由取色弹窗写入),
            # 整体替换会把它们从内存和 settings.json 里一起抹掉 —— 表现就是"保存的颜色
            # 下次再存就互相覆盖, 只剩最新的一个"。
            s = dict(self.state.settings or {})
            s.update(self._collect_settings())
            self.state.settings = s
            core.save_settings(s)                       # 原子写, 见核心 _save_json
        except Exception as e:                          # noqa: BLE001 - 存不上不该影响使用
            self._status(self.tr("自动保存失败：{err}").format(err=e))

    def _touch(self):
        """参数变更的统一出口: 排一次保存 + 刷预览。"""
        self._save_timer.start()
        self.request_preview()

    def _open_color_dialog(self, row):
        """点「选择颜色」开弹窗(与「管理所有型号映射」同一形态)。弹窗里点色块即时应用。"""
        dlg = ColorDialog(self.state, row.value(), self)
        dlg.picked.connect(lambda c, r=row: (r.set_value(c), self._on_param(r.key, c)))
        dlg.exec()
        self.request_preview()

    def _open_map_dialog(self):
        dlg = MapDialog(self.state, self)
        dlg.exec()
        # 映射改过之后, 用新表重算美化型号
        def _do():
            self._reload_model_map()
            self._touch()

        # 映射表也进撤销(它就在 _params["model_map"] 里); 整批算一步
        self._undo_step("__model_map__", "型号映射表", _do)

    def _reset_settings(self):
        """

        注意 logo_dir 不在参数行里(没有控件), 得单独从 default_params 取回。
        """
        if _message(self, QMessageBox.Icon.Question, self.tr("恢复默认设置"),
                    self.tr("将把所有参数恢复为出厂默认，当前的调参会被覆盖。继续？"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        d = core.default_params()

        def _do():
            self._apply_params(d)
            self._params["logo_dir"] = d.get("logo_dir") or self._params.get("logo_dir")
            self._reload_model_map()
            self._preset_name.clear()

        # 整批算一步撤销(快照里连 logo_dir / model_map 一起存, 所以撤得回来)
        self._undo_step("__reset_settings__", "恢复默认设置", _do)
        self._status(self.tr("已恢复默认设置"))

    def _reload_model_map(self):
        try:
            self._params["model_map"] = core.load_model_map(
                self._params.get("logo_dir") or core.DEFAULT_LOGO_DIR)
        except Exception:
            self._params["model_map"] = {}
        self._update_effective_model()

    # ---------- 处理模式 / 批量 ----------
    def _set_mode(self, mode):
        self._mode = mode
        for k, b in self._mode_btns.items():
            b.setChecked(k == mode)
        self._sync_files_ui()
        if self._sync_preview_source():
            self.request_preview()

    def _desired_preview_source(self):
        if self._mode == "single":
            return self._input_path
        return self._batch_files[0]["path"] if self._batch_files else ""

    def _sync_preview_source(self):
        """预览取样图: 单张=选中的图, 批量=批量第一张, **都没有时沿用上一次的源**。

        从单张切到还没载入文件的批量模式会把预览清空, 再切回来又要重渲一次出空档。
        返回 True 表示取样图变了、需要重渲。
        """
        want = self._desired_preview_source() or getattr(self, "_preview_src", "")
        if want == getattr(self, "_preview_src", ""):
            return False
        self._preview_src = want
        self._sync_refresh_btn()        # 「刷新预览效果」只在有图可预览时可点
        self._sync_info_btn()           # 「ℹ 图片信息」同理
        if not want:
            self._preview.clear()
            self._preview.set_hint(self.tr("拖入图片，或点「打开图片」"))
        return True

    def _sync_files_ui(self):
        single = self._mode == "single"
        self._row_single.setVisible(single)
        self._row_batch.setVisible(not single)
        self._row_batch_rec.setVisible(not single)
        self._batch_count.setVisible(not single)
        if not single and self._batch_files:
            self._batch_count.setText(self.tr("已载入 {n} 张（预览与调参取样第一张）")
                                      .format(n=len(self._batch_files)))
        elif not single:
            self._batch_count.setText(self.tr("尚未载入图片"))

    def _on_recursive(self, on):
        self._batch_recursive = bool(on)

    def _pick_batch_dir(self):
        folder = _open_dir_dialog(self, self.tr("选择图片文件夹"), self._batch_dir or "")
        if folder:
            self._load_batch_dir(folder)

    def _load_batch_dir(self, folder):
        cand = core._list_images_in_dir(folder, recursive=self._batch_recursive)
        images = [p for p in cand if self.state._sniff_image_type(p)]
        if not images:
            self._status(self.tr("所选目录里没有受支持的图片")
                         + (self.tr("（含子目录）") if self._batch_recursive
                            else self.tr("（可勾选「包含子文件夹」）")))
            return
        self.load_batch([{"path": p, "name": os.path.basename(p)} for p in images], folder)

    def load_batch(self, items, folder=""):
        self._batch_files = items
        self._batch_dir = folder or os.path.dirname(items[0]["path"])
        self._batch_val.setText(self._batch_dir)
        self._batch_val.setToolTip(self._batch_dir)
        self._set_mode("batch")
        self._status(self.tr("已载入 {n} 张图片，可点「开始生成」").format(n=len(items)))

    # ---------- 预览 ----------
    def _manual_exif(self):
        return dict(self._manual) if self._manual_on else None

    def _refresh_preview_clicked(self):
        """

        自动预览之外的单入口: 渲染失败、或想对同一组参数再跑一次时用。点下去先按灰、等响应
        """
        if not getattr(self, "_preview_src", ""):
            self._status(self.tr("请先选择一张图片（也可直接拖入图片或文件夹）"))
            return
        self._manual_preview = True
        self._refresh_btn.setEnabled(False)
        self._status(self.tr("正在生成预览…"))
        self.request_preview()

    def _finish_manual_preview(self, ok):
        """手动刷新的收尾: 复位按钮 + 给一句结果。

        **只有手动点过才碰状态栏** —— 自动预览每次改参数都触发, 若也写一行"预览已生成",
        状态栏就会随滑块的拖动不停闪。
        """
        if not getattr(self, "_manual_preview", False):
            return
        self._manual_preview = False
        self._sync_refresh_btn()
        if ok:
            self._status(self.tr("预览已生成"))

    def _sync_refresh_btn(self):
        """没有可预览的图时把「刷新预览效果」按灰。

        常驻一句"请先选择图片"看着像出错了, 所以改成按钮自身置灰 + tooltip 说明。
        """
        if not hasattr(self, "_refresh_btn"):
            return
        has = bool(getattr(self, "_preview_src", ""))
        self._refresh_btn.setEnabled(has)
        self._refresh_btn.setToolTip(self.tr("立即按当前参数重渲一次预览") if has
                                     else self.tr("先选择一张图片（或拖入图片）再刷新预览"))

    def _sync_info_btn(self):
        """没图可看时把「ℹ 图片信息」按灰(与「刷新预览效果」同一个口径)。"""
        if not hasattr(self, "_info_btn"):
            return
        self._info_btn.setEnabled(bool(getattr(self, "_preview_src", "")))

    def request_preview(self):
        """请求一次预览。

        画布固定按核心的 2500 档渲染(与原项目一致): 预览里的水印字本来就只有几像素高
        (成品 4000px 高、预览框 720px 高 → 字缩到约 1/6), 想看清细节用放大镜(它按像素宽度
        从固有源重采样)。**不要为了"上屏 1:1"去缩小画布** —— 实测那样更糊, 原因见核心
        make_preview 里的注释。
        """
        if not getattr(self, "_preview_src", ""):
            return
        # 不防抖、不作废: 覆盖待渲染槽即可, 渲染线程每轮取最新一份
        self._preview_worker.request({
            "input_path": self._preview_src,
            "params": backend_params(self._params),
            "raw_model": self._raw_model,
            "custom_model": self._custom_model,
            "manual_exif": self._manual_exif(),
            # 直接要内存图像: 核心不落盘, 省掉 JPEG 编码/写盘/读回解码(约 95ms/次)
            "inline_image": True,
        })

    def _on_rendered(self, resp):
        if not resp.get("ok"):
            err = i18n.tr_msg(str(resp.get("error") or ""))
            self._finish_manual_preview(False)          # 手动刷新失败也要把按钮放回可点(重试入口)
            self._status(self.tr("预览失败：") + err)
            logging.warning(f"[预览] 渲染失败: {err}")   # 失败也要留痕(以前只在状态栏闪一下)
            return
        img = resp.get("image")
        if img is not None:
            self._preview.set_image(img)
        self._preview.set_geo(resp.get("geom"))
        e2e = (time.perf_counter() - resp.get("_req_t", time.perf_counter())) * 1000
        self._perf.setText(self.tr("渲染 {ms:.0f} ms · 请求→显示 {e2e:.0f} ms")
                          .format(ms=resp.get("ms", 0), e2e=e2e))
        self._finish_manual_preview(True)

        ms = resp.get("ms", 0.0)
        queue_ms = resp.get("queue_ms", 0.0)
        qimg_ms = resp.get("qimg_ms", 0.0)
        arrived = resp.get("arrived_during", 0)
        size = f"{img.width()}x{img.height()}" if img is not None else "-"
        parts = [f"[预览] 渲染 {ms:.0f}ms (排队 {queue_ms:.0f}ms, 转图 {qimg_ms:.0f}ms)",
                 f"画布 {size}",
                 f"档 {resp.get('max_side', '-')}",
                 f"请求→就绪 {e2e:.0f}ms"]
        if arrived:
            # 排队时间往往就是它造成的: 在跑的那一轮不会被中断, 新请求要等它跑完
            parts.append(f"渲染期间又到达 {arrived} 次请求(上一轮不可中断, 排队主要来自这里)")
        line = " | ".join(parts)
        if ms + queue_ms >= _SLOW_RENDER_MS:
            logging.warning(line + f" [偏慢: 渲染 {ms:.0f}ms]")
        else:
            logging.info(line)

        # 会话统计: 与界面上的 _stats 标签同一份数据源
        self._stats.add_preview(ms=ms, queue_ms=queue_ms, qimg_ms=qimg_ms, e2e_ms=e2e,
                                arrived=arrived)
        self._refresh_stats()
        # 上一次渲染的分段耗时挂到 tooltip: 参数改完不用去翻日志就知道时间花在哪一段
        st = resp.get("stage") or {}
        if st.get("stages"):
            # 分段名与参数指纹都来自核心(stage_out, 中文), 显示前逐条过 tr_msg
            self._perf.setToolTip(
                self.tr("渲染分段({tag}) 合计 {total:.0f}ms").format(
                    tag=i18n.tr_msg(st.get("tag", "")), total=st.get("total_ms", 0))
                + "\n" + "\n".join("· " + i18n.tr_msg(s) for s in st["stages"])
                + "\n\n" + self.tr("参数指纹:") + "\n" + str(st.get("sig", "")))
        else:
            self._perf.setToolTip("")

    # ---------- 自定义水印信息(手动 EXIF) ----------
    def _on_manual_toggle(self, on):
        self._manual_on = bool(on)
        for e in self._manual_edits.values():
            e.setEnabled(self._manual_on)
        self.request_preview()

    def _on_manual_field(self, key, text):
        self._manual[key] = text
        if self._manual_on:
            self.request_preview()

    # ---------- 输入 ----------
    def _pick_image(self):
        path = _open_file_dialog(
            self, self.tr("选择图片"), "",
            self.tr("图片 (*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff *.ico);;所有文件 (*)"))
        if path:
            self.set_input(path)

    def _pick_output(self):
        d = _open_dir_dialog(self, self.tr("选择导出文件夹"), self._output_dir or "")
        if d:
            self._set_output_dir(d, manual=True)

    def _set_output_dir(self, path, manual):
        self._output_dir = path
        self._out_label.setText(("" if manual else self.tr("(跟随原图) ")) + path)
        self._out_label.setToolTip(path)

    def _copy_raw_model(self):
        if self._raw_model:
            QApplication.clipboard().setText(self._raw_model)
            self._status(self.tr("已复制原始型号到剪贴板"))
        else:
            # 按钮在"没识别到型号"时是置灰的, 正常点不到; 这里兜一句免得静默无反应
            self._status(self.tr("还没有识别到型号（先选一张图片）"))

    def _on_custom_model(self, text):
        self._custom_model = text.strip()
        self._update_effective_model()
        self.request_preview()

    def set_input(self, path):
        if not os.path.isfile(path):
            self._status(self.tr("请拖入图片文件"))
            return
        if not self.state._sniff_image_type(path):
            self._status(self.tr("不是受支持的图片格式"))
            return
        self._input_path = path
        self._input_name = os.path.basename(path)
        self._input_label.setText(self._input_name)
        self._input_label.setToolTip(path)
        self._input_val.setText(path)
        self._input_val.setToolTip(path)
        self._set_output_dir(
            os.path.join(os.path.dirname(os.path.abspath(path)), "watermarked"), manual=False)
        try:
            ex = self.state.read_exif(path) or {}
            self._raw_model = (ex.get("model") or "").strip()
            brand = (ex.get("make") or "").strip()
        except Exception:
            self._raw_model, brand = "", ""
        self._raw_brand = brand
        self._update_effective_model()
        if self._mode != "single":
            self._set_mode("single")            # 内部按需重渲
        else:
            self._sync_preview_source()
            self.request_preview()
        self._status(self.tr("已选择：{name}").format(name=self._input_name))

    # ---------- 拖放(Qt 原生: 同进程事件, 无跨进程消息) ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self._preview.set_hint(self.tr("松手以载入"))

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self._preview.set_hint(self.tr("拖入图片，或点「打开图片」"))

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.acceptProposedAction()
        self._preview.set_hint(self.tr("拖入图片，或点「打开图片」"))
        if not paths:
            self._status(self.tr("未收到文件"))
            return
        # 拖入目录 = 批量目录; 拖入多张 = 批量列表; 单张 = 单张
        folders = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if not os.path.isdir(p)]
        if folders:
            self._load_batch_dir(folders[0])
            return
        images = [p for p in files if self.state._sniff_image_type(p)]
        if not images:
            self._status(self.tr("不是受支持的图片格式"))
            return
        if len(images) == 1:
            self.set_input(images[0])
        else:
            self.load_batch([{"path": p, "name": os.path.basename(p)} for p in images])

    # ---------- 生成 ----------
    def start_generate(self):
        if self._generating:
            self._status(self.tr("正在处理中，请稍候"))
            return
        if not self._output_dir:
            self._status(self.tr("请先选择导出目录"))
            return
        req = {
            "params": backend_params(self._params),
            "manual_exif": self._manual_exif(),
            "output_dir": self._output_dir,
            "raw_model": self._raw_model,
            "custom_model": self._custom_model,
            "save_map_flag": True,
        }
        if self._mode == "single":
            if not self._input_path:
                self._status(self.tr("请先选择图片"))
                return
            req.update(mode="single", input_path=self._input_path,
                       original_name=self._input_name or "image.jpg")
        else:
            if not self._batch_files:
                self._status(self.tr("请先选择要批量处理的图片"))
                return
            req.update(mode="batch", file_paths=self._batch_files)
        d = self.state.start_generate(req)
        if not d.get("ok"):
            self._status(i18n.tr_msg(d.get("error")) or self.tr("无法开始处理"))
            return
        self._generating = True
        self._gen_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._progress.setMaximum(len(self._batch_files) if self._mode == "batch" else 100)
        self._status(self.tr("处理中…"))

    def _on_event(self, ev):
        t = ev.get("type")
        if t == "progress":
            total = ev.get("total") or 0
            self._progress.setVisible(True)
            self._progress.setMaximum(total or 100)
            self._progress.setValue(ev.get("done") or 0)
            name = ev.get("name")
            if name:
                self._status(self.tr("处理中 {done}/{total}：{name}").format(
                    done=ev.get("done"), total=total, name=i18n.tr_msg(name)))
        elif t == "log":
            self._status(i18n.tr_msg(str(ev.get("msg") or "")))
        elif t == "finished":
            self._generating = False
            self._gen_btn.setEnabled(True)
            self._progress.setVisible(False)
            msg = i18n.tr_msg(str(ev.get("message") or ""))
            # 生成耗时由核心在 finished 事件里带回来(单张=这一张, 批量=整批), 进会话统计
            self._stats.add_generated(ev.get("ms") or 0.0, ev.get("count") or 1)
            self._refresh_stats()
            if ev.get("success"):
                out_dir = ev.get("out_dir") or self._output_dir
                self._status(self.tr("已完成：{msg}").format(msg=i18n.tr_msg(msg)))
                if out_dir:
                    core.open_path(out_dir)     # 完成后打开所在目录
            else:
                self._status(self.tr("失败：") + msg.splitlines()[0] if msg else "失败")
                _message(self, QMessageBox.Icon.Warning, "处理结果", msg or "处理失败")

    # ---------- 杂项 ----------
    def _fix_status_inset(self):
        """把状态文本精确落到左对齐线上。

        `_status_label.x()` 是相对状态栏的坐标,**已经包含**我们设的 contentsMargins,
        所以超出的部分只有 `x() - margins.left()` 这一段, 补掉它即可。
        """
        m = self._status_bar.contentsMargins()
        extra = self._status_label.x() - m.left()
        if extra:
            self._status_bar.setContentsMargins(max(0, m.left() - extra), m.top(),
                                                m.right(), m.bottom())

    def _status(self, text):
        self._status_label.setText(text)

    def closeEvent(self, e):
        if self._save_timer.isActive():     # 防抖还没到点就关窗: 立刻落盘, 否则最后一次改动会丢
            self._save_timer.stop()
            self._save_settings_now()
        # 退出时落一条会话汇总: 单条日志答不了"这次整体有没有退化/缓存有没有用"
        try:
            logging.info("[统计] " + self._stats.summary_oneline())
        except Exception as ex:             # noqa: BLE001 - 汇总失败不能挡住关窗
            logging.warning(f"[统计] 汇总失败: {ex}")
        self._preview_worker.stop()
        self._preview_worker.wait(2000)
        self._event_watcher.stop()
        self._event_watcher.wait(2000)
        self.state.cleanup()          # 只清本程序自己的 PREVIEW_DIR(见 XSY_PREVIEW_DIR)
        super().closeEvent(e)
