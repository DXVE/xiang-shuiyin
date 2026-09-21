# -*- coding: utf-8 -*-
"""镶·水印 · 入口。

用法: .venv\\Scripts\\python.exe main.py

必须在导入核心之前设置两个环境变量: 核心在 import 阶段就会按 BASE_DIR 建日志目录、轮转旧日志
(`_cleanup_old_logs` 只保留最近 3 份) 并清理 `*.tmp*` 残留, 所以这两个目录要先定下来。
这里是**本程序自己的固定位置**(Windows: %LOCALAPPDATA%\\XiangShuiyin-QT6 + 临时预览目录),
与任何其它程序无关; 用 setdefault 是为了让工具脚本能注入自己的临时目录(见 tools/)。
"""
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

from xiangshuiyin import app as core          # noqa: E402 - 必须晚于上面的环境变量
from xsy_qt import theme                      # noqa: E402
from xsy_qt.window import MainWindow          # noqa: E402


def main():
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    state = core.AppState()

    app = QApplication(sys.argv)
    app.setApplicationName("镶·水印 (Qt 版)")
    theme.apply_font(app)
    theme.apply_palette(app)          # 应用主题配色与字体
    icon = os.path.join(core.ASSETS_DIR, "exif-watermark-icon.ico")
    if os.path.isfile(icon):
        app.setWindowIcon(QIcon(icon))

    win = MainWindow(state)
    win.show()
    print(f"[启动] 数据目录: {core.BASE_DIR}")
    print(f"[启动] 预览目录: {core.PREVIEW_DIR}")
    print(f"[启动] 日志:     {os.path.join(core.LOG_DIR, 'log-' + core.LOG_TIMESTAMP + '.log')}")
    if not os.path.exists(core.SETTINGS_FILE):
        print("[启动] 未找到设置文件, 使用出厂默认参数")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
