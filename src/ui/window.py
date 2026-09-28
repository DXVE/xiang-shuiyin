# -*- coding: utf-8 -*-
"""「镶 · 水印」 水印处理工具 主窗口。

参数面板分三个页签（文件与美化 / 字体与画布 / 水印与效果），控件与取值范围由 PARAM_SPECS
一张表驱动。窗口持有参数状态、预设槽位与撤销/重做栈，并负责 UI 参数到渲染内核参数契约的转换。
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
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QIcon, QImage,
                           QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDoubleSpinBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpacerItem, QSpinBox, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget,
)

from PIL import Image

from core import app as core

from . import i18n, single, theme
from .dialog import MessageDialog, csdify, dlg_button, dlg_button_row, go_frameless
from .render import EventWatcher, PreviewWorker, _pil_to_qimage
from .stats import SessionStats

# process_single 实际接受的形参名 —— backend_params() 据此过滤 UI 侧参数
_BACKEND_KEYS = set(inspect.signature(core.process_single).parameters)

# ---------- 悬停链路诊断开关 ----------
# 置环境变量 XSY_HOVER_TRACE=1 时，将悬停→间距标注链路的各环节写入日志
# （事件是否到达 / 光标命中的参数行 / 高亮键 / 绘制时的 geom 与 img）。默认关闭。
_HOVER_TRACE = bool(os.environ.get("XSY_HOVER_TRACE"))


def _hover_trace(msg):
    if _HOVER_TRACE:
        logging.info("[悬停] " + msg)


# 间距标注的心跳周期(ms)：由定时器按此周期依光标位置重算高亮。周期过短会持续唤醒事件循环，
# 过长则响应迟滞。
_HOVER_POLL_MS = 80


# canvas_ratio_mode 是 UI 侧概念，后端只接受数值型 canvas_ratio
_RATIOS = {'1:1': 1, '3:2': 3 / 2, '2:3': 2 / 3, '4:3': 4 / 3, '3:4': 3 / 4,
           '16:9': 16 / 9, '9:16': 9 / 16, '21:9': 21 / 9, '9:21': 9 / 21}
# 数值型 canvas_ratio → 档位的反查表，migrate_preset() 据此还原档位。
# 浮点相等在此成立：数值由 _RATIOS 同一批字面量算出，JSON 往返精确。
_RATIO_MODES = {v: k for k, v in _RATIOS.items()}

# ---------- 预设 ----------
# 槽位 1..10，每槽一个文件，文件名固定为 presets1..10：不以预设名作文件名，以回避非法字符与重名。
_PRESET_SLOTS = core.MAX_PRESET_SLOTS
# 预设中的绝对路径键：本机存储时保留（同机存取可还原字体），仅在导出时剥离，使导出文件与机器无关。
_PRESET_MACHINE_KEYS = ("logo_dir", "font_path", "font_bold_path",
                        "font_italic_path", "font_bold_italic_path")
# 导出文件里的格式标记: _apply_params 会忽略未知键, 所以它不影响加载, 只用来让将来能识别。
_PRESET_FORMAT = "xiangshuiyin.preset/1"
# 仅用于生成导出时的默认文件名（用户可修改），不参与任何识别。
ILLEGAL_FN_CHARS = set(chr(92) + '/:*?"<>|')

# 撤销/重做：只保留最近 N 步；同一控件的连续改动在停止 _UNDO_COALESCE_MS 后合并为一步
# （滑块逐像素发信号，不合并会为一次拖动产生大量撤销步）。
_UNDO_LIMIT = 100
_UNDO_COALESCE_MS = 600

# 数值框 ± 步进按钮的素材边长（逻辑像素）: 与原先的 12px 粗体字形同量级。
_STEP_ICON = 14
# 色块选中态勾号的素材边长（逻辑像素）。
_CHIP_CHECK = 14


def migrate_preset(p):
    """把预设迁成当前口径，返回副本，不修改传入对象。

    仅含数值 canvas_ratio 而无 canvas_ratio_mode 的记录，经 _RATIO_MODES 反查补上档位；
    反查失败（如自由比例）时取 original。
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
    """把 UI 参数转换成 `process_single` 接受的形参。

    后端契约不对称：`default_params()` 含 UI 专用的 `canvas_ratio_mode`，而 `process_single`
    只接受数值型 `canvas_ratio`；直接展开 `**default_params()` 调用会抛 TypeError。
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
# only_if / only_if_not: (参数名, 允许值) —— 不满足时该行隐藏
PARAM_SPECS = [
    # ---------- 不在页签里平铺的行(收进弹窗) ----------
    # 字体路径条目的 tab=None 使其不被 _build_tab 展开; label/hint/kind 仍由本表提供,
    # 弹窗按 dialog=fonts 取用, 撤销步名亦取自该 label
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_path", label=QT_TRANSLATE_NOOP("ParamRow", "常规字体"), kind="path"),
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_bold_path", label=QT_TRANSLATE_NOOP("ParamRow", "粗体字体"), kind="path"),
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_italic_path", label=QT_TRANSLATE_NOOP("ParamRow", "斜体字体"), kind="path"),
    dict(tab=None, dialog="fonts", card=QT_TRANSLATE_NOOP("Card", "字体文件"), key="font_bold_italic_path", label=QT_TRANSLATE_NOOP("ParamRow", "粗斜体字体"), kind="path"),

    # ---------- 字体与画布 ----------

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "最小字号设置"), key="min_font_size", label=QT_TRANSLATE_NOOP("ParamRow", "最小字号"), kind="int",
         args=(0, 200), hint=QT_TRANSLATE_NOOP("ParamRow", "0~200px，默认 20")),
    # low_res_enhance(低分辨率增强)有意没有控件: 它已内置常驻开启, 不进 default_params / 设置 / 预设,
    # 由 process_single 的形参默认值提供(预览侧显式传 False 以省耗时)。它不是"少写了一行"。

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "画布比例"), key="canvas_ratio_mode", label=QT_TRANSLATE_NOOP("ParamRow", "画布比例"), kind="choice",
         args=[("original", QT_TRANSLATE_NOOP("ParamRow", "跟随原图")), ("1:1", "1:1"), ("3:2", QT_TRANSLATE_NOOP("ParamRow", "3:2（横）")), ("2:3", QT_TRANSLATE_NOOP("ParamRow", "2:3（竖）")),
               ("4:3", QT_TRANSLATE_NOOP("ParamRow", "4:3（横）")), ("3:4", QT_TRANSLATE_NOOP("ParamRow", "3:4（竖）")), ("16:9", QT_TRANSLATE_NOOP("ParamRow", "16:9（横）")),
               ("9:16", QT_TRANSLATE_NOOP("ParamRow", "9:16（竖）")), ("21:9", QT_TRANSLATE_NOOP("ParamRow", "21:9（超宽）")), ("9:21", QT_TRANSLATE_NOOP("ParamRow", "9:21（超长）"))]),

    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "水印位置布局"), key="layout_mode", label=QT_TRANSLATE_NOOP("ParamRow", "布局"), kind="choice",
         args=[("unified", QT_TRANSLATE_NOOP("ParamRow", "统一下方")), ("split_top_bottom", QT_TRANSLATE_NOOP("ParamRow", "上下分离")),
               ("split_left_right", QT_TRANSLATE_NOOP("ParamRow", "下方左右分离"))]),

    # 边距卡片: 这几个百分比参数声明 decimals=1 + step=0.5 —— 数值框可输 1 位小数(0.1% 一档),
    # ± 按钮按 0.5% 走; 其余百分比参数(缩放/浓度/暗化)仍按整数百分比, 不做小数档。
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="image_margin", label=QT_TRANSLATE_NOOP("ParamRow", "图片与边框间距"), kind="pct",
         args=(0.005, 0.05), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.5~5%，默认 1.5%；可输 1 位小数，按钮按 0.5% 步进")),
    # 上下分离为镜像+居中模式：后端以等高中带保证图片垂直居中，外侧实际渲染值可大于滑块值。
    # 该档只提供两个镜像滑块，不提供 4 个独立值。
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_inner", label=QT_TRANSLATE_NOOP("ParamRow", "内侧间距（水印与图片间距）"), kind="pct",
         args=(0.001, 0.20), mirror=("gap_image_top", "gap_image_bottom"), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.1~20%，上下镜像同步（默认 4%，仅上下分离）；可输 1 位小数"),
         only_if=("layout_mode", ("split_top_bottom",))),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_outer", label=QT_TRANSLATE_NOOP("ParamRow", "外侧间距（水印与画布边距）"), kind="pct",
         args=(0.001, 0.20), mirror=("gap_top", "gap_bottom"), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.1~20%，上下镜像同步（默认 4%，仅上下分离）；可输 1 位小数"),
         only_if=("layout_mode", ("split_top_bottom",))),
    # gap_image_top / gap_top 无对应控件：仅由上下分离模式下的镜像行写入，其余布局模式不使用
    # 这两个间距，故固定不显示。参数本身仍由 default_params 提供并传给 process_single。
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_image_bottom", label=QT_TRANSLATE_NOOP("ParamRow", "水印与图片间距"), kind="pct",
         args=(0.001, 0.20), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.1~20%，默认 4%；可输 1 位小数，按钮按 0.5% 步进"),
         only_if_not=("layout_mode", ("split_top_bottom",))),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="gap_bottom", label=QT_TRANSLATE_NOOP("ParamRow", "水印与底部间距"), kind="pct",
         args=(0.001, 0.20), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0.1~20%，默认 4%；可输 1 位小数，按钮按 0.5% 步进"),
         only_if_not=("layout_mode", ("split_top_bottom",))),
    dict(tab="font", card=QT_TRANSLATE_NOOP("Card", "边距"), key="line_gap", label=QT_TRANSLATE_NOOP("ParamRow", "水印行间距"), kind="pct",
         args=(0.0, 0.10), decimals=1, step=0.5,
         hint=QT_TRANSLATE_NOOP("ParamRow", "0~10%，默认 2%；可输 1 位小数，按钮按 0.5% 步进")),

    # ---------- 水印与效果 ----------
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="bg_mode", label=QT_TRANSLATE_NOOP("ParamRow", "背景模式"), kind="choice",
         args=[("blur", QT_TRANSLATE_NOOP("ParamRow", "模糊图片")), ("color", QT_TRANSLATE_NOOP("ParamRow", "纯色"))]),
    # 背景色仅在纯色模式下生效：模糊模式的底色由工作图放大生成，不使用该颜色。
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="bg_color", label=QT_TRANSLATE_NOOP("ParamRow", "背景色"), kind="color",
         only_if=("bg_mode", ("color",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="blur_radius", label=QT_TRANSLATE_NOOP("ParamRow", "模糊半径"), kind="int",
         args=(1, 1000), hint=QT_TRANSLATE_NOOP("ParamRow", "1~1000，默认 150"), only_if=("bg_mode", ("blur",))),
    # 纯色模式下不显示：核心对纯色背景不做暗化（见 process_single）。
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="bg_darken_strength", label=QT_TRANSLATE_NOOP("ParamRow", "背景暗化"), kind="pct",
         args=(0.0, 1.0), hint=QT_TRANSLATE_NOOP("ParamRow", "0~100%，默认 0；100=全黑（仅模糊模式）"),
         only_if=("bg_mode", ("blur",))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="corner_radius", label=QT_TRANSLATE_NOOP("ParamRow", "图片圆角"), kind="int",
         args=(0, 1000), hint=QT_TRANSLATE_NOOP("ParamRow", "0~1000 圆角，默认 0")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "背景模式"), key="corner_circle", label=QT_TRANSLATE_NOOP("ParamRow", "圆形（整图正圆）"), kind="bool"),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="text_color", label=QT_TRANSLATE_NOOP("ParamRow", "文字颜色"), kind="color"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="scale_logo", label=QT_TRANSLATE_NOOP("ParamRow", "Logo 缩放"), kind="pct",
         args=(0.1, 2.0), hint=QT_TRANSLATE_NOOP("ParamRow", "10~200%，默认 70%")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="scale_model", label=QT_TRANSLATE_NOOP("ParamRow", "型号缩放"), kind="pct",
         args=(0.1, 2.0), hint=QT_TRANSLATE_NOOP("ParamRow", "10~200%，默认 50%")),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印文字"), key="scale_params", label=QT_TRANSLATE_NOOP("ParamRow", "参数缩放"), kind="pct",
         args=(0.1, 2.0), hint=QT_TRANSLATE_NOOP("ParamRow", "10~200%，默认 30%")),

    # 字体样式为网格布局(行=文字种类, 列=显示/粗体/斜体)，由 _build_font_style_card 构建；
    # 本条目仅用于占位卡片顺序。
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "水印开关"), key=None, special="_build_font_style_card"),

    # 描边与投影: 四项平时很少调整, 合成一张**可折叠**卡片放在本页签最末, 默认折叠(折叠状态不落盘,
    # 每次启动都是折叠, 见 Card.toggle); 展开后卡内按 group 分成四组、组间画分割线, 组内不再各折一层。
    # 本条目只占卡片顺序(key=None): 卡内的行由 special 构建函数生成(见 _build_fx_card)。
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), key=None, special="_build_fx_card"),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片描边"),
         key="enable_image_stroke", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片描边"),
         key="image_stroke_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color",
         only_if=("enable_image_stroke", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片描边"),
         key="image_stroke_width", label=QT_TRANSLATE_NOOP("ParamRow", "宽度"), kind="int",
         args=(1, 50), hint=QT_TRANSLATE_NOOP("ParamRow", "1~50px，默认 5"), only_if=("enable_image_stroke", (True,))),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="enable_image_shadow", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color",
         only_if=("enable_image_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_angle", label=QT_TRANSLATE_NOOP("ParamRow", "方向"), kind="int",
         args=(0, 360), hint=QT_TRANSLATE_NOOP("ParamRow", "0~360°，默认 135"), only_if=("enable_image_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_offset", label=QT_TRANSLATE_NOOP("ParamRow", "偏移"), kind="int",
         args=(0, 100), hint=QT_TRANSLATE_NOOP("ParamRow", "0~100px，默认 25"), only_if=("enable_image_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_alpha", label=QT_TRANSLATE_NOOP("ParamRow", "浓度"), kind="pct",
         args=(0.05, 1.0), hint=QT_TRANSLATE_NOOP("ParamRow", "5~100%，默认 35"), only_if=("enable_image_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "图片投影"),
         key="image_shadow_blur", label=QT_TRANSLATE_NOOP("ParamRow", "晕开范围"), kind="int",
         args=(0, 80), hint=QT_TRANSLATE_NOOP("ParamRow", "0~80px，默认 30"), only_if=("enable_image_shadow", (True,))),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字描边"),
         key="enable_text_stroke", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字描边"),
         key="text_stroke_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color",
         only_if=("enable_text_stroke", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字描边"),
         key="text_stroke_width", label=QT_TRANSLATE_NOOP("ParamRow", "宽度"), kind="int",
         args=(1, 50), hint=QT_TRANSLATE_NOOP("ParamRow", "1~50px，默认 1"), only_if=("enable_text_stroke", (True,))),

    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="enable_text_shadow", label=QT_TRANSLATE_NOOP("ParamRow", "开启"), kind="bool"),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_color", label=QT_TRANSLATE_NOOP("ParamRow", "颜色"), kind="color",
         only_if=("enable_text_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_angle", label=QT_TRANSLATE_NOOP("ParamRow", "方向"), kind="int",
         args=(0, 360), hint=QT_TRANSLATE_NOOP("ParamRow", "0~360°，默认 135"), only_if=("enable_text_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_offset", label=QT_TRANSLATE_NOOP("ParamRow", "偏移"), kind="int",
         args=(0, 40), hint=QT_TRANSLATE_NOOP("ParamRow", "0~40px，默认 3"), only_if=("enable_text_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_alpha", label=QT_TRANSLATE_NOOP("ParamRow", "浓度"), kind="pct",
         args=(0.05, 1.0), hint=QT_TRANSLATE_NOOP("ParamRow", "5~100%，默认 20"), only_if=("enable_text_shadow", (True,))),
    dict(tab="effects", card=QT_TRANSLATE_NOOP("Card", "描边与投影"), group=QT_TRANSLATE_NOOP("MainWindow", "文字投影"),
         key="text_shadow_blur", label=QT_TRANSLATE_NOOP("ParamRow", "晕开范围"), kind="int",
         args=(0, 30), hint=QT_TRANSLATE_NOOP("ParamRow", "0~30px，默认 5"), only_if=("enable_text_shadow", (True,))),
]

_LABEL_W = 128
# 百分比类参数(pct)内部为 0~1 的小数，显示值 = 内部值 × 100，数值框不带单位显示。
# 声明了 decimals=1 的行(边距类)再放大 10 倍：滑块/换算用的整数是"十分位"(1.5% -> 15)，
# 数值框按十分位的小数显示(1.5)，于是既能手输 0.1% 也要得动 ± 按钮的 0.5% 步进。
_PCT_SCALE = 100


def _display_range(kind, lo, hi, decimals=0):
    """参数区间 -> 数值框/滑块的整数显示区间。

    int 类原样返回；pct 类按百分比换算(0.04 -> 4)，decimals=1 时再 ×10(0.015 -> 15)。
    下界向上取整：下界 0.001 取整得 0，而 0 间距无意义，须取 1(或 0.1)以对应最低一档。
    """
    if kind != "pct":
        return int(lo), int(hi)
    mult = _PCT_SCALE * (10 ** int(decimals or 0))
    return math.ceil(lo * mult - 1e-9), int(round(hi * mult))

# 持久化时剥离的键：输入/输出路径不落盘（model_map 存放于 logo_dir 下的 model_map.json）。
_NOT_PERSISTED = {"logo_dir", "input_path", "output_path", "batch_dir", "output_dir", "model_map"}
# 放大镜尺寸、倍数与圆角
MAG_W, MAG_H, MAG_ZOOM, MAG_RADIUS = 240, 160, 3, 12
MAG_MARGIN = 12      # 离预览框右上角的距离
# 预览图占预览框面积的上限: 贴合矩形按面积的平方根等比收缩, 四边各留 1 - √0.95 ≈ 1.3% 的余量,
# 避免宽图与长图贴住预览框边缘。该收缩量大于圆角的咬合深度(半径 12 时约 3.5px),
# 因此画布四角不会被圆角裁掉。
_PREVIEW_FIT_AREA = 0.95
_PREVIEW_FIT_SCALE = math.sqrt(_PREVIEW_FIT_AREA)
# 竖向滚动条占位宽度，必须与 theme.QSS 中 `QScrollBar:vertical` 的 width 一致：
# _segbar_row 据此为页签行留出右侧占位。
_SB_W = 14

# 预览上屏缩图在渲染线程完成(见 PreviewWorker._render), 用的是 PIL 的 LANCZOS(非
# QPixmap.scaled(Smooth)): 后者降采样是面积平均(盒式滤波), 平滑但不锐。界面只在窗口尺寸
# 与请求时不符时回落到现场重采样(见 PreviewBox._scale_for_display), 同样是 PIL LANCZOS。

# ---------- 预览状态标识 ----------
# 标识当前预览图是否由当前参数渲染而来。各状态圆点色取自 PRESET_COLORS，不另造色号。
PV_CHIP_DOT = {
    "none": theme.EMPTY_SLOT,  # 未选图：暗中性灰
    "rendering": "#7C98A6",
    "synced": theme.ACCENT,
    "stale": "#B8895E",
    "failed": "#B0676B",
}
PV_CHIP_H = 20          # 胶囊高度，必须定高（见 PreviewStateChip 类注释）

# 文案表。中文字面量必须包在 QT_TRANSLATE_NOOP 中：lupdate 无法解析变量取值，否则漏译。
# context 须与消费它的类名（PreviewStateChip）一致。
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
# 初始尺寸按屏幕可用区(已扣除任务栏)的百分比计算, 不写死像素: 高缩放比下可用区变小,
# 写死高度会把底栏顶出屏幕。留边 _WIN_EDGE px 保证四边不贴边且可拖动。
_WIN_W_RATIO, _WIN_H_RATIO, _WIN_EDGE = 0.8, 0.9, 100
# 左列占比(左 4 : 右 6)。窗口缩放时按此比例重排; 用户手动拖过分隔条后不再自动分配。
_SPLIT_LEFT = 0.4

# 慢调用阈值(ms)：超过则把请求日志降级为 WARNING 并标注偏慢。渲染与排队一并计入，
# 排队源于上一轮让位前的剩余时间。这条线只定义在核心(见 core.SLOW_RENDER_MS)，那里还用它
# 决定要不要为这一帧落"渲染分段"日志 —— 两处同一条线，不得各写一个数。
_SLOW_RENDER_MS = core.SLOW_RENDER_MS

# 字体样式：每行一种文字(显示 / 粗体 / 斜体 三列)
FONT_STYLE_COLS = ("显示", "粗体", "斜体")
FONT_STYLE_GROUPS = [
    ("Logo 文字", ("show_brand", "bold_logo", "italic_logo")),
    ("型号文字", ("show_model", "bold_model", "italic_model")),
    ("参数文字", ("show_params", "bold_params", "italic_params")),
]


# ---- 滚轮行为 ----
# 滑块与下拉框不响应滚轮：wheelEvent 中 ignore() 使事件上抛父级滚动区，滚轮仅滚动页面，
# 避免滑过控件时误改数值；数值框(QSpinBox)保留滚轮调节。
# 下拉框仅在收起状态不响应滚轮，展开后的列表为独立控件，仍可滚轮选择。
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
    colorClicked = Signal()      # 点「选择颜色」: 取色弹窗由主窗提供(需读取用户保存的颜色)

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

        # 表里的 label/hint/选项文案均为中文原文(中文即源语言), 仅在显示时 tr() ——
        # 因此参数面板各处只需包装一次 tr(), 不必逐条改表。
        if self.kind == "bool":
            self.chk = QCheckBox(self.tr(spec["label"]))
            self.chk.setCursor(Qt.PointingHandCursor)
            self.chk.toggled.connect(self._from_check)
            lay.addWidget(self.chk)
            if spec["label"].strip():
                # 标签为空(字体/水印开关网格的格位)时**不加**弹性空白 —— 那些格位靠 grid 的
                # AlignCenter 居中, 加了 stretch 就会把开关推偏。
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
                # 色块**本身就是按钮**: 直接点色块开取色弹窗, 右边缘仍与数值行的步进器组对齐(宽度由布局拉伸给出)。
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

            else:                                   # pct / int —— 整数显示; pct 可声明 decimals 走小数档
                lo, hi = spec["args"]
                # decimals=1 的行(边距类): 显示保留 1 位小数, ± 按钮按 spec 的 step 走(当前 0.5)
                self._decimals = int(spec.get("decimals", 0) or 0) if self.kind == "pct" else 0
                self._step = float(spec.get("step", 1.0) or 1.0)
                self._disp_lo, self._disp_hi = _display_range(self.kind, lo, hi, self._decimals)
                self.slider = NoWheelSlider(Qt.Horizontal)
                self.slider.setRange(self._disp_lo, self._disp_hi)
                # 滑块与方向键的步长: 小数档为 0.1%(显示整数即十分位), 其余 1(1% / 1px)
                self.slider.setSingleStep(1)
                if self._decimals:
                    # 只有小数档用 QDoubleSpinBox: 其余参数都是整数, 混两套只会让宽度统一更难算。
                    # ± 按钮调 stepDown/stepUp, 走的就是 singleStep, 因此按钮步进 = step。
                    self.spin = QDoubleSpinBox()
                    self.spin.setDecimals(self._decimals)
                    self.spin.setRange(self._disp_lo / 10, self._disp_hi / 10)
                    self.spin.setSingleStep(self._step)
                else:
                    self.spin = QSpinBox()
                    self.spin.setRange(self._disp_lo, self._disp_hi)
                    self.spin.setSingleStep(self._step)
                # − / + 步进按钮: 关掉 QSpinBox 自带箭头 —— QSS 下 Qt 既不画原生箭头, 也不支持
                # CSS 的 border 三角写法, 只能改用素材图标。
                self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
                # 宽度与对齐不在此处确定: 由 MainWindow._unify_spins() 按全表最宽取值统一计算
                if hint:
                    self.spin.setToolTip(hint)
                self.slider.valueChanged.connect(self._from_slider)
                self.spin.valueChanged.connect(self._from_spin)
                # 两个按钮都出素材而不是字形: 界面字体是内置子集, 其中的减号(U+2212)缺码位,
                # 而加号(U+002B)有 —— 一字形一素材必然不成对(见 theme.py 的「界面图标」)。
                minus = QPushButton()
                plus = QPushButton()
                _iconify(minus, theme.STEP_MINUS, _STEP_ICON)
                _iconify(plus, theme.STEP_PLUS, _STEP_ICON)
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
            self.spin.setValue(self._slider_to_spin(disp))
        self._updating = False

    def value(self):
        return self._value

    # ---- 显示值(整数) <-> 内部值 ----
    # pct 类内部是 0~1 的小数, 差一个 ×100(小数档再 ×10, 见 _PCT_SCALE); int 类两者相同。
    def _disp_to_value(self, disp):
        if self.kind != "pct":
            return int(disp)
        return disp / (_PCT_SCALE * (10 ** self._decimals))

    def _value_to_disp(self, value):
        if self.kind != "pct":
            return int(value)
        return int(round(float(value) * _PCT_SCALE * (10 ** self._decimals)))

    # 小数档: 滑块的显示整数是"十分位", 数值框是带小数的百分数 —— 两者差 10 倍
    def _slider_to_spin(self, disp):
        return disp / 10 if self._decimals else disp

    def _spin_to_slider(self, val):
        return int(round(val * 10)) if self._decimals else int(val)

    def _paint_swatch(self, color):
        """色块外观: 底色为颜色本身, 色号用对比色写在上面(浅底黑字/深底白字)。

        **与取色弹窗里的色块共用 `_swatch_qss`**: 全项目「选中/悬停高亮」只有这一份定义。
        悬停/按下态必须在本控件自己的样式表里把背景写掉, 否则会落到主题那条 `QPushButton:hover`,
        鼠标一上去色块变灰。
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
        self.spin.setValue(self._slider_to_spin(v))      # 小数档差 10 倍, 其余直接同步
        self._updating = False
        self.changed.emit(self.key, self._disp_to_value(v))

    def _from_spin(self, v):
        if self._updating:
            return
        disp = self._spin_to_slider(v)
        self._updating = True
        self.slider.setValue(disp)
        self._updating = False
        self.changed.emit(self.key, self._disp_to_value(disp))

    # ---- 悬停: 让预览图高亮对应的间距段 ----
    # **只有一条机制**: 主窗按光标位置重算(`MainWindow._sync_gap_hover`), 由 80ms 心跳定时器驱动。
    # 本类**不接** Enter/Leave —— 在嵌套布局里不可靠: 行 95% 的面积被子控件盖着(子控件抢事件,
    # 从行外进入时会先给行 Enter 再紧跟一个 Leave)、两行之间直接移动时 Leave/Enter 的到达顺序不定、
    # 参数行的 tooltip 弹出时主窗还会收到 Leave。事件到达与否不影响结果, 最多晚一个心跳周期出现。
    def _hover_keys(self):
        """本行要顺带高亮的间距段: 镜像行高亮上下两段, 其余就是它自己。"""
        return list(self._spec.get("mirror") or [self.key])

    def _pick_font(self):
        path = _open_file_dialog(self, self.tr("选择字体文件"),
                                 os.path.dirname(self._value or ""), _FONT_FILTER)
        if path:
            self.set_value(path)
            self.changed.emit(self.key, path)


