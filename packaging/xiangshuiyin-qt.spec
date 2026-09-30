# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置: 无控制台, XSY_ONEDIR=1 出目录形态"""
import os
import re
import shutil
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # SPECPATH = 本文件所在目录(packaging/), 其父目录即仓库根
# 产物目录用 PyInstaller 注入的 DISTPATH(与 --distpath 一致), 取不到时回退到 <仓库根>/dist
DIST_ROOT = Path(globals().get("DISTPATH") or (ROOT / "dist"))
ONEDIR = os.environ.get("XSY_ONEDIR") == "1"

_APP_VER = re.search(r'^__version__\s*=\s*"([^"]+)"',
                     (ROOT / "src" / "core" / "__init__.py").read_text(encoding="utf-8"),
                     re.M).group(1)
_VERSION_ISS = ROOT / "build" / "version.iss"
_VERSION_ISS.parent.mkdir(parents=True, exist_ok=True)
_VERSION_ISS.write_text(
    "; 本文件由 packaging/xiangshuiyin-qt.spec 在打包时生成, 请勿手工编辑。\n"
    "; 内容取自 src/core/__init__.py 的 __version__（版本号唯一来源）。\n"
    '#define AppVer "%s"\n' % _APP_VER,
    encoding="utf-8")

a = Analysis(
    [str(ROOT / "main.py")],                                     # 入口
    pathex=[str(ROOT / "src")],                                  # 让打包器找到 core / ui 两个包
    binaries=[],
    datas=[
        (str(ROOT / "src" / "assets"), "assets"),                # 资源 -> <_MEIPASS>/assets
        (str(ROOT / "src" / "ui" / "i18n"), "ui/i18n"),
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
        exclude_binaries=True,               # 目录模式: 二进制交给 COLLECT 收集
        name="xiangshuiyin-qt",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
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
        console=False,
        disable_windowed_traceback=False,
        icon=_ICO,
    )
    DIST_DIR = Path(globals().get("DISTPATH") or (ROOT / "dist"))

if not os.environ.get("XSY_SKIP_LICENSE_COPY") == "1":
    dst = DIST_DIR / "licenses"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "THIRD_PARTY_LICENSES.md"):
        shutil.copy2(ROOT / name, DIST_DIR / name)
    for f in sorted((ROOT / "licenses").iterdir()):
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    # 字体许可与来源说明的权威副本在 src/assets/fonts/, 一并放进 licenses/
    for src_name, dst_name in (("OFL.txt", "Sarasa-Gothic-OFL-1.1.txt"),
                               ("NOTICE.txt", "Sarasa-Gothic-NOTICE.txt")):
        shutil.copy2(ROOT / "src" / "assets" / "fonts" / src_name, dst / dst_name)
    print("[licenses] copied to: %s" % dst)
