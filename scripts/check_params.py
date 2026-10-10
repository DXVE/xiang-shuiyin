# -*- coding: utf-8 -*-
"""参数契约审计: 参数表、内核签名与默认值三者必须自洽, 任一不成立就非 0 退出。

跑法: .venv\\Scripts\\python.exe scripts/check_params.py
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from core.params import default_params          # noqa: E402
from core.pipeline import process_single        # noqa: E402
from ui.specs import PARAM_SPECS, backend_params   # noqa: E402
from ui.winconst import FONT_STYLE_GROUPS       # noqa: E402

# 不进内核的界面专有键
UI_ONLY = {"canvas_ratio_mode"}
# 镜像行: 本身不存值, 写的是两个底层参数
MIRROR_ONLY = {"gap_inner", "gap_outer"}
# 由 backend_params() 从界面键派生出来的内核参数
DERIVED = {"canvas_ratio"}
# 由调用方(界面/脚本)直接传给内核的参数
CALLER = {"input_path", "output_path", "logo_dir", "model_map", "manual_exif"}
# 内核内部参数, 界面不提供控件
INTERNAL = {"low_res_enhance", "geom_out", "image_out", "stage_out", "stage_log", "cancel_check"}
# 由「水印开关」卡的网格控件生成, 不在参数表里
GRID = {k for _name, keys in FONT_STYLE_GROUPS for k in keys}
# 特殊卡的头行(不是参数行)
HEAD_ROWS = {"水印开关", "描边与投影"}

KINDS = {"choice", "pct", "int", "bool", "color", "path"}
fails = []


def fail(title, detail):
    fails.append(title)
    print(f"FAIL {title}")
    for d in detail[:12]:
        print(f"      {d}")
    if len(detail) > 12:
        print(f"      ... 还有 {len(detail) - 12} 条")


def ok(title, note=""):
    print(f"PASS {title}{('  ' + note) if note else ''}")


kernel = list(inspect.signature(process_single).parameters)
defaults = default_params()
rows = [s for s in PARAM_SPECS]
keys = [s.get("key") for s in rows if s.get("key")]
table = set(keys)

# 1. 行结构: 每行有 card; 无 key 的行只能是特殊卡的头行
bad = [f"{s.get('card')}" for s in rows if not s.get("card")]
fail("每行都有 card", bad) if bad else ok("每行都有 card")
headless = [str(s.get("card")) for s in rows if not s.get("key")]
bad = [c for c in headless if c not in HEAD_ROWS]
if bad:
    fail("无 key 的行只出现在特殊卡", bad)
else:
    ok("无 key 的行只出现在特殊卡", f"{len(headless)} 行")
dupes = sorted({k for k in keys if keys.count(k) > 1})
fail("表里 key 不重复", dupes) if dupes else ok("表里 key 不重复", f"{len(table)} 个")

# 2. kind 与 args 的形状
bad_kind = [f"{s.get('key') or s.get('card')}: {s.get('kind')}" for s in rows if s.get("key") and s.get("kind") not in KINDS]
fail("kind 取值合法", bad_kind) if bad_kind else ok("kind 取值合法")
bad_args = []
for s in rows:
    if not s.get("key"):
        continue
    k, kind, args = s["key"], s.get("kind"), s.get("args")
    if kind == "choice":
        if not isinstance(args, (list, tuple)) or not args or any(
                not isinstance(a, (list, tuple)) or len(a) != 2 for a in args):
            bad_args.append(f"{k}: choice 需要 [(值, 标签), …]")
    elif kind in ("pct", "int"):
        if not isinstance(args, (list, tuple)) or len(args) != 2 or args[0] > args[1]:
            bad_args.append(f"{k}: {kind} 需要 (下限, 上限)")
fail("args 与 kind 匹配", bad_args) if bad_args else ok("args 与 kind 匹配")

# 3. 表里的键分成三类, 且互不重叠
misc = [k for k in table if k not in kernel and k not in UI_ONLY and k not in MIRROR_ONLY]
fail("表里的键都是内核参数或已登记的界面键", misc) if misc else ok("表里的键都是内核参数或已登记的界面键")
overlap = sorted(k for k in table if k in (UI_ONLY | MIRROR_ONLY) and k in kernel)
fail("界面专有键与内核键不重名", overlap) if overlap else ok("界面专有键与内核键不重名")

# 4. 内核每个形参都有归属: 表项 / 网格控件 / 镜像代写 / 派生 / 调用方 / 内部
mirror_targets = {t for s in rows if s.get("mirror") for t in s["mirror"]}
covered = table | GRID | mirror_targets | DERIVED | CALLER | INTERNAL
missing = [k for k in kernel if k not in covered]
fail("内核形参都有归属(表项/网格/镜像/派生/调用方/内部)", missing) if missing else \
    ok("内核形参都有归属", f"{len(kernel)} 个")

# 5. 界面能改的参数都要有默认值可选(镜像代写键同样要)
need_default = (table - MIRROR_ONLY) | mirror_targets
no_default = sorted(k for k in need_default if k in kernel and k not in defaults)
fail("界面可改参数在内核默认表里都有值", no_default) if no_default else ok("界面可改参数都有默认值")

# 6. 镜像行: 代写键在核心里; 代写键若另有自己的行, 该行必须只在非镜像布局下出现
bad_mirror = []
for s in rows:
    for t in s.get("mirror") or ():
        if t not in kernel:
            bad_mirror.append(f"{s.get('key')} → {t} 不是内核参数")
        row = next((r for r in rows if r.get("key") == t), None)
        if row is not None and "split_top_bottom" not in (row.get("only_if_not") or (None, ()))[1]:
            bad_mirror.append(f"{t} 有独立行却在上下分离时未隐藏(应加 only_if_not layout_mode)")
fail("镜像行的代写键合法且入口不冲突", bad_mirror) if bad_mirror else ok("镜像行的代写键合法且入口不冲突")

# 7. only_if / only_if_not 引用的键必须存在
bad_ref = []
for s in rows:
    for field, _vals in (("only_if", None), ("only_if_not", None)):
        ref = s.get(field)
        if ref and ref[0] not in table and ref[0] not in kernel:
            bad_ref.append(f"{s.get('key')}: {field} 指向不存在的键 {ref[0]}")
fail("显隐条件指向的键存在", bad_ref) if bad_ref else ok("显隐条件指向的键存在")

# 8. 默认值类型与 kind 相容(避免控件按错的类型读写)
bad_type = []
for s in rows:
    k, kind = s.get("key"), s.get("kind")
    if not k or k not in defaults or k in MIRROR_ONLY:
        continue
    v = defaults[k]
    fine = ((kind == "bool" and isinstance(v, bool))
            or (kind == "int" and isinstance(v, int) and not isinstance(v, bool))
            or (kind == "pct" and isinstance(v, (int, float)) and not isinstance(v, bool))
            or (kind in ("color", "path", "choice") and isinstance(v, str)))
    if not fine:
        bad_type.append(f"{k}: kind={kind} 但默认值是 {type(v).__name__}")
fail("默认值类型与控件类型相容", bad_type) if bad_type else ok("默认值类型与控件类型相容")

# 9. backend_params() 的输出必须干净: 只有内核形参, 不含界面专有键
probe = dict(defaults)
probe.update({k: probe.get(k) for k in UI_ONLY})
probe["canvas_ratio_mode"] = "1:1"
out = backend_params(probe)
dirty = sorted(k for k in out if k not in kernel)
fail("backend_params 只产出内核形参", dirty) if dirty else ok("backend_params 只产出内核形参", f"{len(out)} 个")

# 10. 必填(无默认值)的内核参数必须有人提供
required = [p.name for p in inspect.signature(process_single).parameters.values()
            if p.default is inspect.Parameter.empty]
bad_req = [k for k in required if k not in (table | GRID | MIRROR_ONLY | mirror_targets | DERIVED | CALLER)]
fail("必填内核参数有人提供", bad_req) if bad_req else ok("必填内核参数有人提供", f"{len(required)} 个")

print("\n结论:", "通过" if not fails else f"未通过({len(fails)} 项)")
sys.exit(0 if not fails else 1)
