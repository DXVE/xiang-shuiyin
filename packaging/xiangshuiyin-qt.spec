# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（默认 onefile，无控制台）。

`datas` 必须把 `src/assets` 放到包根目录下的 `assets/`：冻结后 `core/app.py` 以
`_MEIPASS/assets` 定位资源。位置错误会表现为字体回退到系统字体、界面图标与关于框 logo 缺失。

许可文本：`licenses/` + `LICENSE` + `THIRD_PARTY_LICENSES.md` 既打进产物内部，也在构建结束后
被复制到 `dist/` 下（LGPLv3 第 4 条 b 项要求分发时随附 GPL 与 LGPL 全文；单文件产物内部
解包目录用户看不到，所以必须在产物**旁边**也放一份）。字体那两份从 `src/assets/fonts/` 复制。

构建形态：
    默认        onefile —— 单个 `dist/xiangshuiyin-qt.exe`。Qt 库被封在 exe 内、运行时解包到临时
                目录，用户无法替换，因此 LGPLv3 第 4 条 d 项走 0 款（源码位置见 licenses/Qt-Notice.txt）。
    XSY_ONEDIR=1  目录模式 —— 产物是 `dist/xiangshuiyin-qt/` 文件夹，Qt 的 DLL 就在文件夹里、
                用户可替换，满足 LGPLv3 第 4 条 d 项 1 款（"可替换该库"）。

用法（在仓库根目录）：
    .venv\\Scripts\\python.exe -m pip install -e ".[build]"
    .venv\\Scripts\\python.exe -m PyInstaller packaging\\xiangshuiyin-qt.spec
    set XSY_ONEDIR=1 && .venv\\Scripts\\python.exe -m PyInstaller packaging\\xiangshuiyin-qt.spec
"""
import os
import shutil
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # SPECPATH = 本文件所在目录(packaging/), 其父目录即仓库根
# 产物目录用 PyInstaller 注入的 DISTPATH（与 --distpath 一致），取不到时回退到 <仓库根>/dist
DIST_ROOT = Path(globals().get("DISTPATH") or (ROOT / "dist"))
ONEDIR = os.environ.get("XSY_ONEDIR") == "1"

a = Analysis(
    [str(ROOT / "main.py")],                                     # 入口
    pathex=[str(ROOT / "src")],                                  # 让打包器找到 core / ui 两个包
    binaries=[],
    datas=[
        (str(ROOT / "src" / "assets"), "assets"),                # 资源 -> <_MEIPASS>/assets
        (str(ROOT / "licenses"), "licenses"),                    # 许可文本 -> <_MEIPASS>/licenses
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD_PARTY_LICENSES.md"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

_ICO = str(ROOT / "src" / "assets" / "icons" / "exif-watermark-icon.ico")

if ONEDIR:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,               # 目录模式：二进制交给 COLLECT 收集
        name="xiangshuiyin-qt",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,                       # 无控制台
        disable_windowed_traceback=False,
        icon=_ICO,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="xiangshuiyin-qt",
    )
    DIST_DIR = Path(globals().get("DISTPATH") or (ROOT / "dist")) / "xiangshuiyin-qt"
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="xiangshuiyin-qt",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,                       # 无控制台
        disable_windowed_traceback=False,
        icon=_ICO,
    )
    DIST_DIR = Path(globals().get("DISTPATH") or (ROOT / "dist"))

# ---- 构建后：把许可文本放到产物旁边（分发单位 = 程序 + LICENSE + THIRD_PARTY_LICENSES.md + licenses/）----
if not os.environ.get("XSY_SKIP_LICENSE_COPY") == "1":
    dst = DIST_DIR / "licenses"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "THIRD_PARTY_LICENSES.md"):
        shutil.copy2(ROOT / name, DIST_DIR / name)
    for f in sorted((ROOT / "licenses").iterdir()):
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    # 字体许可与来源说明的权威副本在 src/assets/fonts/，一并放进 licenses/
    for src_name, dst_name in (("OFL.txt", "Sarasa-Gothic-OFL-1.1.txt"),
                               ("NOTICE.txt", "Sarasa-Gothic-NOTICE.txt")):
        shutil.copy2(ROOT / "src" / "assets" / "fonts" / src_name, dst / dst_name)
    print("[licenses] 已把许可文本复制到: %s" % dst)