def _fx_divider():
    """「描边与投影」卡内相邻功能组之间的分割线。

    自带上下留白(分割线两侧各留一段空), 否则会贴着上一组最后一行。用 QFrame 而非 QWidget:
    样式表里的 background 只有 QFrame 这类部件会直接绘制。
    """
    holder = QWidget()
    v = QVBoxLayout(holder)
    v.setContentsMargins(0, 9, 0, 3)
    v.setSpacing(0)
    line = QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    v.addWidget(line)
    return holder


class _CardHead(QWidget):
    """可折叠卡片的标题行: 整条可点, 与点左端的开合按钮等价。

    行内的子控件(标题、开合按钮、重置按钮)自己吃鼠标事件, 因此点它们不会连带触发本行的点击。
    """

    clicked = Signal()

    def mouseReleaseEvent(self, e):        # noqa: N802 - Qt 命名
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class Card(QFrame):
    """卡片: 标题 + 右上角「重置本组」+ 内容区。

    可折叠卡片(`collapsible=True`)用于「描边与投影」这类平时不动、需要时才展开的整块参数:
    标题行整条可点(左端另有开合按钮), 内容区默认收起。收放只改显示, 不碰参数值; 状态不落盘,
    每次启动都是收起(见 `toggle`)。卡内的分组与分割线不参与折叠, 展开后一次看全。
    """

    def __init__(self, title, parent=None, collapsible=False):
        super().__init__(parent)
        self.setObjectName("card")
        self.keys = []
        self.title_display = self.tr(title)   # 卡名显示文本(撤销步名与提示直接取它, 不从控件反查)
        self.collapsible = collapsible
        self.collapsed = bool(collapsible)
        self.body_host = None        # 可折叠卡的内容宿主(布局本身没有可见性, 收起要落在 widget 上)
        theme.apply_shadow(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD - 2, theme.PAD_CARD, theme.PAD_CARD)
        v.setSpacing(theme.GAP_ROW)

        if collapsible:
            self._head = _CardHead(self)
            self._head.clicked.connect(self.toggle)
            head = QHBoxLayout(self._head)
        else:
            self._head = None
            head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self._toggle_btn = None
        if collapsible:
            # 开合按钮: 负担指示与点击两件事。用 #ghost(既有四档之一)而不是新增按钮分量。
            self._toggle_btn = QPushButton()
            self._toggle_btn.setObjectName("ghost")
            self._toggle_btn.setFixedWidth(24)
            self._toggle_btn.setCursor(Qt.PointingHandCursor)
            self._toggle_btn.clicked.connect(self.toggle)
            head.addWidget(self._toggle_btn)
        t = QLabel(self.title_display)        # 卡片名来自 PARAM_SPECS 或手写字面量
        t.setObjectName("cardTitle")
        head.addWidget(t)
        head.addStretch(1)
        self.reset_btn = QPushButton(self.tr("重置本组"))
        self.reset_btn.setObjectName("mini")
        _iconify(self.reset_btn, theme.RESET, 14)
        self.reset_btn.setToolTip(self.tr("将本卡片参数恢复为出厂默认值"))
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.reset_btn)
        if collapsible:
            v.addWidget(self._head)
        else:
            v.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(theme.GAP_ROW - 2)
        if collapsible:
            # 内容宿主: 收起即隐藏它。可见性由构建方(主窗)按 _vis_rule 登记, 本类不直接 setVisible。
            self.body_host = QWidget(self)
            self.body_host.setLayout(self.body)
            v.addWidget(self.body_host)
            self._sync_collapse_ui()
        else:
            v.addLayout(self.body)

    # ---- 折叠 ----
    def toggle(self):
        """收起 / 展开内容区。

        只改状态, 随后由主窗走**统一的显隐出口**(`_sync_visibility`): 它先冻结重绘、把终态布局与
        容器高度算完再放开。卡片带 QGraphicsDropShadowEffect(渲染到离屏图), 自己 setVisible 会先贴出
        旧尺寸的图, 表现为"跳一下再闪出内容"。折叠不进撤销、不触发预览: 它只影响显示。
        """
        if not self.collapsible:
            return
        self.collapsed = not self.collapsed
        self._sync_collapse_ui()
        sync = getattr(self.window(), "_sync_visibility", None)
        if sync is not None:
            sync()

    def _sync_collapse_ui(self):
        """开合指示: 收起=右向箭头, 展开=下向箭头; 提示语同状态一起切。"""
        if not self.collapsible:
            return
        _iconify(self._toggle_btn, theme.ARROW_RIGHT if self.collapsed else theme.ARROW_DOWN, 12)
        tip = self.tr("展开本组") if self.collapsed else self.tr("收起本组")
        self._toggle_btn.setToolTip(tip)
        self._head.setToolTip(tip)
        self._head.setCursor(Qt.PointingHandCursor)


