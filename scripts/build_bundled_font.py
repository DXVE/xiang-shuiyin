# -*- coding: utf-8 -*-
"""从 Sarasa Gothic 的单面 TTF 生成程序内置字体(只做子集, **不改名**)。

**这个脚本入库**(与其它发布门禁同放 scripts/), 它承担内置字体的重建; README 里记了等价的
命令行步骤, 换 Sarasa 版本时照着重跑即可。产物提交进仓库(`src/assets/fonts/`)。

依赖(开发期, 不进运行时): `fonttools`, 用 `uv run --with fonttools python ...` 临时安装

**默认是"演练"(dry-run)**: 产物写进临时目录并自检, **不碰仓库里的字体**(字体不入 git, 覆盖了
没有回退); 看过报告没问题再加 `--write` 才真写进 `src/assets/fonts/`:

    uv run --with fonttools python scripts/build_bundled_font.py <Sarasa-TTF 解包目录>            :: 演练
    uv run --with fonttools python scripts/build_bundled_font.py <Sarasa-TTF 解包目录> --write    :: 真写

为什么要子集化: 单个 SC 面原文件 22.8~23.7 MB, 四档 93 MB —— 对"随程序分发"太重。
裁到「简体(GB2312 一二级 6763) + 繁体常用(Big5 一级 5401) + 拉丁/希腊/西里尔 + 常用符号 +
假名 + 全角」后单面约 5.1 MB、**四档约 20.6 MB**。**代价**: 繁体次常用、谚文(韩文)与扩展区汉字
会变成方框(例如 龘) —— 要补的话把对应码位加回 keep_chars(数字见 README)。

**名称、唯一标识与版权行一律原样保留**(用户: "不要改动字体的名称，用原名称"):
OFL 第 1 条要求保留版权声明与许可; 第 3 条只禁止**修改版使用保留名**, 而这份 OFL 声明的保留名
是 Adobe 的 'Source'(来自 Source Han Sans), 不含 "Sarasa Gothic" —— 所以沿用原名是允许的。
出处与改动(子集范围)写进 `assets/fonts/NOTICE.txt`。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.environ.get("FONTTOOLS_PATH", ""))          # 允许把 fontTools 装在临时目录
from fontTools import subset                                      # noqa: E402
from fontTools.ttLib import TTFont                                # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "src" / "assets" / "fonts"

# **产出文件名与字体内部名称都保持上游原名**(用户: "不要改动字体的名称，用原名称") ——
# 表里左右两列现在同名, 之所以还留着这张表, 是为了把"要哪四个面"写清楚。
WEIGHTS = [
    ("SarasaGothicSC-Regular.ttf",    "SarasaGothicSC-Regular.ttf"),
    ("SarasaGothicSC-Bold.ttf",       "SarasaGothicSC-Bold.ttf"),
    ("SarasaGothicSC-Italic.ttf",     "SarasaGothicSC-Italic.ttf"),
    ("SarasaGothicSC-BoldItalic.ttf", "SarasaGothicSC-BoldItalic.ttf"),
]
UPSTREAM = "Sarasa Gothic SC 1.0.41"


def gb2312_hanzi(level):
    """GB2312 一级(3755 常用)/二级(3008 次常用)汉字 —— 按区码算, 不需要外部字表。"""
    rows = range(0xB0, 0xD8) if level == 1 else range(0xD8, 0xF8)
    out = []
    for hi in rows:
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except Exception:
                continue
            if "\u4e00" <= ch <= "\u9fff":
                out.append(ch)
    return "".join(out)


def big5_hanzi(level):
    """Big5 一级(常用 5401)/二级(次常用 7652)汉字 —— 同样按区码算(注意第二字节有两段)。
    一级用来补**繁体常用**: 只按 GB2312(简体) 裁的话, 「問」这种繁体字形也会被裁掉
    (实测过), 繁体文案就成方框了。加一级只多 0.55 MB/面(四档 +2.2 MB), 值得。"""
    lo_h, hi_h = (0xA4, 0xC6) if level == 1 else (0xC9, 0xF9)
    # **Big5 的第二个字节有两段**: 0x40-0x7E 与 0xA1-0xFE —— 只写第一段会漏掉一半汉字
    # (实测: 「問」「華」「為」这些常用的都落在 0xA1-0xFE 那一段里, 漏了就等于繁体全没进来)
    out = []
    for hi in range(lo_h, hi_h + 1):
        for l in list(range(0x40, 0x7F)) + list(range(0xA1, 0xFF)):
            try:
                ch = bytes([hi, l]).decode("big5")
            except Exception:
                continue
            if "一" <= ch <= "鿿":
                out.append(ch)
    return "".join(out)


def keep_chars():
    """保留的码位: 拉丁/希腊/西里尔/常用符号/假名/全角 + **简体(GB2312 一二级)** + **繁体常用(Big5 一级)**。
    **不含**: 繁体次常用(Big5 二级, 四档 +12 MB)、谚文(11172 个音节, 四档 +4 MB)、扩展区汉字。
    需要哪一样就把对应的一段加回来(数字见 README)。"""
    ranges = [(0x20, 0x7F), (0xA0, 0x250), (0x370, 0x400), (0x400, 0x460),
              (0x2000, 0x2070), (0x2100, 0x2150), (0x3000, 0x3100), (0xFF00, 0xFFF0)]
    chars = "".join(chr(c) for lo, hi in ranges for c in range(lo, hi))
    return chars + gb2312_hanzi(1) + gb2312_hanzi(2) + big5_hanzi(1)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    write = "--write" in argv
    if not args:
        print(__doc__)
        return 2
    src_dir = Path(args[0])
    # 演练默认写临时目录(见模块 docstring): 仓库里那份字体是最终产物, 覆盖了没有回退
    out_dir = FONT_DIR if write else Path(tempfile.gettempdir()) / "xsy_font_build"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("[%s] 产物目录: %s" % ("写入仓库" if write else "演练, 不改仓库", out_dir))
    keep = keep_chars()
    print("保留码位 %d 个(其中汉字 %d)" % (len(set(keep)), len(gb2312_hanzi(1)) + len(gb2312_hanzi(2))))
    total = 0
    for out_name, src_name in WEIGHTS:
        src = src_dir / src_name
        if not src.is_file():
            print("!! 找不到源文件:", src)
            return 1
        dst = out_dir / out_name
        subset.main([str(src), "--text=%s" % keep, "--output-file=%s" % dst,
                     "--name-IDs=*",              # 全部 name 记录: 版权/名称/标识**原样保留**(OFL 第 1 条)
                     "--layout-features=*",       # kern/locl 等, 排版质量
                     "--glyph-names", "--drop-tables+=DSIG"])
        mb = dst.stat().st_size / 1048576
        total += mb
        print("  %-30s %6.2f MB  (源 %.1f MB)" % (out_name, mb, src.stat().st_size / 1048576))
    print("四档合计 %.1f MB" % total)

    # 自检: 能打开 + 四档是真面 + 常用字在 / 被裁的字确实没了
    from PIL import ImageFont
    S = 40
    fonts = {Path(out).stem.split("-")[-1]: ImageFont.truetype(str(out_dir / out), S)
             for out, _s in WEIGHTS}
    ref = fonts["Regular"].getmask("\U00020000")          # 扩展B, 已裁 -> 缺字基准
    miss = [ch for ch in "徕卡问中文测试，。·" if fonts["Regular"].getmask(ch) == ref]
    print("自检: 常用字缺失 =", miss or "无")
    print("自检: 粗体是真面 =", fonts["Regular"].getmask("M") != fonts["Bold"].getmask("M"),
          " 斜体是真面 =", fonts["Regular"].getmask("M") != fonts["Italic"].getmask("M"))
    if write:
        print("已写入仓库:", FONT_DIR)
    else:
        print("演练结束: **仓库里的字体一个字也没改**。确认无误后加 --write 写入", FONT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
