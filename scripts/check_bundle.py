# -*- coding: utf-8 -*-
"""打包产物自检: 资源、翻译、许可与版本号必须在位且自洽, 任一不成立就非 0 退出。

跑法: python scripts/check_bundle.py [--dist dist/xiangshuiyin-qt] [--expect-version v1.0]
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

fails = []


def report(title, ok, note=""):
    print(f"{'PASS' if ok else 'FAIL'} {title}{('  ' + note) if note else ''}")
    if not ok:
        fails.append(title)


def main():
    ap = argparse.ArgumentParser(description="校验 PyInstaller 产物是否完整")
    ap.add_argument("--dist", default=str(ROOT / "dist" / "xiangshuiyin-qt"), help="产物根目录")
    ap.add_argument("--expect-version", help="期望版本(发布时传 tag, 带不带 v 前缀都行)")
    args = ap.parse_args()

    dist = Path(args.dist)
    if not dist.is_dir():
        print(f"FAIL 产物目录不存在: {dist}")
        return 1
    payload = dist / "_internal" if (dist / "_internal").is_dir() else dist   # onedir 与 onefile 两种形态

    report("主程序在产物里", (dist / "xiangshuiyin-qt.exe").is_file())

    # 资源: 仓库里有几个, 产物里就要有几个(图标与字体随包分发, 界面与水印都靠它们)
    for kind in ("icons", "fonts"):
        want = {p.name for p in (ROOT / "src" / "assets" / kind).iterdir() if p.is_file()}
        got = {p.name for p in (payload / "assets" / kind).iterdir()} if (payload / "assets" / kind).is_dir() else set()
        report(f"assets/{kind} 齐全", want <= got, f"缺 {sorted(want - got)}" if want - got else f"{len(want)} 个")

    qm = sorted((payload / "ui" / "i18n").glob("*.qm")) if (payload / "ui" / "i18n").is_dir() else []
    report("界面翻译(.qm)在产物里", bool(qm), ", ".join(p.name for p in qm))

    lic_dir = dist / "licenses"
    report("许可文本在产物里", (dist / "LICENSE").is_file()
           and (dist / "THIRD_PARTY_LICENSES.md").is_file()
           and lic_dir.is_dir() and any(lic_dir.iterdir()))

    # 版本: 源码是唯一真源, 打包生成的 version.iss 必须与它一致; 发布时还要与 tag 一致
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "src" / "core" / "__init__.py").read_text(encoding="utf-8"))
    ver = m.group(1) if m else None
    report("能从源码读到版本号", bool(ver), ver or "")
    iss = ROOT / "build" / "version.iss"
    iss_ver = None
    if iss.is_file():
        m2 = re.search(r'AppVer\s+"([^"]+)"', iss.read_text(encoding="utf-8"))
        iss_ver = m2.group(1) if m2 else None
    report("安装器版本号与源码一致", bool(ver) and ver == iss_ver, f"source={ver} iss={iss_ver}")
    if args.expect_version:
        tag = args.expect_version.lstrip("vV")
        report("发布 tag 与源码版本一致", ver == tag, f"tag={tag} source={ver}")

    print("\n结论:", "通过" if not fails else f"未通过({len(fails)} 项)")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
