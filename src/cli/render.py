"""命令行出图入口: 供本机脚本或 AI agent 直接调用渲染内核, 不依赖界面框架。

用法: python src/cli/render.py <图片或目录>... [选项]; 也可 cd src 后 python -m cli。
"""
import argparse
import inspect
import json
import logging
import os
import sys
import time

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 本文件位于 src/cli/
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 控制台编码异常时不让中文输出把程序打断
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:                                       # noqa: BLE001
        pass

from core.config import DATA_DIR, DEFAULT_LOGO_DIR, MAX_PRESET_SLOTS   # noqa: E402
from core.params import _format_manual_exif, default_params             # noqa: E402
from core.paths import load_model_map, preset_path, _load_json         # noqa: E402
from core.pipeline import _unique_output_path, process_single          # noqa: E402


# 内核真正接受的形参名: 预设文件里的界面专有键据此过滤(与 ui/specs.py 同一判据, 此处不引 Qt)
_BACKEND_KEYS = set(inspect.signature(process_single).parameters)

# 画布比例: 界面档位字符串 -> 内核数值(0 = 跟随原图); 与界面下拉的档位一致
_RATIOS = {"original": 0, "1:1": 1, "3:2": 3 / 2, "2:3": 2 / 3, "4:3": 4 / 3, "3:4": 3 / 4,
           "16:9": 16 / 9, "9:16": 9 / 16, "21:9": 21 / 9, "9:21": 9 / 21}

_LAYOUTS = ("unified", "split_top_bottom", "split_left_right")
_BG_MODES = ("color", "blur")
_EXIF_KEYS = ("make", "model", "exposure", "fnumber", "iso")

def _image_ext(path):
    # 按文件头魔数判定受支持的图片并返回真实扩展名, 与内核 _sniff_image_type 同一套判据
    # 按文件头返回真实扩展名, 非受支持图片返回 None(不信任用户扩展名)
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if head.startswith(b"BM"):
        return ".bmp"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return ".tif"
    if head.startswith(b"\x00\x00\x01\x00"):
        return ".ico"
    return None


def _coerce(key, raw, cur):
    # 按默认值的类型转换 --set 的字符串; 无法判定类型时报错(避免静默写成字符串)
    if isinstance(cur, bool):
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"{key} 需要布尔值(true/false), 收到 {raw!r}")
    if isinstance(cur, int):
        return int(float(raw))
    if isinstance(cur, float):
        return float(raw)
    if isinstance(cur, str):
        return raw
    raise ValueError(f"{key} 的类型不支持命令行赋值({type(cur).__name__})")


def _apply_overrides(p, pairs):
    # 逐条写入 --set: 键必须是参数表里已知的键, 值按默认值类型转换
    known = sorted(k for k, v in default_params().items() if k in _BACKEND_KEYS)
    for raw in pairs:
        if "=" not in raw:
            raise ValueError(f"--set 需要 KEY=VALUE 形式, 收到 {raw!r}")
        key, val = raw.split("=", 1)
        key = key.strip()
        if key not in known:
            raise ValueError(f"未知参数 {key!r}; 已知参数见 --list-params")
        p[key] = _coerce(key, val, p.get(key))
    return p


def _load_preset(ref):
    # 取一个槽位的预设记录: ref 可以是槽位号(1~10), 也可以是方案名; 取不到返回 None
    rec, slot = None, None
    if str(ref).strip().isdigit():
        slot = int(str(ref).strip())
        if 1 <= slot <= MAX_PRESET_SLOTS:
            rec = _read_slot(slot)
    else:
        want = str(ref).strip().lower()
        for i in range(1, MAX_PRESET_SLOTS + 1):
            cand = _read_slot(i)
            if cand is not None and str(cand.get("_preset_name", "")).strip().lower() == want:
                rec, slot = cand, i
                break
    if not isinstance(rec, dict) or not rec:
        return None, None
    out = dict(rec)
    out.pop("_preset_name", None)
    return out, slot


def _read_slot(slot):
    # 读一个槽位的预设记录: 文件不存在或内容为空返回 None(_load_json 对缺失文件返回空字典)
    rec = _load_json(preset_path(slot), check_file=True)
    return rec if isinstance(rec, dict) and rec else None


def _list_presets():
    rows = []
    for i in range(1, MAX_PRESET_SLOTS + 1):
        rec = _read_slot(i)
        if rec is not None:
            rows.append({"slot": i, "name": rec.get("_preset_name") or f"方案 {i}",
                         "path": preset_path(i)})
    return rows


