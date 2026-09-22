# -*- coding: utf-8 -*-
"""多语言: 中文(源语言) + 英文。

用的是 Qt 的标准那一套, 不是自造字典:
  - 源码里中文字面量用 `tr()` / `QCoreApplication.translate()` 包起来, **中文就是源语言**;
  - `pyside6-lupdate` 扫出 `i18n/xiangshuiyin_en.ts`(翻译源), 翻好之后 `pyside6-lrelease`
    生成 `.qm`;
  - 运行期 `install()` 按设置里的 `lang` 加载 `.qm`。
仓库里同时放 `.ts`(源)与生成的 `.qm` —— 有了 `.qm`, `python main.py` 直接就能跑, 谁都不必去装
lrelease; 改文案时才需要 `tools/build_i18n.py` 重跑一遍。

**为什么不用自己写个 {中文: 英文} 字典**: 因为 lupdate 会把"**还没翻**的条目"列出来, 这正是
"不能出现半英半中"这条要求的执行手段; 配上 `tools/check_i18n.py` 的静态扫描, 漏译跑一次就现形。

**核心线程的消息怎么办**: 那些中文串(批量完成、预览失败…)是核心用 f-string 拼好的, 没法逐个
`tr()` —— 所以在这里列成**模板**(`_CORE_MSG_TEMPLATES`, 带 `{占位符}`), 在**显示边界**过一次
`tr_msg()`: 先精确匹配, 再按模板匹配取到占位符的值, 用该模板的译文重新格式化。匹配不到就
**原样显示**(降级成中文, 绝不显示乱码)。这样核心一行都不用改, 也就不必改动内核。
`QT_TRANSLATE_NOOP` 是给 lupdate 看的标记(运行期原样返回): 模板是变量, lupdate 扫不到
`translate(ctx, 变量)` 这种写法, 得把字面量摆出来它才认。
"""
import logging
import os
import re

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QTranslator

# 可选语言(菜单里按这个列表生成); 第一项是源语言, 不需要 .qm
# 第三项是**跟在「语言」后面的语言码**(让不懂中文的人也知道那个入口是干嘛的, 见 _sync_lang_menu):
# 刻意**不是** code.upper() —— zh 的大写是 "ZH"(ISO 639 的语言码), 没人认; 大家认的是 CN/EN。
LANGS = (("zh", "中文", "CN"), ("en", "English", "EN"))
DEFAULT_LANG = "zh"

_QM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n")
_CTX = "xiangshuiyin"           # 运行期查表用的 context; **NOOP 那边必须写同样的字面量**
                               # (lupdate 不认变量, 写 _CTX 会把条目归到空 context 去)
_translator = None              # 必须留引用: 局部变量被回收, 翻译就没了

# 核心/弹窗里那些"没法逐个 tr()"的中文串。带 {占位符} 的是模板, 其余是精确匹配。
# 注意: 只列**用户看得到**的; 只进日志的中文不进这里(那些不翻译)。
_CORE_MSG_TEMPLATES = (
    # ---- 核心 RenderWorker 的事件 message ----
    QT_TRANSLATE_NOOP("xiangshuiyin", "处理中..."),
    QT_TRANSLATE_NOOP("xiangshuiyin", "已完成: {name}"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "处理失败"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "处理失败: {err}"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "处理出错: {err}"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "没有可处理的图片"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "用户取消：已完成 {success}/{total}"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "批量处理完成：{success}/{total} -> {out_dir}"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "失败详情："),
    QT_TRANSLATE_NOOP("xiangshuiyin", "... 及其他 {n} 个文件"),
    # ---- 核心 start_generate / make_preview 的错误 ----
    QT_TRANSLATE_NOOP("xiangshuiyin", "已有任务正在处理，请先等待完成或停止"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "请先选择一张输入图片"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "导出目录不可写：{path}（{err}）"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "请先选择要批量处理的图片"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "批量文件格式错误"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "批量文件路径无效（请重新选择或拖入）"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "无法创建导出子目录: {err}"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "预览生成失败"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "预览生成失败: {err}"),
    # ---- 预览线程回传的失败文本 ----
    QT_TRANSLATE_NOOP("xiangshuiyin", "预览失败: {err}"),
    # ---- 弹窗(dialog.py)里那几个按钮/标题 ----
    QT_TRANSLATE_NOOP("xiangshuiyin", "确定"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "是"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "否"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "关闭"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "提示"),
    # ---- 核心渲染分段的阶段名(会出现在"上一次渲染"的 tooltip 里) ----
    QT_TRANSLATE_NOOP("xiangshuiyin", "预处理(解码/转正/增强)"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "EXIF读取"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "字体/几何"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "背景层"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "图片外观(圆角/正圆)"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "图片阴影层"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "图片描边层"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "水印文字绘制"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "低清缩回"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "编码保存"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "预览"),
    QT_TRANSLATE_NOOP("xiangshuiyin", "成品"),
)

_re_cache = None


def _compile_templates():
    """把带占位符的模板编成正则: {name} -> 命名捕获组, 其余转义。懒做一次。"""
    global _re_cache
    if _re_cache is not None:
        return _re_cache
    out = []
    for tpl in _CORE_MSG_TEMPLATES:
        if "{" not in tpl:
            continue
        pattern = "".join(
            f"(?P<{m.group(1)}>.*?)" if (m := re.match(r"\{(\w+)\}", part)) else re.escape(part)
            for part in re.split(r"(\{\w+\})", tpl) if part)
        try:
            out.append((re.compile("^" + pattern + "$"), tpl))
        except re.error as e:                        # 模板写错不该让界面崩
            logging.warning(f"[i18n] 模板编译失败 {tpl!r}: {e}")
    _re_cache = out
    return out


def _tr(text):
    """查翻译(没装翻译器 / 没这条时原样返回中文)。"""
    return QCoreApplication.translate(_CTX, text)


def tr_msg(text):
    """把核心返回的中文消息翻一下(显示边界用)。单行/多行都能过。

    先精确匹配, 再按模板匹配; 都不中就原样返回 —— **降级成中文, 不会显示乱码**, 而且
    tools/check_i18n.py 会把没覆盖的串报出来。
    """
    if not text:
        return text
    lines = str(text).split("\n")
    out = []
    for line in lines:
        got = _tr(line)
        if got != line:
            out.append(got)
            continue
        for rx, tpl in _compile_templates():
            m = rx.match(line)
            if m:
                out.append(_tr(tpl).format(**m.groupdict()))
                break
        else:
            out.append(line)                        # 未覆盖(多半是文件路径/异常原文) -> 原样
    return "\n".join(out)


def install(app, lang):
    """按语言装翻译器。**必须在建主窗口之前调用**(窗口里的字面量在构造时就 tr 了)。"""
    global _translator
    if not lang or lang == DEFAULT_LANG:
        return None
    path = os.path.join(_QM_DIR, f"xiangshuiyin_{lang}")
    if not os.path.isfile(path + ".qm"):
        logging.warning(f"[i18n] 缺少翻译文件 {path}.qm, 本次仍按中文显示")
        return None
    t = QTranslator(app)
    if not t.load(path):
        logging.warning(f"[i18n] 加载 {path}.qm 失败, 本次仍按中文显示")
        return None
    app.installTranslator(t)
    _translator = t
    logging.info(f"[i18n] 界面语言: {lang}")
    return t
