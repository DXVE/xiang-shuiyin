# -*- coding: utf-8 -*-
"""多语言: 中文(源语言) + 英文, install() 按设置里的 lang 装载 .qm"""
import logging
import os
import re

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QTranslator

# 可选语言(菜单按此列表生成): (语言码, 显示名, 「语言」入口后面显示的语言码)
# 第一项是源语言, 不需要 .qm; 第三个码不取 code.upper()(zh 的大写 ZH 是 ISO 639 语言码)
LANGS = (("zh", "中文", "CN"), ("en", "English", "EN"))
DEFAULT_LANG = "zh"

_QM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n")
_CTX = "core"                   # 运行期查表用的 context
_translator = None              # 必须保留引用: 局部变量被回收后翻译即失效
_qt_translators = []            # 同上, 这是 Qt 自带翻译的引用

# Qt 翻译目录名用地区码(zh_CN), 这里是语言码到地区码的映射表; 表里没有的语言不装载
_QT_LOCALE = {"zh": "zh_CN", "en": "en"}
_QT_CATALOGS = ("qtbase", "qt")     # 基础控件 + Qt 通用文案

# 核心与弹窗中无法逐个 tr() 的中文串: 带 {占位符} 的是模板, 其余为精确匹配
_CORE_MSG_TEMPLATES = (
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
    QT_TRANSLATE_NOOP("core", "已有任务正在处理，请先等待完成或停止"),
    QT_TRANSLATE_NOOP("core", "请先选择一张输入图片"),
    QT_TRANSLATE_NOOP("core", "导出目录不可写：{path}（{err}）"),
    QT_TRANSLATE_NOOP("core", "请先选择要批量处理的图片"),
    QT_TRANSLATE_NOOP("core", "批量文件格式错误"),
    QT_TRANSLATE_NOOP("core", "批量文件路径无效（请重新选择或拖入）"),
    QT_TRANSLATE_NOOP("core", "无法创建导出子目录: {err}"),
    QT_TRANSLATE_NOOP("core", "预览生成失败"),
    QT_TRANSLATE_NOOP("core", "预览生成失败: {err}"),
    QT_TRANSLATE_NOOP("core", "预览失败: {err}"),
    QT_TRANSLATE_NOOP("core", "确定"),
    QT_TRANSLATE_NOOP("core", "是"),
    QT_TRANSLATE_NOOP("core", "否"),
    QT_TRANSLATE_NOOP("core", "关闭"),
    QT_TRANSLATE_NOOP("core", "提示"),
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
    # 把带占位符的模板编成正则: {name} -> 命名捕获组, 其余转义(懒执行一次)
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
    # 查翻译: 未安装翻译器或查不到该条时原样返回中文
    return QCoreApplication.translate(_CTX, text)


def tr_msg(text):
    # 把核心返回的中文消息翻译一次(显示边界调用): 先精确匹配, 再按模板匹配, 都不中则原样返回
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


def _install_qt_translations(app, lang):
    # 装载 Qt 自带翻译(按语言码换地区码), 返回装载成功的目录名列表
    from PySide6.QtCore import QLibraryInfo

    locale = _QT_LOCALE.get(lang)
    if not locale:
        return []
    base = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    loaded = []
    for name in _QT_CATALOGS:
        t = QTranslator(app)
        if t.load(f"{name}_{locale}", base):
            app.installTranslator(t)
            _qt_translators.append(t)
            loaded.append(f"{name}_{locale}")
    if loaded:
        logging.info(f"[i18n] Qt 自带翻译: {', '.join(loaded)}")
    else:
        logging.warning(f"[i18n] 未找到 Qt 自带翻译({locale})，Qt 绘制的文案将显示英文")
    return loaded


def install(app, lang):
    # 按语言安装翻译器
    global _translator
    lang = lang or DEFAULT_LANG
    # Qt 自带翻译先装: 中文是源语言, 下面那段对本程序自己的翻译器会直接返回
    _install_qt_translations(app, lang)
    if lang == DEFAULT_LANG:
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
