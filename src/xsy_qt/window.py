# -*- coding: utf-8 -*-
"""镶·水印 · 主窗口。

参数面板: 三个页签「文件与美化 / 字体与画布 / 水印与效果」,
控件与取值范围由参数表驱动 —— 加参数只改表,

已对齐: 三页签 · 全部参数控件 · 取色(预设色/收藏色) · 字体路径 · 画布比例 · 布局 ·
       显隐联动 · 拖放/选图 · 批量 · 实时预览(含状态标识) · 单张/批量生成 · 撤销重做 ·
       预设(10 槽位) · 型号映射 · 手动 EXIF · 放大镜 · 间距几何标注 · 图片信息 · 吐司通知
未做: 启动页(窗口秒开, 加了反而多一道闪烁)
"""
import inspect
import hashlib
import json
import logging
import math
import os
import time
from datetime import datetime

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QParallelAnimationGroup,
                            QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
                            QT_TRANSLATE_NOOP, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QImage, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpacerItem, QSpinBox, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget,
)

from PIL import Image

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
# 免得为非法字符/保留名/重名操心(见核心 preset_path)。
_PRESET_SLOTS = core.MAX_PRESET_SLOTS
# 预设里"跟着机器走"的键: 都是绝对路径, 换台机器就是死链。**本机存的预设里保留**(同机存取要能
# 还原字体), 只在导出给别人时剥掉 —— 分享出去的那份必须是机器无关的。
_PRESET_MACHINE_KEYS = ("logo_dir", "font_path", "font_bold_path",
                        "font_italic_path", "font_bold_italic_path")
# 导出文件里的格式标记: _apply_params 会忽略未知键, 所以它不影响加载, 只用来让将来能识别。
_PRESET_FORMAT = "xiangshuiyin.preset/1"
# **只用于给出"导出时的默认文件名"**(用户可以改), 程序不靠它认任何东西 —— 槽位文件名是固定的
ILLEGAL_FN_CHARS = set(chr(92) + '/:*?"<>|')

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
    # ---------- 不在页签里平铺的行(收进弹窗) ----------
    # 字体文件: 四条路径原来占掉「字体与画布」页签的第一张卡片, 而字体路径一年改一两次 ——
    # 收进 ☰ →「字体文件…」的弹窗(见 FontDialog)。
    # `tab=None` 让 _build_tab 跳过; 标签/提示/kind 仍留在本表里, 弹窗按 `dialog="fonts"` 取用,
    # 免得同一份定义抄两遍(撤销步名也取自这里的 label, 见 _on_param)。
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_path", label=QT_TRANSLATE_NOOP("ParamRow", "常规字体"), kind="path"),
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_bold_path", label=QT_TRANSLATE_NOOP("ParamRow", "粗体字体"), kind="path"),
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_italic_path", label=QT_TRANSLATE_NOOP("ParamRow", "斜体字体"), kind="path"),
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_bold_italic_path", label=QT_TRANSLATE_NOOP("ParamRow", "粗斜体字体"), kind="path"),

    # ---------- 字体与画布 ----------

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "最小字号设置"), key="min_font_size", label=QT_TRANSLATE_NOOP("ParamRow", "最小字号"), kind="int",
         args=(0, 200), hint=QT_TRANSLATE_NOOP("ParamRow", "0~200px，默认 20")),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "最小字号设置"), key="low_res_enhance", label=QT_TRANSLATE_NOOP("ParamRow", "低分辨率增强"),
         kind="bool", hint=QT_TRANSLATE_NOOP("ParamRow", "图片高度小于 1500px 时 2 倍渲染")),

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
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="bg_mode", label=QT_TRANSLATE_NOOP("ParamRow", "背景模式"), kind="choice",
         args=[("blur", QT_TRANSLATE_NOOP("ParamRow", "模糊图片")), ("color", QT_TRANSLATE_NOOP("ParamRow", "纯色"))]),
    # 背景色只在纯色模式下有意义: 模糊模式的底是"工作图放大回来的毛玻璃", 这个颜色根本不参与
    # (见 core._build_background)。留在那儿只会让人以为"调了不起作用"(用户: "模糊背景模式下，
    # 背景色选项应为隐藏"), 与下面那条"纯色模式不显示背景暗化"是同一套口径。
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="bg_color", label=QT_TRANSLATE_NOOP("ParamRow", "背景色"), kind="color",
         only_if=("bg_mode", ("color",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="blur_radius", label=QT_TRANSLATE_NOOP("ParamRow", "模糊半径"), kind="int",
         args=(1, 1000), hint=QT_TRANSLATE_NOOP("ParamRow", "1~1000，默认 150"), only_if=("bg_mode", ("blur",))),
    # 纯色模式不显示: 核心对纯色背景有意不做暗化(见 process_single 里的注释), 露着控件
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="bg_darken_strength", label=QT_TRANSLATE_NOOP("ParamRow", "背景暗化"), kind="pct",
         args=(0.0, 1.0), hint=QT_TRANSLATE_NOOP("ParamRow", "0~100%，默认 0；100=全黑（仅模糊模式）"),
         only_if=("bg_mode", ("blur",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="corner_radius", label=QT_TRANSLATE_NOOP("ParamRow", "图片圆角"), kind="int",
         args=(0, 1000), hint=QT_TRANSLATE_NOOP("ParamRow", "0~1000 圆角，默认 0")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="corner_circle", label=QT_TRANSLATE_NOOP("ParamRow", "圆形（整图正圆）"), kind="bool"),

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
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印开关"), key=None, special="_build_font_style_card"),

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
MAG_MARGIN = 12      # 离预览框右上角的距离(投影已去掉, 这里就是纯留白)
# 竖向滚动条占位宽度, 必须与 theme.QSS 里 `QScrollBar:vertical { width }` 一致(现在 14px):
# _segbar_row 按这个值给页签行留右侧占位, 两边不同步的话页签卡片会比参数卡片宽出那条差
_SB_W = 14

# 预览显示缩放: 用 PIL 的 LANCZOS 而不是 QPixmap.scaled(Smooth) —— 见 PreviewBox._scale_for_display。
# **这里不再做锐化**: 曾经有一档 `_PREVIEW_SHARPEN`(半径1/阈值3 的 UnsharpMask)是为补偿"上屏那步
# 被 Qt 放大 1.25 倍"加的; 那个损失按设备像素上屏修掉之后它就成了纯粹的观感旋钮, 代价却是每次
# 换图/改尺寸白花 ~29ms(1088x963 实测), 而且让**预览比成品更锐**(percent=60 时 99.9 分位过冲
# 34/255、最大 68) —— 等于你看到的不是会生成出来的东西。已删除, 预览现在就是成品口径。

# ---------- 预览状态标识 ----------
# 回答一个问题: 屏幕上这张图, 是不是当前参数渲出来的。
# 五个状态的圆点色**取自主程序自己那组"莫兰迪"色**(见 PRESET_COLORS), 不另外造色号 ——
# 否则标识与旁边的卡片/按钮一眼看出不是一个体系。
PV_CHIP_DOT = {
    "none": theme.EMPTY_SLOT,  # 未选图: 一档暗中性灰, 天然表示"没有"
    "rendering": "#7C98A6",
    "synced": theme.ACCENT,
    "stale": "#B8895E",
    "failed": "#B0676B",
}
PV_CHIP_H = 20          # 胶囊高度; **必须定高**, 理由见 PreviewStateChip 的类注释

# 文案表。**中文字面量必须包在 NOOP 里**: lupdate 看不穿变量, 只包运行时那个 tr() 的话这几条
# 会被整条漏译(表里的 context 要与消费它的类名一致 —— PreviewStateChip, 这样运行时 self.tr()
# 查到的就是这里登记的那条)。
_PV_CHIP_TEXT = {
    "none": QT_TRANSLATE_NOOP("PreviewStateChip", "无预览"),
    "rendering": QT_TRANSLATE_NOOP("PreviewStateChip", "渲染中…"),
    "synced": QT_TRANSLATE_NOOP("PreviewStateChip", "已按当前参数渲染"),
    "stale": QT_TRANSLATE_NOOP("PreviewStateChip", "参数已改，待渲染"),
    "failed": QT_TRANSLATE_NOOP("PreviewStateChip", "渲染失败，未更新"),
}
_PV_CHIP_TIP = {
    "none": QT_TRANSLATE_NOOP("PreviewStateChip", "未选择图片"),
    "rendering": QT_TRANSLATE_NOOP("PreviewStateChip", "正在渲染，完成后自动更新"),
    "synced": QT_TRANSLATE_NOOP("PreviewStateChip", "预览已与当前参数一致（缩略档渲染，细节请用放大镜查看）"),
    "stale": QT_TRANSLATE_NOOP("PreviewStateChip", "预览尚未更新到当前参数，渲染完成后自动更新"),
    "failed": QT_TRANSLATE_NOOP("PreviewStateChip", "上次预览渲染失败，当前显示的是上一张成功的预览"),
}

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
                # 标签为空(字体/水印开关网格的格位)时**不加**弹性空白 —— 那些格位靠 grid 的
                # AlignCenter 居中, 加了 stretch 就会把开关推偏。
                # 注: 这里曾经是为了绕开"空标签的 QCheckBox 不画指示器", 那条在现在这套 QSS
                # 素材下不成立(2026-09-23 复测: 空标签与空格标签的非白像素都是 1102)。
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

        **与取色弹窗里的色块共用 `_swatch_qss`**: 这里以前另写了一套(悬停/按下变成 2px 主色
        描边), 于是同一个色块在参数行里"悬停描绿边"、在弹窗里又是另一套 —— 全项目的"选中/
        悬停高亮"该只有一份定义(用户让"检查其他地方有没有同样的样式")。
        悬停/按下仍必须在本控件自己的样式表里把背景写掉, 否则会落到主题那条
        `QPushButton:hover`, 鼠标一上去色块变灰 —— 看着像控件坏了, 也正是"点不动"观感的来源。
        """
        self.swatch.setText(str(color))
        self.swatch.setStyleSheet(_swatch_qss(color, radius=theme.RADIUS_CTRL))

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
        self.reset_btn.setToolTip(self.tr("将本卡片参数恢复为出厂默认值"))
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


class PreviewStateChip(QFrame):
    """预览标题条里的状态标识: 一个圆点 + 一句话(胶囊底), 挨着「效果预览」放。

    **它要回答的问题**: 屏幕上这张图, 是不是当前参数渲出来的。
    以前答不了 —— 回传的数据里只有图和几个耗时, 参数本身没被带回来, 请求发出去也没留下
    可比的凭证; 而这条链路**天然存在"屏上这帧已经过期"的时刻**(在跑的那一轮不可中断、
    待渲染槽还会被新请求覆盖)。所以现在请求带参数指纹、渲染线程原样回传, 界面把"屏上指纹"
    与"此刻指纹"比一次就够了 —— 见 MainWindow._preview_token / _on_rendered。

    四条判据(顺序即优先级, 由 MainWindow._preview_state 给出):
      none      没选图 / 取样图被清空
      rendering 有在途或排队的请求 —— **合并了"正在渲"与"改过还没渲"**: 拖滑块时一帧上屏时
                "又改过了"几乎是常态, 若照字面各报一态, 两态会来回跳, 而它们对用户是同一种
                事实(还没跟上)。真正该报"待渲染"的是"没人在渲、屏上还是旧参数"那种, 它只在
                渲染失败后才出现。判据见 tools/_probe_preview_state.py 第 ⑤ 组
                (模拟拖滑块 16 帧: 8 帧判为"还没跟上"、一次都没误报"待渲染")。
      stale     没人在渲, 但屏上这帧的参数与当前不符
      failed    最后一次尝试失败 —— 屏上留的是上一张成功的图, 不担保对应当前参数

    **定高 20px**(PV_CHIP_H): 标题条那一行是旁边的「↺ 刷新预览效果」撑起来的 28px, 不给它
    定高, 布局会把标识也拉成 28px —— 于是胶囊和按钮一样高、一样圆, 看着就是个能点的按钮
    (在试验台里实测到的)。20px 也让 11px 的字上下各余约 3px。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(PV_CHIP_H)
        # 不做成按钮: 不给光标、不给悬停态、不给边框 —— 旁边就有一个真按钮, 有这三样会被当成
        # 第二个按钮(试验台里"胶囊"和`#mini`按钮并排时就是这个观感)。
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 1, 8, 1)
        lay.setSpacing(5)
        self._dot = QLabel(self)
        self._dot.setFixedSize(6, 6)
        self._text = QLabel(self)
        self._text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._dot)
        lay.addWidget(self._text)
        self._state = ""
        self.set_state("none")

    def set_state(self, state, tip=""):
        """切换状态。tip 给的是"为什么是这个状态"的一句话(渲染失败时带上原因)。"""
        if state not in PV_CHIP_DOT:
            state = "none"
        self._state = state
        dot = PV_CHIP_DOT[state]
        self._text.setText(self.tr(_PV_CHIP_TEXT[state]))
        self._dot.setStyleSheet(f"background: {dot}; border-radius: 3px;")
        self._text.setStyleSheet("color: %s; font-size: 11px; background: transparent;" % dot)
        # 淡底用状态色自己调透明度: 同一色系、不另造颜色, 与 `#mini` 悬停只有一档深浅同一口径
        self.setStyleSheet("background: %s; border: none; border-radius: 9px;"
                           % _rgba(dot, 0.13))
        self.setToolTip(tip or self.tr(_PV_CHIP_TIP[state]))
        self.adjustSize()

    def state(self):
        return self._state


