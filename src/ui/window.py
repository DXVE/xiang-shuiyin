# -*- coding: utf-8 -*-
"""「镶 · 水印」 水印处理工具 主窗口: 参数面板由 PARAM_SPECS 一张表驱动, 类由若干 mixin 按职责组合
(见同目录 win_*.py), 本文件只保留 __init__ 与类说明。"""
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


from core import cache
from . import i18n, single, theme
from .dialog import MessageDialog, csdify, dlg_button, dlg_button_row, go_frameless
from .render import EventWatcher, PreviewWorker, _pil_to_qimage
from .stats import SessionStats
from .toast import Toast
from .specs import PARAM_SPECS, _BACKEND_KEYS, backend_params, migrate_preset
from .dialogs.base import (_CHIP_GAP, _CHIP_H, _CHIP_W, _COLS_PRESET, _COLS_USER, _CUR_W,
                           _DLG_PAD, _DLG_W, _message, _open_and_report, _open_dir_dialog,
                           _open_file_dialog, _prep_dialog, _save_file_dialog)
from .widgets import (Card, ElidedLabel, NoWheelComboBox, ParamRow, PreviewBox, PreviewStateChip,
                      _ColorChip, _IconLink, _LABEL_W, _PV_CHIP_TIP, _fx_divider, _hover_trace,
                      _iconify, _swatch_check, _swatch_qss)
from .dialogs.about import AboutDialog
from .dialogs.image_info import ImageInfoDialog
from .dialogs.preset import PresetDialog
from .dialogs.color import ColorDialog
from .dialogs.map import MapDialog
from .dialogs.font import FontDialog
from .winconst import _HOVER_POLL_MS, _UNDO_COALESCE_MS
from .win_build import BuildMixin
from .win_undo import UndoVisibilityMixin
from .win_preset import PresetMixin
from .win_preview import PreviewMixin
from .win_files import FilesMixin
from .win_actions import ActionMixin
from .win_generate import GenerateMixin
from .win_events import EventMixin




class MainWindow(BuildMixin, UndoVisibilityMixin, PresetMixin, PreviewMixin, FilesMixin, ActionMixin, GenerateMixin, EventMixin, QMainWindow):
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
# 预览状态标识的两份账(见 _preview_state): _shown_token = 屏上那帧的参数指纹(只在成功上屏时更新),
# _preview_err = 最近一次尝试的失败原因(None = 最近一次是成功的)
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
        self._stats = SessionStats(cache)  # 会话统计: 界面右侧显示 + 退出时落一条汇总
        self._fit_to_screen()
        self._build_ui()
        self._sync_visibility()
        self._mode_btns["single"].setChecked(True)
        self._sync_files_ui()
        self._sync_refresh_btn()         # 启动时还没选图: 「刷新预览效果」先按灰
        self._sync_info_btn()            # 「图片信息」同理
# 必须执行一次: 「复制原始型号」的可用状态与说明文字在此确定
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


# 根布局在顶栏与分栏之外只保留底栏: 预览区获得完整高度、两列底边齐平; 一次性结果走吐司(_notify)


            # 颜色行的色块不用管: 它自己铺满控件区(拉伸), 右边缘天然落在步进器组那一列