def _qimage_to_pil(img: QImage):
    """QImage -> PIL(仅回落路径用: 窗口尺寸/dpr 与请求时不符时现场重采样)"""
    q = img.convertToFormat(QImage.Format.Format_RGB888)
    return Image.frombytes("RGB", (q.width(), q.height()), q.constBits().tobytes(),
                           "raw", "RGB", q.bytesPerLine())


class PreviewStateChip(QFrame):
    """预览标题条中的状态标识: 一个圆点 + 一句话(胶囊底), 紧随「效果预览」。

    它回答的问题: 屏幕上的图是否由当前参数渲染。请求携带参数指纹, 渲染线程原样回传,
    界面比较「屏上指纹」与「当前指纹」即可 —— 见 MainWindow._preview_token / _on_rendered。

    四条判据(顺序即优先级, 见 MainWindow._preview_state):
      none      未选图 / 取样图被清空
      rendering 有在途或排队的请求 —— 合并了「正在渲染」与「参数已改」: 拖动时一帧上屏即已再次改动
                是常态, 分开报告会使两态来回跳, 而对用户是同一件事(尚未跟上)
      stale     无在途请求, 但屏上这帧的参数与当前不符
      failed    最后一次尝试失败 —— 屏上为上一张成功结果, 不保证对应当前参数

    定高 20px(PV_CHIP_H): 同一行高度由「刷新预览效果」按钮撑起(28px), 不显式定高会被拉成 28px
    而形似按钮; 20px 也使 11px 文字上下各余约 3px。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(PV_CHIP_H)
        # 不做成按钮: 不给光标、不给悬停态、不给边框 —— 旁边就有一个真按钮, 有这三样
        # 会被当成第二个按钮。
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
        """切换状态。tip 给的是「为什么是这个状态」的一句话(渲染失败时带上原因)。"""
        if state not in PV_CHIP_DOT:
            state = "none"
        self._state = state
        dot = PV_CHIP_DOT[state]
        self._text.setText(self.tr(_PV_CHIP_TEXT[state]))
        self._dot.setStyleSheet(f"background: {dot}; border-radius: 3px;")
        self._text.setStyleSheet("color: %s; font-size: 11px; background: transparent;" % dot)
        # 淡底用状态色自身调透明度得到: 同一色系, 不另造颜色。
        self.setStyleSheet("background: %s; border: none; border-radius: 9px;"
                           % _rgba(dot, 0.13))
        self.setToolTip(tip or self.tr(_PV_CHIP_TIP[state]))
        self.adjustSize()

    def state(self):
        return self._state


def _rgba(hex_color, alpha):
    """#RRGGBB + 透明度 -> rgba(...)。用于状态标识的淡底。"""
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


class PreviewBox(QWidget):
    """预览显示区: 等比缩放居中。自绘(而非 QLabel), 为放大镜与间距几何标注提供绘制入口。

    上屏缩放已由渲染线程完成(见 PreviewWorker._render): 本控件收到的 image 就是"请求时预览框
    尺寸"下的位图, 绘制路径只剩 QPixmap.fromImage 与贴图 —— 37MP 画布下这一步在界面线程上
    原本要约 400ms(转 PIL 89ms + LANCZOS 缩放 275ms + 两次 QPixmap 33ms), 拖动滑块时每次
    新画面都会卡一下界面。

    窗口被拖动缩放或换了屏(尺寸/dpr 与请求时不符)才回落到现场重采样, 见 _scale_for_display;
    回落需要整幅画布的 PIL, 因此那一次才付 QImage->PIL 的转换。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pix = None         # 已缩到上屏尺寸的位图(设备像素, 不设 dpr: 贴图按设备几何算)
        self._src = None         # 整幅画布 QImage: 放大镜源 + 回落路径的重采样源
        self._canvas = None      # 整幅画布尺寸(px), 算等比尺寸用
        self._shown_for = None   # _pix 对应的可用区设备尺寸 + dpr: 相符才直接贴
        self._pil = None         # 回落路径用(按需从 _src 转换, 见 _scale_for_display)
        self._scaled = None
        self._scaled_for = None
        self._hint = self.tr("请拖入图片，或点击「打开图片」")
        self._geom = None      # 间距段几何(画布像素坐标), 由 make_preview 的 resp.geom 回传
        self._hl = set()       # 当前要高亮的间距段键
        self._mag_pos = None   # 放大镜: 光标在控件内的位置(None=不显示)
        self.setMouseTracking(True)

    def avail_dev(self):
        """当前预览框能给出的上屏设备像素尺寸(渲染线程按同一算法缩图, 见 PreviewWorker)。"""
        dpr = self.devicePixelRatioF()
        avail = QSize(max(1, int(self.width() * _PREVIEW_FIT_SCALE)),
                      max(1, int(self.height() * _PREVIEW_FIT_SCALE)))
        return QSize(max(1, round(avail.width() * dpr)), max(1, round(avail.height() * dpr)))

    def set_image(self, image: QImage, source=None, canvas=None, shown=None, shown_dpr=None):
        """接收一帧: image 已是上屏尺寸, source 是整幅画布(放大镜与回落路径用)。

        shown 为渲染线程缩图时用的可用区设备尺寸; 与本控件当前尺寸一致才 1:1 直贴。
        """
        self._pix = QPixmap.fromImage(image)
        self._src = source if source is not None else image
        self._canvas = tuple(canvas) if canvas else (image.width(), image.height())
        self._shown_for = (tuple(shown), shown_dpr) if shown else None
        self._pil = None
        self._scaled = None
        self._scaled_for = None
        self.update()

    def _fit_dev(self):
        """按整幅画布的宽高比, 在本控件可用区内等比取出的设备像素尺寸。"""
        cw, ch = self._canvas or (1, 1)
        return QSize(cw, ch).scaled(self.avail_dev(), Qt.AspectRatioMode.KeepAspectRatio)

    def _scale_for_display(self, target_dev):
        """回落路径: 控件尺寸/dpr 与请求时不符时, 从整幅画布现场重采样。

        用 PIL 的 LANCZOS, 不用 QPixmap.scaled(SmoothTransformation): 后者降采样是面积平均
        (盒式滤波), 平滑但不锐; LANCZOS 带负瓣, 缩小的同时略有锐化。两者数值差约 1.6/255,
        但差在高对比边缘(水印笔画、圆角)上。只在窗口拖动/换屏时执行, 不进逐帧路径。
        """
        dpr = self.devicePixelRatioF()
        t0 = time.perf_counter()
        if self._pil is None:
            self._pil = _qimage_to_pil(self._src)      # 整幅画布转 PIL: 仅回落时付一次
        im = self._pil.resize((target_dev.width(), target_dev.height()), Image.LANCZOS)
        pm = QPixmap.fromImage(_pil_to_qimage(im))
        scale_ms = (time.perf_counter() - t0) * 1000
        logging.info(f"[上屏] 回落重采样 {self._pil.size[0]}x{self._pil.size[1]}"
                     f"-->{target_dev.width()}x{target_dev.height()} dpr={dpr:g} "
                     f"耗时 {scale_ms:.0f}ms (PIL LANCZOS)")
        return pm

    def set_geo(self, geom):
        """间距段几何(画布像素): {canvas:(w,h), img:(x0,y0,x1,y1), gaps:{键:[(y0,y1),…]}}"""
        self._geom = geom or None
        self.update()

    def set_highlight(self, keys):
        keys = set(keys or ())
        _hover_trace("set_highlight(%s) 旧=%s" % (sorted(keys), sorted(self._hl)))
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
        self._src = None
        self._canvas = None
        self._shown_for = None
        self._pil = None
        self._scaled = None
        self._scaled_for = None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        # 抗锯齿必须显式开启: QPainter 默认关闭, 否则 fillPath/setClipPath 的圆弧会出现硬台阶,
        # 与 QSS 绘制的卡片圆角(同一 RADIUS_CARD)观感不一致。
        # setClipPath 同样受该提示影响(仅加此提示时, 裁剪+fillRect 与不裁剪的 fillPath 结果逐像素相同),
        # 因此图片/标注/放大镜的边缘一并平滑
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 深色底取**与卡片同一档圆角**, 并裁剪到这条圆角路径上(图片/标注/放大镜一并被裁)。
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), theme.RADIUS_CARD, theme.RADIUS_CARD)
        p.setClipPath(path)
        p.fillPath(path, QColor("#333333"))
        if self._pix is None:
            p.setPen(QColor("#9aa0a6"))
            p.drawText(self.rect(), Qt.AlignCenter, self._hint)
            return
        # SmoothPixmapTransform 是兜底: 目标矩形按设备像素对齐时是 1:1 拷贝, 不对齐(拖动窗口
        # 过程中的小数位置)时开着它最多是一次极轻的重采样, 关着(Qt 默认)会变成最近邻抽样。
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        dpr = self.devicePixelRatioF()
        dev = self._fit_dev()
        # 渲染线程正是按"这个可用区尺寸"缩的图: 相符就直接贴, 不相符(窗口刚被拖动/换屏)才回落
        if self._shown_for == ((dev.width(), dev.height()), dpr):
            pm = self._pix
        else:
            # dpr 进缓存键: 移到另一块缩放率不同的屏上必须重算(否则会沿用旧 dpr 的位图)
            key = (dev, dpr)
            if self._scaled is None or self._scaled_for != key:
                self._scaled = self._scale_for_display(dev)
                self._scaled_for = key
            pm = self._scaled
        # 原点取整到**设备像素**: 位图不设 dpr, 由这里按设备几何给出目标矩形, 与屏幕 1:1。
        ox = round((self.width() * dpr - pm.width()) / 2) / dpr
        oy = round((self.height() * dpr - pm.height()) / 2) / dpr
        p.drawPixmap(QRectF(ox, oy, pm.width() / dpr, pm.height() / dpr), pm,
                     QRectF(0, 0, pm.width(), pm.height()))
        box = QRectF(ox, oy, pm.width() / dpr, pm.height() / dpr)
        self._paint_geom(p, box)
        self._paint_magnifier(p, box)

    def _paint_magnifier(self, p, target):
        """放大镜: 固定预览框右上角, 内容随光标平移(光标所指的源点居中)。

        按**像素宽度**从固有源重采样(不是把已缩小的位图再放大), 所以边缘不虚。
        光标不在图像区域内(深色留白)时不显示: 那里没有可放大的内容。

        源是整幅画布的 QImage: 只取光标附近那一小块重采样, 实测与从 QPixmap 画同样快
        (约 0.4ms), 因此不必在界面线程为它多留一张 37MP 的 QPixmap。
        """
        pos = self._mag_pos
        src = self._src
        if pos is None or src is None or not target.contains(pos):
            return
        sw, sh = src.width(), src.height()
        dw, dh = target.width(), target.height()
        if dw <= 0 or dh <= 0 or sw <= 0 or sh <= 0:
            return
        src_w = min(float(sw), MAG_W * sw / (dw * MAG_ZOOM))
        src_h = min(float(sh), MAG_H * sh / (dh * MAG_ZOOM))
        cx = (pos.x() - target.left()) / dw * sw
        cy = (pos.y() - target.top()) / dh * sh
        # 贴边时夹住源矩形(而不是让 drawImage 拿到越界的源区)
        x0 = max(0.0, min(cx - src_w / 2, sw - src_w))
        y0 = max(0.0, min(cy - src_h / 2, sh - src_h))
        box = QRectF(self.width() - MAG_W - MAG_MARGIN, MAG_MARGIN, MAG_W, MAG_H)
        p.save()
        # QPainter 的 Antialiasing 与 SmoothPixmapTransform **默认都是关的**:
        #   - 不开 SmoothPixmapTransform → drawPixmap 走最近邻, 缩放后成马赛克
        #   - 不开 Antialiasing → 圆角边框出锯齿
        # 主预览路径不受此约束: pixmap.scaled(..., Smooth) 自带平滑, 且其为矩形不需抗锯齿。
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # **不做投影**。放大镜压在深色预览底上, 白色边框已足够分辨。

        # 圆角裁剪必须走 path: 用 setClipRect 会把图像内容填进方角, 再叠一圈圆角白边
        # 就成了「方角 + 白线溢出」。
        clip = QPainterPath()
        clip.addRoundedRect(box, MAG_RADIUS, MAG_RADIUS)
        p.save()
        p.setClipPath(clip)
        p.fillRect(box, QColor("#222222"))
        p.drawImage(box, src, QRectF(x0, y0, src_w, src_h))
        p.restore()

        # 白色边框: 内缩半个笔宽, 否则一半压在裁剪外被切掉
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#ffffff"), 2))
        inner = box.adjusted(1, 1, -1, -1)
        p.drawRoundedRect(inner, MAG_RADIUS - 1, MAG_RADIUS - 1)
        p.restore()

    def _paint_geom(self, p, target):
        """在预览图上叠加「当前悬停的那个间距」的可视化。

        间距段画横向色带(上/下各一条 2px 描边); image_margin 填图片四周的外边距区域。
        未悬停时不画任何东西。
        """
        g = self._geom
        if not g or not self._hl:
            _hover_trace("绘制跳过: geom=%s 高亮=%s" % ("有" if g else "无", sorted(self._hl)))
            return
        # 此处画的都是横平竖直的 2px 色带/矩形边, **关掉抗锯齿**保持边缘利落 —— paintEvent
        # 开头那把 Antialiasing 是给圆弧用的, 直角线开了只会发虚。
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        cw, ch = g.get("canvas") or (0, 0)
        if not cw or not ch:
            return
        sx = target.width() / cw
        sy = target.height() / ch
        fill = QColor(255, 184, 0, 77)        # rgba(255,184,0,.30)
        edge = QColor(255, 160, 0, 140)       # rgba(255,160,0,.55)
        drew = []
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
                drew.append(f"{key}[{y0},{y1}]")
        ig = g.get("img")
        if ig and "image_margin" in self._hl:
            x0, y0, x1, y1 = ig
            rx0 = target.left() + x0 * sx
            ry0 = target.top() + y0 * sy
            rx1 = target.left() + x1 * sx
            ry1 = target.top() + y1 * sy
            # **填「图片四周那一圈外边距」, 不画压在图片上的细虚线框**: 图片一旦接近铺满预览框,
            # 虚线框的上下两条正好落在预览框边界上被圆角裁掉, 左右两条又与照片内容糊在一起
            # (而且 2px 虚线本身就很弱)。此处与上面的色带同一套视觉: 四块半透明填充 + 图片边界那条 2px 实线。
            ring = [QRectF(target.left(), target.top(), target.width(), ry0 - target.top()),
                    QRectF(target.left(), ry1, target.width(), target.bottom() - ry1),
                    QRectF(target.left(), ry0, rx0 - target.left(), ry1 - ry0),
                    QRectF(rx1, ry0, target.right() - rx1, ry1 - ry0)]
            for rr in ring:
                if rr.width() > 0.5 and rr.height() > 0.5:
                    p.fillRect(rr, fill)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(edge, 2))
            p.drawRect(QRectF(rx0, ry0, rx1 - rx0, ry1 - ry0))
            drew.append(f"外边距四块+图片框 {x0},{y0},{x1},{y1}->屏幕({rx0:.0f},{ry0:.0f},{rx1-rx0:.0f}x{ry1-ry0:.0f})")
        elif "image_margin" in self._hl:
            drew.append("图片外边距没画: geom 里没有 img")
        _hover_trace("绘制: 高亮=%s 画了[%s]" % (sorted(self._hl), "; ".join(drew) or "无"))


# 预设色板(灰阶 / 莫兰迪 / 浅色清新, 每行一个色系)
PRESET_COLORS = [
    '#FFFFFF', '#F0F0F0', '#D6D6D6', '#B0B0B0', '#808080', '#404040', '#000000',   # 灰阶
    '#6F9C7D', '#7C98A6', '#B0676B', '#B8895E', '#C9A227',                          # 莫兰迪
    '#F7E8D0', '#A8D0E6', '#F5D6D6', '#D5E8D4', '#DCE4EE', '#FFF3CD',               # 浅色清新
]
_COLS_PRESET = 6   # 预设色每行几个(18 色 = 3 行)
_COLS_USER = _COLS_PRESET   # 与预设色同列数: 两组色板列宽一致, 上下对齐
MAX_USER_COLORS = 10


def _prep_dialog(dlg):
    """对话框显示前定死尺寸与位置, 并离屏预演一次完整显示流程。

    `move()` 会置上 WA_Moved, Qt 显示时不再自行摆位; 位置未定死的窗口会先按 sizeHint
    摆在一处, 布局定稿后再居一次, 表现为显示瞬间位置跳变。故不要使用
    `QColorDialog.getColor()` / `QMessageBox.warning()` 这类静态便捷函数 —— 它们内部
    自行 new + show, 走的是"先显示、显示后再挪"的路径。需要同等语义时自行 new 出来,
    过一遍本函数再 exec。

    `ensurePolished()` 不可省: 未上样式表时 QLabel/QPushButton 的 sizeHint 偏小,
    量得的尺寸偏小, 显示后布局定稿会再撑大一次。

    WA_DontShowOnScreen 下 `show()` 仍走完整布局与绘制(只是不映射到桌面), 使真正
    exec() 时后备存储已有内容, 避免窗口先以空白出现再补内容。调用方原有的该属性会被还原。
    """
    dlg.ensurePolished()
    dlg.adjustSize()
    p = dlg.parentWidget()
    if p is not None:
        host = p.window()
        c = host.frameGeometry().center()
        x = c.x() - dlg.width() // 2
        y = c.y() - dlg.height() // 2
        # 父窗中心可能把对话框推出可用区(父窗跨屏/贴着屏幕边), 需拉回
        # 否则 Qt 会重新摆位一次越界的窗口, 又变成先闪一下再跳
        scr = host.screen()
        if scr is not None:
            a = scr.availableGeometry()
            x = max(a.left(), min(x, a.right() - dlg.width()))
            y = max(a.top(), min(y, a.bottom() - dlg.height()))
        dlg.move(x, y)

    # 离屏预演一次完整显示流程: 窗口映射到屏幕的那一刻内容常未画完,
    # 会先以纯白空窗口出现再补内容, 且属竞态(表现为时有时无)
    # WA_DontShowOnScreen 下 show() 照常走完整布局与绘制(只是不映射到桌面),
    # 真正 exec() 时后备存储已有内容。调用方设过该属性的会被还原。
    primed = dlg.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dlg.show()
    dlg.hide()
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, primed)


# ---- 消息框 / 文件对话框 ----
# 一律走下列函数, 不要用 QMessageBox.warning() / QMessageBox.question() /
# QFileDialog.getOpenFileName() / QFileDialog.getExistingDirectory() 这类静态便捷函数:
# 它们内部自行 new + show, 走"先显示、显示后再挪"的路径, 绕过了 _prep_dialog 的 move()。
# 下列函数把顺序倒过来: 先 new、先定死尺寸与位置, 再 exec。
def _message(parent, icon, title, text,
             buttons=QMessageBox.StandardButton.Ok, default=None):
    """消息框。返回被点中的标准按钮(与 `QMessageBox.exec()` 的返回值口径一致)。

    实现是自绘标题栏的 `dialog.MessageDialog`: QMessageBox 带 DWM 绘制的边框, 首次显示
    会出现一两帧错位(见 dialog.py 的说明); 且三种弹窗(取色/型号映射/消息框)需同一套样式。
    `icon` 参数为兼容调用方保留, 当前不绘制图标(标题 + 正文已足够表达)。
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


