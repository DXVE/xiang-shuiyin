# -*- coding: utf-8 -*-
"""多语言: 中文(源语言) + 英文。

采用 Qt 标准机制, 不使用自定义字典:
  - 源码中的中文字面量用 `tr()` / `QCoreApplication.translate()` 包裹, 中文即源语言;
  - `pyside6-lupdate` 生成 `i18n/xiangshuiyin_en.ts`(翻译源), 翻译后用 `pyside6-lrelease`
    生成 `.qm`;
  - 运行期由 `install()` 按设置中的 `lang` 加载 `.qm`。

`.ts`(源)与生成的 `.qm` 都随仓库提交, 因此 `python main.py` 可直接运行, 无需安装 lrelease;
改动文案时才需要 `tools/build_i18n.py` 重新生成。

核心线程的消息是 f-string 拼好的中文串, 无法逐个 `tr()`, 因此在
`_CORE_MSG_TEMPLATES` 中登记为模板(带 `{占位符}`), 在**显示边界**经 `tr_msg()` 处理:
先精确匹配, 再按模板匹配取占位符的值并用该模板的译文重新格式化, 匹配不到则原样显示
(降级为中文, 不产生乱码)。核心无需修改。
`QT_TRANSLATE_NOOP` 是给 lupdate 的标记(运行期原样返回): 模板是变量, lupdate 无法识别
`translate(ctx, 变量)` 这种写法, 必须把字面量列出才会收录。
"""
import logging
import os
import re

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QTranslator

# 可选语言(菜单按此列表生成); 第一项是源语言, 不需要 .qm
# 第三项是跟在「语言」后面的语言码(非中文使用者据此识别该入口用途):
# 刻意不是 code.upper() —— zh 的大写是 "ZH"(ISO 639 语言码), 通用写法是 CN/EN。
LANGS = (("zh", "中文", "CN"), ("en", "English", "EN"))
DEFAULT_LANG = "zh"

_QM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n")
_CTX = "core"                   # 运行期查表用的 context; NOOP 处必须写同样字面量
                               # (lupdate 不认变量, 写 _CTX 会把条目归到空 context)
_translator = None              # 必须保留引用: 局部变量被回收后翻译即失效

# 核心与弹窗中无法逐个 tr() 的中文串。带 {占位符} 的是模板, 其余为精确匹配。
# 只登记用户可见的消息; 仅进日志的中文不在此列(不翻译)。
_CORE_MSG_TEMPLATES = (
    # ---- 核心 RenderWorker 的事件 message ----
    QT_TRANSLATE_NOOP("core", "处理中..."),
    QT_TRANSLATE_NOOP("core", "已完成: {name}"),
    QT_TRANSLATE_NOOP("core", "处理失败"),
    QT_TRANSLATE_NOOP("core", "处理失败: {err}"),
    QT_TRANSLATE_NOOP("core", "处理出错: {err}"),
    QT_TRANSLATE_NOOP("core", "没有可处理的图片"),
    QT_TRANSLATE_NOOP("core", "用户取消：已完成 {success}/{total}"),
    QT_TRANSLATE_NOOP("core", "批量处理完成：{success}/{total} -> {out_dir}"),
    QT_TRANSLATE_NOOP("core", "失败详情："),
    QT_TRANSLATE_NOOP("core", "... 及其他 {n} 个文件"),
    # ---- 核心 start_generate / make_preview 的错误 ----
    QT_TRANSLATE_NOOP("core", "已有任务正在处理，请先等待完成或停止"),
    QT_TRANSLATE_NOOP("core", "请先选择一张输入图片"),
    QT_TRANSLATE_NOOP("core", "导出目录不可写：{path}（{err}）"),
    QT_TRANSLATE_NOOP("core", "请先选择要批量处理的图片"),
    QT_TRANSLATE_NOOP("core", "批量文件格式错误"),
    QT_TRANSLATE_NOOP("core", "批量文件路径无效（请重新选择或拖入）"),
    QT_TRANSLATE_NOOP("core", "无法创建导出子目录: {err}"),
    QT_TRANSLATE_NOOP("core", "预览生成失败"),
    QT_TRANSLATE_NOOP("core", "预览生成失败: {err}"),
    # ---- 预览线程回传的失败文本 ----
    QT_TRANSLATE_NOOP("core", "预览失败: {err}"),
    # ---- 弹窗(dialog.py)里的按钮与标题 ----
    QT_TRANSLATE_NOOP("core", "确定"),
    QT_TRANSLATE_NOOP("core", "是"),
    QT_TRANSLATE_NOOP("core", "否"),
    QT_TRANSLATE_NOOP("core", "关闭"),
    QT_TRANSLATE_NOOP("core", "提示"),
    # ---- 核心渲染分段的阶段名(出现在"上一次渲染"的 tooltip 中) ----
    QT_TRANSLATE_NOOP("core", "预处理(解码/转正/增强)"),
    QT_TRANSLATE_NOOP("core", "EXIF读取"),
    QT_TRANSLATE_NOOP("core", "字体/几何"),
    QT_TRANSLATE_NOOP("core", "背景层"),
    QT_TRANSLATE_NOOP("core", "图片外观(圆角/正圆)"),
    QT_TRANSLATE_NOOP("core", "图片阴影层"),
    QT_TRANSLATE_NOOP("core", "图片描边层"),
    QT_TRANSLATE_NOOP("core", "水印文字绘制"),
    QT_TRANSLATE_NOOP("core", "低清缩回"),
    QT_TRANSLATE_NOOP("core", "编码保存"),
    QT_TRANSLATE_NOOP("core", "预览"),
    QT_TRANSLATE_NOOP("core", "成品"),
)

_re_cache = None


def _compile_templates():
    """把带占位符的模板编成正则: {name} -> 命名捕获组, 其余转义。懒执行一次。"""
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
        except re.error as e:                        # 模板写错不应导致界面崩溃
            logging.warning(f"[i18n] 模板编译失败 {tpl!r}: {e}")
    _re_cache = out
    return out


def _tr(text):
    """查翻译(未安装翻译器或查不到该条时原样返回中文)。"""
    return QCoreApplication.translate(_CTX, text)


def tr_msg(text):
    """把核心返回的中文消息翻译一次(显示边界调用)。单行与多行均可。

    先精确匹配, 再按模板匹配; 都不中则原样返回 —— 降级为中文, 不会产生乱码。
    `tools/check_i18n.py` 会报出未被覆盖的字符串。
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
            out.append(line)                        # 未覆盖(多为文件路径或异常原文) -> 原样
    return "\n".join(out)


def install(app, lang):
    """按语言安装翻译器。必须在建主窗口之前调用(窗口字面量在构造时即 tr())。"""
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
