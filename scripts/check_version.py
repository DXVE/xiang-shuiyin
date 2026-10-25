"""版本号核对: 源码 __version__ 是唯一真源, 发布 tag 必须与它一致。

用法:
    python scripts/check_version.py              # 只校验源码版本号格式
    python scripts/check_version.py --tag v1.0   # 另校验 tag 与源码版本一致

发布流程里放在打包之前: tag 与源码不一致时直接失败, 不进入编译。
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "core" / "__init__.py"
VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
TAG_RE = re.compile(r"v\d+(?:\.\d+)*")
VER_RE = re.compile(r"\d+(?:\.\d+)*")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=None, help="发布 tag（如 v1.0）")
    args = ap.parse_args()

    text = VERSION_FILE.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        print(f"FAIL 读不到版本号 src/core/__init__.py 的 __version__")
        print("结论: 未通过")
        return 1
    version = m.group(1)
    if not VER_RE.fullmatch(version):
        print(f"FAIL 源码版本号格式 {version!r}（应为 1.0 / 1.2.3 这类数字串）")
        print("结论: 未通过")
        return 1
    print(f"PASS 源码版本号 {version}")

    if args.tag is not None:
        tag = args.tag.strip()
        if not TAG_RE.fullmatch(tag):
            print(f"FAIL 发布 tag 格式 {tag!r}（应为 v1.0 / v1.2.3）")
            print("结论: 未通过")
            return 1
        if tag[1:] != version:
            print(f"FAIL 发布 tag 与源码版本不一致: tag={tag} 源码=__version__ {version}")
            print(f"     先改 src/core/__init__.py 的 __version__ 为 {tag[1:]!r} 再打 tag")
            print("结论: 未通过")
            return 1
        print(f"PASS 发布 tag 与源码版本一致 {tag}")

    print("结论: 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
