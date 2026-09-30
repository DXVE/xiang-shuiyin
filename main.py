# -*- coding: utf-8 -*-
"""「镶 · 水印」 水印处理工具 入口。"""
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _data_root():
    if os.name == "nt":
        return os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


os.environ.setdefault("XSY_BASE_DIR", os.path.join(_data_root(), "XiangShuiyin-QT6"))
os.environ.setdefault("XSY_PREVIEW_DIR",
                      os.path.join(tempfile.gettempdir(), "XiangShuiyin_preview_qt"))

from core import __version__ as app_version   # noqa: E402
from core.config import (BASE_DIR, ICONS_DIR, LOG_DIR, LOG_FILE, LOG_TIMESTAMP, PREVIEW_DIR,
                         PREVIEW_MAX_SIDE, SETTINGS_FILE, _LAYER_CACHE_BUDGET, _LAYER_CACHE_MAX_PX)
from core.jobs import AppState
from ui import theme                      # noqa: E402
from ui.window import MainWindow   # noqa: E402
from ui.winconst import _SLOW_RENDER_MS   # noqa: E402


def _say(text):
    # 向控制台输出一行(无控制台打包时 stdout 为 None, 输出失败一律静默)
    try:
        print(text)
    except Exception:  # noqa: BLE001
        pass


def main():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from ui import i18n, single

    app = QApplication(sys.argv)
    keep, srv = single.claim(single.server_name())
    if not keep:
        _say("[启动] 程序已在运行，已激活已有窗口；本次启动退出")
        return 0

    app.setApplicationName(QCoreApplication.translate("main", "「镶 · 水印」 水印处理工具"))
    theme.apply_font(app)
    theme.apply_palette(app)
    icon = os.path.join(ICONS_DIR, "exif-watermark-icon.ico")
    if os.path.isfile(icon):
        app.setWindowIcon(QIcon(icon))

    state = AppState()
    i18n.install(app, state.settings.get("lang") or i18n.DEFAULT_LANG)
    win = MainWindow(state)
    win.show()
    single.wire(srv, win)
    _say(f"[启动] 数据目录: {BASE_DIR}")
    _say(f"[启动] 预览目录: {PREVIEW_DIR}")
    _say(f"[启动] 日志:     {os.path.join(LOG_DIR, 'log-' + LOG_TIMESTAMP + '.log')}")
    if not os.path.exists(SETTINGS_FILE):
        _say("[启动] 未找到设置文件, 使用出厂默认参数")
    _log_environment(win)
    return app.exec()


def _log_environment(win):
    # 把本次运行的环境信息一次写进日志, 供排查 DPI / 屏幕 / 窗口尺寸这类环境问题
    import platform

    from PySide6 import __version__ as pyside_version

    logging.info(f"[环境] 版本 v{app_version} | Python {platform.python_version()} | "
                 f"PySide6 {pyside_version} | {platform.system()}-{platform.release()}-{platform.version()}")
    # 实现版本标记行: 用于确认当前运行的是哪一份行为
    logging.info("[环境] 悬停判据=光标位置复核(ParamRow) | 界面图标=SVG")
    scr = win.screen()
    if scr is not None:
        geo, avail = scr.geometry(), scr.availableGeometry()
        sizes = win._split.sizes() if getattr(win, "_split", None) else []
        logging.info(f"[环境] 屏幕 {scr.name()} {int(geo.width() * scr.devicePixelRatio())}x"
                     f"{int(geo.height() * scr.devicePixelRatio())}@{scr.devicePixelRatio():g}x "
                     f"(逻辑 {geo.width()}x{geo.height()}, 可用 {avail.width()}x{avail.height()})")
        logging.info(f"[环境] 窗口 {win.width()}x{win.height()} 分栏 {sizes} "
                     f"预览区 {win._preview.width()}x{win._preview.height()}")
    logging.info(f"[环境] 数据目录 {BASE_DIR} | 预览目录 {PREVIEW_DIR} | 日志 {LOG_FILE}")
    logging.info(f"[环境] 档位 PREVIEW_MAX_SIDE={PREVIEW_MAX_SIDE} "
                 f"层缓存预算={_LAYER_CACHE_BUDGET >> 20}MB 单层上限={_LAYER_CACHE_MAX_PX // 1_000_000}MP "
                 f"慢调用阈值={_SLOW_RENDER_MS}ms")


if __name__ == "__main__":
    sys.exit(main())