def _rgba(hex_color, alpha):
    """#RRGGBB + 透明度 -> rgba(...)。标识的淡底用它, 免得手写一串近似色。"""
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


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
        self._hint = self.tr("请拖入图片，或点击「打开图片」")
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

        只在控件尺寸/画面/dpr 变化时算一次(缓存在 _scaled), 一张 4090x3526 的画布约 10~40ms。
        """
        dpr = self.devicePixelRatioF()
        dev = QSize(max(1, round(target.width() * dpr)), max(1, round(target.height() * dpr)))
        t0 = time.perf_counter()
        im = self._pil.resize((dev.width(), dev.height()), Image.LANCZOS)
        pm = QPixmap.fromImage(_pil_to_qimage(im))
        pm.setDevicePixelRatio(dpr)      # 位图是"1.25 倍的像素", 按逻辑坐标画才是 1:1
        # 上屏缩放跑在 GUI 线程上(实测慢的时候几十毫秒), 值得单独记一条。这里只在
        # "新画面/新尺寸"时执行(结果缓存在 _scaled), 所以每次预览最多一行 —— 绝不进逐帧重绘路径。
        scale_ms = (time.perf_counter() - t0) * 1000
        src = self._pil.size
        logging.info(f"[上屏] 缩放 {src[0]}x{src[1]}→{dev.width()}x{dev.height()} "
                     f"dpr={dpr:g} 耗时 {scale_ms:.0f}ms (PIL LANCZOS)")
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
        # **抗锯齿必须在这里显式打开**: QPainter 默认是关的, 于是 fillPath/setClipPath 的圆弧
        # 会画成一圈硬台阶 —— 与 QSS 画的卡片圆角(同一个 RADIUS_CARD)并排就是"看着不是一个体系"
        # 的来源。实测(设备像素, 探针 tools/_probe_corner.py):
        #     不开 → 圆弧上"半透明过渡像素" 0 个(纯台阶), 拟合半径 12.7
        #     开   → 54 个, 拟合半径 11.9(=12, 卡片 11.6), 外轮廓剖面与卡片逐值差 <= 1 设备像素
        # setClipPath 本身也会跟着抗锯齿(离屏对照实验: 只加这条提示, 裁剪+fillRect 与
        # 不裁剪的 fillPath 结果逐像素相同), 所以图片/标注/放大镜的边缘一并变平滑。
        # 剩余的一点"毛躁"来自深色与米色底的反差(同一条斜坡在浅色卡片上看不见), 试过加一圈
        # 与卡片同口径的 1px 描边把深色从底色边上挪开 —— 数据上成立, 但 1:1 下用户看不出区别,
        # 属于过度设计, 已撤掉(见 README「视觉节奏」)。
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 深色底做成**与卡片同一档圆角**, 并裁剪到这条圆角路径上(图片/标注/放大镜一并被裁) ——
        # 以前是整块方形灰底, 与左边那些圆角卡片放一起显得不是一个体系(用户提的)。
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), theme.RADIUS_CARD, theme.RADIUS_CARD)
        p.setClipPath(path)
        p.fillPath(path, QColor("#333333"))            # 预览底色
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

        # **不做投影**。原来用"几层递增加粗的半透明描边"近似模糊, 但
        # 层数太少(宽 12/7/3、alpha 8/14/22)、半径还跟着 w/2 变, 三层叠不齐 → 透明度沿边缘**非单调**
        # (实测剖面 `#15120f → #130f0d → #100e0c → #10100c` 先降后升), 肉眼就是沿弧一圈细纹
        # (用户报"边框有一圈不明条纹")。放大镜本来就压在深色预览底上, 白边框已经够分辨, 索性去掉。

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
        # 这里画的都是横平竖直的 2px 色带/虚线框, **关掉抗锯齿**保持边缘利落(与预览底色的
        # 圆角相反: paintEvent 开头那把 Antialiasing 是给圆弧用的, 直角线开了只会发虚)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
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


def _hamburger_icon(color, size=16, dpr=1.0):
    """画"三横线"菜单图标。

    为什么要自绘、为什么要按设备像素画(三个坑都踩过):
      ① 不直接用 ☰(U+2630): 那个码位在中文字体里没有, 靠字体回退拿到别的字体的字形,
         小字号下看着像个小箭头(v);
      ② **按 devicePixelRatio 出图**: 第一版在 15px 的位图上画完交给 Qt 放大 1.25 倍(本机
         125% 缩放), 线条被重采样得发虚 —— 与预览上屏那条教训是同一个(见 README「预览发虚」);
      ③ **出图的逻辑尺寸必须正好等于调用方要的 iconSize**, 否则照样被重采样一次: 以前默认
         `size=15` 而 QPushButton 的 iconSize 是 16, Qt 会把 15 拉伸到 16 —— 三横于是被挤成
         "间距 3、2"(`y4-5 / y9-10 / y13-14`, 用户报"三横不是均匀分布的")。
         所以这里 `size` 默认改成 16(与 QPushButton 的默认 iconSize 一致), 并且
         **`setDevicePixelRatio(px / size)` 而不是直接用 dpr** —— 让位图的逻辑尺寸正好是 size,
         Qt 取图时一次重采样都不做。

    横线用 `fillRect` 按整数设备像素画, 不用 QPen: 笔宽是"以坐标为中心"的, 在非整数位置会落成
    半个像素(实心矩形则是精确的 w 像素厚)。三条的**间距由算式保证相等**, 见下面的 g。
    """
    dpr = float(dpr or 1.0)
    px = max(9, int(round(size * dpr)))                        # 设备像素画布
    w = max(2, int(round(px / 9)))                             # 每条横线的厚度(设备像素)
    gap = max(2, int(round(px * 0.15)))                        # 横线之间的空档(设备像素)
    block = 3 * w + 2 * gap                                    # 三条横线 + 两个空档的总高
    top = max(0, (px - block) // 2)                            # 上下留白相等 => 整块居中
    inset = int(round(px * 0.18))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)   # 横平竖直, 不要抗锯齿
    for i in range(3):
        p.fillRect(inset, top + i * (w + gap), px - 2 * inset, w, QColor(color))
    p.end()
    pm.setDevicePixelRatio(px / float(size))                   # 见上面 ③
    return QIcon(pm)


def _open_and_report(win, path, what):
    """打开一个文件/目录并把结果写进状态栏(打不开就说一句 + 记日志, 不弹窗)。

    与 `_open_logo_dir` 用的是同一个机制(QDesktopServices); 那条另有"目录不存在就先建出来"
    的逻辑, 所以没合到一起。
    """
    if not os.path.exists(path):
        win._notify(f"{what}不存在：{path}")
        return False
    try:
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception as e:                      # noqa: BLE001
        ok = False
        logging.warning(f"[打开] {what} 失败 {path}: {e}")
    win._notify(f"已打开{what}：{path}" if ok else f"打不开，路径是：{path}")
    return bool(ok)


# 项目仓库地址。**占位符**: 仓库还没建, 建好后只改这一处(它同时是那个 GitHub 图标的 tooltip)。
REPO_URL = "https://github.com/your-name/ShuiYin-QT6"
_ABOUT_ICON = 20            # 「关于」弹窗里 GitHub 标识的显示边长(逻辑像素)


class _IconLink(QLabel):
    """一个只有图标、点它开浏览器的控件(「关于」弹窗底部的 GitHub 标识)。

    **图标按设备像素出图**: `QIcon.pixmap(size, dpr)` 会让 SVG 在 size×dpr 上光栅化, 上屏 1:1,
    一次重采样都不做(见 README「预览发虚」那条; 富文本 `<a><img>` 会被 QTextDocument 再缩一次)。
    没有做成真锚点(富文本里的 `<a>`): 那样图标会发虚, 而这里要的只是"点一下打开仓库"。
    给了手型光标 + tooltip(就是 URL) + 读屏名字, 可点性够。
    """

    def __init__(self, icon_path, size, url, accessible_name, parent=None):
        super().__init__(parent)
        self._url = url
        pm = QIcon(icon_path).pixmap(QSize(size, size), self.devicePixelRatioF())
        self.setPixmap(pm)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(url)
        self.setAccessibleName(accessible_name)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            QDesktopServices.openUrl(QUrl(self._url))


def _about_logo(size, dpr=1.0):
    """关于框顶部的 logo。取打包素材, 按**设备像素**缩放(项目的老规矩: 别让 Qt 再放一次)。

    拿不到素材就返回 None —— 关于框照样要能开(缺图不该让它崩)。
    """
    path = os.path.join(core.ASSETS_DIR, "exif-watermark-logo-1024.png")
    pm = QPixmap(path)
    if pm.isNull():
        return None
    px = max(1, int(round(size * dpr)))
    pm = pm.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                   Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


def _about_label(text, obj_name, spacing=0.0):
    """关于框里的一行文字: 字号/字重/颜色全交给 QSS(见 theme 里 `QLabel#about*` 那段注释 ——
    全局 `QWidget {font-size:12px}` 会压过 setFont, 字号只能写 QSS)。

    这里只做两件 QSS 做不到的事: **字距**(QSS 没有 letter-spacing)与**可选中**。两件都不在本函数里
    当场做, 都只把意图留下: 字距记在属性上, 由 AboutDialog 结尾(抛光之后)统一施加; 可选中是
    **整框的性质**(那个框里所有文字都要能选中复制), 由 AboutDialog 建完所有标签后统一开。
    """
    lab = QLabel(text)
    lab.setObjectName(obj_name)
    lab.setProperty("letterSpacing", float(spacing))
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lab


class AboutDialog(QDialog):
    """关于: 顶部 logo + 程序名 + 一句话 + 版本/环境 + 数据目录 + 分发许可。

    也是 logo + 标题 + 副标题, 见 README「顶部栏只留…」):
        名字    22px / 字重 600 / 字距 6px / `#2f4a38`
        副标题  13px / MUTED / 字距 2px
    两者都是居中排布。字距只能用 QFont 设(QSS 没有 letter-spacing)。

    **目录只留数据目录一条**(用户: "不需要写这么多目录, 只留程序数据目录就够"): 程序目录 =
    日志目录, 它在 ☰ 菜单里能直接打开; 预览目录是临时目录, 对用户没有意义; 而数据目录是
    设置/预设/字体的家, 需要能照着去找。

    会话统计也在这儿(状态栏删掉后它的新家) —— 它是"本次会话累计", 不需要实时看, 按需打开正合适。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        go_frameless(self)
        self.setWindowTitle(self.tr("关于"))
        import platform

        from PySide6 import __version__ as pyside_version

        from xiangshuiyin import __version__ as app_version

        v = QVBoxLayout(self)
        v.setContentsMargins(_DLG_PAD + 8, _DLG_PAD + 8, _DLG_PAD + 8, _DLG_PAD)
        v.setSpacing(0)                  # 间距靠固定间隔撑, 比统一 spacing 好控(品牌区要更松)

        logo = _about_logo(88, self.devicePixelRatioF())    # 关于弹窗标识尺寸
        if logo is not None:
            lb = QLabel()
            lb.setPixmap(logo)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(lb)
            v.addSpacing(18)             # 标识下方的间距

        v.addWidget(_about_label(self.tr("「镶 · 水印」"), "aboutName", spacing=6.0))
        v.addSpacing(6)
        v.addWidget(_about_label(self.tr("一帧入画　一镶成框"), "aboutSub", spacing=2.0))
        v.addSpacing(18)
        v.addWidget(_about_label(f"v{app_version}", "aboutVer"))    # 三级标题那一档
        v.addSpacing(14)
        # 环境两行: 版本号/系统名是数据, 只有下面那条模板要翻
        v.addWidget(_about_label(self.tr("Python {py} · PySide6 {pyside}").format(
            py=platform.python_version(), pyside=pyside_version), "aboutInfo"))
        v.addSpacing(2)
        build = platform.version().rsplit(".", 1)[-1]
        v.addWidget(_about_label(f"{platform.system()} {platform.release()} · {build}",
                                 "aboutInfo"))
        v.addSpacing(8)
        v.addWidget(_about_label(self.tr("数据目录：{path}").format(path=core.BASE_DIR),
                                 "aboutInfo"))
        v.addSpacing(4)
        v.addWidget(_about_label(self.tr("本次会话统计：{stats}").format(
            stats=self._stats_text(parent)), "aboutInfo"))
        v.addSpacing(16)
        note = _about_label(self.tr(
            "分发时请遵守 PySide6 的 LGPLv3 与内置字体的 SIL OFL 1.1"
            "（详见仓库 THIRD_PARTY_LICENSES.md 与 assets/fonts/OFL.txt）。"),
            "aboutNote")             # 备注: 灰色小字
        note.setWordWrap(True)
        note.setMaximumWidth(420)
        v.addWidget(note)
        v.addSpacing(14)
        # 项目仓库的链接: GitHub 标识(素材 assets/github.svg) + 点击开默认浏览器。
        # **不用富文本 <a><img>**: 那条路会把 SVG 交给 QTextDocument 再光栅化一次, 出来是发虚的
        # (与"预览发虚/图标重采样"同一类问题)。改成自己按设备像素出图的标签(见 _IconLink)。
        v.addWidget(_IconLink(theme._asset("github.svg"), _ABOUT_ICON, REPO_URL,
                              self.tr("项目仓库（GitHub）"), self))
        v.addSpacing(14)
        v.addLayout(dlg_button_row(dlg_button(self.tr("关闭"), self.accept, primary=True)))
        # 可选中**必须在定尺寸之前开**: 开了它 Qt 才给标签建文本控件(QTextDocument), 标签的期望
        # 尺寸随之变化(22px 行 22→26、13px 行 13→15、12px 行 14→12); 放到 csdify/adjustSize 之后
        # 开, 尺寸已按旧期望定死, 多出的像素会被布局从别处抠掉(实测抠的是顶部 logo: 88px→82px, 图被裁)。
        # 按"有文字的标签"统一开: 图标标签无文字(_IconLink), 挂文本交互只会多一个用不上的文本控件。
        # 标题栏那行「关于」由下面 csdify 建, 本段在它之前跑, 天然不在其列 —— 也应如此: 它压在拖动
        # 区上, 开了文本交互会吃掉鼠标按下, 标题就拖不动窗口。若把本段挪到 csdify 之后, 记得排除它。
        # Qt 对"只允许鼠标选中"的标签给的是点击聚焦(不进 Tab 链), Tab 仍只停在两个按钮上。
        for lab in self.findChildren(QLabel):
            if lab.text():
                lab.setTextInteractionFlags(lab.textInteractionFlags()
                                            | Qt.TextInteractionFlag.TextSelectableByMouse)
        csdify(self, self.tr("关于"), _prep_dialog)
        # **字距放在最后设**: QSS 抛光会把 setFont 的字号压回 12px, 但**不影响字距**; 而若在
        # 抛光之前设, 那一次 setFont 会被整体覆盖掉。所以等 csdify/adjustSize 走完再逐行施加。
        for lab in self.findChildren(QLabel):
            sp = lab.property("letterSpacing")
            if sp:
                f = lab.font()
                f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(sp))
                lab.setFont(f)

    @staticmethod
    def _stats_text(parent):
        """会话统计(挂在主窗口上, 拿不到就留空 —— 关于框不该因为统计出错打不开)。"""
        try:
            return parent._stats.summary(compact=True)
        except Exception:                            # noqa: BLE001
            return ""


