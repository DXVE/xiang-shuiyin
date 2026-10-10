# -*- coding: utf-8 -*-
"""i18n 完整性检查: 三件事, 任一不通过就非 0 退出。

  1. **静态扫描**: 源码里"该翻但没翻"的中文字面量 —— 在 UI 文本槽(QLabel/QPushButton/
     setToolTip/self._status/addAction/… )里的中文, 没被 tr()/translate() 包着的都列出来。
     只扫 UI 槽是为了少误报: 纯日志、注释、核心的消息模板(它们在 i18n.py 里统一登记)不算。
  2. **.ts 的翻译完整度**: 有没有 type="unfinished"(lupdate 认得出、但还没翻的条目)。
  3. **核心消息模板**: i18n._CORE_MSG_TEMPLATES 里每一条都要在 .qm 里查得到译文
     (核心线程产生的中文消息靠它翻, 漏一条就会在界面上留下中文)。

跑法: .venv\\Scripts\\python.exe scripts/check_i18n.py
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

CJK = r"[\u4e00-\u9fff]"
UI_SLOTS = ["QLabel", "QPushButton", "QCheckBox", "setToolTip", "setAccessibleName",
            "setPlaceholderText", "setWindowTitle", "addAction", "addMenu", "addItem",
            "self._status", "self._row", "self._btn", "_card", "group",
            "setText", "setName", "Card"]
SCAN_FILES = ["src/ui/window.py", "src/ui/dialog.py", "src/ui/stats.py",
              "src/ui/render.py", "main.py"]
# 明确不翻的: 只进日志/控制台的诊断串(它们出现在 logging/print 里, 不是界面)
LOG_HINTS = ("logging.", "print(", "[启动]", "[环境]", "[单例]", "[单例]", "[i18n]", "[统计]", "[预设]",
             "[上屏]", "[预览]", "[环境]")

slot_re = re.compile(r'\b(' + "|".join(re.escape(x) for x in UI_SLOTS) + r')\(("|f")([^"\n]*' + CJK
                     + r'[^"\n]*)"')
unwrapped = []
for f in SCAN_FILES:
    p = ROOT / f
    if not p.exists():
        continue
    for i, ln in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
        if any(h in ln for h in LOG_HINTS) or ln.lstrip().startswith("#"):
            continue
        for m in slot_re.finditer(ln):
            rest = ln[m.end():]
            # 后面紧跟 .format( 或 ) 都算"取出来用", 只看有没有 tr(/translate( 包住
            head = ln[max(0, m.start() - 40):m.start()]
            # 认 tr( / translate( / QT_TRANSLATE_NOOP( 三种写法(后者是给 lupdate 看的标记)
            if any(k in head for k in ("tr(", "translate(", "TRANSLATE_NOOP(")):
                continue
            unwrapped.append((f, i, ln.strip()[:110]))

# 第二遍: `_message(` 那种多行调用 —— 标题/正文可能跨好几行、正文还常是 f-string,
# 上面按"调用槽 + 同一行字面量"的规则会漏(关于弹窗就漏过一次)。这里对每个 _message( 往后看 4 行,
# 逐行找中文, 只要那一行没有 tr(/translate(/NOOP( 就报出来。
msg_unwrapped = []
for f in SCAN_FILES:
    p = ROOT / f
    if not p.exists():
        continue
    lines = p.read_text(encoding="utf-8").split(chr(10))
    for i, ln in enumerate(lines):
        if "_message(" not in ln or ln.lstrip().startswith("def ") or any(h in ln for h in LOG_HINTS):
            continue                      # def _message(...) 是定义, 不是调用
        for j in range(i, min(i + 4, len(lines))):
            l = lines[j].split("  #")[0]              # 去掉行尾注释(注释里的中文不翻)
            if not re.search(CJK, l) or l.lstrip().startswith("#"):
                continue
            prev = "".join(lines[max(0, j - 3):j])     # 包裹可能写在前面几行(多行模板)
            if any(k in l + prev for k in ("tr(", "translate(", "TRANSLATE_NOOP(")):
                continue                      # 跨行包裹也算(如 "…。" ))\) 与 self.tr( 分行写)
            msg_unwrapped.append((f, j + 1, l.strip()[:100]))

ts = ROOT / "src/ui/i18n/xiangshuiyin_en.ts"
ts_text = ts.read_text(encoding="utf-8") if ts.exists() else ""
unfinished = ts_text.count('type="unfinished"')

spec = importlib.util.spec_from_file_location("ui_i18n", ROOT / "src/ui/i18n.py")
i18n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i18n)
import xml.etree.ElementTree as ET                          # noqa: E402
imported = {m.findtext("source") for m in ET.parse(ts).getroot().iter("message")} if ts.exists() else set()
missing_tpl = [t for t in i18n._CORE_MSG_TEMPLATES if t not in imported]

print(f"1) 静态扫描: {len(SCAN_FILES)} 个文件, 没被 tr()/translate() 包住的 UI 中文 {len(unwrapped)} 处")
for f, i, ln in unwrapped[:25]:
    print(f"     {f}:{i}  {ln}")
if len(unwrapped) > 25:
    print(f"     ... 还有 {len(unwrapped) - 25} 处")
print(f"1b) _message 多行调用里没被包住的中文: {len(msg_unwrapped)} 处")
for f, i, ln in msg_unwrapped[:10]:
    print(f"     {f}:{i}  {ln}")
print(f"2) .ts 未翻译条目(unfinished): {unfinished}")
print(f"3) 核心消息模板: {len(i18n._CORE_MSG_TEMPLATES)} 条, 没进 .ts 的 {len(missing_tpl)} 条")
for t in missing_tpl[:10]:
    print("     缺:", t[:70])

ok = not unwrapped and not msg_unwrapped and not unfinished and not missing_tpl
print("\n结论:", "通过" if ok else "未通过")
sys.exit(0 if ok else 1)
