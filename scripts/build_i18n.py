# -*- coding: utf-8 -*-
"""重建翻译: lupdate 扫源码 -> .ts，lrelease 出 .qm。

扫描面是 `src/ui/*.py` + `src/ui/dialogs/*.py` + `main.py` 两级目录(逐个列出而非递归,
免得把 `__pycache__` 里的 .pyc 也算进来)。**弹窗是独立包, 漏掉它整批弹窗文案会被当成作废条目删掉。**

**默认是"演练"(dry-run)**: 在临时目录生成一份新的 .ts 并报告差异, **不碰仓库里的 .ts/.qm**
—— 这个脚本会改写仓库文件, 误跑一次就是一大片 diff。看过报告没问题再加 `--write` 才真写:

    .venv\\Scripts\\python.exe scripts/build_i18n.py            :: 演练(不改任何文件)
    .venv\\Scripts\\python.exe scripts/build_i18n.py --write    :: 真写: 归一化 .ts + 重出 .qm

**改过界面文案之后要跑一次**(改了中文原文、加了新控件、或动了 PARAM_SPECS 表), 否则
.ts/.qm 里还是旧文案, 新文案在英文界面下会退回中文(有 check_i18n.py 兜着, 不会静默漏)。

翻译怎么加: 直接编辑 `src/ui/i18n/xiangshuiyin_en.ts`(或者用 Qt Linguist 打开它,
venv 里就有 `pyside6-linguist.exe`), 把 `<translation>` 里的英文写上、去掉 `type="unfinished"`,
再跑一次本脚本出 .qm。本脚本**不会**覆盖已有的译文(lupdate 默认保留), 只会补新条目。

**lupdate 之后要过三道归一化**, 否则每跑一次就是一大片没法审的 diff:

1. `_normalize` —— XML 排版。lupdate 用它自己那套写法(双引号属性 + `<!DOCTYPE TS>`), 而仓库里
   那份当初是用 ElementTree 写出来的(单引号声明、无 DOCTYPE)。不归一化时只删一条文案也会让
   .ts 整个文件 diff 一遍(实测 1789 行)。
2. `_relativize_locations` —— location 路径。lupdate 按调用时的绝对路径写, 不归一化会把本机
   盘符路径写进仓库, 换台机器再跑同一份源码也会整文件 diff。
3. `_clear_stale_unfinished` —— 未完成标记。lupdate 见到 context 变化(方法搬去别的类)会给
   已有译文的条目重新打上 `type="unfinished"`, 留着会被 check_i18n.py 当成没翻。
"""
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv" / "Scripts"
TS = ROOT / "src" / "ui" / "i18n" / "xiangshuiyin_en.ts"
QM = TS.with_suffix(".qm")
# 逐个列出两级目录, 不用 rglob: 递归会把 __pycache__ 里的 .pyc 也算进扫描面
SOURCES = [str(p) for p in sorted((ROOT / "src" / "ui").glob("*.py"))] \
    + [str(p) for p in sorted((ROOT / "src" / "ui" / "dialogs").glob("*.py"))] \
    + [str(ROOT / "main.py")]


def run(*args):
    exe = VENV / args[0] if (VENV / args[0]).exists() else args[0]
    print("$", " ".join(str(a) for a in args))
    return subprocess.run([str(exe), *args[1:]], cwd=str(ROOT), check=True).returncode


def _normalize(path):
    """把 .ts 重新序列化成仓库里那份的排版(见模块 docstring)。译文原样保留。"""
    tree = ET.parse(path)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _relativize_locations(path):
    """把 <location filename> 一律改成相对 .ts 所在目录: lupdate 按调用时的绝对路径去写,
    不归一化会把本机盘符路径写进仓库, 且换台机器再跑同一份源码也会整文件 diff。"""
    tree = ET.parse(path)
    base = path.parent
    for loc in tree.getroot().iter("location"):
        raw = (loc.get("filename") or "").replace("\\", "/")
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = ROOT / raw
        try:
            rel = p.resolve().relative_to(base.resolve())
        except ValueError:
            continue
        loc.set("filename", rel.as_posix())
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _clear_stale_unfinished(path):
    """译文已写好的条目去掉 type="unfinished": lupdate 见到 context 变化(如方法搬去别的类)
    会给已有译文的条目重新打上这个标记, 留着会让 check_i18n.py 把已翻的当没翻。"""
    tree = ET.parse(path)
    for tr in tree.getroot().iter("translation"):
        if tr.get("type") == "unfinished" and (tr.text or "").strip():
            del tr.attrib["type"]
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _messages(path):
    """{context | source: 翻译状态} —— 只比"有哪些条目", 不比 location 行号(那会天天变)。"""
    out = {}
    for ctx in ET.parse(path).getroot().iter("context"):
        name = (ctx.findtext("name") or "").strip()
        for msg in ctx.iter("message"):
            src = msg.findtext("source") or ""
            tr = msg.find("translation")
            state = "unfinished" if (tr is not None and tr.get("type") == "unfinished") else "ok"
            out[f"{name} | {src}"] = state
    return out


def main(argv):
    write = "--write" in argv
    if write:
        target = TS
    else:                                   # 演练: 拿仓库那份当底稿, 生成到临时目录
        target = Path(tempfile.mkdtemp(prefix="xsy_i18n_")) / TS.name
        target.write_text(TS.read_text(encoding="utf-8"), encoding="utf-8")

    run("pyside6-lupdate.exe", "-no-obsolete", *SOURCES, "-ts", str(target))
    _normalize(target)
    _relativize_locations(target)
    _clear_stale_unfinished(target)

    if write:
        run("pyside6-lrelease.exe", str(target), "-qm", str(QM))
        print(f"\n完成: {TS.name} -> {QM.name}")
        print("提示: 跑 scripts/check_i18n.py 看还有没有没翻的条目 / 没被 tr() 包住的文案")
        return 0

    old, new = _messages(TS), _messages(target)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    unfinished = sorted(k for k, v in new.items() if v == "unfinished")
    print("\n[演练] 未写入仓库(.ts/.qm 一个字也没改)。要写回请加 --write。")
    print(f"  条目: 现有 {len(old)} -> 演练 {len(new)}   新增 {len(added)}   删除 {len(removed)}")
    print(f"  未翻译(unfinished): {len(unfinished)} 条")
    for label, keys in (("新增", added), ("删除", removed), ("未翻", unfinished)):
        for k in keys[:10]:
            print(f"    [{label}] {k}")
        if len(keys) > 10:
            print(f"    [{label}] ... 另有 {len(keys) - 10} 条")
    if not (added or removed or unfinished):
        print("  与仓库里那份完全一致 —— 不需要 --write。")
    print(f"  (演练文件留在 {target}, 可自行对比)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