def _collect_params(args):
    # 参数组装顺序: 内核默认值 -> 预设 -> 命令行覆盖(命令行优先级最高)
    p = {k: v for k, v in default_params().items() if k in _BACKEND_KEYS}
    base = dict(p)
    source = "默认值"
    if args.preset is not None:
        rec, slot = _load_preset(args.preset)
        if rec is None:
            raise ValueError(f"找不到预设 {args.preset!r}; 可用预设见 --list-presets")
        for k, v in rec.items():
            if k in _BACKEND_KEYS:
                p[k] = v
        # 预设文件里同时存了界面键与数值, 数值优先; 只存界面键时按档位换算
        if rec.get("canvas_ratio_mode") is not None and rec.get("canvas_ratio") is None:
            p["canvas_ratio"] = _RATIOS.get(str(rec["canvas_ratio_mode"]), 0)
        source = f"预设 {slot}「{rec.get('_preset_name') or slot}」"

    logo_dir = args.logo_dir or p.get("logo_dir") or DEFAULT_LOGO_DIR
    p["logo_dir"] = logo_dir
    p["model_map"] = load_model_map(logo_dir)

    if args.bg:
        p["bg_mode"] = args.bg
    if args.layout:
        p["layout_mode"] = args.layout
    if args.ratio is not None:
        key = str(args.ratio)
        if key not in _RATIOS:
            raise ValueError(f"比例只支持 {'/'.join(_RATIOS)}; 收到 {key!r}")
        p["canvas_ratio"] = _RATIOS[key]
    for name, key in (("bg_color", "bg_color"), ("text_color", "text_color")):
        val = getattr(args, name)
        if val:
            p[key] = val
    if args.blur_radius is not None:
        p["blur_radius"] = args.blur_radius
    if args.darken is not None:
        p["bg_darken_strength"] = args.darken
    if args.corner_radius is not None:
        p["corner_radius"] = args.corner_radius
    if args.margin is not None:
        p["image_margin"] = args.margin
    if args.line_gap is not None:
        p["line_gap"] = args.line_gap
    if args.gap_inner is not None:                 # 镜像语义: 内侧 = 水印与图片的间距
        p["gap_image_top"] = p["gap_image_bottom"] = args.gap_inner
    if args.gap_outer is not None:                 # 外侧 = 水印与画布边的间距
        p["gap_top"] = p["gap_bottom"] = args.gap_outer
    _apply_overrides(p, args.set)

    manual = _format_manual_exif(dict(kv.split("=", 1) for kv in args.exif)) if args.exif else None
    return p, base, source, manual


def _expand_inputs(paths, recursive):
    # 把入参展开成图片文件清单: 目录按文件名排序展开, 逐个按魔数筛图
    files, skipped = [], []
    for raw in paths:
        if os.path.isdir(raw):
            walker = os.walk(raw) if recursive else [(raw, [], sorted(os.listdir(raw)))]
            for root, _dirs, names in walker:
                for nm in sorted(names):
                    cand = os.path.join(root, nm)
                    if os.path.isfile(cand):
                        (files if _image_ext(cand) else skipped).append(cand)
            continue
        if not os.path.isfile(raw):
            raise FileNotFoundError(f"输入不存在: {raw}")
        (files if _image_ext(raw) else skipped).append(raw)
    return files, skipped


def _run_one(src, out_dir, params, manual_exif, want_stage):
    stem = os.path.splitext(os.path.basename(src))[0]
    out_path = _unique_output_path(out_dir, stem)
    stages = []
    t0 = time.perf_counter()
    try:
        process_single(src, out_path, params["logo_dir"],
                       **{k: v for k, v in params.items() if k != "logo_dir"},
                       manual_exif=manual_exif, stage_out=stages,
                       stage_log=want_stage)
    except Exception as e:                                  # noqa: BLE001
        return {"input": src, "ok": False, "error": f"{type(e).__name__}: {e}",
                "ms": (time.perf_counter() - t0) * 1000, "stages": []}
    row = {"input": src, "output": out_path, "ok": True,
           "ms": (time.perf_counter() - t0) * 1000}
    if want_stage and stages:
        row["stages"] = stages[0]["stages"]
    return row