class ImageInfoDialog(QDialog):
    """图片信息: **如实报告这张图上读到了什么** —— 文件信息(只读文件头) + 照片自己的 EXIF。

    这里**不推算"最后会印成什么"**: 曾经并排一列「当前生效值」、底下还有一行「水印显示（最终）」,
    回答的是"印出来的到底是哪个"。但那属于推算结果 —— 一旦有「美化型号」/「型号映射」/「自定义
    水印信息」参与进来, 它就跟"这张图里到底存了什么"混在一张表里, 反而看不清(用户: "只需要如实
    显示出图片上读出的 exif 信息即可, 不需要显示最终啥样生效值是啥样")。
    要看"水印里会显示什么", 主界面「识别型号」那一行的 tooltip 已经在说。

    所以这一组的小标题是**「照片 EXIF 信息」**而不是原来的「水印里会用到的值」: 勾了「自定义
    水印信息」之后水印用的并不是这几个值(那个勾的语义就是"忽略照片 EXIF"), 沿用旧标题就成了假话。

    文件那几项(大小/像素/格式/时间)都是**只读文件头**, 不解码整张图。
    """

    def __init__(self, path, parent=None):
        super().__init__(parent)
        go_frameless(self)
        self.setWindowTitle(self.tr("图片信息"))
        self.resize(540, 420)

        info = self._collect(path, parent)
        photo = info["photo"]

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

        def row(label, value, elide=False):
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
            r[0] += 1

        group(self.tr("文件"))
        row(self.tr("路径"), path, elide=True)
        kb = info["size_kb"]
        row(self.tr("大小"), ("%.1f MB" % (kb / 1024.0)) if kb >= 1024 else (f"{kb:,.0f} KB" if kb else "—"))
        row(self.tr("像素"), f"{info['w']} × {info['h']}" if info["w"] else "—")
        row(self.tr("格式"), (info["ext"] or "—").lstrip(".").upper())
        row(self.tr("修改时间"), info["mtime"])
        # 只有一列值, 就不必再来一行列头了(原来那行「照片里的值 / 当前生效值」是为两列对照用的)
        group(self.tr("照片 EXIF 信息"))
        for label, key in ((QT_TRANSLATE_NOOP("ImageInfoDialog", "品牌"), "make"),
                           (QT_TRANSLATE_NOOP("ImageInfoDialog", "型号"), "model"),
                           (QT_TRANSLATE_NOOP("ImageInfoDialog", "快门"), "exposure"),
                           (QT_TRANSLATE_NOOP("ImageInfoDialog", "光圈"), "fnumber"),
                           ("ISO", "iso")):
            row(label, photo[key])

        v.addLayout(grid)
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


