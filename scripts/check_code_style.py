# -*- coding: utf-8 -*-
"""代码检查: 本机路径、凭据、会过期的定位信息与注释体例, 覆盖 src/ 与入口脚本。

硬项不过即以非 0 退出; 注释体例类只报告(现存数量较大, 先可见再逐步收敛)。
跑法: python scripts/check_code_style.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = sorted(list((ROOT / "src").rglob("*.py")) + [ROOT / "main.py"])

# 硬项: 写死本机信息的写法(读环境变量、平台常量不算)
HARD = (
    ("本机用户目录写死", re.compile(r"[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9_.]+|/(Users|home)/[A-Za-z0-9_.]+")),
    ("凭据/密钥字面量", re.compile(r"\b(sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,})"
                                   r"|password\s*=\s*[\"'][^\"']+[\"']")),
    ("会过期的定位信息", re.compile(r"\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b|\b[0-9a-f]{7,40}\b")),
)
# 报告项: 注释体例(中文正文用半角逗号与括号、句末不加句号), 只统计不拦截
WARN = (
    ("注释句末句号", re.compile(r"#.*。\s*$")),
    ("注释全角逗号", re.compile(r"#.*，")),
    ("注释全角括号", re.compile(r"#.*[（|）]")),
)

fails = []


def scan(path):
    lines = path.read_text(encoding="utf-8").split("\n")
    rel = path.relative_to(ROOT).as_posix()
    hits = {name: [] for name, _ in HARD}
    warns = {name: [] for name, _ in WARN}
    for i, ln in enumerate(lines, 1):
        for name, pat in HARD:
            if pat.search(ln):
                hits[name].append(f"{rel}:{i}: {ln.strip()[:90]}")
        if ln.lstrip().startswith("#"):
            for name, pat in WARN:
                if pat.search(ln):
                    warns[name].append(f"{rel}:{i}")
    return hits, warns


def check_syntax(path):
    # 单文件语法: 搬家漏括号一类错误在这里就能挡住, 不必等到启动
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        return None
    except SyntaxError as e:
        return f"{path.relative_to(ROOT).as_posix()}:{e.lineno}: {e.msg}"


syntax = [m for m in (check_syntax(p) for p in TARGETS) if m]
if syntax:
    fails.append("语法错误")
    print("FAIL 语法错误")
    for m in syntax[:10]:
        print(f"      {m}")
else:
    print(f"PASS 语法错误  0 个文件有问题(共 {len(TARGETS)} 个)")

total_hard = {name: [] for name, _ in HARD}
total_warn = {name: [] for name, _ in WARN}
for p in TARGETS:
    hits, warns = scan(p)
    for name in hits:
        total_hard[name] += hits[name]
    for name in warns:
        total_warn[name] += warns[name]

for name, _pat in HARD:
    hits = total_hard[name]
    if hits:
        fails.append(name)
    print(f"{'FAIL' if hits else 'PASS'} {name}  命中={len(hits)}")
    for h in hits[:5]:
        print(f"      {h}")

print("\n以下只报告(注释体例, 不计入失败):")
for name, _pat in WARN:
    hits = total_warn[name]
    print(f"  {name}  命中={len(hits)}" + (f"  例: {hits[0]}" if hits else ""))

print("\n结论:", "通过" if not fails else f"未通过({len(fails)} 项)")
sys.exit(0 if not fails else 1)