def _iconify(btn, asset, size=16):
    """给按钮装一个 SVG 图标; 界面图标只有这一种来源(见 theme.py 的「界面图标」)。

    SVG 由 Qt 按 目标尺寸 x devicePixelRatio 重新栅格化, 缩放不失真; 置灰态由 QIcon 的
    Disabled 模式自动变淡, 无需另出灰版素材。

    `size` 需与按钮的 iconSize 一致。
    """
    btn.setIcon(QIcon(asset))
    btn.setIconSize(QSize(size, size))


def _open_and_report(win, path, what):
    """打开一个文件或目录, 结果用吐司提示(失败只提示并记日志, 不弹窗)。

    与 `_open_logo_dir` 同用 QDesktopServices; 后者另有"目录不存在则先创建"的逻辑, 故未合并。
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


# 项目仓库地址。占位符: 仓库建成后只需改这一处(同时用作 GitHub 图标的 tooltip)。
REPO_URL = "https://github.com/your-name/ShuiYin-QT6"
_ABOUT_ICON = 20            # 「关于」弹窗里 GitHub 标识的显示边长(逻辑像素)


class _IconLink(QLabel):
    """只有图标、点击打开浏览器的控件(「关于」弹窗底部的 GitHub 标识)。

    图标按设备像素出图: `QIcon.pixmap(size, dpr)` 使 SVG 在 size×dpr 上光栅化, 上屏 1:1,
    不再重采样。不用富文本 `<a><img>` 锚点 —— 那样会经 QTextDocument 再缩放一次而发虚。
    带手型光标、tooltip(URL)与读屏名字。
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
    """关于框顶部的 logo。按设备像素缩放, 避免 Qt 再缩放一次。

    素材缺失时返回 None, 关于框仍能打开。
    """
    path = os.path.join(core.ICONS_DIR, "exif-watermark-logo-1024.png")
    pm = QPixmap(path)
    if pm.isNull():
        return None
    px = max(1, int(round(size * dpr)))
    pm = pm.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                   Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


def _about_label(text, obj_name, spacing=0.0):
    """关于框里的一行文字。字号/字重/颜色交给 QSS(见 theme 的 `QLabel#about*`):
    全局 `QWidget {font-size:12px}` 会压过 setFont, 字号只能写在 QSS 里。

    本函数只登记 QSS 无法表达的两项意图: 字距(QSS 无 letter-spacing)记在属性上, 由
    AboutDialog 在抛光之后统一施加; 可选中属整框性质, 由 AboutDialog 建完标签后统一开启。
    """
    lab = QLabel(text)
    lab.setObjectName(obj_name)
    lab.setProperty("letterSpacing", float(spacing))
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lab


class AboutDialog(QDialog):
    """关于弹窗: 顶部 logo + 程序名 + 一句话 + 版本与环境 + 数据目录 + 分发许可。

    品牌区版式(居中):
        名字    22px / 字重 600 / 字距 6px / `#2f4a38`
        副标题  13px / MUTED / 字距 2px
    字距只能用 QFont 设置(QSS 无 letter-spacing)。

    目录只列数据目录: 程序目录等于日志目录, 可由 ☰ 菜单打开; 预览目录为临时目录;
    数据目录存放设置/预设/字体, 需要可直接定位。

    会话统计在此显示, 属本次会话累计值, 按需查看。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        go_frameless(self)
        self.setWindowTitle(self.tr("关于"))
        import platform

        from PySide6 import __version__ as pyside_version

        from core import __version__ as app_version

        v = QVBoxLayout(self)
        v.setContentsMargins(_DLG_PAD + 8, _DLG_PAD + 8, _DLG_PAD + 8, _DLG_PAD)
        v.setSpacing(0)                  # 间距由固定间隔撑开, 便于单独控制(品牌区需要更大间距)

        logo = _about_logo(88, self.devicePixelRatioF())
        if logo is not None:
            lb = QLabel()
            lb.setPixmap(logo)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(lb)
            v.addSpacing(18)

        v.addWidget(_about_label(self.tr("「镶 · 水印」 水印处理工具"), "aboutName", spacing=6.0))
        v.addSpacing(6)
        v.addWidget(_about_label(self.tr("一帧入画　一镶成框"), "aboutSub", spacing=2.0))
        v.addSpacing(18)
        v.addWidget(_about_label(f"v{app_version}", "aboutVer"))
        v.addSpacing(14)
        # 环境两行: 版本号/系统名是数据, 只有模板串需要翻译
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
            "aboutNote")
        note.setWordWrap(True)
        note.setMaximumWidth(420)
        v.addWidget(note)
        v.addSpacing(14)
        # 项目仓库链接: GitHub 标识 + 点击开默认浏览器。
        # 不用富文本 <a><img> —— 那条路会把 SVG 交给 QTextDocument 再光栅化一次而发虚。
        # 改为按设备像素出图的标签(见 _IconLink)。
        v.addWidget(_IconLink(theme._asset("github.svg"), _ABOUT_ICON, REPO_URL,
                              self.tr("项目仓库（GitHub）"), self))
        v.addSpacing(14)
        v.addLayout(dlg_button_row(dlg_button(self.tr("关闭"), self.accept, primary=True)))
        # 可选中必须在定尺寸之前开启: 开启后 Qt 才为标签建立文本控件(QTextDocument),
        # 标签期望尺寸随之变化(22px 行 22→26、13px 行 13→15); 若在 csdify/adjustSize 之后开启,
        # 尺寸已按旧期望定死, 多出的像素会被布局从别处扣除(表现为顶部 logo 被裁)。
        # 仅对含文字的标签开启: 图标标签无文字, 附加文本交互只会多出一个无用的文本控件。
        # 标题栏的「关于」由 csdify 建立, 本段在其之前执行因而不在其列 —— 它压在拖动区上,
        # 开启文本交互会拦截鼠标按下, 导致标题无法拖动窗口。
        # Qt 对仅允许鼠标选中的标签赋予点击聚焦(不进 Tab 链), Tab 仍只停在两个按钮上
        for lab in self.findChildren(QLabel):
            if lab.text():
                lab.setTextInteractionFlags(lab.textInteractionFlags()
                                            | Qt.TextInteractionFlag.TextSelectableByMouse)
        csdify(self, self.tr("关于"), _prep_dialog)
        # 字距必须在最后设: QSS 抛光会把 setFont 的字号压回 12px(不影响字距), 而抛光之前
        # 设的 setFont 会被整体覆盖。故等 csdify/adjustSize 走完再逐行施加。
        for lab in self.findChildren(QLabel):
            sp = lab.property("letterSpacing")
            if sp:
                f = lab.font()
                f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(sp))
                lab.setFont(f)

    @staticmethod
    def _stats_text(parent):
        """会话统计文本; 挂在主窗口上, 取不到时返回空串(统计出错不应导致关于框打不开)。"""
        try:
            return parent._stats.summary(compact=True)
        except Exception:                            # noqa: BLE001
            return ""


class ImageInfoDialog(QDialog):
    """图片信息: 如实报告该图读到的内容 —— 文件信息(只读文件头) + 照片自带的 EXIF。

    不推算"最终会印成什么": 「当前生效值」/「水印显示(最终)」属推算结果, 一旦有「美化型号」
    「型号映射」「自定义水印信息」参与, 就与"图中实际存了什么"混在一张表里而看不清。需要
    了解水印实际显示内容时, 主界面「识别型号」那一行的 tooltip 已在说明。

    因此本组小标题为「照片 EXIF 信息」: 勾选「自定义水印信息」后水印并不使用这些值,
    沿用「水印里会用到的值」即为不实。

    文件各项(大小/像素/格式/时间)均只读文件头, 不解码整张图。
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
                grid.setRowMinimumHeight(r[0], 10)
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
        # 与渲染端共用同一套格式化函数, 保证显示的快门/光圈与印出来的写法一致
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

    **每次打开都重新读盘**: 手工往 presets 目录里放一个 presets5.json, 无需重启即可看到。

    交互与「型号映射」弹窗一致: 每个动作即时生效(无 OK/取消), 关闭即关闭。控件在本类、
    逻辑在 MainWindow(它持有 _params/_apply_params/_undo_step), 故各按钮都是
    "把控件交给窗口对应方法"。
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

    # ---- 供 MainWindow 各方法调用的小接口 ----
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