class PresetDialog(QDialog):
    """预设方案弹窗: 槽位 / 名称 / 加载·保存·删除, 外加 导入·导出 与 目录入口。

    里面就是 Logo 目录 + 预设槽 + 名称 + 加载/保存/删除), 卡片才是当初的偏离; ② 同一件事的
    入口被拆成了两处 —— 槽位/名称/加载/保存/删除在卡片里、导入/导出在 ☰ 菜单里, 收进一个
    弹窗后菜单里只剩「预设方案…」一项; ③ tab1 是全窗最长的一页, 省下这一块滚动短一截。

    **每次打开都重新读盘**: 手工往 presets 目录里丢一个 presets5.json, 不用重启就能看到。

    交互与「型号映射」那个弹窗一致: 每个动作**即时生效**(没有 OK/取消), 关闭就是关闭。
    控件住这里、逻辑在 MainWindow(它手里有 _params/_apply_params/_undo_step), 所以下面每个
    按钮都是"把我的控件交给窗口的那个方法"。
    """

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        go_frameless(self)
        self.setWindowTitle(self.tr("预设方案"))
        self.resize(560, 250)

        def row(label, widgets):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 2, 0, 2)
            h.setSpacing(theme.GAP_CTRL)
            lab = QLabel(self.tr(label))
            lab.setObjectName("fieldLabel")
            lab.setFixedWidth(_LABEL_W)
            lab.setWordWrap(True)
            h.addWidget(lab)
            for i, sub in enumerate(widgets):
                h.addWidget(sub, 1 if i == 0 else 0)
            return w

        v = QVBoxLayout(self)
        tip = QLabel(self.tr("一个槽位一个文件（presets1..10.json）；导入将保存到当前选中的槽位。"))
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self.combo = NoWheelComboBox()
        v.addWidget(row("槽位", [self.combo]))

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self.tr("方案名（保存时必填）"))
        v.addWidget(row("名称", [
            self.name_edit,
            dlg_button(self.tr("加载"), lambda: win._preset_load(self)),
            dlg_button(self.tr("保存"), lambda: win._preset_save(self)),
            dlg_button(self.tr("删除"), lambda: win._preset_delete(self))]))

        self.dir_label = ElidedLabel(core.PRESETS_DIR)
        self.dir_label.setObjectName("muted")
        self.dir_label.setToolTip(core.PRESETS_DIR)
        v.addWidget(row("目录", [
            self.dir_label,
            dlg_button(self.tr("打开目录"),
                       lambda: _open_and_report(win, core.PRESETS_DIR, self.tr("预设目录")))]))
        v.addStretch(1)
        v.addLayout(dlg_button_row(
            dlg_button(self.tr("导入…"), lambda: win._import_preset(self)),
            dlg_button(self.tr("导出…"), lambda: win._export_preset(self)),
            dlg_button(self.tr("关闭"), self.reject, primary=True)))
        csdify(self, self.tr("预设方案"), _prep_dialog)
        self.reload()

    # ---- 给 MainWindow 的那几个方法用的小接口 ----
    def current_slot(self):
        return self.combo.currentData() or ""

    def name_text(self):
        return self.name_edit.text().strip()

    def set_name(self, text):
        self.name_edit.setText(text)

    def reload(self):
        self.win._reload_presets(self)


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
    `QPushButton:hover{background:#EEF2EC}`, 鼠标一上去色块就变灰, 看着像坏了。

    **四种状态的几何完全一样**(margin 2px + border 2px), 只有描边颜色变:
      · 粗细一变, 未选中↔选中的色块本身就会大小不一、"跳"一下;
      · 那 2px margin 是关键: 描边于是**离开色块一圈**, 中间露出卡片底色 —— 选中态看着是
        "色块外面套了一圈", 而不是描边糊在颜色上(用户报"高亮状态不好看": 深色块紧贴着一圈
        绿边, 像被装进绿框里)。不选中时这圈是 LINE, 选中是主色。
      · **不"悬停描绿边"**: 与按钮那条口径一致(README「按钮的悬停/按下只动底色, 不描边」),
        悬停只把描边换成深一档的暖灰(MUTED), 不换色相。
    选中另配 ✓ 字形, 不靠描边单独表达。
    """
    col = QColor(color)
    fg = "#000" if col.lightness() > 128 else "#fff"
    ring, ring_hot = ((theme.ACCENT, theme.ACCENT2) if selected else (theme.LINE, theme.MUTED))
    return ("QPushButton { background:%s; border:2px solid %s; border-radius:%dpx; margin:2px;"
            " color:%s; font-weight:bold; padding:0; }"
            "QPushButton:hover, QPushButton:focus { background:%s; border:2px solid %s; }"
            "QPushButton:pressed { background:%s; border:2px solid %s; }"
            % (col.name(), ring, radius, fg,
               col.name(), ring_hot, col.name(), ring_hot))


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
      我的颜色   点应用, **双击改这一格的颜色**, 右键删除。空位画成浅底格子(不勾边、不可点)
                 并固定画满 10 格, 卡片标题右边另写「已保存 n / 10」—— 格子只说得出"还有空",
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
        b.setToolTip(self.tr("将当前颜色保存到「我的颜色」，下次可直接使用"))
        acts.addWidget(b)
        acts.addStretch(1)                       # 右侧那个"落点"按钮靠右
        # 取色是即时应用的, "关闭"就是这次操作的落点(不是取消), 给主按钮的强调色
        acts.addWidget(dlg_button(self.tr("关闭"), self.accept, primary=True))
        v.addLayout(acts)

        # 初始焦点收到对话框自身, **不要落在子控件上**: 色块与色号框都有 :focus 的外观变化, 而焦点是
        # 窗口激活时才生效的, 比首帧晚一两帧 —— 那两帧里控件先按"无焦点"画一遍, 随后才补上,
        # 肉眼就是刚打开时那个色块"闪"了一下(实测: 首帧与稳定帧在色块区域差 204 个像素,
        # 色块的描边从 LINE #E6E0D6 变成 ACCENT #6F9C7D, 约 40ms 后才稳定)。
        # 焦点放在对话框上, 从首帧起就没有变化; Tab 照常进入各控件(那时才该显出焦点)。
        # 注意: 不能改成"把焦点给别的控件"—— 焦点落到色号输入框上一样会闪(主题里 QLineEdit:focus
        # 会换底色), 必须让这一轮没有子控件持有焦点。
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

        hint = QLabel(self.tr("点击色块打开取色器，也可直接输入色号"))
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
        tip = QLabel(self.tr("点击色块即应用；双击修改颜色，右键删除"))
        tip.setObjectName("hint")
        body.addWidget(tip)
        self._sync_custom_note()
        return frame

    def _sync_custom_note(self):
        """把「已存几个 / 共几个」写在卡片标题右边。"""
        self._custom_note.setText(self.tr("已保存 {n} / {total}").format(n=len(self._custom()), total=MAX_USER_COLORS))

    def _custom(self):
        try:
            return list((self._state.get_custom_colors() or {}).get("colors") or [])
        except Exception:                     # noqa: BLE001 - 读不到就当没有收藏色
            return []

    def _grid(self, colors, cols, total=0, editable=False):
        """色块网格。`total>0` 时**固定画满 total 个位置**, 没存颜色的画浅底空位格 ——
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
        if color is None:                     # 空位: 浅实底、无描边, 不可点
            lab = QLabel()
            lab.setFixedSize(_CHIP_W, _CHIP_H)
            lab.setAccessibleName(self.tr("未使用"))
            lab.setToolTip(self.tr("未使用"))
            # 只给一层极浅的底, **不勾边**: 十格里通常只有一两格有颜色, 剩下的并排勾边会把
            # 整张卡片的注意力全吸过去(用户: "看着好突兀"), 而"还剩几格"标题右边已经写着
            lab.setStyleSheet("background:%s; border:none; border-radius:%dpx;"
                              % (theme.EMPTY_SLOT_BG, theme.RADIUS_SMALL))
            return lab
        b = _ColorChip(color, index)
        b.setFixedSize(_CHIP_W, _CHIP_H)
        b.setCursor(Qt.PointingHandCursor)
        b.setAccessibleName(self.tr("颜色 {color}").format(color=color))   # 色块只画颜色, 读屏得靠名字才认得出
        b.setToolTip(color + (self.tr("　双击修改颜色 · 右键删除") if editable
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
        menu.addAction(self.tr("修改该位置的颜色…")).triggered.connect(lambda: self._edit_saved(index))
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
        tip = QLabel(self.tr("识别到的原始型号 → 水印上显示的型号。"
                             "在列表里选中一条即可修改或删除（删除前会先确认）。"))
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

        # 三个动作都即时生效(点完写盘), 所以没有单独的「保存到映射表」—— 列表当场就变了。
        # 按钮次序: 删除在最左、关闭在右下角(与其它自绘弹窗一致: 关于/图片信息/取色/预设的右下角
        # 都是「关闭」且是主色)。删除曾经落在右下角, 与"关闭"的位置习惯冲突, 容易误按。
        # NOOP 必须包在**字面量**上(lupdate 看不穿变量): 曾经包在 text 上, 结果整条漏译
        v.addLayout(dlg_button_row(
            dlg_button(self.tr(QT_TRANSLATE_NOOP("MapDialog", "删除选中")), self._remove),
            dlg_button(self.tr(QT_TRANSLATE_NOOP("MapDialog", "添加 / 修改")), self._add),
            dlg_button(self.tr("关闭"), self.accept, primary=True)))

        self._list.currentItemChanged.connect(self._on_select)
        try:
            self._data = dict(core.load_model_map(self._logo_dir) or {})
        except Exception:                           # noqa: BLE001 - 读不出就当空表
            self._data = {}
        self._reload()
        csdify(self, self.tr("型号映射管理"), _prep_dialog)   # 无边框 + 自绘标题栏

    def _reload(self, select=None):
        """把**内存里**这份映射表画进列表; `select` = 要选中的"原始型号"。

        不在这里读文件: 每调一次就 load_model_map() 会把刚写进 self._data 的条目冲成文件里的旧内容
        (表现为"点添加没反应、列表还是空的")。读盘只在 __init__, 写盘只在 _persist。

        `select` 供"添加/修改之后"用: 整表重画会清掉选中状态(currentRow 回到 -1), 刚编辑的那条在
        列表里就没有任何标记, 而表单里还留着它 —— 会出现"看不出当前是哪条、点删除删掉刚加的那条"。
        选中它并滚到可见处。
        """
        self._list.clear()
        for k in sorted(self._data):
            self._list.addItem(f"{k}   →   {self._data[k]}")
        if select is None:
            return
        for i in range(self._list.count()):
            if self._item_key(self._list.item(i)) == select:
                self._list.setCurrentRow(i)        # 会连带触发 _on_select(表单跟着这条)
                self._list.scrollToItem(self._list.item(i))
                break

    @staticmethod
    def _item_key(item):
        """列表项文本 "原始   →   显示" 里取出原始型号。分隔符与上面拼串时**必须一致**。"""
        return item.text().split("   →   ")[0]

    def _on_select(self, cur, _prev):
        if cur is None:
            return
        key = self._item_key(cur)
        self._raw.setText(key)
        self._nice.setText(self._data.get(key, ""))

    def _push_to_window(self):
        """把表同步给主窗口那份 `_params["model_map"]`。

        撤销的快照取的就是它(`_snapshot` 连 model_map 一起存), 改动后不对齐, 撤销栈里就看不到
        这一步。顺带刷新「水印将显示：X」那行。主窗口不在时(测试里直接构造弹窗)只管自己。
        """
        win = self.parent()
        if win is None or not hasattr(win, "_params"):
            return
        win._params["model_map"] = dict(self._data)
        upd = getattr(win, "_update_effective_model", None)
        if upd is not None:
            upd()

    def _apply_change(self, label, fn):
        """做一次改动并**单独记一步撤销**(每次增 / 改 / 删各一步, 步名进撤销按钮的 tooltip)。

        确认框由调用方在进事务**之前**弹: 它是嵌套事件循环, 放进事务里会让 600ms 的收尾定时器有机会
        提前把这一步落定 —— 不致命, 但"一步"的边界变得不确定。
        """
        win = self.parent()
        begin = getattr(win, "_begin_undo", None) if win is not None else None
        commit = getattr(win, "_commit_undo", None) if win is not None else None
        if begin is None or commit is None:          # 没有主窗口: 退化成"只干活"
            fn()
            return
        begin("__model_map__", label)
        try:
            fn()
        finally:
            commit()

    def _add(self):
        raw = self._raw.text().strip()
        nice = self._nice.text().strip()
        if not raw:
            _message(self, QMessageBox.Icon.Information, self.tr("提示"), self.tr("请先填写「原始型号」"))
            return
        existed = raw in self._data
        label = (self.tr("修改映射「{raw}」") if existed
                 else self.tr("新增映射「{raw}」")).format(raw=raw)

        def _do():
            self._data[raw] = nice      # 允许把显示名清空
            self._persist()             # 先落盘(失败会回滚 _data), 再把当前结果给窗口
            self._push_to_window()
            self._reload(select=raw)    # 选中它: 刚编辑的是哪条要看得见(见 _reload)

        self._apply_change(label, _do)

    def _remove(self):
        """删掉表单里那条。先确认一次: 这是即时写盘的破坏性动作, 且按钮紧邻「添加 / 修改」。"""
        raw = self._raw.text().strip()
        if raw not in self._data:
            # 不静默返回: 否则用户以为"点了没反应、条目还在"(旧版还会把表单清空)
            _message(self, QMessageBox.Icon.Information, self.tr("提示"),
                     self.tr("「原始型号」里现在这条不在映射表里，请先在列表里选中要删除的条目。"))
            return
        nice = self._data.get(raw) or ""
        if _message(self, QMessageBox.Icon.Question, self.tr("删除型号映射"),
                    self.tr("确定删除「{raw}」→「{nice}」这条映射？").format(
                        raw=raw, nice=nice or self.tr("（显示为空）")),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        def _do():
            del self._data[raw]
            self._persist()
            self._push_to_window()
            self._reload()
            self._raw.clear()
            self._nice.clear()

        self._apply_change(self.tr("删除映射「{raw}」").format(raw=raw), _do)

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


class FontDialog(QDialog):
    """「字体文件」的管理弹窗(☰ →「字体文件…」)。

    四种字重各一个字体文件, 原来平铺在「字体与画布」页签的第一张卡片里 —— 占一整张卡的高度, 而字体
    路径一年改一两次, 所以收进菜单。形态与「预设方案…」一致: 改动即时生效, 右下角是「关闭」。

    行复用参数行(`ParamRow` 的 path 型): 标签/提示/「选择字体」按钮与原先卡片里一致; 改动走
    `_on_param` —— 撤销(步名即该行标签)、写设置、重渲预览三条链路不变。
    """

    def __init__(self, state, parent=None):
        super().__init__(parent)
        go_frameless(self)                         # 必须在建窗之前(见 dialog.py)
        self.setWindowTitle(self.tr("字体文件"))
        # 给路径框留出可读宽度(放不下也能横向滚); adjustSize 不会压到最小宽度以下
        self.setMinimumWidth(560)

        v = QVBoxLayout(self)
        tip = QLabel(self.tr("四种字重各一个字体文件（常规 / 粗体 / 斜体 / 粗斜体）；改完立即生效。"))
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self._rows = {}
        for s in [s for s in PARAM_SPECS if s.get("dialog") == "fonts"]:
            init = parent._params.get(s["key"]) if parent is not None else None
            row = ParamRow(s, init)
            if parent is not None:
                row.changed.connect(parent._on_param)
            self._rows[s["key"]] = row
            v.addWidget(row)

        v.addSpacing(6)
        # 等价于卡片自带的「重置本组」—— 否则只剩「恢复默认设置」那条会重置全部参数的路
        v.addLayout(dlg_button_row(
            dlg_button(self.tr("恢复默认字体"), self._reset),
            dlg_button(self.tr("关闭"), self.accept, primary=True)))
        csdify(self, self.tr("字体文件"), _prep_dialog)

    def _reset(self):
        """四个路径恢复出厂默认, 整批算撤销里的一步。

        按 `_reset_card` 的写法直接改 `_params` 并回填控件, 不走 `_on_param` —— 后者自己会开一次撤销
        事务, 会把"整批一步"拆成四步。
        """
        win = self.parent()
        defaults = core.default_params()
        keys = list(self._rows)

        def _do():
            for key in keys:
                if key not in defaults:
                    continue
                if win is not None:
                    win._params[key] = defaults[key]
                self._rows[key].set_value(defaults[key])
            if win is not None and hasattr(win, "_touch"):
                win._touch()

        step = getattr(win, "_undo_step", None) if win is not None else None
        if step is None:                           # 没有主窗口(测试里直接构造)时只管自己
            _do()
        else:
            step("__reset_fonts__", self.tr("重置「字体文件」"), _do)
        notify = getattr(win, "_notify", None) if win is not None else None
        if notify is not None:
            notify(self.tr("已重置「{title}」为默认值").format(title=self.tr("字体文件")))


# ---------- 吐司(一次性提示) ----------
TOAST_MS = 3200
TOAST_FADE_MS = 350
TOAST_TOP = 14           # 离窗口内容顶部的距离
TOAST_SLIDE = 16         # 出场与收起的位移
TOAST_MIN_W, TOAST_MAX_W = 240, 560   # 吐司最小与最大宽度
TOAST_PAD_X = 20         # 盒子左右内边距
TOAST_CHROME = TOAST_PAD_X * 2 + 2   # 文字宽度之外的固定开销: 左右内边距 + 两侧 1px 描边


class Toast(QWidget):
    """**顶部居中**的一次性提示: "刚才发生了什么"(已保存/已导出/失败/请先选图…)。

    它补的是**中间那一档**: 弹窗要用户点一下才走(适合"要不要"这类决策), 常驻的状态行又不显眼
    且不会自己消失(适合"现在是什么"), 而"结果/失败"这类消息既需要被看到、又不需要回应。

    内容一变就加 `.show`、3200ms 后移除; 视觉是深色底 + 0.35s 淡入 + 从上滑下 16px +
    `pointer-events:none`。**位移与位置照抄**(顶部居中、从上方 16px 滑下、收起时往上退回去),
    另外两处按本程序改:
      · **做成主窗口内的浮层, 不开独立的半透明顶层窗口** —— 项目在 Win11 上被 DWM 的首帧错位
        坑过一次(README「三种弹窗是自绘标题栏」), 新开无边框半透明窗口是同一类风险; 挂在中央
        部件上则零窗口、零 DWM、dpr 也不用自己算(代价是它不能浮到窗口外, 对这个程序反而合适)。
        构造函数里的注释(会与淡入用的 opacity 效果构成"效果套效果", 把整块画坏)。

    顶部位置会压住工具栏那一行(文件名那一带)3.2 秒 —— 它**不吃鼠标事件**, 所以下面的按钮照常
    可点(见 WA_TransparentForMouseEvents)。

    两条纪律:
      · **只由用户显式动作触发**。后台循环(自动预览、自动保存)一条都不发 —— 否则拖一次滑块
        会弹出一串吐司, 比状态栏闪烁更糟(那条教训见 README「自动预览不碰状态栏」)。
    """

    def __init__(self, parent):
        super().__init__(parent)
        # 落到没设的那一层(然后按父子链传播, 不会穿透到下面真正该收到点击的控件)。
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._box = QFrame(self)
        self._box.setObjectName("toastBox")
        self._box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # **样式表必须带选择器**: 不带选择器的规则会**连子控件一起**吃到 —— 第一版就是这样,
        # 里面的 QLabel 也被套上了同一圈描边(抓图里文字外多出一个方框)。
        # QGraphicsEffect —— 外层要用 opacity 做淡入, 投影就成了"效果套效果", Qt 会重入画笔并
        # **把这一块整个画坏**(实测刷 QPainter::begin: A paint device can only be painted by one
        # painter at a time, 抓图里吐司是花的)。深底在浅色界面上本来就跳得出来, 不差这一层影。
        self._box.setStyleSheet(
            "QFrame#toastBox { background: rgba(15,23,42,0.94); border: none;"
            " border-radius: 10px; }")
        bl = QVBoxLayout(self._box)
        bl.setContentsMargins(TOAST_PAD_X, 12, TOAST_PAD_X, 12)   # 盒子内边距
        self._label = QLabel(self._box)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color:#e2e8f0; font-size:13px; background:transparent;")
        bl.addWidget(self._label)
        lay.addWidget(self._box)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim_pos = QPropertyAnimation(self, b"pos", self)
        self._anim_op = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim = QParallelAnimationGroup(self)
        self._anim.addAnimation(self._anim_pos)
        self._anim.addAnimation(self._anim_op)
        for a in (self._anim_pos, self._anim_op):
            a.setDuration(TOAST_FADE_MS)
            # 起步快、收尾缓, 对应 OutCubic; Qt 默认的 Linear 会显得生硬
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
        # **听动画自己的 finished, 别用 singleShot 猜时间**: 猜早了那会儿还在收起、条件不成立,
        # 就再没人来隐藏它了 —— 表现是 opacity 已经是 0、控件却还 visible(探针第 ⑤ 组抓到的)。
        self._anim.finished.connect(self._on_anim_finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TOAST_MS)
        self._timer.timeout.connect(self._fade_out)
        self._hiding = False       # 当前那段动画是"出场"还是"收起"(缩放重排时要按方向改终点)
        self.hide()

    def target_pos(self):
        """它该待的位置: 水平居中、离内容顶部 TOAST_TOP。"""
        p = self.parentWidget()
        if p is None:
            return QPointF(0, TOAST_TOP)
        return QPointF(max(0, (p.width() - self.width()) // 2), TOAST_TOP)

    def show_message(self, text):
        """显示一条(已显示的直接换文字并重置计时 —— 不排队、不堆叠)。"""
        text = str(text)
        self._label.setText(text)
        # **换行要自己算**: 只给 QLabel 设 min/max 宽度 + WordWrap 是不够的 —— 布局拿 sizeHint
        # 定尺寸时它按"当前宽度"算, 结果是压在最小宽度里**截断**(长文案只显示一行)。
        # 正确做法: 按单行文本量一个宽度、夹到 [min,max], 用这个宽度当**固定宽**, 再用
        # heightForWidth() 求出该宽度下真正要几行(短文案就停在最小宽度上)。
        fm = self._label.fontMetrics()
        w = min(TOAST_MAX_W - TOAST_CHROME,
                max(TOAST_MIN_W - TOAST_CHROME, fm.horizontalAdvance(text)))
        self._label.setFixedWidth(w)
        self._label.setFixedHeight(self._label.heightForWidth(w))
        self._box.adjustSize()
        self.adjustSize()
        self._hiding = False
        end = self.target_pos()
        was_visible = self.isVisible()
        if not was_visible:
            self.move(int(end.x()), int(end.y()) - TOAST_SLIDE)   # 从上方的 16px 处滑下来
            self.show()
        else:
            self.move(int(end.x()), int(end.y()))                 # 换文字不重放动画, 只把位置摆正
        self.raise_()
        self._animate(end, 1.0)
        self._timer.start()

    def reflow(self):
        """父控件尺寸变了: 重算目标位置。

        **动画在跑时要改它的终点, 不能只 move()** —— 否则动画下一个 tick 就把控件拉回旧位置
        (实测: 出场动画还没跑完时缩放窗口, 吐司会停在旧的水平位置上, 探针第 ⑨ 组抓到的)。
        """
        if not self.isVisible():
            return
        end = self.target_pos()
        if self._hiding:
            end = QPointF(end.x(), end.y() - TOAST_SLIDE)
        if self._anim.state() == QAbstractAnimation.State.Running:
            # **重起一段, 不要改在跑那段的终点**: `setEndValue()` 在运行中改终点会把控件
            # 挪到 (0,0)(实测, 原因在 Qt 内部; 探针第 ⑨ 组抓到的), 而 _animate() 会拿
            # "当前位置"当新起点, 于是动作接得上、终点也对。
            self._animate(end, 0.0 if self._hiding else 1.0)
        else:
            self.move(int(end.x()), int(end.y()))

    def _animate(self, pos, opacity):
        self._anim.stop()
        self._anim_pos.setStartValue(QPointF(self.x(), self.y()))
        self._anim_pos.setEndValue(pos)
        self._anim_op.setStartValue(self._effect.opacity())
        self._anim_op.setEndValue(opacity)
        self._anim.start()

    def _fade_out(self):
        """收起: 透明度归零 + 往上退回 TOAST_SLIDE(与出场反向)。"""
        if not self.isVisible():
            return
        self._hiding = True
        end = self.target_pos()
        self._animate(QPointF(end.x(), end.y() - TOAST_SLIDE), 0.0)

    def _on_anim_finished(self):
        """动画跑完时收尾。只在**收到 0** 时隐藏 —— 出场动画跑完也走这里, 别把刚显示的藏了。"""
        if self._effect.opacity() <= 0.01:
            self.hide()


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
        # 预览状态标识的两份"账"(见 _preview_state):
        #   _shown_token —— 屏幕上那张图对应的参数指纹(只有**成功**上屏才更新)
        #   _preview_err —— 最近一次尝试的失败原因(None=最近一次是成功的)
        self._shown_token = None
        self._preview_err = None
        # 撤销/重做(见 _begin_undo): 栈 + 在途事务 + 收尾定时器
        self._undo_stack, self._redo_stack, self._undo_txn = [], [], None
        # 当前界面语言(启动时 main.py 已按 settings['lang'] 装好翻译器)
        self._lang = (state.settings or {}).get("lang") or i18n.DEFAULT_LANG

        self.setWindowTitle(self.tr("镶 · 水印（Qt 版）"))
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

        `_apply_params` 已处理镜像行、显隐联动、模型映射表与「水印将显示」那行, 并触发保存与预览;
        `model_map` 在 `_params` 里, 会跟着回去(并写回 model_map.json, 见 `_persist_model_map`)。
        不要再调 `_reload_model_map()`: 从磁盘重读会盖掉快照里那份, 等于把刚撤回的改动装回来。
        """
        self._apply_params(snap)

    def undo(self):
        self._commit_undo()                     # 在途的那笔先落定, 再撤它
        if not self._undo_stack:
            return
        e = self._undo_stack.pop()
        self._restore(e["before"])
        self._redo_stack.append(e)
        self._notify(self.tr("已撤销：{label}").format(label=e["label"]))
        self._sync_undo_btns()

    def redo(self):
        self._commit_undo()
        if not self._redo_stack:
            return
        e = self._redo_stack.pop()
        self._restore(e["after"])
        self._undo_stack.append(e)
        self._notify(self.tr("已重做：{label}").format(label=e["label"]))
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

    def _vis_rule(self, widget, predicate):
        """给"不在参数表里的行"登记显隐条件(目前只有「单张 / 批量」那几行)。

        它们随的是"当前模式"这类状态、不是某个参数, 用不了 only_if。但改显隐必须走同一个出口:
        各自 setVisible 会绕过"冻结重绘 + 容器当场定高度"这两步, 点「单张 / 批量」就会像点联动
        开关那样抽一下。登记到 _vis_rules 后与参数行一起在 _sync_visibility 里算。
        """
        self._vis_rules.append((widget, predicate))

    def _sync_visibility(self):
        """**唯一改显隐的地方**: 参数行按 only_if / only_if_not, 手写行按 _vis_rules。

        整批改完再放开重绘: 逐个 setVisible 会触发多次布局, 而卡片带 QGraphicsDropShadowEffect
        (渲染到离屏图), 尺寸一变会先贴出旧尺寸的图 —— 表现为"先跳一下再闪现出子选项"。

        只冻结重绘不够: 布局与几何的传播要等下一轮事件, 冻结跨不过那一轮, 收尾必须把该传播的当场
        传播完(见 finally), 否则下一轮照样画出中间态。
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
            for widget, predicate in self._vis_rules:
                widget.setVisible(bool(predicate()))
        finally:
            if host is not None:
                lay = host.layout()
                if lay is not None:
                    lay.activate()          # 先算完终态布局, 再放开重绘, 避免中间态被画出来
                # 收尾这两步缺一不可:
                #  ① 当场消化挂起的布局请求 —— 隐藏子控件不会立刻让布局缓存失效, 期望高度与最小高度
                #     要等下一轮那次布局请求才更新(收起「文字投影」5 个子行后容器期望仍是 1220,
                #     派发完才变 1050)。
                #  ② 容器当场取下这个高度 —— 容器高度归滚动区管, 同样要等下一轮, 而下一轮在冻结之外。
                #     宽度不动(widgetResizable 会在同步事件里改回去)。
                # 试过让容器高度自己跟着内容走(尺寸约束): 约束把容器最小高度钉在旧值上, "变矮"时
                # resize 被夹回旧高度 —— 只有"变高"不抽、"变矮"照抽, 已弃用。
                QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
                host.resize(host.width(), host.sizeHint().height())
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
        self._notify(self.tr("已重置「{title}」为默认值").format(title=title))

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
        窗口极窄时 QSplitter 会自动钳到各自最小值, 比例优雅退让, 不会挤坏布局。
        实测: 左列最小 415、窗口最小宽 801 —— 40% = 320 < 415, 所以在最小宽度下比例让位给
        各列最小值(该最小宽度的来历见 _build_ui)。
        """
        sp = getattr(self, "_split", None)
        if sp is None or self._split_user_set:
            return
        total = sum(sp.sizes())
        if total <= 0:                  # 首次布局还没走完, sizes() 没有意义
            return
        left = int(total * _SPLIT_LEFT)
        sp.setSizes([left, total - left])
        self._sync_bottom_lead()        # 底栏那组跟着分栏走(见 _sync_bottom_lead)

    def _on_split_moved(self, *_pos):
        self._split_user_set = True
        self._sync_bottom_lead()        # 用户拖过分隔条: 底栏那组也要跟着

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_split_ratio()
        # 用户拖过分隔条后 _apply_split_ratio 会直接返回(不再自动分配比例), 但"分隔条占宽 =
        # 分隔条宽度 − sizes 之和"会随窗口尺寸变 —— 底栏那段留白得跟着重算。
        self._sync_bottom_lead()
        # 吐司是绝对定位的浮层, 窗口一改尺寸就得重算(在它显示的那 3.2 秒里窗口可能被拖过)
        if getattr(self, "_toast", None) is not None and self._toast.isVisible():
            self._toast.reflow()

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
            # 底栏那段留白: 首帧算一次, 再补一次 —— 首帧窗口尚未映射, 拿不到分栏面板的最终坐标
            # (显式 activate() 也一样, 实测差 1px), 延到下一轮事件循环才准。
            self._sync_bottom_lead()
            QTimer.singleShot(0, self, self._sync_bottom_lead)

    # ---------- 构建 ----------
    def _build_ui(self):
        # 预览控件先建: 页签里的参数行在构造时就要连它的 set_highlight
        self._preview = PreviewBox()
        self._only_if = {s["key"]: s["only_if"] for s in PARAM_SPECS if s.get("only_if")}
        self._only_if_not = {s["key"]: s["only_if_not"] for s in PARAM_SPECS if s.get("only_if_not")}
        self._spec_by_key = {s["key"]: s for s in PARAM_SPECS if s.get("key")}
        self._controllers = {c[0] for c in list(self._only_if.values()) + list(self._only_if_not.values())}
        # 不在参数表里的行的显隐条件: [(控件, 判据)] —— 随的是状态而不是某个参数(见 _vis_rule)
        self._vis_rules = []

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
        # 左列的最小宽度取"三个页签宿主的期望最小宽度里最大的那个 + 竖向滚动条 + 两侧 INSET_COL"。
        # 不能改由页签行决定: 滚动区可滚、报出的最小宽度很小, 左列会被压到"页签行那么窄", 而参数卡
        # 有硬下限(定宽标签 + 滑块 + 数值框) —— 压过头时参数宿主比视口宽, 卡片右边缘与页签卡不再
        # 对齐、还会冒出横向滚动条(实测宿主 386 > 视口 336)。
        min_host = max(t.widget().minimumSizeHint().width()
                       for t in (self._stack.widget(i) for i in range(self._stack.count())))
        left.setMinimumWidth(min_host + _SB_W + 2 * theme.INSET_COL)
        split.addWidget(left)
        split.addWidget(self._build_preview_pane())
        # 左右按 _SPLIT_LEFT 的比例排布(见 _apply_split_ratio)。**不要**设 setStretchFactor:
        # 那套是"初始尺寸之外多出来的空间按权重分", 左列会被冻在初始值上(改造前就是 540 恒定、
        # 右边的预览独吞所有增量), 与"按比例"是两种语义。也不在这里 setSizes: 首次布局前
        # sizes() 还没有意义, 初值由 showEvent 里的 _apply_split_ratio() 给。
        split.setChildrenCollapsible(False)   # 左列变窄后防止被拖塌
        split.splitterMoved.connect(self._on_split_moved)
        outer.addWidget(split, 1)

        self._bottombar = self._build_bottombar()   # 留引用: 吐司要浮在它之上(见 Toast._place)
        outer.addWidget(self._bottombar)
        # 吐司: 挂在中央部件上的浮层(不是独立窗口, 理由见 Toast 的类注释)。**最后建、raise_(),
        # 保证它在所有控件之上** —— 它要盖在自己的布局之上而不参与布局。
        self._toast = Toast(root)
        # **状态栏整条删了**(它原来是"一次性结果"与"常驻状态"挤在一行的通道):
        #   · 一次性结果 -> 吐司(_notify);
        #   · 那几条常驻状态本来就有别的家在(文件名在顶栏与「输入图片」行、张数在「处理模式」卡片、
        #     启动提示就是预览框里那句) -> 直接删, 不是搬;
        #   · 耗时数字 -> 预览标题条右侧(见 _build_preview_header); 会话统计 -> 「关于」弹窗。
        # 净赚 28px 预览高度, 而右列本来就只剩"标题条 + 预览"两段, 那条"两列底边齐平"的不变量不变。

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
        # 「入口」按钮: 顶栏这一排都是中性小按钮, 白底的它不显眼(见 theme 里 #entry 那段注释)
        self._open_btn.setObjectName("entry")
        self._open_btn.clicked.connect(self._pick_image)
        t.addWidget(self._open_btn)
        self._input_label = QLabel(self.tr("未选择图片"))
        self._input_label.setObjectName("muted")
        self._input_label.setMinimumWidth(160)
        t.addWidget(self._input_label, 1)
        # 「识别型号」不再放顶栏: 它和「型号美化」卡里的那一行显示的是同一件事, 两处重复
        # (用户报"功能上冲突"), 现在统一到卡片里(见 _build_model_card)。
        # 撤销 / 重做放在顶栏、「EXIF信息」左侧。原先在左列页签卡内部的右端(那里有 addStretch,
        # 不破坏"页签卡与参数卡等宽"这条不变量); 但那一处离被撤销的参数卡隔着半屏, 而且带上标题
        # 文字后页签卡放不下 —— 移到顶栏后与「EXIF信息」「恢复默认设置」并排, 宽度由文字决定。
        self._undo_btn = QPushButton(self.tr("↶ 撤销"))
        self._redo_btn = QPushButton(self.tr("↷ 重做"))
        for b, slot in ((self._undo_btn, self.undo), (self._redo_btn, self.redo)):
            b.setObjectName("mini")
            b.setEnabled(False)                  # 栈空时置灰, 见 _sync_undo_btns
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            t.addWidget(b)
        self._sync_undo_btns()
        # ℹ 图片信息: 没图可看时置灰(见 _sync_info_btn)。带标题文字 —— 单独的 ℹ 认不出是什么,
        # 与旁边「↶ 撤销 / ↷ 重做」是同一套观感。
        self._info_btn = QPushButton(self.tr("ℹ EXIF信息"))
        self._info_btn.setObjectName("mini")
        self._info_btn.setCursor(Qt.PointingHandCursor)
        self._info_btn.setToolTip(self.tr("查看照片 EXIF 与文件信息"))
        self._info_btn.setAccessibleName(self.tr("EXIF信息"))
        self._info_btn.clicked.connect(self._show_image_info)
        t.addWidget(self._info_btn)
        reset = QPushButton(self.tr("恢复默认设置"))
        reset.setObjectName("mini")
        reset.setToolTip(self.tr("将所有参数恢复为出厂默认值"))
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._reset_settings)
        t.addWidget(reset)
        return top

    def _build_menu(self):
        """顶栏 ☰ 的菜单。长尾功能都收在这里 —— 界面是"全平铺"的, 不能再往顶栏堆按钮。"""
        m = QMenu(self)
        m.addAction(self.tr("关于"), self._show_about)
        m.addSeparator()
        m.addAction(self.tr("预设方案…"), self._open_preset_dialog)
        # 与「预设方案…」并列: 都是"偶尔才动一次"的东西, 收进菜单不占页签的高度
        m.addAction(self.tr("字体文件…"), self._open_font_dialog)
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
        # **可选语言码跟在文字后面**(「语言  CN/EN」): 「语言」二字非中文使用者认不出, 加上码就
        # 一眼知道这个入口是"语言切换、可选中文与英文"**——两个码都写出来, 不是只显示当前那个**
        # (它说明的是"这个入口是干嘛的"; 当前选的是哪个由子菜单里的 ✓ 表示)。
        # 放在**文字里**而不是当图标: ① Qt 的菜单图标只能画在**左侧**, 而且会给整份菜单留出图标列
        # (其余项一起右移), 放不到文字右边; ② 图标与勾号共用左侧同一列, 一挂图标就不画 ✓ 了
        # (上一版就这么把勾号吃掉的)。做进文字则两样都不动。
        # 码本身**不翻译**; 子菜单里的两项仍**各用母语写自己**(「中文」/「English」, 也不拿去 tr())。
        self._lang_menu = lang
        self._lang_acts = []
        for code, label, badge in i18n.LANGS:
            act = lang.addAction(label)
            act.setCheckable(True)
            act.setChecked(code == self._lang)
            act.triggered.connect(lambda _c, co=code: self._set_lang(co))
            self._lang_acts.append((code, badge, act))
        self._sync_lang_menu()
        return m

    def _sync_lang_menu(self):
        """刷这一行(文字里的可选语言码)与子菜单的勾号。

        文字是**从 `i18n.LANGS` 现算**的(加了语言不用再手改这一行); 勾号跟着 `self._lang`
        —— 切语言要重启才生效(见 _set_lang), 但**设置是当场就存下的**, 菜单还显示旧值会让人
        以为没点上。
        """
        if not hasattr(self, "_lang_menu"):
            return
        self._lang_menu.menuAction().setText(
            self.tr("语言") + "  " + "/".join(b for _c, b, _a in self._lang_acts))
        for c, _b, act in self._lang_acts:
            act.setChecked(c == self._lang)

    def _set_lang(self, code):
        """切语言: 写进设置, 提示**自己关掉重开**才生效。

        **不做热切换, 也不自动重启**: 参数面板那 122 处文案是建窗时 tr() 出来的, 热切换得把
        整窗重建一遍(批量生成/预览线程还在跑时风险高); 自动重启则要走"先放掉单实例管道再起新
        进程"那套, 从 IDE/快捷方式等不同入口启动时未必都起得来。交给用户手动重启最稳。
        """
        if code == self._lang:
            return
        self._lang = code
        self._sync_lang_menu()          # 菜单当场反映新选的语言(界面文案要重启才变, 见类注释)
        try:
            self.state.settings["lang"] = code
            self._save_settings_now()               # 合并式保存: 只多写一个 lang 键
        except Exception as e:                      # noqa: BLE001 - 存不上不影响这次运行
            logging.warning(f"[i18n] 保存语言设置失败: {e}")
        _message(self, QMessageBox.Icon.Information, self.tr("语言"),
                 self.tr("界面语言已保存，请关闭程序后重新打开生效。"))

    def _show_about(self):
        """关于: 品牌区 + 版本/环境 + 数据目录 + 分发许可(见 AboutDialog)。"""
        AboutDialog(self).exec()

    def _open_log_dir(self):
        _open_and_report(self, core.LOG_DIR, "日志目录")

    def _open_data_dir(self):
        _open_and_report(self, core.DATA_DIR, "数据目录")

    def _show_image_info(self):
        """「图片信息」弹窗: 文件信息 + 照片自己的 EXIF(如实报告, 不推算"最后会印成什么")。

        要看"水印里到底会显示什么"就去看「识别型号」那一行的 tooltip —— 那件事要综合「美化型号」
        /「型号映射」/「自定义水印信息」, 不该跟"这张图里存了什么"混在同一张表里(见 ImageInfoDialog)。
        """
        path = getattr(self, "_preview_src", "")
        if not path or not os.path.isfile(path):
            self._notify(self.tr("请先选择图片"))
            return
        dlg = ImageInfoDialog(path, self)
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
        + 1px 边框 ×2, 这里用同一套算法(竖向 6px 内边距 + 同一个 28px 高的按钮), 于是
        两块的上沿、下沿在同一条线上。这是拿预览高度换来的(42px + GAP_CARD = 52px), 是
        "两列顶部协调"的代价; 想省下来就得让标题条浮在预览之上(不占布局高度), 见 README。
        里面三样: 「效果预览」标题 + 状态标识(见 PreviewStateChip) + 「↺ 刷新预览效果」
        (幽灵按钮 #ghost: 无描边无底色, 悬停才浮出色块 —— 它是次要动作, 见那里的注释)。
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
        # 状态标识紧跟在标题右边(而不是挨着那个刷新按钮): 它说的是"这张图与当前参数的关系",
        # 读起来是「效果预览 · 已按当前参数渲染」, 属于标题; 挪到右边就会与真按钮并排、被当成
        # 第二个按钮。它**不参与撑高标题条**(固定 20px, 见 PV_CHIP_H), 42px 那条不变量不受影响。
        self._state_chip = PreviewStateChip(self._preview_header)
        h.addWidget(self._state_chip)
        h.addStretch(1)
        self._refresh_btn = QPushButton(self.tr("↺ 刷新预览效果"))
        # **幽灵按钮**(#ghost: 无描边无底色, 悬停才浮出色块), 不是 #mini —— 它是"再来一次"的
        # 次要动作, 而 #mini 是迷你号的普通按钮(带描边 + 卡片白底 + 28px 高), 摆在标题条上
        # 看着就是个正经按钮, 分量比它该有的大(用户: "应该是次要的")。
        self._refresh_btn.setObjectName("ghost")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._refresh_preview_clicked)
        # 上次渲染的耗时: 原来在状态栏右侧, 状态栏删掉后挪到这儿 —— 它跟预览最相关, 放标题条里
        # 比放底栏(与「导出到」的路径共一条伸缩带)更顺; 等宽小字弱色, 与按钮同排不抢。
        # 数字每次渲染会跳, 但那是"仪表"行为(不是 README 里禁的那种整句状态消息刷屏)。
        # **放在按钮左边**: 右边那个位置是「↺ 刷新预览效果」的(它是这一行的落点动作)。
        self._perf = QLabel("")
        self._perf.setStyleSheet("color:%s; font-family:Consolas,monospace; background:transparent;"
                                 % theme.MUTED)
        h.addWidget(self._perf)
        h.addWidget(self._refresh_btn)
        return self._preview_header

    def _build_bottombar(self):
        bottom = QFrame()
        b = QHBoxLayout(bottom)
        b.setContentsMargins(theme.INSET_BAR, 0, 0, 0)   # 与顶栏、页签行同一条左对齐线
        b.setSpacing(8)
        # 「导出到 …」这一组(导出到 / 路径 / 选择输出目录)与**预览框左缘对齐**: 这几件说的都是
        # "输出到哪", 与右侧预览/生成同属一列; 左列下方那段留白是有意的。宽度随分栏走, 由
        # _sync_bottom_lead 算。
        # 用忽略型间距项、不用固定宽度的控件: 固定宽度会算进布局的最小宽度 —— 生成时那条 200px
        # 进度条一出现, 底栏最小宽会顶到 994 > 窗口最小宽 802, 窗口自己变宽; 忽略型的最小贡献是 0。
        # (底栏控件的引用叫 _bottombar, 由 _build_ui 赋值 —— 吐司要浮在它之上, 见 Toast._place)
        self._bottom_lead = QSpacerItem(0, 0, QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Minimum)
        b.addSpacerItem(self._bottom_lead)
        b.addWidget(QLabel(self.tr("导出到")))
        self._out_label = ElidedLabel(self.tr("（跟随原图目录）"))
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

    def _sync_bottom_lead(self):
        """底栏那段留白 = 预览框左缘 − 底栏内容左缘, 把「导出到」推到预览框左缘上。

        用坐标实测, 不用推算: 预览框左缘与底栏内容左缘都换算到根控件同一坐标系再相减。
        (按分栏推算过一版 —— 左列宽 + 分隔条占宽 + INSET_COL − INSET_BAR, 其中"分隔条占宽"要用
        `sizes() 之和与分隔条宽度之差` 反推, 实测在 0 与 −1 之间抖, 会差 1px。)
        调用点都在分栏/窗口刚定完之后(setSizes 后、拖分隔条后、resizeEvent、首帧); 底栏在根布局里,
        与分栏互不影响。挤不下时间距项让路(整组左移), 不影响窗口最小宽度。
        """
        lead = getattr(self, "_bottom_lead", None)
        bar = getattr(self, "_bottombar", None)
        if lead is None or bar is None or getattr(self, "_preview", None) is None:
            return
        host = self.centralWidget()
        if host is None:
            return
        want = (self._preview.mapTo(host, QPoint(0, 0)).x()
                - bar.mapTo(host, QPoint(0, 0)).x() - theme.INSET_BAR)
        lead.changeSize(max(0, want), 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        if bar.layout() is not None:
            bar.layout().invalidate()           # changeSize 只是改了数据, 得让布局重算一次

    def _build_tab(self, tab_id):
        # s.get("tab"): 有些行不在任何页签里(它们收进了弹窗, 见 PARAM_SPECS 开头那几条)
        specs = [s for s in PARAM_SPECS if s.get("tab") == tab_id]
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
            # 预设不在这里了: 它搬进了 ☰ 菜单的「预设方案…」弹窗(见 PresetDialog)
            for builder in (self._build_mode_card, self._build_model_card,
                            self._build_manual_card):
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
        # (撤销 / 重做**不在**这里: 它们已移到顶栏「EXIF信息」左边, 见 _build_topbar —— 那个位置
        #  离被撤销的参数卡更近, 也放得下标题文字。页签卡右端只剩这条 addStretch, 卡宽与下方参数卡
        #  仍然严格等宽, 那条不变量不受影响。)
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
        """水印开关: 行=文字种类, 列=显示/粗体/斜体。标题从「字体样式」改叫「水印开关」, 因为
        这格里全是要"开/关"的东西(而且现在长得就是开关), 「样式」那个词反而像在说参数值。

        原来做成「Logo 文字 / └ 粗体 / └ 斜体 / 型号文字 / …」的平铺复选列表,
        三层归属全靠 └ 前缀暗示, 读起来是一串没有结构的复选框。改成网格后
        """
        card = Card(QT_TRANSLATE_NOOP("Card", "水印开关"))
        card.reset_btn.clicked.connect(lambda _c, cd=card: self._reset_card(cd))
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        # 横向列间距给 0: 右侧三列是**等分**的, 间距由三列自己吸收(见下面 setColumnStretch),
        # 再叠一层列间距会让三个格位各自偏一点
        hsp = 0
        grid.setHorizontalSpacing(hsp)
        grid.setVerticalSpacing(5)
        # 第 0 列宽度按"让第一列控件落在其它卡片的控件列上"反推:
        #   本卡: 12 + col0 + hsp   其它卡片: 12 + _LABEL_W + GAP_CTRL
        # 自适应的话 col0 只有约 85px, 网格里的列会比其它卡片靠左, 同一页两条缩进线不齐
        grid.setColumnMinimumWidth(0, _LABEL_W + theme.GAP_CTRL - hsp)
        # 右侧三列**三等分**(用户: "改为右侧区域三等分"): 三列都写 stretch=1, 余量等比摊分;
        # 三列的最小宽度又都一样(格位就是 36px 的开关本身, 列头两字更窄), 所以算下来正好三等分 ——
        # 每个列头都落在它下面那个开关的正上方。以前这里是把**多余的空白列**设成 stretch=1,
        # 结果三列挤在左边、右边空一大片, 列头和开关也就贴在一起。
        for c in range(1, len(FONT_STYLE_COLS) + 1):
            grid.setColumnStretch(c, 1)
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
                # 格位给**空标签**(不是空格): 空标签下这个 QCheckBox 的宽度就是开关本身(配
                # QSS 的 `#cell { spacing: 0px }`), AlignCenter 居中之后才与列头精确对齐;
                # 语义靠无障碍名与提示补上(格位没有可见文字)
                row = ParamRow(dict(key=key, label="", kind="bool"), self._params.get(key))
                row.hovered.connect(self._preview.set_highlight)
                row.chk.setObjectName("cell")
                row.chk.setAccessibleName(f"{name}·{FONT_STYLE_COLS[c - 1]}")
                row.chk.setToolTip(f"{name} · {FONT_STYLE_COLS[c - 1]}")
                row.changed.connect(self._on_param)
                self._rows[key] = row
                card.keys.append(key)
                grid.addWidget(row, r, c, Qt.AlignmentFlag.AlignCenter)
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
        # 这一行**必须走 _row 那套栅格**(定宽标签 + 控制列), 不能自成一整行的胶囊:
        # 胶囊的文字天生要留横向内边距, 所以它贴不到卡片内容左线 —— 以前它带 addStretch 独占一行,
        # 结果是"文字比下面的标签右移 14px、右边界又落在控制列中间"(用户报"大小对不上, 缩进也对不上";
        # 实测胶囊文字 x=27 vs 标签 x=13、右边界 175 vs 控制列 149..438)。放进控制列之后:
        # 标签贴左线、胶囊的左/右边界就是控制列的左/右边界, 三条线全对上。
        seg = QWidget()
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(6)
        self._mode_btns = {}
        for key, text in (("single", QT_TRANSLATE_NOOP("MainWindow", "单张图片")),
                          ("batch", QT_TRANSLATE_NOOP("MainWindow", "批量目录"))):
            b = QPushButton(self.tr(text))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c, k=key: self._set_mode(k))
            self._mode_btns[key] = b
            sh.addWidget(b, 1)          # 各占一半: **不写死宽度** —— 窗口拉宽时两格跟着分
        # (两格会是 142/141: 控制列宽 289 减 6px 间距是奇数, 除不尽 —— 外边缘与栅格严丝合缝,
        #  内侧那 1px 差看不出; 要两格严格等宽就得留 1px 空档, 那反而更没道理)
        card.body.addWidget(self._row(QT_TRANSLATE_NOOP("MainWindow", "模式"), seg))

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

        # 这四行的显隐随「单张 / 批量」而变 —— 注册进统一出口, 不在这里自己 setVisible(见 _vis_rule)
        self._vis_rule(self._row_single, lambda: self._mode == "single")
        for w in (self._row_batch, self._row_batch_rec, self._batch_count):
            self._vis_rule(w, lambda: self._mode != "single")
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
        self._model_edit.setPlaceholderText(self.tr("临时美化型号（仅本次有效）"))
        self._model_edit.setToolTip(self.tr("留空则使用映射表；填写后强制使用该名称"))
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
        下面那个 tooltip 里(「识别型号」那一行显示的是**识别到的**型号, 两件事不混着说; 「图片
        信息」弹窗只如实报照片自己的 EXIF, 不推算这个)。
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
            tips.append(self.tr("水印将显示：{name}").format(name=name))
        self._model_lab.setToolTip("\n".join(tips))
        # 「复制原始型号」: 没有识别到型号(没选图 / 图里没 EXIF)时置灰 —— 以前它一直是 #mini 的
        # 灰字样式, 明明可点却看着像禁用了(用户报过), 现在改成普通按钮 + 真的按状态置灰
        self._copy_btn.setEnabled(bool(raw))
        self._copy_btn.setToolTip(self.tr("复制识别到的型号到剪贴板") if raw
                                  else self.tr("未识别到型号（请先选择图片）"))

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
                  QT_TRANSLATE_NOOP("MainWindow", "自动补充 s 后缀")),
                 ("fnumber", QT_TRANSLATE_NOOP("MainWindow", "光圈"),
                  QT_TRANSLATE_NOOP("MainWindow", "如 2.8"),
                  QT_TRANSLATE_NOOP("MainWindow", "自动补充 f/ 前缀")),
                 ("iso", "ISO", QT_TRANSLATE_NOOP("MainWindow", "如 100"),
                  QT_TRANSLATE_NOOP("MainWindow", "自动补充 ISO 前缀")))
        for key, label, ph, tip in specs:
            e = QLineEdit()
            e.setPlaceholderText(self.tr(ph))
            e.setToolTip(self.tr(tip))
            e.setEnabled(False)
            e.textChanged.connect(lambda t, k=key: self._on_manual_field(k, t))
            self._manual_edits[key] = e
            card.body.addWidget(self._row(label, e))
        return card

    def _open_preset_dialog(self):
        """打开「预设方案」弹窗(构造即读盘, 见 PresetDialog)。"""
        dlg = PresetDialog(self)
        dlg.exec()

    def _open_logo_dir(self):
        """

        这个目录**不会被自动创建**(ensure_dir 只管日志/字体/预览三个), 所以第一次点它时先
        """
        path = self._params.get("logo_dir") or core.DEFAULT_LOGO_DIR
        try:
            os.makedirs(path, exist_ok=True)        # 已存在时不报错
        except Exception as e:                      # noqa: BLE001 - 建不出来就退化成"报一句"
            self._notify(self.tr("Logo 目录不可用：{err}").format(err=e))
            logging.warning(f"[目录] 创建 Logo 目录失败: {path} ({e})")
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self._notify(self.tr("已打开 Logo 目录：{path}").format(path=path))
        else:
            self._notify(self.tr("无法打开 Logo 目录：{path}").format(path=path))

    def _reload_presets(self, view):
        """把 10 个槽位列进 view 的下拉(显示各自存着的方案名, 空槽显示「— 空 —」)。

        `view` 就是 PresetDialog(控件在它那儿); 预设数据仍缓存在窗口的 self._preset_data。
        键是槽位号(字符串), 与 `core.preset_path(slot)` 的槽位一致。
        """
        try:
            self._preset_data = self.state.load_presets() or {}
        except Exception as e:                      # noqa: BLE001
            logging.warning(f"[预设] 读取失败: {e}")
            self._preset_data = {}
        cur = view.current_slot()
        view.combo.clear()
        for i in range(1, _PRESET_SLOTS + 1):
            key = str(i)
            rec = self._preset_data.get(key) or {}
            name = rec.get("_preset_name") if isinstance(rec, dict) else None
            view.combo.addItem(self.tr("槽位 {slot}：{name}").format(
                slot=i, name=name or self.tr("— 空 —")), key)
        if cur:
            j = view.combo.findData(cur)
            if j >= 0:
                view.combo.setCurrentIndex(j)

    def _preset_load(self, view):
        key = view.current_slot()
        p = self._preset_data.get(key) if key else None
        if not isinstance(p, dict):
            self._notify(self.tr("该槽位为空，无法加载"))
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
        view.set_name(name)
        self._notify(self.tr("已加载预设：{name}").format(name=name))

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

    def _preset_save(self, view):
        """把当前调参存进**当前选中的槽位**(方案名只是存在文件里的一个名字)。

        槽位里已有方案而名字又变了(等于覆盖另一个方案)时先问一句; 名字没变就是"存回它", 不问。
        """
        key = view.current_slot()
        if not key:
            return
        name = view.name_text()
        if not name:
            self._notify(self.tr("请先输入方案名后再保存"))
            return
        cur = self._preset_data.get(key) or {}
        cur_name = cur.get("_preset_name") if isinstance(cur, dict) else None
        if cur_name and cur_name != name:
            if _message(self, QMessageBox.Icon.Question, self.tr("保存预设"),
                        self.tr("槽位 {slot} 已有方案「{name}」，保存会覆盖它。继续？")
                        .format(slot=key, name=cur_name),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        d = self.state.preset_save(key, self._preset_payload(), name)
        if not d.get("ok"):
            _message(self, QMessageBox.Icon.Warning, self.tr("保存预设"),
                     str(d.get("error") or ""))
            return
        view.reload()
        view.combo.setCurrentIndex(view.combo.findData(key))
        logging.info(f"[预设] 已保存: 槽位 {key}「{name}」-> {core.preset_path(key)}")
        self._notify(self.tr("已保存到槽位 {slot}：{name}").format(slot=key, name=name))

    def _export_preset(self, view):
        """把当前选中的预设导出成一个 .json(给别人用)。

        导出的是**剥掉机器路径**的那份: logo_dir 与 4 个字体路径换台机器就是死链, 带出去只会
        让对方拿到一个静默降级的方案(品牌 logo 变文字、字体回落到内置字体)。
        """
        key = view.current_slot()
        rec = self._preset_data.get(key) if key else None
        if not isinstance(rec, dict):
            self._notify(self.tr("该槽位为空，没有可导出的方案"))
            return
        name = rec.get("_preset_name") or f"方案 {key}"
        # 默认文件名只是给对话框提个建议(用户可改), 所以顺手把 Windows 不认的字符换掉;
        # 程序自己不靠这个名字认任何东西(槽位文件名固定是 presets1..10)
        suggest = "".join("_" if c in ILLEGAL_FN_CHARS else c for c in name).strip() or f"preset{key}"
        path = _save_file_dialog(self, self.tr("导出预设"), "", f"{suggest}.json",
                                 self.tr("预设 (*.json)"))
        if not path:
            return
        data = {k: v for k, v in rec.items() if k not in _PRESET_MACHINE_KEYS}
        data["_format"] = _PRESET_FORMAT
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:                      # noqa: BLE001 - 写不出去就报一句, 不弹窗
            self._notify(self.tr("导出失败：{err}").format(err=e))
            logging.warning(f"[预设] 导出失败 {path}: {e}")
            return
        logging.info(f"[预设] 已导出槽位 {key}「{name}」-> {path} (剔除机器路径) 共 {len(data)} 键")
        self._notify(self.tr("已导出方案「{name}」").format(name=name))

    def _import_preset(self, view):
        """把一个 .json 导入成预设。

        预设名取自文件里的 `_preset_name`, 没有再退回文件名 —— 而**文件里的机器路径键一律忽略**
        (不吃别人的绝对路径): 导入进来的东西只该包含"方案参数"。
        同名会覆盖, 覆盖前问一句。
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
                     self.tr("无法读取该文件：\n{err}").format(err=e))
            return
        if not isinstance(data, dict) or not data:
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"), self.tr("该文件中没有方案数据。"))
            return
        # 只吃"单个方案"。老版 presets.json(槽位版)是"槽位 -> 方案"的字典, 判据是"有没有方案参数键"
        if not any(k in data for k in ("bg_color", "canvas_ratio_mode", "canvas_ratio",
                                      "font_path", "layout_mode")):
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"),
                     self.tr("该文件不是单个方案格式（可能是旧版 presets.json）。"
                             "请先用「导出预设」导出单个方案文件，再导入。"))
            return
        name = str(data.get("_preset_name") or os.path.splitext(os.path.basename(path))[0])
        payload = {k: v for k, v in data.items()
                   if k not in _PRESET_MACHINE_KEYS and k != "_format"}
        # 导入目标是**当前选中的槽位**(不再弹"导入到哪个槽位"): 想换目标先在下拉里选好槽位再来
        key = view.current_slot()
        if not key:
            return
        cur = self._preset_data.get(key) or {}
        cur_name = cur.get("_preset_name") if isinstance(cur, dict) else None
        if cur_name and _message(self, QMessageBox.Icon.Question, self.tr("导入预设"),
                                 self.tr("槽位 {slot} 已有方案「{name}」，导入会覆盖它。继续？")
                                 .format(slot=key, name=cur_name),
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        d = self.state.preset_save(key, migrate_preset(payload), name)
        if not d.get("ok"):
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"),
                     str(d.get("error") or ""))
            return
        view.reload()
        view.combo.setCurrentIndex(view.combo.findData(key))
        logging.info(f"[预设] 已导入 {path} -> 槽位 {key}「{name}」（{'覆盖' if cur_name else '空槽'}）")
        self._notify(self.tr("已导入方案「{name}」到槽位 {slot}").format(name=name, slot=key))

    def _preset_delete(self, view):
        key = view.current_slot()
        if not key or not isinstance(self._preset_data.get(key), dict):
            self._notify(self.tr("该槽位为空，无需删除"))
            return
        name = self._preset_data[key].get("_preset_name") or key
        if _message(self, QMessageBox.Icon.Question, self.tr("删除预设"),
                    self.tr("确定删除「{name}」？").format(name=name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        d = self.state.preset_delete(key)
        if not d.get("ok"):
            _message(self, QMessageBox.Icon.Warning, self.tr("删除预设"), str(d.get("error") or ""))
            return
        view.reload()
        view.set_name("")
        self._notify(self.tr("已删除槽位 {slot}：{name}").format(slot=key, name=name))

    def _apply_params(self, src):
        """把一整份参数铺回(加载预设 / 恢复默认 / 撤销重做用)。只吃已知键, 未知键忽略。

        **按参数遍历, 不是按控件遍历**: 有些参数没有控件(如 gap_image_top/gap_top, 它们
        只由上下分离下的镜像行代写), 按控件遍历会把它们漏掉 —— 表现为"加载预设后某些间距
        """
        map_before = dict(self._params.get("model_map") or {})
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
        self._persist_model_map(map_before)
        self._update_effective_model()      # 「水印将显示：X」依赖映射表, 跟着刷一次
        self._touch()

    def _persist_model_map(self, before):
        """整批铺参数改动过映射表时, 把内存那份写回 model_map.json。

        映射表的家在 logo 目录下的 model_map.json, `_params["model_map"]` 只是内存里的副本。只改内存
        不写文件的话, 撤销"看着生效"(预览里水印变回旧名), 但一重开映射弹窗(读文件)改动又回来了。
        写盘与弹窗里的 `_persist()` 是同一份文件、同一套函数。
        只在真的变了时写: 「恢复默认设置」与不含映射表的预设都不动这个文件。
        """
        cur = dict(self._params.get("model_map") or {})
        if cur == before:
            return
        try:
            core.save_model_map(self._params.get("logo_dir") or core.DEFAULT_LOGO_DIR, cur)
        except Exception as e:                      # noqa: BLE001
            logging.warning(f"[映射] 撤销/铺参数后写盘失败: {e}")
            self._notify(self.tr("映射表没能写回文件：{err}").format(err=e))

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
            self._notify(self.tr("自动保存失败：{err}").format(err=e))

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

    def _open_font_dialog(self):
        """☰ →「字体文件…」: 字体文件收进弹窗管理(见 FontDialog)。

        不需要收尾: 弹窗里每次改文件都走 `_on_param`(进撤销、写设置、重渲预览), 已是即时生效。
        """
        FontDialog(self.state, self).exec()

    def _open_map_dialog(self):
        dlg = MapDialog(self.state, self)
        dlg.exec()
        # 撤销由弹窗逐步记(见 MapDialog._apply_change), 这里只收尾: 内存与文件对齐一次
        # (写盘失败时弹窗会回滚内存, 那份要以文件为准), 并重渲预览。
        self._reload_model_map()
        self._touch()

    def _reset_settings(self):
        """

        注意 logo_dir 不在参数行里(没有控件), 得单独从 default_params 取回。
        """
        if _message(self, QMessageBox.Icon.Question, self.tr("恢复默认设置"),
                    self.tr("将把当前所有参数恢复为出厂默认，现有调整会丢失。继续？"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        d = core.default_params()

        def _do():
            self._apply_params(d)
            self._params["logo_dir"] = d.get("logo_dir") or self._params.get("logo_dir")
            self._reload_model_map()

        # 整批算一步撤销(快照里连 logo_dir / model_map 一起存, 所以撤得回来)
        self._undo_step("__reset_settings__", "恢复默认设置", _do)
        self._notify(self.tr("已恢复默认设置"))

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
        # 「单张 / 批量」那四行的显隐走统一出口(冻结重绘 + 容器当场定高度), 见 _vis_rule
        self._sync_visibility()
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
            self._preview.set_hint(self.tr("请拖入图片，或点击「打开图片」"))
        return True

    def _sync_files_ui(self):
        """「文件与美化」页签里随处理模式变化的文案。

        只刷文案, 不管显隐 —— 那四行注册在 _vis_rules(见 _vis_rule), 由 _set_mode 走统一的
        _sync_visibility。
        """
        single = self._mode == "single"
        if not single and self._batch_files:
            self._batch_count.setText(self.tr("已载入 {n} 张（预览与调参取第一张）")
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
            self._notify(self.tr("所选目录里没有受支持的图片")
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
        self._notify(self.tr("已载入 {n} 张图片，可开始生成").format(n=len(items)))

    # ---------- 预览 ----------
    def _manual_exif(self):
        return dict(self._manual) if self._manual_on else None

    def _refresh_preview_clicked(self):
        """

        自动预览之外的单入口: 渲染失败、或想对同一组参数再跑一次时用。点下去先按灰、等响应
        状态标识本来就会一直显示「渲染中…」。
        """
        if not getattr(self, "_preview_src", ""):
            self._notify(self.tr("请先选择图片（也可直接拖入图片或文件夹）"))
            return
        self._manual_preview = True
        self._refresh_btn.setEnabled(False)
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
            self._notify(self.tr("预览已生成"))

    def _sync_refresh_btn(self):
        """没有可预览的图时把「刷新预览效果」按灰。

        常驻一句"请先选择图片"看着像出错了, 所以改成按钮自身置灰 + tooltip 说明。
        """
        if not hasattr(self, "_refresh_btn"):
            return
        has = bool(getattr(self, "_preview_src", ""))
        self._refresh_btn.setEnabled(has)
        self._refresh_btn.setToolTip(self.tr("按当前参数重新渲染预览") if has
                                     else self.tr("请先选择图片后再刷新预览"))

    def _sync_info_btn(self):
        """没图可看时把「ℹ 图片信息」按灰(与「刷新预览效果」同一个口径)。"""
        if not hasattr(self, "_info_btn"):
            return
        self._info_btn.setEnabled(bool(getattr(self, "_preview_src", "")))

    def _preview_payload(self):
        """预览请求的载荷 —— **发请求与算指纹共用这一份**, 免得两边以后漂移。

        指纹必须覆盖"会让这张图变的全部输入": 取样图、全部后端参数、美化/自定义型号、手动
        EXIF。少一项, 改它就会显示成"屏上这张就是当前参数渲的"(错的那一侧)。
        """
        return {
            "input_path": self._preview_src,
            "params": backend_params(self._params),
            "raw_model": self._raw_model,
            "custom_model": self._custom_model,
            "manual_exif": self._manual_exif(),
            # 直接要内存图像: 核心不落盘, 省掉 JPEG 编码/写盘/读回解码(约 95ms/次)
            "inline_image": True,
        }

    def _preview_token(self):
        """当前参数对应的指纹(短哈希)。它与"屏上那帧的指纹"相等 <=> 屏上这帧就是当前参数渲的。

        取哈希而不是自增版本号: 参数改一圈又改回来时, 版本号变了、图其实没变, 而指纹天然
        相等(不会白报一次"待渲染")。序列化一次小字典 + sha1 是微秒级, 每次请求/每帧上屏各算
        一次也不值得缓存。

        `default=repr` 是给"万一有非 JSON 值"兜底用的 —— repr 不会抛异常, 所以这个函数**没有
        失败分支**: 标识宁可少报一次"最新", 也不能因为算不出指纹就报错或说谎。
        """
        raw = json.dumps(self._preview_payload(), sort_keys=True, ensure_ascii=False,
                         default=repr)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def _preview_state(self, queued_hint=False):
        """状态标识的判据(顺序即优先级)。

        `queued_hint` 是回传帧带来的"回传那一刻队列里是否已排着更新的请求" —— 只在
        _on_rendered 里传, 用来堵住"两次渲染的响应在事件队列里排队、界面处理前一帧时后一帧
        还没轮到"这种瞬间(那一刻 _preview_worker.busy() 可能已经空了)。
        """
        if not getattr(self, "_preview_src", ""):
            return "none"
        # 建窗过程中(渲染线程还没起)也可能走到这里, 所以不直接取属性
        worker = getattr(self, "_preview_worker", None)
        if queued_hint or (worker is not None and worker.busy()):
            # **"正在渲"与"改过还没渲"合并成这一态**: 拖滑块时"帧上屏时又改过了"几乎是常态,
            # 各报一态的话两态会来回跳, 而这对用户是同一种事实 —— 还没跟上。真正该报
            # 「待渲染」的是"没人在渲、屏上还是旧参数"(它只在渲染失败后才出现)。
            # 判据(拖动 16 帧: 8 帧判为"还没跟上"、0 次误报"待渲染")见 tools/_probe_preview_state.py 第 ⑤ 组。
            return "rendering"
        if self._preview_err is not None:
            # 失败**优先于**"指纹相等"与"一张都没渲成过":
            #   · 屏上留的是上一张成功的图, 即使参数没变过(指纹相等)也不担保它对应现在的输入
            #     (比如源文件已被换掉/删掉);
            #   · 一张都没成过时(首次渲染就失败), 报「无预览」会把失败藏起来 —— 那正是最需要
            #     告诉用户的一刻。
            return "failed"
        if self._shown_token is None:
            return "none"           # 一张都还没渲成过, 且没有失败
        if self._shown_token != self._preview_token():
            return "stale"
        return "synced"

    def _sync_chip(self, queued_hint=False):
        """把状态标识刷成当前判据的结果。每次请求发出、每帧上屏/失败、取样图变更时各调一次。"""
        if not hasattr(self, "_state_chip"):
            return
        st = self._preview_state(queued_hint)
        tip = ""
        if st == "failed" and self._preview_err:
            # 失败原因来自核心(中文 f-string), 按项目约定在**显示边界**过 tr_msg, 匹配不到就原样
            tip = self.tr(_PV_CHIP_TIP["failed"]) + "\n" + i18n.tr_msg(str(self._preview_err))
        self._state_chip.set_state(st, tip)

    def request_preview(self):
        """请求一次预览。

        画布按核心的缩略档渲染(见核心 PREVIEW_MAX_SIDE; 现 3400, **为放大镜的清晰度**从 2500 提上来的): 预览里的水印字本来就只有几像素高
        (成品 4000px 高、预览框 720px 高 → 字缩到约 1/6), 想看清细节用放大镜(它按像素宽度
        从固有源重采样)。**不要为了"上屏 1:1"去缩小画布** —— 实测那样更糊, 原因见核心
        make_preview 里的注释。
        """
        if not getattr(self, "_preview_src", ""):
            self._sync_chip()
            return
        req = self._preview_payload()
        # 身份标识随请求走, 由渲染线程原样回传: 界面据此知道"屏上这帧是哪一组参数渲的"
        req["token"] = self._preview_token()
        # 不防抖、不作废: 覆盖待渲染槽即可, 渲染线程每轮取最新一份
        self._preview_worker.request(req)
        self._sync_chip()

    def _on_rendered(self, resp):
        if not resp.get("ok"):
            err = i18n.tr_msg(str(resp.get("error") or ""))
            # 屏上那张图**不动**(保留上一张成功的), 但记下失败: 状态标识要据此报「渲染失败」
            # —— 以前失败只在状态栏闪一下、日志留一条, 内存里没留痕, 于是"屏上这张对不上"
            # 这件事在界面上看不出来。
            self._preview_err = err
            self._finish_manual_preview(False)          # 手动刷新失败也要把按钮放回可点(重试入口)
            self._notify(self.tr("预览失败：") + err)
            logging.warning(f"[预览] 渲染失败: {err}")   # 失败也要留痕(以前只在状态栏闪一下)
            self._sync_chip()
            return
        img = resp.get("image")
        if img is not None:
            self._preview.set_image(img)
        # 只有**成功**才更新"屏上指纹": 失败时屏上留的是上一张, 指纹也就还该是上一帧的
        self._shown_token = resp.get("token")
        self._preview_err = None
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
            parts.append(f"渲染期间新增 {arrived} 次请求(上一轮不可中断, 排队主要来自这里)")
        line = " | ".join(parts)
        if ms + queue_ms >= _SLOW_RENDER_MS:
            logging.warning(line + f" [偏慢: 渲染 {ms:.0f}ms]")
        else:
            logging.info(line)

        # 会话统计: 与界面上的 _stats 标签同一份数据源
        self._stats.add_preview(ms=ms, queue_ms=queue_ms, qimg_ms=qimg_ms, e2e_ms=e2e,
                                arrived=arrived)
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
        # 状态标识放最后刷: 它要看的是"这一刻"的判据(回传时队列里有没有已经排上的新请求)
        self._sync_chip(queued_hint=bool(resp.get("queued")))

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
        self._out_label.setText(("" if manual else self.tr("（跟随原图） ")) + path)
        self._out_label.setToolTip(path)

    def _copy_raw_model(self):
        if self._raw_model:
            QApplication.clipboard().setText(self._raw_model)
            self._notify(self.tr("已复制原始型号到剪贴板"))
        else:
            # 按钮在"没识别到型号"时是置灰的, 正常点不到; 这里兜一句免得静默无反应
            self._notify(self.tr("未识别到型号（请先选择图片）"))

    def _on_custom_model(self, text):
        self._custom_model = text.strip()
        self._update_effective_model()
        self.request_preview()

    def set_input(self, path):
        if not os.path.isfile(path):
            self._notify(self.tr("请拖入图片文件"))
            return
        if not self.state._sniff_image_type(path):
            self._notify(self.tr("不支持的图片格式"))
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
        self._notify(self.tr("已选择：{name}").format(name=self._input_name))

    # ---------- 拖放(Qt 原生: 同进程事件, 无跨进程消息) ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self._preview.set_hint(self.tr("松开鼠标以载入"))

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self._preview.set_hint(self.tr("请拖入图片，或点击「打开图片」"))

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.acceptProposedAction()
        self._preview.set_hint(self.tr("请拖入图片，或点击「打开图片」"))
        if not paths:
            self._notify(self.tr("未收到文件"))
            return
        # 拖入目录 = 批量目录; 拖入多张 = 批量列表; 单张 = 单张
        folders = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if not os.path.isdir(p)]
        if folders:
            self._load_batch_dir(folders[0])
            return
        images = [p for p in files if self.state._sniff_image_type(p)]
        if not images:
            self._notify(self.tr("不支持的图片格式"))
            return
        if len(images) == 1:
            self.set_input(images[0])
        else:
            self.load_batch([{"path": p, "name": os.path.basename(p)} for p in images])

    # ---------- 生成 ----------
    def start_generate(self):
        if self._generating:
            self._notify(self.tr("正在处理中，请稍候"))
            return
        if not self._output_dir:
            self._notify(self.tr("请先选择导出目录"))
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
                self._notify(self.tr("请先选择图片"))
                return
            req.update(mode="single", input_path=self._input_path,
                       original_name=self._input_name or "image.jpg")
        else:
            if not self._batch_files:
                self._notify(self.tr("请先选择要批量处理的图片"))
                return
            req.update(mode="batch", file_paths=self._batch_files)
        d = self.state.start_generate(req)
        if not d.get("ok"):
            self._notify(i18n.tr_msg(d.get("error")) or self.tr("无法开始处理"))
            return
        self._generating = True
        self._gen_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._progress.setMaximum(len(self._batch_files) if self._mode == "batch" else 100)

    def _on_event(self, ev):
        t = ev.get("type")
        if t == "progress":
            total = ev.get("total") or 0
            self._progress.setVisible(True)
            self._progress.setMaximum(total or 100)
            done = ev.get("done") or 0
            self._progress.setValue(done)
            # 进度写在**进度条自己身上**(格式 n / N): 状态栏删掉之后, 那一行"处理中 x/y：文件名"
            # 没有去处了 —— 而条本身就摆在那儿。文件名进它的 tooltip(要看清就悬停)。
            if total:
                self._progress.setFormat(f"{done} / {total}")
            name = ev.get("name")
            if name:
                self._progress.setToolTip(i18n.tr_msg(name))
        elif t == "log":
            self._notify(i18n.tr_msg(str(ev.get("msg") or "")))
        elif t == "finished":
            self._generating = False
            self._gen_btn.setEnabled(True)
            self._progress.setVisible(False)
            msg = i18n.tr_msg(str(ev.get("message") or ""))
            # 生成耗时由核心在 finished 事件里带回来(单张=这一张, 批量=整批), 进会话统计
            self._stats.add_generated(ev.get("ms") or 0.0, ev.get("count") or 1)
            if ev.get("success"):
                out_dir = ev.get("out_dir") or self._output_dir
                self._notify(self.tr("已完成：{msg}").format(msg=i18n.tr_msg(msg)))
                if out_dir:
                    core.open_path(out_dir)     # 完成后打开所在目录
            else:
                _message(self, QMessageBox.Icon.Warning, self.tr("处理结果"),
                                         msg or self.tr("处理失败"))

    # ---------- 杂项 ----------
    def _notify(self, text):
        """一次性提示(吐司): 结果 / 失败 / 前置条件提示。只由**用户显式动作**触发。

        后台循环(自动预览、自动保存)一条都不要发 —— 拖一次滑块弹一串吐司比状态栏闪烁更糟。
        """
        if getattr(self, "_toast", None) is not None:
            self._toast.show_message(text)

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
