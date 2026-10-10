# -*- coding: utf-8 -*-
"""CLI 冒烟: 在临时沙箱里跑真实渲染, 校验退出码、产物与参数是否真的生效。

只依赖 Pillow 与内核, 不需要界面框架与显示器。跑法: python scripts/check_cli.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "cli" / "render.py"

fails = []
sandbox = tempfile.mkdtemp(prefix="xsy_cli_check_")


def report(title, ok, note=""):
    print(f"{'PASS' if ok else 'FAIL'} {title}{('  ' + note) if note else ''}")
    if not ok:
        fails.append(title)


def env():
    # 沙箱隔离: 数据目录/预览目录都指到临时目录, 不碰用户数据
    e = dict(os.environ)
    e["XSY_BASE_DIR"] = os.path.join(sandbox, "data")
    e["XSY_PREVIEW_DIR"] = os.path.join(sandbox, "preview")
    e["PYTHONUTF8"] = "1"
    return e


def run(args):
    return subprocess.run([sys.executable, "-B", str(CLI), *args], cwd=str(ROOT), env=env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def make_image(path, w=320, h=240):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (x * 255 // w, y * 255 // h, (x + y) * 255 // (w + h))
    d = ImageDraw.Draw(im)
    d.rectangle([w // 4, h // 4, w * 3 // 4, h * 3 // 4], outline=(255, 255, 255), width=3)
    im.save(path, quality=95)


def size_of(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


src_img = os.path.join(sandbox, "in.jpg")
make_image(src_img)

# 1. 出图: 模糊 + 上下分离 + 1:1, JSON 输出可解析
out1 = os.path.join(sandbox, "o1")
r = run([src_img, "--bg", "blur", "--layout", "split_top_bottom", "--ratio", "1:1",
         "--out", out1, "--json", "--stage"])
try:
    data = json.loads(r.stdout)
except json.JSONDecodeError as e:
    data = None
    report("--json 输出可解析", False, str(e))
if data is not None:
    report("--json 输出可解析", True)
    report("退出码为 0", r.returncode == 0, f"returncode={r.returncode}")
    report("结果标记成功", bool(data.get("ok")), json.dumps(data.get("results", [])[:1], ensure_ascii=False)[:120])
    outs = [x.get("output") for x in data.get("results", []) if x.get("ok")]
    report("产物存在", bool(outs) and all(os.path.isfile(p) for p in outs))
    if outs:
        w, h = size_of(outs[0])
        report("画布为 1:1", w == h and w > 320, f"{w}x{h}")
        report("分段耗时可读", bool(data["results"][0].get("stages")),
               f"{len(data['results'][0].get('stages') or [])} 段")

# 2. 参数真的生效: 3:2 画布必须是横的
out2 = os.path.join(sandbox, "o2")
r = run([src_img, "--bg", "blur", "--ratio", "3:2", "--out", out2, "--json"])
data = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
outs = [x.get("output") for x in data.get("results", []) if x.get("ok")]
if outs:
    w, h = size_of(outs[0])
    report("换比例后画布随之改变", w > h, f"{w}x{h}")
else:
    report("换比例后画布随之改变", False, "未拿到产物")

# 3. 同参数两次出图逐字节一致(纯色背景: 模糊背景带噪声, 不适合字节比对)
out3 = os.path.join(sandbox, "o3a")
out4 = os.path.join(sandbox, "o3b")
run([src_img, "--bg", "color", "--ratio", "1:1", "--out", out3, "--quiet"])
run([src_img, "--bg", "color", "--ratio", "1:1", "--out", out4, "--quiet"])
a = sorted(Path(out3).glob("*.jpg"))
b = sorted(Path(out4).glob("*.jpg"))
same = bool(a) and bool(b) and a[0].read_bytes() == b[0].read_bytes()
report("同参数出图可复现", same)

# 4. 错误路径: 未知参数与不存在的输入都要以退出码 2 收场
r1 = run([src_img, "--set", "no_such_param=1", "--out", os.path.join(sandbox, "e1")])
report("未知参数报参数错误", r1.returncode == 2, f"returncode={r1.returncode}")
r2 = run([os.path.join(sandbox, "不存在.jpg"), "--out", os.path.join(sandbox, "e2")])
report("输入不存在报参数错误", r2.returncode == 2, f"returncode={r2.returncode}")

# 5. --dry-run 不产生任何产物
out5 = os.path.join(sandbox, "o5")
r = run([src_img, "--out", out5, "--dry-run", "--json"])
info = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
report("--dry-run 只列计划", bool(info.get("count")) and not os.path.exists(out5),
       f"count={info.get('count')}")

# 6. 帮助与参数清单可发现
report("--list-params 可发现", "gap_image_bottom" in run(["--list-params"]).stdout)
report("--help 可读", "用法" in run(["--help"]).stdout or "usage" in run(["--help"]).stdout.lower())

print(f"\n沙箱: {sandbox}" if fails else f"\n沙箱已清理: {sandbox}")
if not fails:
    shutil.rmtree(sandbox, ignore_errors=True)
print("结论:", "通过" if not fails else f"未通过({len(fails)} 项)")
sys.exit(0 if not fails else 1)