def _build_parser():
    ap = argparse.ArgumentParser(
        prog="xsy-render",
        description="「镶 · 水印」命令行出图: 默认参数 + 可选预设 + 命令行覆盖, 逐张调用渲染内核",
        epilog="示例: python src/cli/render.py a.jpg b/ --out out --bg blur --layout split_top_bottom "
               "--ratio 1:1 --set corner_radius=120 --json",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", help="输入图片或目录(至少一个; 目录需配合 --recursive 走子目录)")
    ap.add_argument("--out", help="输出目录(默认 <首张图所在目录>/watermarked)")
    ap.add_argument("--preset", help="先套用某预设: 槽位号 1~10 或方案名")
    ap.add_argument("--logo-dir", help="Logo 目录(默认取参数默认值, 型号映射也从这里读)")
    ap.add_argument("--bg", choices=_BG_MODES, help="背景模式: color / blur")
    ap.add_argument("--layout", choices=_LAYOUTS, help="水印布局: unified / split_top_bottom / split_left_right")
    ap.add_argument("--ratio", help="画布比例: " + " / ".join(_RATIOS))
    ap.add_argument("--bg-color", help="纯色背景色, 如 #FFFFFF")
    ap.add_argument("--text-color", help="水印文字色, 如 #383a42")
    ap.add_argument("--blur-radius", type=int, help="模糊半径(1~1000)")
    ap.add_argument("--darken", type=float, help="背景暗化强度(0~1)")
    ap.add_argument("--corner-radius", type=int, help="图片圆角半径(0~1000)")
    ap.add_argument("--margin", type=float, help="图片外边距(占图片高度比例, 如 0.015)")
    ap.add_argument("--line-gap", type=float, help="水印行间距(比例)")
    ap.add_argument("--gap-inner", type=float, help="内侧间距: 同时写 gap_image_top 与 gap_image_bottom")
    ap.add_argument("--gap-outer", type=float, help="外侧间距: 同时写 gap_top 与 gap_bottom")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="覆盖任意内核参数(可重复); 键名与范围见 --list-params")
    ap.add_argument("--exif", action="append", default=[], metavar="KEY=VALUE",
                    help="自定义水印信息(可重复): " + "/".join(_EXIF_KEYS))
    ap.add_argument("--recursive", action="store_true", help="目录入参时含子文件夹")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果(供程序解析)")
    ap.add_argument("--stage", action="store_true", help="输出每张的渲染分段耗时")
    ap.add_argument("--dry-run", action="store_true", help="只列将处理的文件与生效参数, 不渲染")
    ap.add_argument("--list-params", action="store_true", help="列出可用参数名与默认值后退出")
    ap.add_argument("--list-presets", action="store_true", help="列出预设槽位后退出")
    ap.add_argument("--quiet", action="store_true", help="只输出结果, 不打日志")
    return ap


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.quiet:
        logging.disable(logging.WARNING)

    if args.list_params:
        rows = [{"key": k, "default": v, "type": type(v).__name__}
                for k, v in default_params().items() if k in _BACKEND_KEYS]
        print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json
              else "\n".join(f"{r['key']:<22} {r['type']:<6} 默认 {r['default']!r}" for r in rows))
        return 0
    if args.list_presets:
        rows = _list_presets()
        print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json
              else "\n".join(f"槽位 {r['slot']}: {r['name']}" for r in rows) or "(无预设)")
        return 0

    try:
        params, base, source, manual_exif = _collect_params(args)
    except (ValueError, KeyError) as e:
        print(f"参数错误: {e}", file=sys.stderr)
        return 2
    if not args.inputs:
        print("缺少输入图片; 用法见 --help", file=sys.stderr)
        return 2

    try:
        files, skipped = _expand_inputs(args.inputs, args.recursive)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not files:
        print("没有可处理的受支持图片", file=sys.stderr)
        return 2

    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(files[0])), "watermarked")
    changed = {k: v for k, v in params.items() if k not in ("logo_dir", "model_map") and base.get(k) != v}
    if args.dry_run:
        info = {"out_dir": out_dir, "count": len(files), "files": files, "skipped": skipped,
                "param_source": source, "logo_dir": params["logo_dir"], "changed": changed}
        print(json.dumps(info, ensure_ascii=False, indent=2) if args.json
              else f"将处理 {len(files)} 张 -> {out_dir}\n参数来源: {source}\n非默认参数: {changed}")
        return 0

    os.makedirs(out_dir, exist_ok=True)
    results = []
    for src in files:
        row = _run_one(src, out_dir, params, manual_exif, args.stage)
        results.append(row)
        if not args.json:
            if row["ok"]:
                extra = f" [{', '.join(row.get('stages', []))}]" if args.stage else ""
                print(f"OK   {row['ms']:>6.0f}ms  {src} -> {row['output']}{extra}")
            else:
                print(f"FAIL {src}: {row['error']}", file=sys.stderr)

    success = sum(1 for r in results if r["ok"])
    if args.json:
        print(json.dumps({"ok": success == len(results), "total": len(results), "success": success,
                          "out_dir": out_dir, "param_source": source, "changed": changed,
                          "skipped": skipped, "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print(f"完成: {success}/{len(results)} -> {out_dir}")
    return 0 if success == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
