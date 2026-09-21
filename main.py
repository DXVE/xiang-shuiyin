# -*- coding: utf-8 -*-
"""镶·水印 · 入口。

用法: .venv\\Scripts\\python.exe main.py

必须在导入核心之前设置两个环境变量: 核心在 import 阶段就会按 BASE_DIR 建日志目录、轮转旧日志
(`_cleanup_old_logs` 只保留最近 3 份) 并清理 `*.tmp*` 残留, 所以这两个目录要先定下来。
这里是**本程序自己的固定位置**(Windows: %LOCALAPPDATA%\\XiangShuiyin-QT6 + 临时预览目录),
与任何其它程序无关; 用 setdefault 是为了让工具脚本能注入自己的临时目录(见 tools/)。
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

from xiangshuiyin import __version__ as app_version   # noqa: E402
from xiangshuiyin import app as core          # noqa: E402 - 必须晚于上面的环境变量
from xsy_qt import theme                      # noqa: E402
from xsy_qt.window import MainWindow, _PREVIEW_SHARPEN, _SLOW_RENDER_MS   # noqa: E402


def main():
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from xsy_qt import single

    app = QApplication(sys.argv)
    # 单实例闸门**必须早于 AppState**: AppState 一构造就读设置、清预览残留, 而"两个进程各写一份
    # settings.json、后写的那份盖掉先写的"正是单实例要防的事(见 xsy_qt/single.py)。
    # 已有实例时: 通知它激活窗口(由本进程去抢前台 —— 它持有前台权限), 然后本进程退出。
    keep, srv = single.claim(single.server_name())
    if not keep:
        print("[启动] 程序已在运行，已激活已有窗口；本次启动退出")
        return 0

    app.setApplicationName("镶·水印 (Qt 版)")
    theme.apply_font(app)
    theme.apply_palette(app)          # 应用主题配色与字体
    icon = os.path.join(core.ASSETS_DIR, "exif-watermark-icon.ico")
    if os.path.isfile(icon):
        app.setWindowIcon(QIcon(icon))

    state = core.AppState()
    win = MainWindow(state)
    win.show()
    single.wire(srv, win)             # 此后再有实例启动, 就把这个窗口提到前台
    print(f"[启动] 数据目录: {core.BASE_DIR}")
    print(f"[启动] 预览目录: {core.PREVIEW_DIR}")
    print(f"[启动] 日志:     {os.path.join(core.LOG_DIR, 'log-' + core.LOG_TIMESTAMP + '.log')}")
    if not os.path.exists(core.SETTINGS_FILE):
        print("[启动] 未找到设置文件, 使用出厂默认参数")
    _log_environment(win)
    return app.exec()


def _log_environment(win):
    """把"这次跑在什么环境"一次写进日志。

    以前这些数字每次排查都要现写探针去量(DPI/可用区/窗口尺寸/档位), 现在启动即可见 ——
    "发虚/顶出屏幕/预览慢"这类问题第一眼就能排除环境因素。
    """
    import platform

    from PySide6 import __version__ as pyside_version

    logging.info(f"[环境] 版本 {app_version} | Python {platform.python_version()} | "
                 f"PySide6 {pyside_version} | {platform.system()}-{platform.release()}-{platform.version()}")
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
    logging.info(f"[环境] 档位 PREVIEW_MAX_SIDE={core.PREVIEW_MAX_SIDE} 上屏锐化={_PREVIEW_SHARPEN} "
                 f"层缓存预算={core._LAYER_CACHE_BUDGET >> 20}MB 单层上限={core._LAYER_CACHE_MAX_PX // 1_000_000}MP "
                 f"慢调用阈值={_SLOW_RENDER_MS}ms")


if __name__ == "__main__":
    sys.exit(main())
