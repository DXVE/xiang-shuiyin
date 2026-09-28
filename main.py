# -*- coding: utf-8 -*-
"""「镶 · 水印」 水印处理工具 入口。

用法: .venv\\Scripts\\python.exe main.py

两个目录环境变量必须在导入核心之前设置: 核心在 import 阶段即按 BASE_DIR 建日志目录、
轮转旧日志(_cleanup_old_logs 只保留最近 3 份)并清理 `*.tmp*` 残留。
回退值固定为本程序自己的位置(Windows: %LOCALAPPDATA%\\XiangShuiyin-QT6 与临时预览目录),
不得指向其它程序的目录; 使用 setdefault 是为了让工具脚本能注入自己的临时目录。
"""
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
from core import app as core          # noqa: E402 - 必须晚于上面的环境变量
from ui import theme                      # noqa: E402
from ui.window import MainWindow, _SLOW_RENDER_MS   # noqa: E402


def _say(text):
    """向控制台输出一行。

    以无控制台方式打包(--windowed)时 `sys.stdout` 为 None, 直接 print 会抛出
    AttributeError 并中断启动, 因此输出失败一律静默。
    """
    try:
        print(text)
    except Exception:                     # noqa: BLE001 - 没有控制台就静默
        pass


def main():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from ui import i18n, single

    app = QApplication(sys.argv)
    # 单实例闸门必须先于 AppState: AppState 构造时会读写设置并清理预览残留,
    # 两个进程并发运行会各写一份 settings.json 而互相覆盖(见 ui/single.py)。
    # 已有实例时通知其激活窗口, 本进程退出。
    keep, srv = single.claim(single.server_name())
    if not keep:
        _say("[启动] 程序已在运行，已激活已有窗口；本次启动退出")
        return 0

    app.setApplicationName(QCoreApplication.translate("main", "「镶 · 水印」 水印处理工具"))
    theme.apply_font(app)
    theme.apply_palette(app)          # 主题与调色板定义见 ui/theme.py
    icon = os.path.join(core.ICONS_DIR, "exif-watermark-icon.ico")
    if os.path.isfile(icon):
        app.setWindowIcon(QIcon(icon))

    state = core.AppState()
    # 翻译器必须在建窗口之前安装: 窗口文案在构造时即通过 tr() 取译文
    i18n.install(app, state.settings.get("lang") or i18n.DEFAULT_LANG)
    win = MainWindow(state)
    win.show()
    single.wire(srv, win)             # 此后新实例启动时由此把窗口提到前台
    _say(f"[启动] 数据目录: {core.BASE_DIR}")
    _say(f"[启动] 预览目录: {core.PREVIEW_DIR}")
    _say(f"[启动] 日志:     {os.path.join(core.LOG_DIR, 'log-' + core.LOG_TIMESTAMP + '.log')}")
    if not os.path.exists(core.SETTINGS_FILE):
        _say("[启动] 未找到设置文件, 使用出厂默认参数")
    _log_environment(win)
    return app.exec()


def _log_environment(win):
    """把本次运行的环境信息一次写入日志。

    含版本、屏幕与 devicePixelRatio、可用区、窗口与分栏尺寸、数据目录、预览档位与缓存预算,
    便于在日志中直接排除 DPI/屏幕/窗口尺寸这类环境因素。
    """
    import platform

    from PySide6 import __version__ as pyside_version

    logging.info(f"[环境] 版本 v{app_version} | Python {platform.python_version()} | "
                 f"PySide6 {pyside_version} | {platform.system()}-{platform.release()}-{platform.version()}")
    # 实现版本标记行: 用于确认当前运行的是哪一份行为(见 README「已知事项」)
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
    logging.info(f"[环境] 数据目录 {core.BASE_DIR} | 预览目录 {core.PREVIEW_DIR} | 日志 {core.LOG_FILE}")
    logging.info(f"[环境] 档位 PREVIEW_MAX_SIDE={core.PREVIEW_MAX_SIDE} "
                 f"层缓存预算={core._LAYER_CACHE_BUDGET >> 20}MB 单层上限={core._LAYER_CACHE_MAX_PX // 1_000_000}MP "
                 f"慢调用阈值={_SLOW_RENDER_MS}ms")


if __name__ == "__main__":
    sys.exit(main())