_CHIP_W = theme.CTRL_H + 20          # 色块尺寸: 宽于控件高度, 便于点击与辨识
_CHIP_H = theme.CTRL_H
_CHIP_GAP = theme.GAP_CTRL
# 弹窗四周留白: 兼作卡片投影的外扩余量(投影贴边会被裁成直角)
_DLG_PAD = 12
# 弹窗最小宽 = 一行色块 + 卡片内边距 + 弹窗留白, 使色板恰好铺满卡片宽度
_DLG_W = (_COLS_PRESET * _CHIP_W + (_COLS_PRESET - 1) * _CHIP_GAP
          + 2 * theme.PAD_CARD + 2 * _DLG_PAD)
# 「当前颜色」色块: 与色板同高但更宽, 是这张卡的焦点而非网格里的一格
_CUR_W = theme.CTRL_H * 2 + 8


def _swatch_fg(color):
    """色块上的前景色: 浅底用黑(000)、深底用白(fff)。

    色块文字与选中态勾号的素材都按这一条判据选色, 因此判据只写在这里一处。
    """
    return "#000" if QColor(color).lightness() > 128 else "#fff"


def _swatch_check(color):
    """选中态勾号的素材路径: 与 `_swatch_fg` 同判据, 深浅底各一份(SVG 填充色写死)。"""
    return theme.CHIP_CHECK_DARK if _swatch_fg(color) == "#000" else theme.CHIP_CHECK_LIGHT


def _swatch_qss(color, radius=theme.RADIUS_SMALL, selected=False):
    """色块样式(取色弹窗的色块与「当前颜色」共用此定义)。

    悬停/按下/聚焦都必须同时写出 background —— 仅写 border 会落到主题的
    `QPushButton:hover{background:#EEF2EC}`, 鼠标移入后色块变灰。

    四种状态的几何完全一致(margin 2px + border 2px), 仅描边颜色变化:
      · 粗细变化会使未选中与选中的色块大小不一, 切换时跳动;
      · 2px margin 使描边离开色块一圈、中间露出卡片底色, 选中态表现为色块外套一圈;
        未选中为 LINE, 选中为主色;
      · 悬停不改变色相, 仅把描边换成深一档的暖灰(MUTED)。
    选中另配勾号素材(见 `_swatch_check`), 不单独依赖描边表达。
    """
    col = QColor(color)
    fg = _swatch_fg(color)
    ring, ring_hot = ((theme.ACCENT, theme.ACCENT2) if selected else (theme.LINE, theme.MUTED))
    return ("QPushButton { background:%s; border:2px solid %s; border-radius:%dpx; margin:2px;"
            " color:%s; font-weight:bold; padding:0; }"
            "QPushButton:hover, QPushButton:focus { background:%s; border:2px solid %s; }"
            "QPushButton:pressed { background:%s; border:2px solid %s; }"
            % (col.name(), ring, radius, fg,
               col.name(), ring_hot, col.name(), ring_hot))


class _ColorChip(QPushButton):
    """取色弹窗里的一个色块。

    QPushButton 只有 clicked、没有 doubleClicked, 而"双击该格换色"需要的正是双击,
    故自行转发鼠标双击事件(单击仍走 clicked)。
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

    三组内容各占一张卡片, 与主窗参数卡片同一套外观(白底 / 圆角 / 暖投影 / cardTitle + hint)。

      当前颜色   一格色块(点它开系统取色器, 拖动取色/调透明度都靠它) + 一个色号输入框。
                  色号只显示一处: 色块只表达颜色, 文字交给输入框。
      预设颜色   点一下即应用; 当前使用的带 ✓ 和强调色描边。
      我的颜色   点应用, 双击改该格颜色, 右键删除。空位为浅底格子(不勾边、不可点)并固定
                  画满 10 格; 卡片标题右边另写「已保存 n / 10」以表达占用格数。

    点色块是**即时应用**(弹窗不关): 预览在工作线程里跑、主窗口照常重绘, 可边点边看效果,
    满意后点「关闭」。收藏色存 settings.json 的 custom_colors(核心截到 10 个)。
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
        acts.addStretch(1)                       # 撑开间隔, 使右侧按钮靠右
        # 取色即时应用, 「关闭」是本次操作的落点而非取消, 故给主按钮的强调色
        acts.addWidget(dlg_button(self.tr("关闭"), self.accept, primary=True))
        v.addLayout(acts)

        # 初始焦点收到对话框自身, 不落在子控件上: 色块与色号框都有 :focus 外观变化,
        # 而焦点在窗口激活后才生效, 比首帧晚一两帧 —— 那两帧内控件先按无焦点绘制再补上,
        # 表现为打开时色块闪一下。焦点置于对话框上则自首帧起无变化, Tab 仍可照常进入各控件。
        # 不可改为将焦点交给其它控件: 焦点落到色号输入框同样会闪(主题 QLineEdit:focus 会换底色),
        # 必须保证该轮没有子控件持有焦点
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

        self._sync_current()
        csdify(self, self.tr("选择颜色"), _prep_dialog)

    # ---- 取色弹窗自身的构造 ----
    def _card(self, title, note=""):
        """分组卡片: 与主窗 Card 同一套外观, 但没有「重置本组」—— 此处没有"一组参数"可重置。

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

        色块只表达颜色本身(与色板同高的一格), 色号的显示与编辑只由输入框负责。
        """
        frame, body, _ = self._card(self.tr("当前颜色"))
        row = QHBoxLayout()
        row.setSpacing(theme.GAP_CTRL)
        self._cur_chip = QPushButton()
        self._cur_chip.setFixedSize(_CUR_W, theme.CTRL_H)
        self._cur_chip.setCursor(Qt.PointingHandCursor)
        self._cur_chip.setAccessibleName(self.tr("当前颜色"))   # 按钮内无文字, 需提供读屏名称
        self._cur_chip.setToolTip(self.tr("点击打开系统取色器（可拖动取色、调透明度）"))
        self._cur_chip.clicked.connect(self._native)
        row.addWidget(self._cur_chip)
        self._hex = QLineEdit()
        self._hex.setFixedHeight(theme.CTRL_H)
        self._hex.setMaxLength(9)
        self._hex.setAccessibleName(self.tr("色号"))   # 卡片标题不能作为该输入框的标签, 读屏名称另设
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
        # `_reload_custom` 重建色板时的锚点: 必须取卡片 body, 用对话框顶层布局 indexOf 会返回 -1
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
        """在卡片标题右侧刷新「已保存 n / 共 total」计数。"""
        self._custom_note.setText(self.tr("已保存 {n} / {total}").format(n=len(self._custom()), total=MAX_USER_COLORS))

    def _custom(self):
        try:
            return list((self._state.get_custom_colors() or {}).get("colors") or [])
        except Exception:                     # noqa: BLE001 - 读不到就当没有收藏色
            return []

    def _grid(self, colors, cols, total=0, editable=False):
        """色块网格。`total>0` 时固定画满 total 个位置, 未存颜色的位置为浅底空位格。
        末列留一条伸缩位: 色块定宽, 拉宽弹窗时保持左对齐而不被均匀拉开。
        """
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
        if color is None:                     # 空位格: 浅实底、无描边、不可点
            lab = QLabel()
            lab.setFixedSize(_CHIP_W, _CHIP_H)
            lab.setAccessibleName(self.tr("未使用"))
            lab.setToolTip(self.tr("未使用"))
            # 空位格只给一层极浅的底、不勾边: 并排勾边会压过有颜色的格子
            lab.setStyleSheet("background:%s; border:none; border-radius:%dpx;"
                              % (theme.EMPTY_SLOT_BG, theme.RADIUS_SMALL))
            return lab
        b = _ColorChip(color, index)
        b.setFixedSize(_CHIP_W, _CHIP_H)
        b.setCursor(Qt.PointingHandCursor)
        b.setAccessibleName(self.tr("颜色 {color}").format(color=color))   # 色块只绘制颜色, 读屏需靠名字辨认
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
        """色块 = 底色 + 选中态(勾号素材与强调色描边)。新建与刷新统一走此处, 避免两处状态不一致。

        勾号用素材而不是字形: 界面字体是内置子集, 不含 ✓(U+2713); 素材深浅底各一份,
        按 `_swatch_check` 的判据选。
        """
        selected = color.lower() == self._current.lower()
        btn.setText("")
        btn.setIcon(QIcon(_swatch_check(color)) if selected else QIcon())
        btn.setIconSize(QSize(_CHIP_CHECK, _CHIP_CHECK))
        btn.setStyleSheet(_swatch_qss(color, selected=selected))

    def _sync_current(self):
        """把「当前颜色」格与色号框刷成 self._current, 并重刷所有色块的选中态。"""
        self._cur_chip.setStyleSheet(
            _swatch_qss(self._current, radius=theme.RADIUS_CTRL))
        self._hex.setText(self._current)
        for b in self._chips:
            self._paint_chip(b, b.color)

    def _choose(self, color):
        self._current = color
        self._sync_current()
        self.picked.emit(color)               # 即时应用, 弹窗不关闭

    def _apply_hex(self):
        """色号输入框: 回车 / 失焦时生效。输入不合法则回退为当前色号, 不修改参数。"""
        txt = self._hex.text().strip()
        col = QColor(txt if txt.startswith("#") else "#" + txt)
        if not txt or not col.isValid() or col.name().lower() == QColor(self._current).name().lower():
            self._sync_current()
            return
        self._choose(col.name())

    # ---- 系统取色器 ----
    def _pick_native(self, initial=None):
        """开系统取色器, 返回选中的色号(取消返回 None)。

        不使用 `QColorDialog.getColor()`: 该静态函数显示对话框后 Qt 会再次调整位置
        (窗口先闪现在屏幕左上角), 故此处自行构造对话框并经 `_prep_dialog` 固定位置。
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
            self._state.save_custom_colors(arr)      # 上限由核心截断到 MAX_USER_COLORS
        except Exception as e:                       # noqa: BLE001 - 存不上不该把弹窗弄崩
            _message(self, QMessageBox.Icon.Warning, self.tr("保存失败"), str(e))
            return False
        self._reload_custom()
        return True

    def _remember(self):
        cur = self._current
        arr = [c for c in self._custom() if c.lower() != cur.lower()]
        arr.append(cur)                  # 重复项移到末尾: 该列按最近保存排序
        if len(arr) > MAX_USER_COLORS:
            # 超出上限时先挤掉最早项: 核心按 `[:10]` 截断, 直接传 11 项会把刚保存的那项截掉
            arr = arr[len(arr) - MAX_USER_COLORS:]
        self._save_custom(arr)

    def _remove(self, color):
        """从「我的颜色」中删除指定色号。"""
        self._save_custom([c for c in self._custom() if c.lower() != color.lower()])

    def _edit_saved(self, index):
        """把「我的颜色」第 index 项替换为新选的颜色(双击色块或右键菜单进入)。"""
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
        """就地重建「我的颜色」色板, 保持原位置。

        定位必须用卡片自己的 body 布局(`_custom_body`): 色板挂在卡片内, 用对话框顶层布局
        indexOf 返回 -1, insertWidget(-1) 会把新色板追加到弹窗末尾, 卡片被掏空且弹窗变高。
        """
        body = self._custom_body
        idx = body.indexOf(self._custom_box)
        # 旧色块需同步从选中态列表摘除: 它们随后被销毁, 保留会让 _sync_current 访问已析构对象
        self._chips = [c for c in self._chips if not self._custom_box.isAncestorOf(c)]
        box = self._grid(self._custom(), _COLS_USER, total=MAX_USER_COLORS, editable=True)
        body.removeWidget(self._custom_box)
        # 必须 setParent(None): removeWidget 只从布局摘除, 控件仍是本窗口子对象并继续绘制在原位置,
        # 直到 deleteLater 执行 —— 否则新旧两块重叠
        self._custom_box.setParent(None)
        self._custom_box.deleteLater()
        self._custom_box = box
        body.insertWidget(idx, box)
        self._sync_custom_note()      # 同步容量计数, 否则停留在旧值


class ElidedLabel(QLabel):
    """超长文本(路径之类)的标签: 按控件宽度在中间省略。
    QLabel 自身不省略, 长路径会顶大 minimumSizeHint 并撑破布局。
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(70)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)   # 允许选中以复制完整路径
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
    """型号映射表编辑弹窗(列表 + 增删改)。

    映射表按 Logo 目录存 model_map.json。
    """

    def __init__(self, state, parent=None):
        super().__init__(parent)
        go_frameless(self)                 # 须在窗口创建之前调用(见 dialog.py)
        self.setWindowTitle(self.tr("型号映射管理"))
        self.resize(560, 460)
        self._data = {}
        try:
            self._logo_dir = (parent._params.get("logo_dir") if parent else "") or core.DEFAULT_LOGO_DIR
        except Exception:
            self._logo_dir = core.DEFAULT_LOGO_DIR

        v = QVBoxLayout(self)
        tip = QLabel(self.tr("识别到的原始型号 --> 水印上显示的型号。"
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

        # 三个动作即时生效(点完即写盘), 故无独立的「保存到映射表」按钮。
        # 按钮次序: 删除在最左, 「关闭」在右下角且为主色(与其它自绘弹窗一致)。
        # `QT_TRANSLATE_NOOP` 必须包在字面量上(lupdate 看不穿变量), 否则整条漏译
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
        """把内存中的映射表画进列表; `select` = 要选中的「原始型号」。

        此处不读盘: 每次调用都 load_model_map() 会用文件里的旧内容覆盖刚写入 self._data 的条目。
        读盘只在 __init__, 写盘只在 _persist。

        `select` 供添加 / 修改之后使用: 整表重画会清除选中状态(currentRow 回到 -1),
        故重新选中该条并滚动到可见处。
        """
        self._list.clear()
        for k in sorted(self._data):
            self._list.addItem(f"{k}   -->   {self._data[k]}")
        if select is None:
            return
        for i in range(self._list.count()):
            if self._item_key(self._list.item(i)) == select:
                self._list.setCurrentRow(i)        # 会连带触发 _on_select, 表单同步为该条
                self._list.scrollToItem(self._list.item(i))
                break

    @staticmethod
    def _item_key(item):
        """从列表项文本「原始   -->   显示」中取出原始型号。分隔符须与拼串处一致。"""
        return item.text().split("   -->   ")[0]

    def _on_select(self, cur, _prev):
        if cur is None:
            return
        key = self._item_key(cur)
        self._raw.setText(key)
        self._nice.setText(self._data.get(key, ""))

    def _push_to_window(self):
        """把表同步给主窗口的 `_params["model_map"]`。

        撤销快照取自该字段(`_snapshot` 连 model_map 一起存), 不同步则撤销栈中看不到这一步。
        顺带刷新「水印将显示：X」一行。无主窗口时只更新自身。
        """
        win = self.parent()
        if win is None or not hasattr(win, "_params"):
            return
        win._params["model_map"] = dict(self._data)
        upd = getattr(win, "_update_effective_model", None)
        if upd is not None:
            upd()

    def _apply_change(self, label, fn):
        """执行一次改动并单独记一步撤销(增 / 改 / 删各一步, 步名进入撤销按钮的 tooltip)。

        确认框须由调用方在进入事务之前弹出: 嵌套事件循环会让 600ms 的收尾定时器提前落定该步,
        使一步的边界不确定。
        """
        win = self.parent()
        begin = getattr(win, "_begin_undo", None) if win is not None else None
        commit = getattr(win, "_commit_undo", None) if win is not None else None
        if begin is None or commit is None:          # 无主窗口时退化为直接执行
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
            self._data[raw] = nice      # 允许显示名为空
            self._persist()             # 先落盘(失败会回滚 _data), 再同步给窗口
            self._push_to_window()
            self._reload(select=raw)    # 选中该条以标记刚编辑的项(见 _reload)

        self._apply_change(label, _do)

    def _remove(self):
        """删除表单中的那条映射。执行前经一次确认: 该动作即时写盘, 且按钮紧邻「添加 / 修改」。"""
        raw = self._raw.text().strip()
        if raw not in self._data:
            # 不静默返回, 否则会被当作点击无效且条目仍在
            _message(self, QMessageBox.Icon.Information, self.tr("提示"),
                     self.tr("「原始型号」里现在这条不在映射表里，请先在列表里选中要删除的条目。"))
            return
        nice = self._data.get(raw) or ""
        if _message(self, QMessageBox.Icon.Question, self.tr("删除型号映射"),
                    self.tr("确定删除「{raw}」-->「{nice}」这条映射？").format(
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
        """立刻写盘(增 / 改 / 删均即时生效, 无独立的「保存到映射表」按钮)。

        写失败时提示并把内存回滚为磁盘内容, 否则界面、文件与下次打开弹窗三者内容不一致。
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
    """「字体文件」管理弹窗(☰ →「字体文件…」)。

    四种字重各一个字体文件路径。形态与「预设方案…」一致: 改动即时生效, 右下角为「关闭」。

    行复用参数行(`ParamRow` 的 path 型): 改动走 `_on_param`, 即撤销(步名即该行标签)、
    写设置、重渲预览三条链路。
    """

    def __init__(self, state, parent=None):
        super().__init__(parent)
        go_frameless(self)                         # 须在窗口创建之前调用(见 dialog.py)
        self.setWindowTitle(self.tr("字体文件"))
        # 为路径框保留可读宽度(超出可横向滚动); adjustSize 不会压到最小宽度以下
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
        # 等价于卡片自带的「重置本组」: 否则只能经「恢复默认设置」重置全部参数
        v.addLayout(dlg_button_row(
            dlg_button(self.tr("恢复默认字体"), self._reset),
            dlg_button(self.tr("关闭"), self.accept, primary=True)))
        csdify(self, self.tr("字体文件"), _prep_dialog)

    def _reset(self):
        """四个路径恢复出厂默认, 整批计入撤销的一步。

        按 `_reset_card` 的写法直接修改 `_params` 并回填控件, 不走 `_on_param`(后者自开撤销事务,
        会把整批一步拆成四步)。
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
        if step is None:                           # 无主窗口时只处理自身
            _do()
        else:
            step("__reset_fonts__", self.tr("重置「字体文件」"), _do)
        notify = getattr(win, "_notify", None) if win is not None else None
        if notify is not None:
            notify(self.tr("已重置「{title}」为默认值").format(title=self.tr("字体文件")))


# ---------- 吐司(一次性提示) ----------
TOAST_MS = 3200
TOAST_FADE_MS = 350
TOAST_TOP = 14           # 距窗口内容顶部的距离
TOAST_SLIDE = 16         # 出场 / 收起的位移
TOAST_MIN_W, TOAST_MAX_W = 240, 560
TOAST_PAD_X = 20         # 盒子左右内边距
TOAST_CHROME = TOAST_PAD_X * 2 + 2   # 文字宽度之外的固定开销: 左右内边距 + 两侧 1px 描边


class Toast(QWidget):
    """顶部居中显示的一次性提示(已保存 / 已导出 / 失败 …)。

    介于弹窗与常驻状态行之间: 弹窗需用户点击才消失, 状态行不会自行消失, 而结果类消息只需被看到。

    实现为主窗口内的浮层, 而非独立的半透明顶层窗口: 后者在 Win11 上有 DWM 首帧错位风险,
    且需自行换算 dpr; 浮层挂在中央部件上则零窗口、零 DWM。代价是不能浮到窗口之外。
    不设投影: 一个控件的子树只能有一层 QGraphicsEffect, 外层已用 opacity 做淡入,
    投影会构成效果套效果并把该区域绘制错乱。
    显示期间会压住工具栏一行约 3.2 秒, 但控件不吃鼠标事件(WA_TransparentForMouseEvents)。

    两条纪律: 只由用户显式动作触发(后台的自动预览 / 自动保存不发); 单例, 后一条替换前一条。
    """

    def __init__(self, parent):
        super().__init__(parent)
        # 每个子控件都必须设置该属性: 它不向下传递, 命中测试会先落到未设置的那一层,
        # 随后沿父子链传播, 不会穿透到下方应接收点击的控件
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._box = QFrame(self)
        self._box.setObjectName("toastBox")
        self._box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # 样式: 深色半透明底 + 浅色字。
        # 样式表必须带选择器: 不带选择器的规则会同时作用于子控件, 使内部 QLabel 也被套上同一圈描边。
        # 不挂投影: 一个控件的子树内只能存在一层 QGraphicsEffect —— 外层用 opacity 实现淡入时,
        # 投影会构成效果套效果, Qt 将重入画笔并破坏该区域绘制(同一 paint device 同时只能有一个画笔)
        self._box.setStyleSheet(
            "QFrame#toastBox { background: rgba(15,23,42,0.94); border: none;"
            " border-radius: 10px; }")
        bl = QVBoxLayout(self._box)
        bl.setContentsMargins(TOAST_PAD_X, 12, TOAST_PAD_X, 12)   # 左右 20px / 上下 12px
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
        # 位移与淡入使用一个并行动画组(二者同时运行)
        self._anim_pos = QPropertyAnimation(self, b"pos", self)
        self._anim_op = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim = QParallelAnimationGroup(self)
        self._anim.addAnimation(self._anim_pos)
        self._anim.addAnimation(self._anim_op)
        for a in (self._anim_pos, self._anim_op):
            a.setDuration(TOAST_FADE_MS)
            # 缓动取 OutCubic(起步快、收尾缓); Qt 默认的 Linear 观感生硬
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
        # 必须监听动画自身的 finished, 不用 singleShot 估算时间: 估算过早时动画仍在收起、条件不成立,
        # 控件将不再被隐藏(表现为 opacity 为 0 但控件仍可见)
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
        """父控件尺寸变化时重算目标位置。

        动画运行期间必须修改其终点而不能只调用 move(): 否则下一个 tick 会把控件拉回旧位置
        (出场动画未结束时缩放窗口, 控件会停留在旧的水平位置)。
        """
        if not self.isVisible():
            return
        end = self.target_pos()
        if self._hiding:
            end = QPointF(end.x(), end.y() - TOAST_SLIDE)
        if self._anim.state() == QAbstractAnimation.State.Running:
            # 重新启动一段动画, 不修改运行中那段动画的终点: setEndValue() 在运行中修改终点会把控件移到 (0,0);
            # _animate() 以当前位置为新起点, 因此动作连续且终点正确
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
        """动画结束时收尾。仅在透明度收到 0 时隐藏 —— 出场动画结束也会进入此分支, 不应隐藏刚显示的控件。"""
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
        # 自定义水印信息(手动 EXIF); 不做持久化
        self._manual_on = False
        self._manual = {"make": "", "model": "", "exposure": "", "fnumber": "", "iso": ""}
        self._manual_preview = False     # 是否由「刷新预览效果」触发的那一轮(仅它提示结果)
        # 预览状态标识的两份"账"(见 _preview_state):
        #   _shown_token —— 屏幕上那张图对应的参数指纹(只有**成功**上屏才更新)
        #   _preview_err —— 最近一次尝试的失败原因(None=最近一次是成功的)
        self._shown_token = None
        self._preview_err = None
        # 撤销/重做(见 _begin_undo): 栈 + 在途事务 + 收尾定时器
        self._undo_stack, self._redo_stack, self._undo_txn = [], [], None
        # 当前界面语言(启动时 main.py 已按 settings['lang'] 装好翻译器)
        self._lang = (state.settings or {}).get("lang") or i18n.DEFAULT_LANG

        self.setWindowTitle(self.tr("「镶 · 水印」 水印处理工具"))
        self.setAcceptDrops(True)
        self._split_user_set = False     # 用户是否手动拖过分隔条(拖过就不再自动分配比例)
        self._fitted = False             # 是否已按屏幕可用区定过尺寸(见 _fit_to_screen)
        self._stats = SessionStats(core)  # 会话统计: 界面右侧显示 + 退出时落一条汇总
        self._fit_to_screen()
        self._build_ui()
        self._sync_visibility()
        self._mode_btns["single"].setChecked(True)
        self._sync_files_ui()
        self._sync_refresh_btn()         # 启动时还没选图: 「刷新预览效果」先按灰
        self._sync_info_btn()            # 「图片信息」同理
        # 必须执行一次: 「复制原始型号」的可用状态与说明文字在此确定,
        # 否则启动后该按钮为可用状态且 tooltip 为空
        self._update_effective_model()
        self._unify_spins()
        self._unify_buttons()
        # 型号美化卡里两个按钮取同宽, 否则右边缘一宽一窄参差
        w = max(self._map_btn.sizeHint().width(), self._copy_btn.sizeHint().width())
        self._map_btn.setFixedWidth(w)
        self._copy_btn.setFixedWidth(w)
        self.set_tab(0)

        # 参数自动保存: 防抖 500ms 写入 settings.json, 关窗时兜底落盘
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

        # 悬停标注的"心跳": 每 80ms 按光标位置重算一次该高亮哪一段间距。
        # 该布局中子控件的 Enter/Leave 不可靠: 子控件会先取走事件、两行之间移动时到达顺序不定、
        # 参数行 tooltip 弹出时主窗会收到 Leave。因此以定时重算为准 —— 事件丢失或错序最多使标注
        # 晚一个周期出现。
        # 开销: 每拍一次 QCursor.pos() 与一次 widgetAt(或矩形扫描), 结果经 set_highlight 去重,
        # 不涉及预览线程、不进入 paintEvent。
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(_HOVER_POLL_MS)
        self._hover_timer.timeout.connect(self._sync_gap_hover)
        self._hover_timer.start()
        # 全窗第一组快捷键
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
                # 收尾两步缺一不可:
                #  ① 消化挂起的布局请求 —— 隐藏子控件不会立即使布局缓存失效, 期望高度与最小高度需等下一轮布局请求才更新;
                #  ② 容器当场取下新高度 —— 容器高度归滚动区管理, 同样需等下一轮, 而下一轮在冻结之外;
                #     宽度不变(widgetResizable 会在同步事件中改回)。
                # 尺寸约束方案不适用: 约束会把容器最小高度钉在旧值上, 缩小时 resize 被夹回旧高度
                QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
                host.resize(host.width(), host.sizeHint().height())
                host.setUpdatesEnabled(True)

    def _sync_mirror_rows(self, changed):
        """把镜像行的显示值刷新为底层参数的值。

        镜像行(内侧/外侧间距)是 UI 层概念: 它自身不存值, 写入的是 gap_image_top/bottom 等参数。
        因此批量赋值(重置本组 / 加载预设 / 恢复默认)之后必须回填一次, 否则滑块停在旧位置而后端参数已更新,
        表现为重置未生效, 再次拖动滑块又会跳到未设置过的数值。
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
        title = card.title_display

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

        不写死像素: 高缩放比下逻辑可用区会明显变小(如 1080p@125% 为 1536x816), 写死高度会顶出屏幕、
        底栏落到屏幕外。留边 _WIN_EDGE px 保证四边不贴边。

        只影响初始值: 不做尺寸持久化, 每次启动按当前屏幕重新计算(见 README 的"已知事项")。
        """
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
        """按 _SPLIT_LEFT 重排左右列(窗口缩放时保持比例); 用户手动拖动过分隔条后本次会话不再自动分配。

        分母使用 sum(sizes()): 它是分隔条实际可分配的宽度, 与 handle 宽度(8px)无关。
        窗口极窄时 QSplitter 会钳制到各列最小值, 比例自动退让。
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
            # 底栏留白: 首帧计算一次, 再补一次 —— 首帧窗口尚未映射, 分栏面板的最终坐标不可用,
            # 需延到下一轮事件循环
            self._sync_bottom_lead()
            QTimer.singleShot(0, self, self._sync_bottom_lead)

    # ---------- 构建 ----------
    def _build_ui(self):
        # 预览控件先建: 悬停心跳与 set_highlight 都指向它(见 MainWindow.__init__ 里的 _hover_timer)
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
        # 左列不留横向内边距: 留白必须位于视口之内(页签行与卡片宿主); 放在左列等于置于滚动区之外,
        # 卡片投影贴着视口边界会被裁成直角
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(theme.GAP_CARD)
        lv.addWidget(self._segbar_row())
        self._stack = QStackedWidget()
        for tab_id, _title in TABS:
            self._stack.addWidget(self._build_tab(tab_id))
        lv.addWidget(self._stack, 1)
        # 左列最小宽度取三个页签宿主期望最小宽度的最大值 + 竖向滚动条 + 两侧 INSET_COL。
        # 不可交由页签行决定: 滚动区可滚动、报出的最小宽度很小, 左列会被压得过窄, 而参数卡有硬下限
        # (定宽标签 + 滑块 + 数值框); 压过头时参数宿主宽于视口, 卡片右边缘与页签卡不再对齐并出现横向滚动条
        min_host = max(t.widget().minimumSizeHint().width()
                       for t in (self._stack.widget(i) for i in range(self._stack.count())))
        left.setMinimumWidth(min_host + _SB_W + 2 * theme.INSET_COL)
        split.addWidget(left)
        split.addWidget(self._build_preview_pane())
        # 左右按 _SPLIT_LEFT 的比例排布(见 _apply_split_ratio)。不要使用 setStretchFactor:
        # 它按权重分配初始尺寸之外的额外空间, 左列会被固定在初始值上, 与按比例的语义不同。
        # 也不在此调用 setSizes: 首次布局前 sizes() 无意义, 初值由 showEvent 中的 _apply_split_ratio() 给出
        split.setChildrenCollapsible(False)   # 左列变窄后防止被拖塌
        split.splitterMoved.connect(self._on_split_moved)
        outer.addWidget(split, 1)

        self._bottombar = self._build_bottombar()   # 留引用: 吐司要浮在它之上(见 Toast._place)
        outer.addWidget(self._bottombar)
        # 吐司: 挂在中央部件上的浮层(不是独立窗口, 理由见 Toast 的类注释)。**最后建、raise_(),
        # 保证它在所有控件之上** —— 它要盖在自己的布局之上而不参与布局。
        self._toast = Toast(root)
        # 根布局在顶栏与分栏之外只保留底栏: 预览区因此获得完整高度, 右列仅有标题条与预览两段,
        # 两列底边齐平。一次性结果经吐司提示(_notify); 常驻信息各有归属(文件名在顶栏与「输入图片」行、
        # 张数在「处理模式」卡片、启动提示在预览框); 耗时数字在预览标题条右侧, 会话统计在「关于」弹窗。

    def _build_topbar(self):
        top = QFrame()
        t = QHBoxLayout(top)
        # 左缩进对齐左列内容(与页签卡片、参数卡片同一条竖线); 右边缘仍由根布局决定
        t.setContentsMargins(theme.INSET_BAR, 0, 0, 0)
        t.setSpacing(8)
        # ☰ 菜单: 长尾功能收纳于此, 不另起菜单栏(避免在全平铺界面上多出一条空栏)
        self._menu_btn = QPushButton()
        _iconify(self._menu_btn, theme.MENU)
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
        # 「识别型号」不放在顶栏: 它与「型号美化」卡片中该行显示同一信息, 两处重复; 现统一到卡片中。
        # 撤销/重做位于顶栏、「EXIF信息」左侧(见 _build_topbar): 页签卡内部右端放不下带标题文字的两个按钮,
        # 且该位置邻近被撤销的参数卡
        self._undo_btn = QPushButton(self.tr("撤销"))
        self._redo_btn = QPushButton(self.tr("重做"))
        for b, slot, asset in ((self._undo_btn, self.undo, theme.UNDO),
                               (self._redo_btn, self.redo, theme.REDO)):
            b.setObjectName("mini")
            _iconify(b, asset)
            b.setEnabled(False)                  # 栈空时置灰, 见 _sync_undo_btns
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            t.addWidget(b)
        self._sync_undo_btns()
        # 图片信息: 没图可看时置灰(见 _sync_info_btn)。**图标 + 标题文字** —— 单独一个图标
        # 认不出是什么, 与旁边「撤销 / 重做」是同一套观感。
        self._info_btn = QPushButton(self.tr("EXIF信息"))
        self._info_btn.setObjectName("mini")
        _iconify(self._info_btn, theme.INFO)
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
        """顶栏 ☰ 的菜单。长尾功能收纳于此; 界面为全平铺布局, 不再向顶栏添加按钮。"""
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

        预览框下方不得放置任何控件: 否则预览底边会高于左列(含滚动条)21~22px, 视觉上两列不等高。

        标题条与左列页签块等高(42px, 算法见 _build_preview_header), 间隔使用 GAP_CARD,
        因此两块上下沿齐平、预览框顶边与左列第一张卡片齐平; 代价是 42 + GAP_CARD 从预览高度中扣除。

        左留白使用 INSET_COL: 滚动条手柄两侧留白需相等(左侧为卡片宿主留白 + 手柄 margin,
        右侧为手柄 margin + 该留白); 否则预览会紧贴分隔条, 手柄偏向预览一侧。
        """
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(theme.INSET_COL, 0, 0, 0)
        rl.setSpacing(theme.GAP_CARD)       # 与左列"块与块之间"同一节奏
        rl.addWidget(self._build_preview_header())
        rl.addWidget(self._preview, 1)
        return right

    def _build_preview_header(self):
        """预览标题条: 一张圆角小块(与左列卡片同一套底色/边框/圆角), 内含左侧「效果预览」与右侧「刷新预览效果」。

        采用块状而非普通文字行: 左列顶部本身是一张圆角卡片, 右列顶端直接放文字会导致两块观感不同源。

        高度与左列页签块严格相等(42px): 左侧为 6px 内边距 + 28px 按钮 + 6px 内边距 + 1px 边框 x2,
        此处使用同一算法。内容为「效果预览」标题、状态标识(见 PreviewStateChip)与「刷新预览效果」
        (幽灵按钮 #ghost: 无描边无底色, 悬停时显示色块, 属次要动作)。
        不挂投影: 右列顶端无留白余量, 投影会被裁掉。
        """
        self._preview_header = QFrame()
        self._preview_header.setObjectName("card")
        h = QHBoxLayout(self._preview_header)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(theme.GAP_CTRL)
        title = QLabel(self.tr("效果预览"))
        title.setObjectName("cardTitle")
        h.addWidget(title)
        # 状态标识紧随标题右侧(而非靠近刷新按钮): 它描述屏上图像与当前参数的关系, 语义上属于标题;
        # 若置于右侧会与真实按钮并排而被误认为按钮。
        # 它不参与标题条撑高(固定 20px, 见 PV_CHIP_H), 不影响 42px 的不变量
        self._state_chip = PreviewStateChip(self._preview_header)
        h.addWidget(self._state_chip)
        h.addStretch(1)
        self._refresh_btn = QPushButton(self.tr("刷新预览效果"))
        _iconify(self._refresh_btn, theme.RESET, 14)
        # 幽灵按钮(#ghost: 无描边无底色, 悬停时显示色块), 不使用 #mini —— 它是再执行一次的次要动作,
        # 而 #mini 是迷你号普通按钮(带描边、卡片白底、28px 高), 在标题条上视觉分量过重
        self._refresh_btn.setObjectName("ghost")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._refresh_preview_clicked)
        # 上次渲染耗时: 与预览最相关, 置于标题条中; 等宽小字弱色, 与按钮同排不抢。
        # 数值随渲染变化属仪表行为。
        # 位置在按钮左侧(右侧位置属于「刷新预览效果」, 它是该行的落点动作)
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
        """底栏留白 = 预览框左缘 − 底栏内容左缘, 使「导出到」对齐预览框左缘。

        按坐标计算: 预览框左缘与底栏内容左缘换算到根控件同一坐标系后相减。
        调用点均在分栏或窗口尺寸确定之后(setSizes 后、拖动分隔条后、resizeEvent、首帧)。
        空间不足时间距项让路(整组左移), 不影响窗口最小宽度。
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
            # 预设不在本页签: 已移入 ☰ 菜单的「预设方案…」弹窗(见 PresetDialog)
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
        """使滚动条在不可滚动时把手柄绘制为透明。

        滚动条常驻(占位不变使卡片宽度不跳), 但范围为 0 时 Qt 会把 handle 拉满整条轨道。
        此处按滚动范围切换动态属性, QSS 以 `::handle[emptyRange="true"] { background: transparent; }`
        将其绘制为透明; 内容溢出时恢复为深色手柄。
        占位宽度始终保留(不使用 AsNeeded), 避免卡片宽度跳变。
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
        """统一所有按钮的高度。

        宽度仍按各自需要设置(#step/#mini 另有限制)。
        """
        for b in self.findChildren(QPushButton):
            b.setFixedHeight(theme.CTRL_H)

    def _unify_spins(self):
        """统一所有数值框的宽度并将数值居中。

        按全表可能出现的最宽取值计算统一宽度, 避免各行长短不一。
        """
        rows = [r for r in self._rows.values() if hasattr(r, "spin")]
        if not rows:
            return
        fm = rows[0].spin.fontMetrics()
        widest = 0
        for r in rows:
            # 取该行可能出现的最大显示值(整数档是整数, 小数档是带 1 位小数的百分数), 全表按最宽者计算
            txt = f"{r._disp_hi / 10:.1f}" if getattr(r, "_decimals", 0) else str(r._disp_hi)
            widest = max(widest, fm.horizontalAdvance(txt))
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
        """页签实现为分段控件: 外层一张卡片, 内含三个胶囊。"""
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
        # 撤销/重做不在此处: 已移至顶栏「EXIF信息」左侧(见 _build_topbar), 该位置邻近被撤销的参数卡
        # 且可容纳标题文字。页签卡右端仅保留 addStretch, 卡片宽度与下方参数卡仍严格相等
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
        """水印开关: 行=文字种类, 列=显示/粗体/斜体。

        列头说明操作, 行首说明对象, 与三个 .row 一一对应。
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
        # 右侧三列等分: 三列均设 stretch=1, 余量等比摊分; 三列最小宽度相同
        # (格位即 28px 开关, 列头两字更窄), 因此列头与下方开关对齐
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

    def _build_fx_card(self):
        """「描边与投影」: 整块可折叠, 展开后四个功能组用分割线相隔。

        折叠只到"整块"这一层: 组与组之间靠分割线与小标题表达层级, 组内不再各折一次(展开一次看全)。
        组的划分与顺序来自参数表的 `group` 字段(组的顺序 = 首次出现顺序), 组内顺序同表 ——
        不在代码里硬编码任何键(见 AGENTS §2.1)。行仍按表建并登记进 `_rows`, 因此显隐、撤销、
        持久化、预设、预览、悬停与数值框宽度统一这些既有链路全部照旧。
        """
        title = QT_TRANSLATE_NOOP("Card", "描边与投影")
        card = Card(title, collapsible=True)
        # 本卡不提供"整组重置": 四个功能组各有一个「重置」(见 _fx_group_head), 故收起自带那个按钮。
        card.reset_btn.setVisible(False)
        # 收起 = 隐藏内容宿主; 显隐必须走统一出口, 因此在这里登记判据(见 _vis_rule / _sync_visibility)。
        self._vis_rule(card.body_host, lambda c=card: not c.collapsed)
        self._cards.append(card)

        specs = [s for s in PARAM_SPECS if s.get("card") == title and s.get("key")]
        groups = []
        for s in specs:
            if s["group"] not in groups:
                groups.append(s["group"])
        for i, name in enumerate(groups):
            if i:                                   # 组与组之间才画分割线(首组之前不画)
                card.body.addWidget(_fx_divider())
            keys = [s["key"] for s in specs if s["group"] == name]
            card.body.addWidget(self._fx_group_head(name, keys))
            for s in specs:
                if s["group"] != name:
                    continue
                row = ParamRow(s, self._params.get(s["key"]))
                row.changed.connect(self._on_param)
                if s["kind"] == "color":
                    row.colorClicked.connect(lambda r=row: self._open_color_dialog(r))
                self._rows[s["key"]] = row
                card.keys.append(s["key"])
                card.body.addWidget(row)
        return card

    def _fx_group_head(self, name, keys):
        """功能组的小标题行: 组名 + 右端「重置」(只重置本组, 一步撤销)。

        合并成一张卡后, 组的边界只能靠这行表达(否则四组的"颜色 / 宽度 / 方向"分不清是谁的)。
        """
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 0)      # 组与上一组之间多留一点空隙
        h.setSpacing(theme.GAP_CTRL)
        lab = QLabel(self.tr(name))
        lab.setObjectName("groupTitle")
        h.addWidget(lab)
        h.addStretch(1)
        btn = QPushButton(self.tr("重置"))
        btn.setObjectName("mini")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(self.tr("将本组参数恢复为出厂默认值"))
        btn.clicked.connect(lambda _c, g=name, ks=tuple(keys): self._reset_fx_group(g, ks))
        h.addWidget(btn)
        return w

    def _reset_fx_group(self, name, keys):
        """重置一个功能组(开关 + 细节行), 整批算撤销的一步。

        与 `_reset_card` 同形, 范围只到本组; 组内开关复位后其细节行按 `only_if` 收起,
        因此收尾要再走一次显隐出口。
        """
        defaults = core.default_params()
        title = self.tr(name)

        def _do():
            changed = set()
            for key in keys:
                if key not in defaults:
                    continue
                self._params[key] = defaults[key]
                if key in self._rows:
                    self._rows[key].set_value(defaults[key])
                changed.add(key)
            self._sync_mirror_rows(changed)
            self._sync_visibility()
            self._touch()

        self._undo_step(f"__reset_fx__{name}", f"重置「{title}」", _do)
        self._notify(self.tr("已重置「{title}」为默认值").format(title=title))

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
        # 该行必须走 _row 的栅格(定宽标签 + 控制列), 不可自占一整行: 胶囊文字需要横向内边距,
        # 无法贴到卡片内容左线。放入控制列后, 标签贴左线且胶囊左右边界等于控制列边界
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

        # 此处不放「选择图片」按钮: 与顶栏「打开图片」重复, 仅保留顶栏入口;
        # 路径显示保留(顶栏仅显示文件名, 此处显示完整路径并支持选中复制)
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

        # 这四行的显隐随「单张 / 批量」变化 —— 注册到统一出口 _vis_rules, 不单独调用 setVisible(见 _vis_rule)
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
        # 「识别型号」显示照片中识别到的型号(与「美化型号/映射表」无关); 水印最终显示的名称由该行
        # tooltip 给出, 使两项信息共存而不占两行
        self._model_lab = ElidedLabel("—")
        # 不使用 muted 配色: 识别到的型号是判断水印内容的依据, 其余行的值仍为 muted, 此处为例外
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
        # 「复制原始型号」: 未识别到型号(未选图 / 图中无 EXIF)时置灰
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
        # 一项一行(竖排), 标签列与其它卡片对齐, 便于阅读。
        # 文案经 _row/QLineEdit 渲染, lupdate 无法识别变量, 故用 NOOP 列出字面量
        # (context 与运行时查表一致: _row 使用 MainWindow.tr)
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
        """「打开目录」: 在资源管理器中打开 Logo 目录。

        该目录不会自动创建(ensure_dir 仅处理日志/字体/预览), 因此首次点击时先创建再打开。
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
        # 加载 = 用预设覆盖当前全部参数, 且不可撤销, 因此先确认一次(与「恢复默认设置」「删除预设」同一形态)
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
        """存进预设的参数(与 settings 的口径刻意不同)。

        - 带 `canvas_ratio_mode`: 后端只接受数值 `canvas_ratio`, 而 `_params` 中不存在该键(由 UI 档位下拉推导);
          不存档位会导致加载时被 `_apply_params` 跳过, 表现为画布比例不生效。
        - 带 `logo_dir` 与 4 个字体路径: 同一机器上存后取回需还原字体; 导出时由 _PRESET_MACHINE_KEYS 剥离。
        - 不带 mode(单张/批量不属于方案的一部分), 不带 input/output 路径。
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
        # 默认文件名仅作为对话框的建议值(用户可改), 因此替换 Windows 不允许的字符;
        # 程序不依赖该名称识别任何内容(槽位文件名固定为 presets1..10)
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
        # 删除不可撤销, 因此先确认一次(与「恢复默认设置」同一形态)
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
        """把整份参数铺回(加载预设 / 恢复默认 / 撤销重做)。仅接受已知键。

        按参数遍历而非按控件遍历: 部分参数没有控件(如 gap_image_top/gap_top, 仅由上下分离模式下的镜像行写入),
        按控件遍历会遗漏它们, 表现为加载预设后部分间距未更新。
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
        """整批铺参数改动过映射表时, 将内存中的表写回 model_map.json。

        映射表存放于 logo 目录下的 model_map.json, `_params["model_map"]` 只是内存副本。
        仅修改内存而不写文件时, 撤销看似生效(预览中水印名称回退), 但重新打开映射弹窗(读取文件)会再次出现改动。
        仅在内容确实变化时写入: 「恢复默认设置」与不含映射表的预设不触及该文件。
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
        """与 settings 同一口径: 全部参数(除路径类) + canvas_ratio_mode + mode。"""
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
        # 撤销由弹窗逐步记录(见 MapDialog._apply_change), 此处仅收尾: 对齐内存与文件
        # (写盘失败时弹窗会回滚内存, 此时以文件为准)并重渲预览
        self._reload_model_map()
        self._touch()

    def _reset_settings(self):
        """恢复默认设置(会覆盖当前全部调参, 因此先确认一次)。

        logo_dir 不在参数行中(无控件), 需单独从 default_params 取回。
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
        """预览取样图: 单张模式取选中的图, 批量模式取批量列表第一张, 两者都无时沿用上一次的源。

        沿用上一次的源可避免: 从单张切到尚未载入文件的批量模式时预览被清空, 切回后需重新渲染。
        返回 True 表示取样图已变化、需要重渲。
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
        """启用时返回手动信息, 否则返回 None。"""
        return dict(self._manual) if self._manual_on else None

    def _refresh_preview_clicked(self):
        """「刷新预览效果」: 自动预览之外的手动入口, 用于渲染失败后重试或对同一组参数再渲染一次。

        点击后按钮先置灰, 响应到达再复位; 渲染中状态由预览标题条右侧的状态标识常驻显示。
        """
        if not getattr(self, "_preview_src", ""):
            self._notify(self.tr("请先选择图片（也可直接拖入图片或文件夹）"))
            return
        self._manual_preview = True
        self._refresh_btn.setEnabled(False)
        self.request_preview()

    def _finish_manual_preview(self, ok):
        """手动刷新的收尾: 复位按钮 + 给一句结果。

        **仅手动刷新才提示** —— 自动预览在每次参数变更时触发, 若同样提示, 拖动滑块会连续弹出吐司。
        """
        if not getattr(self, "_manual_preview", False):
            return
        self._manual_preview = False
        self._sync_refresh_btn()
        if ok:
            self._notify(self.tr("预览已生成"))

    def _sync_refresh_btn(self):
        """无可预览图像时将「刷新预览效果」置灰。

        使用按钮置灰 + tooltip 说明, 而非常驻的提示文字。
        """
        if not hasattr(self, "_refresh_btn"):
            return
        has = bool(getattr(self, "_preview_src", ""))
        self._refresh_btn.setEnabled(has)
        self._refresh_btn.setToolTip(self.tr("按当前参数重新渲染预览") if has
                                     else self.tr("请先选择图片后再刷新预览"))

    def _sync_info_btn(self):
        """没图可看时把「图片信息」按灰(与「刷新预览效果」同一个口径)。"""
        if not hasattr(self, "_info_btn"):
            return
        self._info_btn.setEnabled(bool(getattr(self, "_preview_src", "")))

    def _preview_payload(self):
        """预览请求的载荷 —— 发请求与计算指纹共用同一份, 避免两处定义漂移。

        指纹必须覆盖所有会改变画面的输入: 取样图、全部后端参数、美化/自定义型号、手动 EXIF。
        遗漏任一输入会导致该输入变化时仍显示为已按当前参数渲染。
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
        """当前参数对应的指纹(短哈希)。与屏上那帧的指纹相等即表示屏上这帧由当前参数渲染。

        使用哈希而非自增版本号: 参数改动后又改回时版本号变化而图像未变, 指纹可自然相等。
        序列化小字典并计算 sha1 为微秒级, 无需缓存。

        `default=repr` 用于非 JSON 值的兜底: repr 不会抛异常, 因此本函数没有失败分支 ——
        标识宁可少报一次最新, 也不能因无法计算指纹而报错。
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
        # 建窗过程中(渲染线程尚未启动)也可能进入此分支, 因此不直接取属性
        worker = getattr(self, "_preview_worker", None)
        if queued_hint or (worker is not None and worker.busy()):
            # **"正在渲"与"改过还没渲"合并成这一态**: 拖滑块时"帧上屏时又改过了"几乎是常态,
            # 各报一态的话两态会来回跳, 而这对用户是同一种事实 —— 还没跟上。真正该报
            # 「待渲染」的是"没人在渲、屏上还是旧参数"(它只在渲染失败后才出现)。
            # 判据(拖动 16 帧: 8 帧判为"还没跟上"、0 次误报"待渲染")见 tools/_probe_preview_state.py 第 ⑤ 组。
            return "rendering"
        if self._preview_err is not None:
            # 失败优先于「指纹相等」与「从未成功渲染」:
            #   · 屏上保留的是上一张成功的图, 即使参数未变也不保证对应当前输入(如源文件已被替换或删除);
            #   · 从未成功渲染时(首次渲染即失败), 报「无预览」会隐藏失败信息
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

        画布按核心的缩略档渲染(见 PREVIEW_MAX_SIDE, 当前 3400): 预览中的水印文字仅数像素高
        (成品 4000px 高、预览框约 720px 高, 文字缩至约 1/6), 细节查看依赖放大镜(按像素宽度从固有源重采样)。
        不要为上屏 1:1 而缩小画布: 更小的画布会导致文字按更小字号绘制、缩放上屏后更模糊。
        """
        if not getattr(self, "_preview_src", ""):
            self._sync_chip()
            return
        req = self._preview_payload()
        # 身份标识随请求走, 由渲染线程原样回传: 界面据此知道"屏上这帧是哪一组参数渲的"
        req["token"] = self._preview_token()
        # 上屏尺寸随请求走(不参与指纹): 渲染线程据此把画布直接缩到预览框要的尺寸,
        # 界面线程就只剩贴图。窗口尺寸变了也只是下一次请求带上新尺寸, 不影响"已同步"判定。
        dev = self._preview.avail_dev()
        req["display"] = (dev.width(), dev.height(), self._preview.devicePixelRatioF())
        # 不防抖、不作废: 覆盖待渲染槽即可, 渲染线程每轮取最新一份
        self._preview_worker.request(req)
        self._sync_chip()

    def _on_rendered(self, resp):
        if not resp.get("ok"):
            err = i18n.tr_msg(str(resp.get("error") or ""))
            # 屏上图像保持不变(保留上一张成功结果), 但记录失败: 状态标识据此报「渲染失败」,
            # 否则屏上图像与参数不符无法从界面察觉
            self._preview_err = err
            self._finish_manual_preview(False)          # 手动刷新失败也要把按钮放回可点(重试入口)
            self._notify(self.tr("预览失败：") + err)
            logging.warning(f"[预览] 渲染失败: {err}")   # 失败也需留痕
            self._sync_chip()
            return
        img = resp.get("image")
        if img is not None:
            self._preview.set_image(img, source=resp.get("source"), canvas=resp.get("canvas"),
                                    shown=resp.get("display"), shown_dpr=resp.get("display_dpr"))
        # 只有**成功**才更新"屏上指纹": 失败时屏上留的是上一张, 指纹也就还该是上一帧的
        self._shown_token = resp.get("token")
        self._preview_err = None
        self._preview.set_geo(resp.get("geom"))
        e2e = (time.perf_counter() - resp.get("_req_t", time.perf_counter())) * 1000
        # 连接符用 ASCII 的 `>`: 箭头字符(如 U+2192)字形小于汉字且与字号不成比例, 小字号下观感异常
        # 本帧耗时 + 本帧靠缓存省下的时间: 后者与渲染耗时并列才看得清"缓存帮了多少";
        # 累计值只会一直变大, 没有对照意义(会话累计仍写在统计汇总里)
        hit_ms = float(resp.get("cache_hit_ms") or 0.0)
        self._perf.setText(self.tr("渲染 {ms:.0f} ms · 请求 > 显示 {e2e:.0f} ms · 缓存命中 -{hit:.0f}ms")
                          .format(ms=resp.get("ms", 0), e2e=e2e, hit=hit_ms))
        self._finish_manual_preview(True)

        # ---- 请求级日志 ----
        # 记录预览链路的排队、渲染、转图与端到端耗时; 预览走内存, 不涉及传输与界面加载
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
            # 该按钮在未识别到型号时已置灰, 此处兜底提示避免静默无响应
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

    def _sync_gap_hover(self):
        """按光标位置决定预览图上高亮哪一段间距 —— 悬停高亮的唯一判据。

        由 MainWindow 的 80ms 心跳定时器驱动(见 __init__ 中的 _hover_timer); 参数行不接收 Enter/Leave 事件。
        两步: ① 通过 QApplication.widgetAt 取得光标下控件并沿父链查找参数行, 不受滚动位置、坐标换算与
        子控件覆盖影响; ② 兜底扫描行矩形是否包含光标(光标位于行的留白上或窗口未上屏时 ① 可能返回空)。

        PreviewBox.set_highlight 内含值未变则不重绘的去重, 因此每拍调用不会产生额外重绘。
        """
        gp = QCursor.pos()
        keys, hit = [], ""
        w = QApplication.widgetAt(gp)
        while w is not None and not isinstance(w, ParamRow):
            w = w.parentWidget()
        if isinstance(w, ParamRow) and w.window() is self and w.isVisible():
            keys, hit = w._hover_keys(), w.key
        else:
            for row in self._rows.values():
                if row.isVisible() and row.rect().contains(row.mapFromGlobal(gp)):
                    keys, hit = row._hover_keys(), row.key
                    break
        _hover_trace("重算: 光标=(%d,%d) -> 命中 %s" % (gp.x(), gp.y(), hit or "无"))
        self._preview.set_highlight(keys)

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
            # 进度显示在进度条自身(格式 n / N); 文件名写入其 tooltip
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
                    core.open_path(out_dir)     # 完成后打开输出目录
            else:
                _message(self, QMessageBox.Icon.Warning, self.tr("处理结果"),
                                         msg or self.tr("处理失败"))

    # ---------- 杂项 ----------
    def _notify(self, text):
        """一次性提示(吐司): 结果 / 失败 / 前置条件提示。只由**用户显式动作**触发。

        后台循环(自动预览、自动保存)不得调用 —— 拖动一次滑块会连续弹出吐司。
        """
        if getattr(self, "_toast", None) is not None:
            self._toast.show_message(text)

    def closeEvent(self, e):
        if getattr(self, "_hover_timer", None) is not None:
            self._hover_timer.stop()        # 关窗时停掉悬停心跳(它只服务界面, 没有收尾动作)
        if self._save_timer.isActive():     # 防抖还没到点就关窗: 立刻落盘, 否则最后一次改动会丢
            self._save_timer.stop()
            self._save_settings_now()
        # 退出时落一条会话汇总: 单条日志答不了"这次整体有没有退化/缓存有没有用"。
        # 分行写(每行带 [统计] 前缀), 不用一行塞满 —— 那行已经长到没人愿意读。
        try:
            for line in self._stats.summary().splitlines():
                logging.info("[统计] " + line)
        except Exception as ex:             # noqa: BLE001 - 汇总失败不能挡住关窗
            logging.warning(f"[统计] 汇总失败: {ex}")
        self._preview_worker.stop()
        self._preview_worker.wait(2000)
        self._event_watcher.stop()
        self._event_watcher.wait(2000)
        self.state.cleanup()          # 只清本程序自己的 PREVIEW_DIR(见 XSY_PREVIEW_DIR)
        super().closeEvent(e)
