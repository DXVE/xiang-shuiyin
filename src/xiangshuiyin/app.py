# -*- coding: utf-8 -*-
"""
镶·水印 · 渲染内核
==========================
参数、EXIF、图像处理、`process_single`、`AppState`(预览/生成/预设/型号映射/配置)。
调用方是同进程的 Qt 界面(`src/xsy_qt/`)与 `main.py`, 不存在跨进程/桥通讯。

- 核心: 6 间距参数的三布局水印渲染(unified / split_top_bottom / split_left_right)
- 预览: 缩略图快速渲染 + 放大镜确认细节, 间距段几何经 `geom_out` 回传界面叠加高亮,
  画布 PIL 图经 `image_out` 直接回给调用方(不落盘)
- 持久化: 参数/预设/型号映射/settings 统一写入本程序自己的数据目录, 见「目录常量」
  (Windows: %LOCALAPPDATA%\\XiangShuiyin-QT6, macOS/Linux 同理; 可用 XSY_BASE_DIR 覆盖)

依赖: Pillow, exifread
"""

import base64
import collections
import json
import logging
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import exifread
from PIL import Image
from PIL import ImageDraw, ImageEnhance, ImageFont, ImageFilter, ImageOps

# 控制台/管道输出以中文为主, 统一 UTF-8 编码避免重定向后乱码(Windows 默认 GBK)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# 预览/上传临时目录(程序退出时清理)
# 由 main.py 的 XSY_PREVIEW_DIR 指定; 这里的回退值也用本程序自己的名字 —— 导入本模块只读不写
# 这个目录, 但共用一个临时目录时, 任一版退出时的 cleanup() 会把对方正在显示的预览文件删掉。
PREVIEW_DIR = os.environ.get("XSY_PREVIEW_DIR") or os.path.join(tempfile.gettempdir(), "XiangShuiyin_preview_qt")

# 实时预览缩略渲染的最大边长(低分辨率快速通道, 高清预览供放大镜确认细节)
#   2500 = 常态档: 输入图先缩到 2500 再渲染(渲染快, 上屏前再由 PreviewBox 缩一次)
#   0    = **全量加载**: 不缩图, 画布/字号/圆角/模糊半径全按原图尺寸算。仅用于画质排查 ——
#          2026-09-20 实测(4000x3000 纯色底): 全量每次调参 275~284ms, 2500 档命中缩略图缓存后
#          110ms(首次要多花 ~230ms 建 psrc 缩略图); 上屏那步的画质两者一致(锐度来自
#          PreviewBox 按设备像素上屏, 不是画布大小)。所以常态就用 2500。
PREVIEW_MAX_SIDE = 2500
# 先导帧边长: 大图先按这一档渲一帧立即显示(感知延迟取决于它), 全帧随后覆盖。
# **先导帧必须用小图档做渲染输入** —— 画布尺寸由输入图决定, 若仍喂 2500 的缩略图, 先导帧画布
# 与全帧同大(同样耗时)且模糊/圆角的相对缩放不同, 两帧会"模糊度跳动"(原项目 2026-09-20 实测)。
PREVIEW_FAST_MAX_SIDE = 1200


# 背景模糊工作图最小边长(px): 降采样优化用, 工作图边长 = 画布的 1/4(面积 1/16),
# 模糊/噪声/增强都在小图上做, 最后放大回画布; 下限防止小图时画质过度损失
BLUR_WORK_MIN_SIDE = 400

# 配置管理：目录常量、默认字体路径、设置文件的读写、型号映射文件的读写等
# ---------- 目录常量 ----------
# 配置数据目录: 按平台惯例选择——Windows: LOCALAPPDATA; macOS: ~/Library/Application Support;
# Linux: $XDG_DATA_HOME 或 ~/.local/share。均在其下建 XiangShuiyin-QT6 子目录。
# Windows 使用 LOCALAPPDATA, 目录名归本程序所有, 配置与日志互不干扰。
if os.name == 'nt':
    _data_root = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
elif sys.platform == 'darwin':
    _data_root = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support')
else:
    _data_root = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
# 数据目录: 正常由 main.py 通过 XSY_BASE_DIR 指定; **回退值也必须是本程序自己的目录** ——
# 导入本模块会立刻按 BASE_DIR 建日志目录、轮转旧日志(_cleanup_old_logs 只留最近 3 份)并清理
# DATA_DIR 下的 *.tmp* 残留, 回退到别的程序的目录就等于去创建/删除别人的文件。
BASE_DIR = os.environ.get("XSY_BASE_DIR") or os.path.join(_data_root, "XiangShuiyin-QT6")
DATA_DIR = os.path.join(BASE_DIR, "watermark_data")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
PRESETS_FILE = os.path.join(DATA_DIR, "presets.json")
DEFAULT_LOGO_DIR = os.path.join(DATA_DIR, "logos")
DEFAULT_SPECIAL_DIR = os.path.join(DEFAULT_LOGO_DIR, "special")

# ---------- 默认字体路径 ----------
# 脚本同目录: 内置资源目录 assets/fonts(Roboto 四变体, 随程序分发, SIL OFL 协议)
# 打包(PyInstaller onefile)后, __file__ 指向 PYZ 内的虚拟路径, 不能直接靠它找 assets;
# 用 _MEIPASS 兜底。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(getattr(sys, "_MEIPASS", SCRIPT_DIR), "assets")
BUNDLED_FONT_DIR = os.path.join(ASSETS_DIR, "fonts")

# 系统字体目录列表(按平台惯例; 仅作最后兜底——内置 Roboto 已在持久/打包目录命中,
# 系统目录在非 Windows 上几乎不触发, 但消除硬编码的 C:\Windows\Fonts)
def _system_font_dirs():
    if os.name == 'nt':
        return [r"C:\Windows\Fonts"]
    if sys.platform == 'darwin':
        return ["/System/Library/Fonts", "/Library/Fonts",
                os.path.join(os.path.expanduser('~'), 'Library', 'Fonts')]
    # Linux/其它: XDG 用户字体目录 + 传统 ~/.fonts + 系统字体目录
    xdg_root = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return [os.path.join(xdg_root, 'fonts'),
            os.path.join(os.path.expanduser('~'), '.fonts'),
            "/usr/share/fonts", "/usr/local/share/fonts"]

# 优先加载已持久化的内置字体, 其次当前打包资源目录, 最后系统字体;
# 都没有返回空串(调用方回退默认字体).
def _default_font_path(filename):
    for base in (PERSISTENT_FONT_DIR, BUNDLED_FONT_DIR) + tuple(_system_font_dirs()):
        p = os.path.join(base, filename)
        if os.path.isfile(p):
            return p
    return ""

# ---------- 工具函数 ----------
# 确保目录存在，若不存在则创建
def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def open_path(path):
    """跨平台打开目录/文件: Windows 资源管理器, macOS Finder, Linux xdg-open"""
    if os.name == 'nt':
        os.startfile(path)
    elif sys.platform == 'darwin':
        subprocess.run(['open', path])
    else:
        subprocess.run(['xdg-open', path])

# ---------- 通用 JSON 读写 ----------
# 读取 JSON 文件: 文件不存在或解析失败时返回 {}; log_func=None 则失败时静默
# check_file=True 时用 isfile 判断(仅文件), 否则用 exists 判断(文件/目录)
def _load_json(path, check_file=False, log_func=None, fail_prefix="加载JSON文件失败"):
    exists = os.path.isfile(path) if check_file else os.path.exists(path)
    if not exists:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        if log_func:
            log_func(f"{fail_prefix} ({path}): {e}")
        return {}

# 写入 JSON 文件(自动创建父目录); 原子替换写 tmp → os.replace, 同分区下 OS 串行化保证
# 文件始终是合法 JSON(并发写最后一个赢). tmp 文件名带 pid+uuid 防多进程写交错.
# 失败时降级裸 open(path,'w')(最后一道防线); 不 fsync——写频率低, 可接受应用崩溃丢最后 1 次.
def _save_json(path, obj, ensure_path=None):
    if ensure_path:
        ensure_dir(ensure_path)
    tmp_path = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    try:
        with open(tmp_path, "wb") as f:
            f.write(json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8"))
            f.flush()
        os.replace(tmp_path, path)
    except Exception:
        # 主路径异常(tmp 写失败/权限/跨分区等): 清理残留 tmp, 退回裸写
        try:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            with open(path, "w", encoding="utf-8") as f2:
                json.dump(obj, f2, indent=2, ensure_ascii=False)
        except Exception:
            pass

# 从 JSON 设置文件加载配置，若文件不存在则返回空字典
def load_settings():
    return _load_json(SETTINGS_FILE, log_func=logging.error, fail_prefix="加载设置文件失败")

# 保存设置字典到 JSON 文件(自动创建目录)
def save_settings(settings):
    _save_json(SETTINGS_FILE, settings, ensure_path=DATA_DIR)

# ---------- 字体路径失效迁移 ----------
# 资源目录重组后(如 fonts -> assets/fonts), settings 里持久化的绝对路径可能失效;
# 启动时校验 4 个字体路径, 不存在则回退内置默认并写回 settings, 避免绑定死目录
FONT_SETTING_MAP = (
    ("font_path", "Roboto-Regular.ttf"),
    ("font_bold_path", "Roboto-Bold.ttf"),
    ("font_italic_path", "Roboto-Italic.ttf"),
    ("font_bold_italic_path", "Roboto-BoldItalic.ttf"),
)

# 内置字体持久化目录: 打包后 _MEI 临时目录每次启动会变, 把内置字体复制到数据目录
# 使 settings/presets 保存的字体路径长期有效.
PERSISTENT_FONT_DIR = os.path.join(DATA_DIR, "fonts", "bundled")

def _ensure_persistent_fonts():
    """把 BUNDLED_FONT_DIR 下的内置字体复制到 PERSISTENT_FONT_DIR, 按大小判断更新.
    返回是否成功确保至少一个字体存在."""
    if not os.path.isdir(BUNDLED_FONT_DIR):
        return False
    ensure_dir(PERSISTENT_FONT_DIR)
    ok = False
    for _, fname in FONT_SETTING_MAP:
        src = os.path.join(BUNDLED_FONT_DIR, fname)
        dst = os.path.join(PERSISTENT_FONT_DIR, fname)
        if not os.path.isfile(src):
            continue
        need_copy = True
        if os.path.isfile(dst) and os.path.getsize(src) == os.path.getsize(dst):
            need_copy = False
        if need_copy:
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                logging.warning(f"复制内置字体失败 {src} -> {dst}: {e}")
                continue
        ok = ok or os.path.isfile(dst)
    return ok

def _repair_font_paths(settings):
    # 先确保内置字体已落到持久目录, 后续修复才能指向稳定路径
    _ensure_persistent_fonts()
    changed = False
    for key, fname in FONT_SETTING_MAP:
        cur = settings.get(key)
        if not cur or not os.path.isfile(cur):
            newp = _default_font_path(fname)
            if newp and settings.get(key) != newp:
                settings[key] = newp
                changed = True
    if changed:
        save_settings(settings)
        logging.info("settings 字体路径已迁移到持久目录: " + PERSISTENT_FONT_DIR)
    return settings

# 加载指定 Logo 目录下的型号映射文件(model_map.json)
def load_model_map(logo_dir):
    map_path = os.path.join(logo_dir, "model_map.json")
    return _load_json(map_path, check_file=True, log_func=logging.warning,
                      fail_prefix="加载型号映射文件失败")

# 保存型号映射字典到指定 Logo 目录下的 model_map.json
def save_model_map(logo_dir, map_dict):
    _save_json(os.path.join(logo_dir, "model_map.json"), map_dict, ensure_path=logo_dir)

# 核心算法及处理：EXIF读取、图像处理、水印生成等

# 将 EXIF 中的有理数字符串解析为 (分子, 分母, 浮点值)
def _parse_ratio(value):
    """
    将可能的有理数转换为 (num, den, float)
    统一使用字符串解析，避免类型判断问题
    """
    s = str(value).strip()
    if '/' in s:
        parts = s.split('/')
        if len(parts) == 2:
            try:
                num = int(parts[0])
                den = int(parts[1])
                if den != 0:
                    return num, den, num / den
            except Exception:
                pass
    try:
        fl = float(s)
        return None, None, fl
    except Exception:
        return None, None, None

# 用 exifread 解析品牌/型号/快门/光圈/ISO, 结果原地写入 result
def _read_exif_exifread(image_path, result, debug=False):
    try:
        with open(image_path, 'rb') as f:
            tags = exifread.process_file(f, details=True)
        if debug:
            print("=== All EXIF tags ===")
            for tag in sorted(tags.keys()):
                print(f"{tag}: {tags[tag]}")

        # 品牌、型号
        if 'Image Make' in tags:
            result['make'] = str(tags['Image Make']).strip()
        if 'Image Model' in tags:
            result['model'] = str(tags['Image Model']).strip()

        # 光圈 FNumber
        if 'EXIF FNumber' in tags:
            num, den, fl = _parse_ratio(tags['EXIF FNumber'])
            if fl is not None:
                result['fnumber'] = fl
                if debug:
                    print(f"FNumber: {fl}")

        # ---- 优先使用 ShutterSpeedValue 计算快门(最精确)----
        if 'EXIF ShutterSpeedValue' in tags:
            _, _, apex = _parse_ratio(tags['EXIF ShutterSpeedValue'])
            if apex is not None and apex != 0:
                exp_time = 2 ** (-apex)
                result['exposure_time'] = exp_time
                if debug:
                    print(f"ShutterSpeedValue -> {exp_time} ({1/exp_time:.1f})")

        # ---- 如果没有 ShutterSpeedValue 则回退到 ExposureTime ----
        if result['exposure_time'] is None and 'EXIF ExposureTime' in tags:
            num, den, fl = _parse_ratio(tags['EXIF ExposureTime'])
            if fl is not None:
                result['exposure_time'] = fl
                if num is not None and den is not None:
                    result['exposure_num'] = num
                    result['exposure_den'] = den
                if debug:
                    print(f"ExposureTime: {fl} ({num}/{den})")

        # ISO
        if 'EXIF ISOSpeedRatings' in tags:
            try:
                result['iso'] = int(float(str(tags['EXIF ISOSpeedRatings'])))
                if debug:
                    print(f"ISO: {result['iso']}")
            except Exception:
                pass

        if debug:
            print("=== exifread extracted ===", result)
        # L2: 不提前 return, 始终执行下方 Pillow 回退补漏,
        # 避免 exifread 部分成功(缺光圈/ISO等)时字段丢失
    except Exception as e:
        # 落日志而不是 print: 之前这两个错在日志文件里完全看不到(EXIF 读失败会静默变成 unknown)
        logging.warning(f"EXIF: exifread 读取失败({type(e).__name__}: {e}), 走 Pillow 回退")

# 用 Pillow 补漏 exifread 缺失的字段(仅填 None 处, 不覆盖已有值)
def _read_exif_pillow(image_path, result):
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if exif:
                make = _str(exif.get(271))
                model = _str(exif.get(272))
                exif_ifd = exif.get_ifd(0x8769)
                if exif_ifd:
                    exp = _number(exif_ifd.get(33434, exif_ifd.get(37377)))
                    fnum = _number(exif_ifd.get(33437, exif_ifd.get(37378)))
                    iso = exif_ifd.get(34855) or exif_ifd.get(34867) or exif_ifd.get(12288)
                    if isinstance(iso, (list, tuple)):
                        iso = iso[0] if iso else None
                    iso = _number(iso)
                    result['make'] = make or result['make']
                    result['model'] = model or result['model']
                    result['exposure_time'] = exp or result['exposure_time']
                    result['fnumber'] = fnum or result['fnumber']
                    result['iso'] = iso or result['iso']
    except Exception as e:
        logging.warning(f"EXIF: Pillow 回退补漏失败({type(e).__name__}: {e})")

# 从图片文件路径读取 EXIF 信息(品牌、型号、快门、光圈、ISO)
def get_exif_data_from_path(image_path, debug=False):
    result = {
        "make": None, "model": None,
        "exposure_time": None, "exposure_num": None, "exposure_den": None,
        "fnumber": None, "iso": None
    }
    _read_exif_exifread(image_path, result, debug)
    _read_exif_pillow(image_path, result)
    if debug:
        print("=== After fallback ===", result)
    return result

# 将 EXIF 字节或字符串安全转换为可读字符串
def _str(val):
    if isinstance(val, bytes):
        s = val.decode("utf-8", errors="ignore")
        s = ''.join(ch for ch in s if ch.isprintable() or ch == ' ')
        return s.strip()
    if isinstance(val, str):
        s = ''.join(ch for ch in val if ch.isprintable() or ch == ' ')
        return s.strip()
    return None

# 将 EXIF 数值安全转换为浮点数
def _number(val):
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None

# ---------- 格式化函数(完全模仿 Windows 快门显示)----------
# 将曝光时间(浮点或分子分母)转换为 Windows 风格的字符串，如 "1/125s"
def exposure_to_str(exp=None, num=None, den=None):
    """
    完全模拟 Windows 文件属性的快门显示逻辑：
    1. 优先使用 ShutterSpeedValue 计算的浮点数 exp。
    2. 否则使用 ExposureTime 的有理数转浮点数。
    3. 将秒数 value 转换为 1/x 格式，其中 x = round(1 / value)。
    """
    # 获取快门秒数(浮点)
    if exp is not None:
        value = exp
    elif num is not None and den is not None and den != 0:
        value = num / den
    else:
        return ""

    if value <= 0:
        return ""
    if value >= 1:
        # 长曝光：保留一位小数
        return f"{value:.1f}s"
    else:
        # 短曝光：计算倒数并四舍五入
        x = int(round(1.0 / value))
        if x <= 0:
            return ""
        return f"1/{x}s"

# 将光圈值格式化为 "f/数值" 形式
def aperture_to_str(f):
    if f is None:
        return ""
    # 判断光圈值是否接近整数(误差小于0.01)
    if abs(f - round(f)) < 0.01:
        return f"f/{int(round(f))}"
    else:
        return f"f/{f:.1f}"

# 将 ISO 值格式化为 "ISO数值" 形式
def iso_to_str(i):
    if i is None:
        return ""
    return f"ISO{int(i)}"

# 注入临时型号美化映射(仅本次生效, 不写映射表):
#   copy_on_inject=True 时先拷贝 params(避免污染请求对象);
#   ensure_map=True 时无论是否注入都确保 params 含 model_map 键(与生成流程一致)
def _inject_model_map(params, raw_model, custom_model, copy_on_inject=False, ensure_map=False):
    if raw_model and custom_model and custom_model != raw_model:
        if copy_on_inject:
            params = dict(params)
        temp_map = dict(params.get("model_map", {}))
        temp_map[raw_model] = custom_model
        params["model_map"] = temp_map
    elif ensure_map:
        params["model_map"] = dict(params.get("model_map", {}))
    return params

# 使用映射表美化型号名称
def prettify_model(model, model_map):
    if not model:
        return model
    return model_map.get(model, model)

# ---------- Logo 处理 ----------
# 根据品牌名在 Logo 目录中查找匹配的 Logo 文件
def find_logo(logo_dir, make):
    if not make or not os.path.isdir(logo_dir):
        return None
    make_clean = make.lower().replace(" ", "")
    candidates = []
    for fname in os.listdir(logo_dir):
        path = os.path.join(logo_dir, fname)
        if not os.path.isfile(path):
            continue
        name = os.path.splitext(fname)[0].lower().replace(" ", "")
        if make_clean in name:
            candidates.append((len(name), path))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]

# 从 special 子目录加载特定名称的特殊图片(如品牌 Logo 文字图)
def load_special_image(special_dir, name):
    if not os.path.isdir(special_dir):
        return None
    for fname in os.listdir(special_dir):
        path = os.path.join(special_dir, fname)
        if not os.path.isfile(path):
            continue
        stem = os.path.splitext(fname)[0]
        if stem == name:
            try:
                return Image.open(path).convert("RGBA")
            except Exception:
                return None
    return None

# 加载 Logo 图片并缩放到指定最大高度(保持宽高比)
def load_logo(path, max_height):
    try:
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        new_h = int(max_height)
        new_w = int(w * new_h / h)
        return img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    except Exception:
        return None

# ---------- 文字绘制 ----------
# 计算给定字体下文本的像素高度
def get_text_height(font, text):
    tmp = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(tmp)
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]

# 在图像上绘制带可选描边/投影的文字
# 按角度分解阴影方向偏移: 角度取时钟系(0°=正上方, 顺时针增大, 90=右, 180=下, 270=左),
# 便于直觉理解; ox 正=右, oy 正=下
def _shadow_offset(offset, angle):
    a = math.radians(angle)
    return int(offset * math.sin(a)), int(-offset * math.cos(a))


def draw_text(draw, canvas, xy, text, font, fill,
              stroke_enable=False, stroke_color="#ffffff", stroke_width=1,
              shadow_enable=False, shadow_color="#000000", shadow_offset=1,
              shadow_blur=2, shadow_alpha=0.5, shadow_angle=135,
              shadow_clip_bottom=None):
    # L6: 用 Pillow 原生描边参数(单次调用), 替代 O(描边宽²) 的逐像素偏移绘制
    use_stroke = stroke_enable and stroke_width > 0
    if shadow_enable and shadow_offset >= 0:
        # 文字方向投影: 文字 mask 沿角度方向平移后作为阴影, 露出文字实心影(边缘柔和);
        # 与图片水墨晕开不同, 文字笔画细不做四周晕开, 否则笔画间隙被填满糊成一块
        # (浓度低又淡得不可见)。blur 只柔和阴影边缘, offset 控制方向位移幅度
        sw = stroke_width if use_stroke else 0
        bbox = draw.textbbox(xy, text, font=font, stroke_width=sw)
        ox, oy = _shadow_offset(shadow_offset, shadow_angle)
        cw, ch = canvas.size
        # 晕开范围=0 时阴影是实心硬边, 大字号行(如型号)底部一条硬边像被框截断;
        # 强制最小 2px 柔和, 让阴影边缘始终有渐变
        eff_blur = max(shadow_blur, 2)
        # 高斯模糊的可见晕开范围约 3 倍半径: 阴影(含晕开)必须完整落在画布内并留出边距,
        # 空间不足时偏移向画布内收(可为负), 避免晕开渐变在画布边缘硬切截断
        spread = eff_blur * 3 + 8
        if ox > 0:
            ox = min(ox, cw - bbox[2] - spread)
        elif ox < 0:
            ox = max(ox, -(bbox[0] - spread))
        if oy > 0:
            oy = min(oy, ch - bbox[3] - spread)
        elif oy < 0:
            oy = max(oy, -(bbox[1] - spread))
        # 限制阴影不延伸到下一行文字(会被下一行遮挡, 看起来像截断):
        # clip_bottom 给定时阴影底(含晕开渐变)不超过该边界, 阴影只在行距空白内结束
        if shadow_clip_bottom is not None:
            oy = min(oy, shadow_clip_bottom - bbox[3] - eff_blur * 3 - 4)
        # pad 需覆盖高斯模糊可见晕开范围(约 4 倍半径)+文字边缘, 否则渐变尾部被 mask 矩形
        # 边缘硬切, 阴影底部/四周会露出水平框线; mask 区域只覆盖阴影内容(文字 bbox + 偏移 + 晕开)
        pad = eff_blur * 4 + 8
        sx0, sy0 = max(0, min(bbox[0], bbox[0] + ox) - pad), max(0, min(bbox[1], bbox[1] + oy) - pad)
        sx1, sy1 = min(cw, max(bbox[2], bbox[2] + ox) + pad), min(ch, max(bbox[3], bbox[3] + oy) + pad)
        if sx1 > sx0 and sy1 > sy0:
            mw, mh = sx1 - sx0, sy1 - sy0
            mask = Image.new("L", (mw, mh), 0)
            ImageDraw.Draw(mask).text((bbox[0] - sx0, bbox[1] - sy0), text, font=font, fill=255,
                                      stroke_width=sw, stroke_fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(eff_blur))
            sh_mask = Image.new("L", (mw, mh), 0)
            sh_mask.paste(mask, (ox, oy))
            sh = Image.new("RGBA", (mw, mh), shadow_color)
            sh.putalpha(sh_mask.point(lambda v: int(v * shadow_alpha)))
            canvas.paste(sh, (sx0, sy0), sh)
    draw.text(xy, text, fill=fill, font=font,
              stroke_width=stroke_width if use_stroke else 0,
              stroke_fill=stroke_color if use_stroke else None)

# 从 EXIF 中删除 Orientation 标记: 像素已 exif_transpose 物理转正, 保留该标记会被查看器二次旋转
def _strip_orientation(exif_bytes):
    if not exif_bytes:
        return exif_bytes
    try:
        ex = Image.Exif()
        ex.load(exif_bytes)
        ex.pop(0x0112, None)
        return ex.tobytes()
    except Exception:
        return exif_bytes

# 圆角矩形精确多边形路径(每象限 n_per_quad 段采样, 直边由相邻端点自然连接)。
# n_super=2 为四分之一圆弧; >2 为超椭圆——拐角曲率渐变、切点处与直边平滑衔接,
# 视觉比纯圆弧更饱满圆润(接近现代 UI 圆角)。Pillow 的 rounded_rectangle 内部用线段数
# 很少的 arc 逼近, 切点处几何偏差产生微小凹凸; 自绘路径用密采样 + 四舍五入, 精确贴合
def _rounded_rect_vertices(w, h, radius, n_per_quad=32, n_super=2.5):
    import math
    r = min(max(radius, 0.0), w / 2.0, h / 2.0)
    pts = []
    # 顺时针: 右上/右下/左下/左上四段, 圆心分别对应四角
    corners = [(w - r, r), (w - r, h - r), (r, h - r), (r, r)]
    a0s = [-math.pi / 2, 0.0, math.pi / 2, math.pi]   # 每段起始角
    for (cx, cy), a0 in zip(corners, a0s):
        for k in range(n_per_quad + 1):
            a = a0 + math.pi / 2 * k / n_per_quad
            c, s = math.cos(a), math.sin(a)
            # 超椭圆参数化 x=r·sign(c)|c|^(2/n), y=r·sign(s)|s|^(2/n); n=2 退化为圆弧
            x = r * math.copysign(abs(c) ** (2.0 / n_super), c)
            y = r * math.copysign(abs(s) ** (2.0 / n_super), s)
            # float→int 四舍五入(ImageDraw 内部按截断转 int, 会整体内偏 0.5px)
            pts.append((int(cx + x + 0.5), int(cy + y + 0.5)))
    return pts

# 整圆路径(圆心居中, 半径 radius)——圆形档(正圆裁剪)用
def _circle_vertices(w, h, radius, n_seg=128):
    import math
    cx, cy = w / 2.0, h / 2.0
    pts = []
    for k in range(n_seg + 1):
        a = math.pi * 2 * k / n_seg - math.pi / 2   # 自顶部顺时针
        pts.append((int(cx + radius * math.cos(a) + 0.5), int(cy + radius * math.sin(a) + 0.5)))
    return pts

# 圆角/圆形 mask 缓存
# mask 只由 (尺寸, 半径) 决定, 与图像内容无关; 而超采样会建一张 4 倍边长的 L 通道图
# (预览 1667×2500 时是 6668×10000 = 66MP/66MB), 再 LANCZOS 缩回 —— 实测占单次预览
# 约 150ms, 是整条渲染链路最贵的一步, 而每次调参都在重算同一张 mask。
# putalpha() 会拷贝 alpha 数据, 缓存对象不被输出图持有, 可安全复用。
_MASK_CACHE = {}
_MASK_CACHE_LOCK = threading.Lock()
_MASK_CACHE_MAX = 3          # 只留最近几项
_MASK_CACHE_MAX_PX = 16_000_000   # 超过约 16MP 的 mask 不缓存: 成品渲染是一次性的,
                                  # 缓存它只会白占内存(24MP 成品的 mask 就是 24MB)
# 命中/未命中计数: 只在落"渲染分段"日志时读一次(见 _cache_stats_line), 不在查找路径上做别的事
_MASK_STATS = {"hit": 0, "miss": 0}

def _cached_mask(key, build):
    with _MASK_CACHE_LOCK:
        m = _MASK_CACHE.get(key)
    if m is not None:
        _MASK_STATS["hit"] += 1
        return m
    m = build()
    _MASK_STATS["miss"] += 1
    if key[1] * key[2] <= _MASK_CACHE_MAX_PX:
        with _MASK_CACHE_LOCK:
            if len(_MASK_CACHE) >= _MASK_CACHE_MAX:
                _MASK_CACHE.clear()
            _MASK_CACHE[key] = m
    return m


# ---------- 渲染中间层缓存(分层缓存) ----------
# 只服务"同一张图、反复调参"的交互路径: 调间距/文字/颜色时, 图片外观层(圆角+阴影+描边)与
# 背景层都不需要重算。各层在预览档(2500 画布)上的实测开销: 背景层 ~90ms、图片阴影层 ~48ms、
# 描边层 ~135ms、圆角 ~6ms —— 而改一个文字参数会把它们全部重算一遍。
#
# **键必须覆盖影响该层的全部参数**: 以后新增/修改任何影响某层的参数, 必须同步加进该层的键,
# 否则会静默复用上一次的结果(表现为"改了参数预览不变")。
#
# 代价是工作图分辨率与实际画布差 <3%(强模糊背景上不可辨)。本项目要求"输出与现状逐像素一致",
# 所以键用**精确尺寸**, 不引入量化 —— 渲染路径一行没改, 只是把"算过的同一张层"留下来复用。
_LAYER_CACHE = collections.OrderedDict()   # LRU: 末尾最新(淘汰从头部取, 需要 popitem(last=False))
_LAYER_CACHE_LOCK = threading.Lock()
_LAYER_CACHE_BUDGET = 128 << 20     # 层数据总量上限(字节); 超了按最久未用淘汰。
                                    # 预览一套层约 15MB(背景/图片/阴影/描边各一张), 128MB ≈ 8 套,
                                    # 够 A/B 对比几个参数状态而不互相淘汰。
_LAYER_CACHE_MAX_PX = 16_000_000    # 单层像素上限(按"预览最长边 2500 -> 最大
                                    # 6.25MP"推的), 但那只算了**输入图**那一档 —— 画布还受
                                    # canvas_ratio 影响: 21:9 时预览画布实测 5215x2235 = 11.7MP,
                                    # 于是整张背景层被这个上限挡在缓存外, 每次调参都重算
                                    # (放大 61ms + 暗化混合 49ms, 实测均值 420ms -> 264ms)。
                                    # 取 16MP: 够放下各种比例下的预览画布, 又仍把成品全分辨率那层
                                    # 挡在外面(4000x3000 + 21:9 的成品画布是 8346x3577 = 29.9MP,
                                    # 单张 RGB 就 90MB, 缓存它纯属白占内存)。
_LAYER_CACHE_BYTES = [0]            # 当前占用(字节)
_LAYER_CACHE_STATS = {"hit": 0, "miss": 0, "skip": 0}   # 观测用: 命中/未命中/未入缓存(层太大)
# 按线程关闭分层缓存: 批量里每张图只渲染一次, 缓存纯空转, 还会把交互预览的层挤出去。
# 必须用 thread-local —— 批量进行时用户仍可能拖图调参, 全局开关会误伤预览线程。
_LAYER_CACHE_TLS = threading.local()
# 预览源缩略图缓存(psrc)的命中计数: 它决定"这张图第一次预览的额外开销"有没有被摊掉
_THUMB_STATS = {"hit": 0, "miss": 0}


def _cache_stats_line():
    """本次渲染期间的缓存使用情况: 增量(相对上一次落日志) + 累计 + 占用。

    只在落"渲染分段"日志时调用一次(每次渲染一行), 不在任何查找热路径上。
    计数是各线程无锁自增的, 这里读到的是近似值; 日志用途足够。
    """
    with _LAYER_CACHE_LOCK:
        cur = dict(_LAYER_CACHE_STATS)
        used_mb = _LAYER_CACHE_BYTES[0] / 1e6
    prev = _CACHE_STATS_SNAPSHOT
    dh, dm = cur["hit"] - prev[0], cur["miss"] - prev[1]
    _CACHE_STATS_SNAPSHOT[0], _CACHE_STATS_SNAPSHOT[1] = cur["hit"], cur["miss"]
    return (f" | 层缓存 +{dh}命中/+{dm}未命中 (累计 {cur['hit']}/{cur['miss']}, 占用 {used_mb:.0f}MB)"
            f" 蒙版 {_MASK_STATS['hit']}/{_MASK_STATS['miss']}"
            f" 缩略图 {_THUMB_STATS['hit']}/{_THUMB_STATS['miss']}")


_CACHE_STATS_SNAPSHOT = [0, 0]      # [层缓存累计命中, 累计未命中] 上一次落日志时的值(算增量用)


def _layer_bytes(im):
    """估算一张 PIL 图的字节数(用于预算淘汰)"""
    try:
        w, h = im.size
        ch = len(im.getbands())
        n = getattr(im, "n_frames", 1)
        return w * h * ch * n
    except Exception:
        return 0


def _layer_cached(key, build, mutable=False):
    """取缓存层; 未命中则用 build() 构造并按预算缓存。key 为 None 表示不缓存(直接构造)。

    mutable=True: 调用方**会就地修改**返回的图(如把背景当画布往上画)。此时命中缓存必须返回
    副本 —— 且必须在锁内判断, 否则"锁外查未命中 -> 期间别的线程填了同一键 -> 拿到共享对象
    直接改"会污染缓存; 未命中则直接返回新构造的对象(省掉一次无谓的 copy)。
    像素数超 _LAYER_CACHE_MAX_PX 的层不入缓存(仍会构造并返回)。"""
    if key is None or getattr(_LAYER_CACHE_TLS, "off", False):
        _LAYER_CACHE_STATS["skip"] += 1
        return build()
    with _LAYER_CACHE_LOCK:
        ent = _LAYER_CACHE.get(key)
        if ent is not None:
            _LAYER_CACHE[key] = _LAYER_CACHE.pop(key)   # LRU: 移到末尾
            _LAYER_CACHE_STATS["hit"] += 1
            return ent[0].copy() if mutable else ent[0]
    _LAYER_CACHE_STATS["miss"] += 1
    val = build()
    try:
        px = val.size[0] * val.size[1]
    except Exception:
        px = 0
    if px and px <= _LAYER_CACHE_MAX_PX:
        with _LAYER_CACHE_LOCK:
            _LAYER_CACHE[key] = (val, _layer_bytes(val))
            _LAYER_CACHE_BYTES[0] += _LAYER_CACHE[key][1]
            while _LAYER_CACHE_BYTES[0] > _LAYER_CACHE_BUDGET and len(_LAYER_CACHE) > 1:
                _, (_, nb) = _LAYER_CACHE.popitem(last=False)
                _LAYER_CACHE_BYTES[0] -= nb
        # 入缓存后返回副本给会改图的调用方(缓存里那份要保持干净)
        return val.copy() if mutable else val
    return val


def _layer_cache_clear():
    with _LAYER_CACHE_LOCK:
        _LAYER_CACHE.clear()
        _LAYER_CACHE_BYTES[0] = 0


def _src_key(input_path):
    """输入图的内容标识(路径+mtime+size)。拿不到就返回 None —— 调用方据此跳过缓存"""
    try:
        st = os.stat(input_path)
        return (os.path.normcase(os.path.abspath(input_path)), st.st_mtime_ns, st.st_size)
    except Exception:
        return None


def _build_round_mask(w, h, radius):
    ss = 4  # 超采样倍数
    # L 单通道 mask 内存便宜(1 字节/像素), 上限放宽到 1.6 亿像素(~160MB);
    # 原 8 千万上限致 24MP 以上大图(如相机原图 6000×4000)被降级到 ss=1, 完全无抗锯齿,
    # 直边转圆角处锯齿明显; 放宽后此类图至少 ss=2, 边缘经 LANCZOS 缩小变平滑
    while ss > 1 and (w * ss) * (h * ss) > 160_000_000:
        ss -= 1
    mw, mh = w * ss, h * ss
    mask = Image.new("L", (mw, mh), 0)
    ImageDraw.Draw(mask).polygon(_rounded_rect_vertices(mw, mh, radius * ss), fill=255)
    if ss > 1:
        mask = mask.resize((w, h), Image.Resampling.LANCZOS)
    return mask

def _build_circle_mask(w, h, d):
    ss = 4
    while ss > 1 and (w * ss) * (h * ss) > 160_000_000:
        ss -= 1
    mw, mh = w * ss, h * ss
    mask = Image.new("L", (mw, mh), 0)
    ImageDraw.Draw(mask).polygon(_circle_vertices(mw, mh, d * ss / 2.0), fill=255)
    if ss > 1:
        mask = mask.resize((w, h), Image.Resampling.LANCZOS)
    return mask

# 为图像添加圆角(生成 alpha 通道)
# 超采样(放大绘制后缩小, 缩小插值产生平滑边缘) + 超椭圆圆角路径(拐角曲率渐变, 视觉圆润)
def round_corners(image, radius):
    w, h = image.size
    mask = _cached_mask(("round", w, h, radius), lambda: _build_round_mask(w, h, radius))
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out

# 整图圆形(圆形档): 以短边为直径的内切圆裁剪, 长方形图也裁成正圆(长边超出部分透明)
def round_image_circle(image):
    w, h = image.size
    d = min(w, h)
    mask = _cached_mask(("circle", w, h, d), lambda: _build_circle_mask(w, h, d))
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out

# ---------- 核心处理函数(增加背景暗化参数)----------
# 按样式参数加载字体: 粗/斜/粗斜优先用独立字体文件, 缺失则回退常规/默认字体
def _load_font(size, bold, italic,
               font_path, font_bold_path, font_italic_path, font_bold_italic_path):
    paths = []
    if bold and italic:
        if font_bold_italic_path and os.path.isfile(font_bold_italic_path):
            paths.append(font_bold_italic_path)
    if bold and font_bold_path and os.path.isfile(font_bold_path):
        paths.append(font_bold_path)
    if italic and font_italic_path and os.path.isfile(font_italic_path):
        paths.append(font_italic_path)
    if font_path and os.path.isfile(font_path):
        paths.append(font_path)
    # 兜底: 传入路径失效时(如预设保存了旧 _MEI 临时目录), 用当前内置/系统字体替代,
    # 避免 fallback 到 ImageFont.load_default() 位图默认字体导致字号缩放失效.
    if not paths:
        if bold and italic:
            p = _default_font_path("Roboto-BoldItalic.ttf")
            if p:
                paths.append(p)
        if bold:
            p = _default_font_path("Roboto-Bold.ttf")
            if p:
                paths.append(p)
        if italic:
            p = _default_font_path("Roboto-Italic.ttf")
            if p:
                paths.append(p)
        p = _default_font_path("Roboto-Regular.ttf")
        if p:
            paths.append(p)
    if not paths:
        return ImageFont.load_default()
    for p in paths:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            continue
    return ImageFont.load_default()

# 计算一组水印行的总高度(含行距): 每行为 (类型, 内容, 字体, 高度), 高度取第4项
def _sec_height(sec, line_spacing):
    if not sec:
        return 0
    return sum(row[-1] for row in sec) + line_spacing * (len(sec) - 1)

# 按扩展名保存成品图: JPEG 优先携带原 EXIF(已去 Orientation), 保存失败逐级回退 PNG
def _save_output(canvas, output_path, original_exif):
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)

    ext = os.path.splitext(output_path)[1].lower()
    if ext in ('.jpg', '.jpeg'):
        try:
            canvas.save(output_path, format='JPEG', quality=100, subsampling=0,
                        exif=_strip_orientation(original_exif) if original_exif else None)
        except Exception:
            try:
                canvas.save(output_path, format='JPEG', quality=100, subsampling=0)
            except Exception:
                png_path = os.path.splitext(output_path)[0] + ".png"
                canvas.save(png_path, format='PNG', compress_level=0)
                output_path = png_path
    else:
        canvas.save(output_path, format='PNG', compress_level=0)
    return True, output_path

# 生成不覆盖已存在成品的唯一输出路径(单张模式用)
def _unique_output_path(out_root, stem, suffix="_watermarked.jpg"):
    out_path = os.path.join(out_root, f"{stem}{suffix}")
    if not os.path.exists(out_path):
        return out_path
    dup = 2
    while True:
        candidate = os.path.join(out_root, f"{stem}_watermarked_{dup}.jpg")
        if not os.path.exists(candidate):
            return candidate
        dup += 1

# 单张图片水印处理主函数：生成带 EXIF 信息水印的最终图片
def process_single(input_path, output_path, logo_dir,
                   font_path, font_bold_path, font_italic_path, font_bold_italic_path,
                   bg_color, text_color, bg_mode, blur_radius, corner_radius,
                   gap_image_top, gap_image_bottom, gap_top, gap_bottom, line_gap,
                   scale_logo, scale_model, scale_params,
                   bold_logo, italic_logo, bold_model, italic_model,
                   bold_params, italic_params,
                   canvas_ratio, image_margin, model_map,
                   corner_circle=False,
                   show_brand=True, show_model=True, show_params=True,
                   min_font_size=20,
                   low_res_enhance=True,
                   enable_image_stroke=False,
                   image_stroke_color="#ffffff",
                   image_stroke_width=5,
                   enable_text_stroke=False,
                   text_stroke_color="#ffffff",
                   text_stroke_width=1,
                   enable_image_shadow=True,
                   image_shadow_color="#000000",
                   image_shadow_offset=25,
                   image_shadow_blur=30,
                   image_shadow_alpha=0.35,
                   image_shadow_angle=135,
                   enable_text_shadow=False,
                   text_shadow_color="#000000",
                   text_shadow_offset=3,
                   text_shadow_blur=5,
                   text_shadow_alpha=0.2,
                   text_shadow_angle=135,
                   bg_darken_strength=0.0,
                   manual_exif=None,
                   layout_mode="unified",   # 水印布局模式
                   geom_out=None,    # 可选: 传入 dict, 填充间距段几何坐标(画布像素), 供预览叠加标注
                   image_out=None,   # 可选: 传入 list, 直接拿到画布 PIL 图而不落盘(调用方要内存图像时用)
                   stage_out=None,   # 可选: 传入 list, 回传本次的"渲染分段"结果(供界面显示/复用, 省得读日志)
                   stage_log=True):  # 内部参数: 是否落"渲染分段"日志(先导帧那类成对刷屏的场景可关)
    # ---- 分段计时: 渲染链路各阶段耗时, 函数结束时一次性落一条日志(排查瓶颈就靠这条) ----
    # 每次渲染只多 1 行日志、10 次 time.monotonic()(约 1µs), 不在任何热路径上逐层/逐行记。
    _t0 = time.monotonic()
    t_prev = _t0
    stages = []   # ["预处理 12ms", ...] 按序累积
    def _stage(name):
        nonlocal t_prev
        now = time.monotonic()
        stages.append(f"{name} {(now - t_prev) * 1000:.0f}ms")
        t_prev = now

    def _flush_stage_log(tag):
        """把分段结果落成一条日志(渲染末尾调用一次)。

        尾部带**参数指纹**: 排查"两次渲染为什么不一样"时, 直接对比两行末尾那段就够了。
        画布尺寸记的是 render_scale 缩回**之前**的逻辑画布;
        低清增强时最终回传的图会小 render_scale 倍, 那个尺寸在调用方的请求级日志里。
        """
        if not stage_log:
            return
        sig = (f"画布{canvas_w}x{canvas_h} {layout_mode} 比例{canvas_ratio:.4g}" if canvas_ratio > 0
               else f"画布{canvas_w}x{canvas_h} {layout_mode} 比例原图") + (
               f" bg={bg_mode}/r{blur_radius}/d{bg_darken_strength} 圆角{corner_radius}{'圆' if corner_circle else ''}"
               f" 边距{image_margin} 间距{gap_image_top}/{gap_image_bottom}/{gap_top}/{gap_bottom}/{line_gap}"
               f" 字{min_font_size_in} 缩{scale_logo}/{scale_model}/{scale_params}"
               f" 描边{int(enable_image_stroke)} 影{int(enable_image_shadow)}o{image_shadow_offset}"
               f"b{image_shadow_blur}a{image_shadow_alpha} 字描{int(enable_text_stroke)} 字影{int(enable_text_shadow)}")
        total_ms = (time.monotonic() - _t0) * 1000
        logging.info(f"[{tag}] 渲染分段: " + ", ".join(stages)
                     + f" | 合计 {total_ms:.0f}ms | {sig}{_cache_stats_line()}")
        if stage_out is not None:
            # 回给调用方: 界面把它放进"上一次渲染"的 tooltip, 不必去读日志文件
            stage_out.append({"tag": tag, "stages": list(stages), "total_ms": total_ms, "sig": sig})

    # 低清增强会放大该值, 参数指纹要记用户输入的原值
    min_font_size_in = min_font_size
    # 显式关闭原图文件句柄, 避免批量处理时句柄/内存堆积;
    # convert()/resize() 会生成新图像对象, 不受 _original.close() 影响.
    # src_key: 本次输入图的内容标识, 分层缓存的键都以它开头(换图/改文件即失效)。
    # 预览档下 input_path 是 psrc 缩略图(文件名带原图 mtime_size 做内容寻址), 所以它就是原图的标识。
    src_key = _src_key(input_path)
    original_exif = b""
    _original = Image.open(input_path)
    try:
        original_exif = _original.info.get("exif", b"")
        original = _original
        # 透明输入(RGBA/LA/P): 透明区合成画布底色, 避免 convert("RGB") 丢弃 alpha 后露出黑/白杂色
        if original.mode in ("RGBA", "LA", "P"):
            original = original.convert("RGBA")
            bg = Image.new("RGBA", original.size, bg_color + "FF")
            original = Image.alpha_composite(bg, original)
        original = original.convert("RGB")
        # H2: 物理转正像素(应用EXIF Orientation), 否则竖拍照片水印会随查看器旋转而转90°
        original = ImageOps.exif_transpose(original)
        img_w, img_h = original.size

        LOW_RES_THRESHOLD = 1500
        render_scale = 1
        if low_res_enhance and img_h < LOW_RES_THRESHOLD:
            render_scale = 2
            # 低分辨率增强时, min_font_size 应随渲染比例放大,
            # 否则最终缩回后有效字号减半, 导致预览(已按 scale2 调整)与成品观感不一致.
            min_font_size = int(min_font_size * render_scale)
            img_w *= render_scale
            img_h *= render_scale
            original_large = original.resize((img_w, img_h), Image.Resampling.LANCZOS)
        else:
            original_large = original
    finally:
        _original.close()
    _stage("预处理(解码/转正/增强)")

    # 获取 EXIF
    exif = get_exif_data_from_path(input_path)
    _stage("EXIF读取")

    # 防御性钳制: 界面仅标准比例档(均在此区间), 防脚本直调/未来回归自由输入产生极端画布
    if canvas_ratio > 0:
        canvas_ratio = min(max(canvas_ratio, 0.05), 20.0)

    margin_ratio = max(0.005, min(0.05, image_margin))
    margin_x = int(min(img_w, img_h) * margin_ratio)
    top_margin = margin_x
    base_canvas_w = img_w + 2 * margin_x

    temp_base = img_h * 0.2
    base_model_size = int(temp_base / 5.5)
    base_param_size = int(temp_base / 5.5)
    font_size_brand = max(min_font_size, int(base_param_size * scale_logo))
    font_size_model = max(min_font_size, int(base_model_size * scale_model))
    font_size_params = max(min_font_size, int(base_param_size * scale_params))

    font_brand = _load_font(font_size_brand, bold_logo, italic_logo,
                            font_path, font_bold_path, font_italic_path, font_bold_italic_path)
    font_model = _load_font(font_size_model, bold_model, italic_model,
                            font_path, font_bold_path, font_italic_path, font_bold_italic_path)
    font_params = _load_font(font_size_params, bold_params, italic_params,
                             font_path, font_bold_path, font_italic_path, font_bold_italic_path)

    special_dir = os.path.join(logo_dir, "special")
    lines = []

    if show_brand:
        make = (manual_exif.get("make") if manual_exif else None) or exif.get("make")
        brand_text = make if make else "UNKNOWN"
        brand_special_img = load_special_image(special_dir, brand_text) if make else None
        if brand_special_img:
            max_h = int(temp_base * 0.45 * scale_logo)
            ratio = max_h / brand_special_img.height
            new_w = int(brand_special_img.width * ratio)
            img = brand_special_img.resize((new_w, max_h), Image.Resampling.LANCZOS)
            lines.append(("logo", img, None, img.height))
        elif make:
            logo_file = find_logo(logo_dir, make)
            if logo_file:
                logo_max_h = int(temp_base * 0.45 * scale_logo)
                limg = load_logo(logo_file, max_height=logo_max_h)
                if limg:
                    lines.append(("logo", limg, None, limg.height))
                else:
                    h = get_text_height(font_brand, brand_text)
                    lines.append(("text", brand_text, font_brand, h))
            else:
                h = get_text_height(font_brand, brand_text)
                lines.append(("text", brand_text, font_brand, h))
        else:
            h = get_text_height(font_brand, brand_text)
            lines.append(("text", brand_text, font_brand, h))

    if show_model:
        # 优先级: 自定义水印信息 > 临时美化/映射表 > EXIF原型号
        manual_model = (manual_exif.get("model") if manual_exif else None)
        if manual_model:
            model_disp = manual_model   # 自定义水印信息最高，直接使用不再映射美化
            has_model = True
        else:
            model_raw = exif.get("model")
            model_disp = prettify_model(model_raw, model_map) if model_raw else "UNKNOWN MODEL"
            has_model = bool(model_raw)
        model_special_img = load_special_image(special_dir, model_disp) if has_model else None
        if model_special_img:
            max_h = int(temp_base * 0.45 * scale_model)
            ratio = max_h / model_special_img.height
            new_w = int(model_special_img.width * ratio)
            img = model_special_img.resize((new_w, max_h), Image.Resampling.LANCZOS)
            lines.append(("logo", img, None, img.height))
        else:
            h = get_text_height(font_model, model_disp)
            lines.append(("text", model_disp, font_model, h))

    if show_params:
        if manual_exif and any([manual_exif.get(k) for k in ("exposure", "fnumber", "iso")]):
            params = "    ".join(filter(None, [
                manual_exif.get("exposure", ""),
                manual_exif.get("fnumber", ""),
                manual_exif.get("iso", ""),
            ]))
            if not params:
                params = "NO EXIF DATA"
        else:
            exp_str = exposure_to_str(
                exif.get("exposure_time"),
                exif.get("exposure_num"),
                exif.get("exposure_den")
            )

            ap_str = aperture_to_str(exif.get("fnumber"))
            iso_str = iso_to_str(exif.get("iso"))
            params = "    ".join(filter(None, [exp_str, ap_str, iso_str]))
            if not params:
                params = "NO EXIF DATA"
        h = get_text_height(font_params, params)
        lines.append(("text", params, font_params, h))

    # ---------- 根据布局模式拆分水印行 ----------
    gap_image_top_px = int(img_h * gap_image_top)
    gap_image_bottom_px = int(img_h * gap_image_bottom)
    gap_top_px = int(img_h * gap_top)
    bottom_gap_px = int(img_h * gap_bottom)

    if layout_mode == "split_top_bottom":
        # 品牌/logo 固定在上方，型号+参数固定在下方
        if show_brand:
            above_lines = lines[:1] if lines else []
            below_lines = lines[1:]
        else:
            above_lines = []
            below_lines = lines
    else:
        above_lines = []
        below_lines = lines

    if not lines:
        min_height = top_margin + img_h + top_margin
        if canvas_ratio <= 0:
            canvas_w = base_canvas_w
            canvas_h = min_height
        else:
            ideal_h = int(base_canvas_w / canvas_ratio)
            if ideal_h < min_height:
                canvas_w = int(min_height * canvas_ratio)
                canvas_h = min_height
            else:
                canvas_w = base_canvas_w
                canvas_h = ideal_h
    else:
        line_spacing = max(0, int(img_h * line_gap))

        if layout_mode == "split_top_bottom":
            # 上下分离(镜像+居中): 上下各留一个等高中带 side_h = max(上带, 下带),
            # 画布高 = 2*边距 + 2*side_h + 图高 → 图片夹在中间始终垂直居中;
            # 矮侧边带内的余量落在外侧间距(gap_top/gap_bottom 实际渲染可略大), 内侧间距精确对应滑块
            above_content_h = _sec_height(above_lines, line_spacing)
            below_content_h = _sec_height(below_lines, line_spacing)
            side_h = max(gap_top_px + above_content_h + gap_image_top_px,
                         gap_image_bottom_px + below_content_h + bottom_gap_px)
            water_area_h = 2 * side_h
            min_height = 2 * margin_x + 2 * side_h + img_h
        elif layout_mode == "split_left_right":
            left_section = lines[:1] if show_brand else []
            right_section = lines[1:] if show_brand else lines
            left_h = _sec_height(left_section, line_spacing)
            right_h = _sec_height(right_section, line_spacing)
            side_h = max(left_h, right_h)
            water_area_h = gap_image_bottom_px + side_h + bottom_gap_px   # 水印与图片间距=gap_image_bottom, 与底部=gap_bottom
            min_height = top_margin + img_h + water_area_h
        else:
            total_text_h = _sec_height(lines, line_spacing)
            water_area_h = gap_image_bottom_px + total_text_h + bottom_gap_px
            min_height = top_margin + img_h + water_area_h

        if canvas_ratio <= 0:
            canvas_w = base_canvas_w
            canvas_h = min_height
        else:
            ideal_h = int(base_canvas_w / canvas_ratio)
            if ideal_h < min_height:
                if layout_mode == "split_top_bottom":
                    content_h = 2 * side_h + img_h
                else:
                    content_h = img_h + water_area_h
                min_height = content_h + 2 * margin_x
                canvas_w = int(min_height * canvas_ratio)
                canvas_h = min_height
            else:
                canvas_w = base_canvas_w
                canvas_h = ideal_h
                if canvas_h < min_height:
                    canvas_h = min_height

    MAX_DIM = 20000
    if canvas_w > MAX_DIM or canvas_h > MAX_DIM:
        scale_factor = min(MAX_DIM / canvas_w, MAX_DIM / canvas_h)
        canvas_w = int(canvas_w * scale_factor)
        canvas_h = int(canvas_h * scale_factor)
        margin_x = int(margin_x * scale_factor)
        top_margin = margin_x

    # 垂直居中
    _u_off = 0   # 统一下方: 比例画布高于内容时, 整块(图+水印)上移量, 供 paste/绘制共用
    if layout_mode == "split_top_bottom" and lines:
        # 等高中带: 图片上缘 = 画布中心 - 图高/2, 上下边带等高 → 图片画布垂直居中;
        # 比例画布更高时多余高度均分到两侧(上带起点 = 中心 - 图高/2 - side_h ≥ margin_x)
        above_content_h = _sec_height(above_lines, line_spacing)
        below_content_h = _sec_height(below_lines, line_spacing)
        side_h = max(gap_top_px + above_content_h + gap_image_top_px,
                     gap_image_bottom_px + below_content_h + bottom_gap_px)
        top_margin = (canvas_h - img_h) // 2 - side_h
    elif lines:
        content_bottom = top_margin + img_h + water_area_h
        extra = canvas_h - content_bottom
        if extra > 0:
            _u_off = extra // 2   # 统一下方: 记录上移量(paste 走底部锚定, 不读 top_margin)
            top_margin += _u_off  # 左右分离借 top_margin 居中(统一下方此变量对定位无效, 兼容保留)
    else:
        content_bottom = top_margin + img_h + top_margin
        extra = canvas_h - content_bottom
        if extra > 0:
            top_margin += extra // 2

    # 确保图片适配画布(极端比例如 10:1 或 1:4 时图片可能超出画布)
    avail_w = canvas_w - 2 * margin_x
    if layout_mode == "split_top_bottom" and lines:
        avail_h = canvas_h - 2 * margin_x - 2 * side_h
    elif layout_mode == "split_left_right" and lines:
        avail_h = canvas_h - top_margin - water_area_h
    elif lines:
        avail_h = img_h   # 统一下方: 图片高度固定, 仅按宽度缩放
    else:
        avail_h = canvas_h - top_margin - top_margin
    scale = min(avail_w / img_w, avail_h / img_h, 1.0)
    if scale < 1.0:
        img_w = int(img_w * scale)
        img_h = int(img_h * scale)
        original_large = original_large.resize((img_w, img_h), Image.Resampling.LANCZOS)
        # L1: 图片缩放后重算依赖 img_h 的几何, 使贴底计算(paste_y)与渲染使用一致的值
        gap_image_top_px = int(img_h * gap_image_top)
        gap_image_bottom_px = int(img_h * gap_image_bottom)
        gap_top_px = int(img_h * gap_top)
        bottom_gap_px = int(img_h * gap_bottom)
        line_spacing = max(0, int(img_h * line_gap))
        if lines and layout_mode == "split_top_bottom":
            # 图片缩放后重算边带高度, 使 paste_y/居中 与绘制阶段使用一致的值(否则布局错位)
            side_h = max(gap_top_px + _sec_height(above_lines, line_spacing) + gap_image_top_px,
                         gap_image_bottom_px + _sec_height(below_lines, line_spacing) + bottom_gap_px)
            water_area_h = 2 * side_h
        elif lines and layout_mode != "split_top_bottom":
            total_text_h = _sec_height(lines, line_spacing)
    _stage("字体/几何")

    # 生成背景(支持暗化)
    def _build_background():
        """背景层: 模糊模式下是"工作图放大回来的毛玻璃", 纯色模式就是一张纯色图。

        与画布同尺寸, 所以键里带画布尺寸 —— 调间距/文字时画布会变, 这层就命中不了;
        但改背景色/模糊半径时画布不变, 直接复用(模糊工作图那一步是整条链路最贵的)。
        暗化不在这里做(见下面的调用处): 它是最后一步的线性混合, 放在缓存之外才能让
        "拖暗化滑块"也命中这一层。"""
        if bg_mode != "blur":
            # 纯色背景不用暗化: 暗化控件仅模糊模式显示,
            # 纯色模式保持 bg_color 原色, 避免界面残留的暗化值误改纯色背景
            return Image.new("RGB", (canvas_w, canvas_h), bg_color)

        # 1/4 边长(1/16 面积)的工作图把全尺寸 3s+ 压到 <0.5s; 毛玻璃是强模糊背景,
        # 分辨率损失肉眼几乎无差; 预览/成品走同一条路径, 一致性有保证
        wk_w, wk_h = max(BLUR_WORK_MIN_SIDE, canvas_w // 4), max(BLUR_WORK_MIN_SIDE, canvas_h // 4)
        # 模糊半径按工作图比例缩放(保持相同视觉模糊程度); 下限 1px 保证始终有柔化
        wk_blur = max(1, round(blur_radius * (wk_w / canvas_w)))

        def _build_work_image():
            """模糊工作图(降采样 → 增强 → 模糊 → 噪声 → 暖白蒙版)。键只含影响这张工作图的参数;
            放大回画布那一步不在这里 —— 那一步很便宜, 且在 key 里带画布尺寸的那一层做。

            注: 噪声(effect_noise)本身随机, 缓存把它固定成"同一键内不变"—— 对调参反而更好
            (不会因为背景噪声每次抖动而看不出参数改动的效果)。"""
            x = original_large.resize((wk_w, wk_h), Image.Resampling.LANCZOS)
            x = ImageEnhance.Color(x).enhance(1.35)
            x = ImageEnhance.Contrast(x).enhance(1.08)
            x = ImageEnhance.Brightness(x).enhance(1.10)
            x = x.filter(ImageFilter.GaussianBlur(wk_blur))
            # 噪声抖动: 小图生成同样保留随机高频, 放大后仍能打破色阶带/磨砂质感
            noise = Image.effect_noise((wk_w, wk_h), 3).convert("RGB")
            x = Image.blend(x, noise, 0.02)
            # 暖白半透明蒙版(雾白玻璃)
            x = Image.blend(x, Image.new("RGB", (wk_w, wk_h), (250, 248, 245)), 0.22)
            return x

        work = _layer_cached(("bg_work", src_key, wk_w, wk_h, wk_blur), _build_work_image)
        return work.resize((canvas_w, canvas_h), Image.Resampling.LANCZOS).convert("RGB")

    # 背景层(键**不含**暗化: 暗化是最后一步单独做的, 见下)。
    # mutable=True 的语义见 _layer_cached: 命中返回副本、未命中返回新对象, 所以下面可以直接把它
    # 当作画布往上画。纯色底传 key=None 不进缓存 —— 它的 build 就是一次 fill(极便宜), 而缓存它
    # 每次命中都要拷一份同尺寸副本(预览档 11.7MP 就是 35MB), 反而更亏。
    bg = _layer_cached(
        ("bg", src_key, canvas_w, canvas_h, bg_mode, blur_radius, bg_color) if bg_mode == "blur" else None,
        _build_background, mutable=True)
    # 暗化处理(最后, 对文字可读性最重要)。放在缓存之外的理由: 拖"背景暗化"滑块时背景层仍然命中
    # —— 若把 bg_darken_strength 放进上面那个键, 每动一格都要整张重建(实测 264ms -> 368ms)。
    # 暗化是线性混合, 放在缓存层之外与放在里面**逐像素等价**(同尺寸同输入, 同样的 blend)。
    if bg_darken_strength > 0:
        canvas = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), bg_darken_strength)
    else:
        canvas = bg
    _stage("背景层")

    paste_x = (canvas_w - img_w) // 2
    paste_y = top_margin
    if layout_mode == "split_top_bottom" and lines:
        paste_y = top_margin + side_h   # 图片上缘 = 上带底(图片画布垂直居中)
    elif layout_mode == "split_left_right":
        pass  # 左右分离保持 paste_y = top_margin
    elif lines:
        # 统一下方: 默认图片+水印整体贴底(gap_image_bottom/gap_bottom 精确对应);
        # 画布高于内容时整块上移 _u_off 垂直居中(与左右/上下布局视觉一致)
        water_bottom = canvas_h - bottom_gap_px
        paste_y = water_bottom - total_text_h - gap_image_bottom_px - img_h - _u_off
        if paste_y < 0:
            paste_y = 0

    # 图片外观层(圆角/正圆): 开销随图像尺寸二次增长(超采样 + LANCZOS 缩回), 而它只依赖
    # 图片与圆角参数 —— 调间距/文字/背景时键不变, 直接命中。display_img 同时是阴影层的形状来源
    display_img = original_large
    circle_mode = corner_circle   # 圆形开关: 整图以短边为直径裁成正圆(长边超出部分透明)
    if circle_mode:
        display_img = _layer_cached(("img_round", src_key, img_w, img_h, "circle"),
                                    lambda: round_image_circle(original_large))
    elif corner_radius > 0:
        display_img = _layer_cached(("img_round", src_key, img_w, img_h, "round", corner_radius),
                                    lambda: round_corners(original_large, corner_radius))
    _stage("图片外观(圆角/正圆)")
    # 图片投影(水墨晕开): 图片形状模糊后作为 alpha, 阴影从内容边缘向外渐变晕开,
    # 而非实心偏移的硬投影; 阴影层需带 padding 外扩区, 否则 Pillow 模糊不超出图像
    # 边界, 无法形成向外晕开渐变(blur 只影响圆角边缘, 调不出效果)
    if enable_image_shadow and image_shadow_offset > 0:
        ox, oy = _shadow_offset(image_shadow_offset, image_shadow_angle)
        pad = image_shadow_blur * 2 + 4   # 晕开外扩区
        cw, ch = canvas.size
        # 限制偏移使阴影完整落在画布内: 图片贴边时向画布内收缩, 避免硬切截断
        if ox > 0:
            ox = min(ox, max(cw - (paste_x + img_w) - pad - 4, 0))
        elif ox < 0:
            ox = max(ox, -(paste_x - pad - 4))
        if oy > 0:
            oy = min(oy, max(ch - (paste_y + img_h) - pad - 4, 0))
        elif oy < 0:
            oy = max(oy, -(paste_y - pad - 4))
        shape = display_img.split()[3] if (circle_mode or corner_radius > 0) else Image.new("L", (img_w, img_h), 255)
        sw_, sh_ = img_w + pad * 2, img_h + pad * 2

        def _build_shadow():
            """形状模糊 + 着色。与画布无关(偏移的钳制按画布算, 不参与缓存键, 在下面做)"""
            shape_p = Image.new("L", (sw_, sh_), 0)
            shape_p.paste(shape, (pad, pad))
            blurred = (shape_p if image_shadow_blur <= 0
                       else shape_p.filter(ImageFilter.GaussianBlur(image_shadow_blur)))
            s = Image.new("RGBA", (sw_, sh_), image_shadow_color)
            s.putalpha(blurred.point(lambda v: int(v * image_shadow_alpha)))
            return s

        sh = _layer_cached(
            ("img_shadow", src_key, img_w, img_h, pad, image_shadow_blur,
             image_shadow_color, image_shadow_alpha, circle_mode, corner_radius),
            _build_shadow)
        canvas.paste(sh, (paste_x + ox - pad, paste_y + oy - pad), sh)
    _stage("图片阴影层")
    canvas.paste(display_img, (paste_x, paste_y), display_img if (circle_mode or corner_radius > 0) else None)

    # 图片内描边
    # Pillow ImageDraw 的 outline 直绘无抗锯齿: 圆角描边硬边叠加在已抗锯齿的圆角图片上,
    # 会露出"两种锯齿叠加"(直边转圆角处尤明显); 圆角时描边改用超采样——
    # 放大画轮廓 mask(L 单通道)后 LANCZOS 缩小, 边缘产生平滑半透明过渡, 与图片圆角一致
    if enable_image_stroke and image_stroke_width > 0:
        rect = (paste_x, paste_y, paste_x + img_w, paste_y + img_h)
        if circle_mode or corner_radius > 0:
            def _build_stroke():
                """描边层(4x 超采样多边形 + LANCZOS 缩回, 单通道 mask 上色)。
                与画布无关 —— 只依赖图片尺寸/圆角/描边参数, 调间距文字背景时键不变"""
                ss = 4
                while ss > 1 and ((img_w + image_stroke_width) * ss) * ((img_h + image_stroke_width) * ss) > 160_000_000:
                    ss -= 1
                out_w, out_h = img_w + image_stroke_width, img_h + image_stroke_width
                if circle_mode:
                    # 圆形描边: 内切圆向内外各扩 W/2 的圆环(圆心居中)
                    mw, mh = out_w * ss, out_h * ss
                    rr = min(img_w, img_h) * ss / 2.0
                    half = image_stroke_width * ss / 2.0
                    edge = Image.new("L", (mw, mh), 0)
                    d = ImageDraw.Draw(edge)
                    d.polygon(_circle_vertices(mw, mh, rr + half), fill=255)
                    d.polygon(_circle_vertices(mw, mh, max(0.0, rr - half)), fill=0)
                else:
                    # 描边 = 图片边缘向内外各扩 W/2: 外缘圆角矩形(尺寸向外扩 W, 半径 R+W/2) 填充 255,
                    # 挖空内缘圆角矩形(尺寸向内缩 W, 半径 R-W/2)。尺寸差保证直边处描边带不空;
                    # 大圆角(超过短边一半退化为椭圆)时内外缘椭圆长短半轴各差 W/2, 描边带仍存在。
                    # 此前外缘/内缘共用同一尺寸只改半径, 直边完全重叠被挖空, 圆角>0 时描边消失
                    sw_, sh_ = out_w * ss, out_h * ss
                    iw_, ih_ = max(2, (img_w - image_stroke_width) * ss), max(2, (img_h - image_stroke_width) * ss)
                    half = image_stroke_width * ss / 2.0
                    r_out = (corner_radius + image_stroke_width / 2.0) * ss
                    r_in = max(0.0, (corner_radius - image_stroke_width / 2.0) * ss)
                    edge = Image.new("L", (sw_, sh_), 0)
                    d = ImageDraw.Draw(edge)
                    d.polygon(_rounded_rect_vertices(sw_, sh_, r_out), fill=255)
                    ox, oy = (sw_ - iw_) // 2, (sh_ - ih_) // 2
                    d.polygon([(x + ox, y + oy) for x, y in _rounded_rect_vertices(iw_, ih_, r_in)], fill=0)
                # 图层含外扩 W/2(外缘在图片外), resize/paste 用外扩尺寸并偏移 W/2
                if ss > 1:
                    edge = edge.resize((out_w, out_h), Image.Resampling.LANCZOS)
                lay = Image.new("RGBA", (out_w, out_h), image_stroke_color)
                lay.putalpha(edge)
                return lay

            stroke_layer = _layer_cached(
                ("img_stroke", src_key, img_w, img_h, image_stroke_width, image_stroke_color,
                 circle_mode, corner_radius),
                _build_stroke)
            canvas.paste(stroke_layer,
                         (paste_x - image_stroke_width // 2, paste_y - image_stroke_width // 2),
                         stroke_layer)
        else:
            # 直角图: ImageDraw 直绘(无抗锯齿但有硬边, 与直角图片一致), 开销可忽略
            draw = ImageDraw.Draw(canvas)
            draw.rectangle(rect, outline=image_stroke_color, width=image_stroke_width)
    _stage("图片描边层")

    _geom = {"gaps": {"image_margin": [], "gap_top": [], "gap_image_top": [],
                      "gap_image_bottom": [], "gap_bottom": [], "line_gap": []}}
    if lines:
        draw = ImageDraw.Draw(canvas)
        line_spacing = max(0, int(img_h * line_gap))
        bottom_gap_px = int(img_h * gap_bottom)
        gap_image_top_px = int(img_h * gap_image_top)
        gap_image_bottom_px = int(img_h * gap_image_bottom)
        gap_top_px = int(img_h * gap_top)   # 绘制阶段重算, 供左右/上下布局的间距使用

        def _render_line(typ, content, fnt, h, cx, cy, align="center", shadow_clip_bottom=None):
            """渲染单行水印，align: center / left / right；左右对齐以图片边线为基准"""
            if typ == "logo":
                lw = content.width
                if align == "left":
                    px = paste_x                       # 图片左缘
                elif align == "right":
                    px = paste_x + img_w - lw          # 图片右缘
                else:
                    px = cx - lw // 2
                canvas.paste(content, (px, int(cy)), content)
            else:
                bbox = draw.textbbox((0, 0), content, font=fnt)
                tw = bbox[2] - bbox[0]
                if align == "left":
                    tx = paste_x
                elif align == "right":
                    tx = paste_x + img_w - tw
                else:
                    tx = cx - tw // 2
                draw_text(draw, canvas, (tx, cy), content, fnt, text_color,
                          stroke_enable=enable_text_stroke,
                          stroke_color=text_stroke_color,
                          stroke_width=text_stroke_width,
                          shadow_enable=enable_text_shadow,
                          shadow_color=text_shadow_color,
                          shadow_offset=text_shadow_offset,
                          shadow_blur=text_shadow_blur,
                          shadow_alpha=text_shadow_alpha,
                          shadow_angle=text_shadow_angle,
                          shadow_clip_bottom=shadow_clip_bottom)

        cx = canvas_w // 2

        if layout_mode == "split_top_bottom":
            # 等高中带渲染: 上方组贴图(品牌底到图顶 = gap_image_top 精确), 下方组贴图
            # (图底到型号顶 = gap_image_bottom 精确); 矮侧/比例画布的余量落在外侧,
            # 图片由画布几何保证垂直居中
            above_content_h = _sec_height(above_lines, line_spacing)
            below_content_h = _sec_height(below_lines, line_spacing)
            cy = paste_y - gap_image_top_px - above_content_h
            above_ys = []
            for i, (typ, content, fnt, h) in enumerate(above_lines):
                clip = cy + h + line_spacing if i < len(above_lines) - 1 else paste_y
                _render_line(typ, content, fnt, h, cx, cy, "center", clip)
                above_ys.append((cy, cy + h))
                cy += h + line_spacing
            below_y = paste_y + img_h + gap_image_bottom_px   # 图底到下方水印顶 = gap_image_bottom
            cy = below_y
            below_ys = []
            for i, (typ, content, fnt, h) in enumerate(below_lines):
                clip = cy + h + line_spacing if i < len(below_lines) - 1 else None
                _render_line(typ, content, fnt, h, cx, cy, "center", clip)
                below_ys.append((cy, cy + h))
                cy += h + line_spacing
            # 几何标注(实际渲染距离): 内侧两段=滑块值; 外侧两段含矮侧余量/画布边界
            if geom_out is not None:
                gap_top_end = above_ys[0][0] if above_ys else min(top_margin, paste_y - gap_image_top_px)
                _geom["gaps"]["gap_top"].append((0, gap_top_end))
                gap_image_top_start = above_ys[-1][1] if above_ys else paste_y - gap_image_top_px
                _geom["gaps"]["gap_image_top"].append((gap_image_top_start, paste_y))
                _geom["gaps"]["gap_image_bottom"].append((paste_y + img_h, below_y))
                if below_ys:
                    _geom["gaps"]["gap_bottom"].append((below_ys[-1][1], canvas_h))
                    for (y0, _), (y1, _) in zip(below_ys, below_ys[1:]):
                        _geom["gaps"]["line_gap"].append((y0, y1))

        elif layout_mode == "split_left_right":
            # 下方：品牌靠左，型号+参数靠右
            left_section = lines[:1] if show_brand else []
            right_section = lines[1:] if show_brand else lines
            left_h = _sec_height(left_section, line_spacing)
            right_h = _sec_height(right_section, line_spacing)
            bottom_h = max(left_h, right_h)
            base_y = paste_y + img_h + gap_image_bottom_px   # 水印与图片间距 = gap_image_bottom

            cy = base_y + (bottom_h - left_h) // 2
            for i, (typ, content, fnt, h) in enumerate(left_section):
                clip = cy + h + line_spacing if i < len(left_section) - 1 else None
                _render_line(typ, content, fnt, h, cx, cy, "left", clip)
                cy += h + line_spacing

            cy = base_y + (bottom_h - right_h) // 2
            right_ys = []
            for i, (typ, content, fnt, h) in enumerate(right_section):
                clip = cy + h + line_spacing if i < len(right_section) - 1 else None
                _render_line(typ, content, fnt, h, cx, cy, "right", clip)
                right_ys.append((cy, cy + h))
                cy += h + line_spacing
            # 几何标注: gap_image_bottom=图底→水印区顶; gap_bottom=水印区底→画布底
            if geom_out is not None:
                _geom["gaps"]["gap_image_bottom"].append((paste_y + img_h, base_y))
                _geom["gaps"]["gap_bottom"].append((base_y + bottom_h, canvas_h))
                for (y0, _), (y1, _) in zip(right_ys, right_ys[1:]):
                    _geom["gaps"]["line_gap"].append((y0, y1))

        else:
            # 默认/统一下方：水印区贴画布底部, 但预留 descender/描边安全区,
            # 避免 bottom_gap=0 时文字笔画贴到画布边缘被切(显示不全)
            fnt_last = lines[-1][2]
            desc = fnt_last.getmetrics()[1] if fnt_last else 0
            bottom_safe = desc + (text_stroke_width if enable_text_stroke else 0)
            water_start_y = canvas_h - bottom_gap_px - bottom_safe - _sec_height(lines, line_spacing) - _u_off
            cy = water_start_y
            all_ys = []
            for i, (typ, content, fnt, h) in enumerate(lines):
                # 阴影 clip 到下一行上边界, 避免阴影投到下一行文字上被遮挡(看起来截断)
                clip = cy + h + line_spacing if i < len(lines) - 1 else None
                _render_line(typ, content, fnt, h, cx, cy, "center", clip)
                all_ys.append((cy, cy + h))
                cy += h + line_spacing
            # 几何标注: gap_image_bottom=图底→水印区顶; gap_bottom=水印区底→画布底
            if geom_out is not None:
                _geom["gaps"]["gap_image_bottom"].append((paste_y + img_h, water_start_y))
                if all_ys:
                    # gap_bottom 只记用户设的 bottom_gap_px 部分(画布底向上的 bottom_safe 留给
                    # descender/描边安全区, 不计入高亮带; 三布局 split_tb/split_lr/unified
                    # 高亮带高度统一与滑块值一致)
                    _geom["gaps"]["gap_bottom"].append((all_ys[-1][1], all_ys[-1][1] + bottom_gap_px))
                    for (y0, _), (y1, _) in zip(all_ys, all_ys[1:]):
                        _geom["gaps"]["line_gap"].append((y0, y1))
    _stage("水印文字绘制")

    if render_scale > 1:
        canvas = canvas.resize((round(canvas_w / render_scale),
                                round(canvas_h / render_scale)),
                               Image.Resampling.LANCZOS)
        _stage("低清缩回")

    # 收集几何坐标(供预览叠加间距标注): _geom 记录的是 render_scale 放大前坐标,
    # 需按最终画布尺寸归一化(canvas 可能被 MAX_DIM 或 render_scale 缩放)
    if geom_out is not None and any(_geom["gaps"].values()):
        fx = canvas.size[0] / canvas_w
        fy = canvas.size[1] / canvas_h
        geom_out["canvas"] = canvas.size
        geom_out["img"] = (round(paste_x * fx), round(paste_y * fy),
                           round((paste_x + img_w) * fx), round((paste_y + img_h) * fy))
        geom_out["gaps"] = {
            k: [(round(y0 * fy), round(y1 * fy)) for (y0, y1) in v]
            for k, v in _geom["gaps"].items()
        }

    if image_out is not None:
        # 调用方直接要内存图像: 跳过编码、写盘与读回解码
        image_out.append(canvas)
        _flush_stage_log("预览")
        return True, None
    ret = _save_output(canvas, output_path, original_exif)
    _stage("编码保存")
    _flush_stage_log("成品")
    return ret


# ---------- 日志文件配置(按时间戳命名，保留最近 3 份)----------
LOG_DIR = BASE_DIR
ensure_dir(LOG_DIR)

# 清理旧日志：只保留最近 3 份 log-*.log
def _cleanup_old_logs():
    log_files = []
    for fname in os.listdir(LOG_DIR):
        if fname.startswith("log-") and fname.endswith(".log"):
            fpath = os.path.join(LOG_DIR, fname)
            if os.path.isfile(fpath):
                log_files.append((os.path.getmtime(fpath), fpath))
    log_files.sort(key=lambda x: x[0], reverse=True)  # 最新的在前
    for _, fpath in log_files[3:]:  # 保留最近 3 份(含当前), 更早的删除
        try:
            os.remove(fpath)
        except Exception:
            pass

_cleanup_old_logs()

# 启动期清理 _save_json 写 tmp 残留(进程崩溃/断电时 .tmp.<pid>.<uuid> 可能留在 DATA_DIR;
# 同目录扫 *.tmp* 兼容本方案命名, 不误删其他文件)
def _cleanup_tmp_files():
    if not os.path.isdir(DATA_DIR):
        return
    for name in os.listdir(DATA_DIR):
        if ".tmp" in name:
            try:
                os.remove(os.path.join(DATA_DIR, name))
            except Exception:
                pass

_cleanup_tmp_files()

# 当前日志文件：log-YYYYMMDD_HHMMSS.log
LOG_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"log-{LOG_TIMESTAMP}.log")

# 时间戳带毫秒: 拖放/预览这类时序问题必须能对齐到毫秒
# 线程名也在格式里: 同时有 界面/预览线程/事件线程/生成线程 四个来源在写同一份日志,
# 出问题时先看是谁写的(线程名由各线程自己设, 见 RenderWorker 与 xsy_qt/render.py)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s.%(msecs)03d [%(levelname)s] [%(threadName)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    filename=LOG_FILE, filemode='w', encoding='utf-8')


# ===== 手动 EXIF 填充的格式化(自动补前缀/后缀) =====
# 通用模板: 去空白 -> 空串返空 -> 已含前缀/后缀则原样返回 -> 否则自动补上
def _fmt_wrap(v, prefix="", suffix="", check=None):
    v = v.strip()
    if not v:
        return ""
    return v if (check and check(v)) else f"{prefix}{v}{suffix}"

def _fmt_iso(v):
    return _fmt_wrap(v, prefix="ISO", check=lambda s: s.upper().startswith("ISO"))

def _fmt_fnumber(v):
    return _fmt_wrap(v, prefix="f/",
                     check=lambda s: s.startswith("f/") or s.startswith("F/"))

def _fmt_exposure(v):
    return _fmt_wrap(v, suffix="s", check=lambda s: s.endswith("s"))

def _format_manual_exif(manual_exif):
    if not manual_exif:
        return None
    return {
        "make": (manual_exif.get("make") or "").strip(),
        "model": (manual_exif.get("model") or "").strip(),
        "exposure": _fmt_exposure(manual_exif.get("exposure") or ""),
        "fnumber": _fmt_fnumber(manual_exif.get("fnumber") or ""),
        "iso": _fmt_iso(manual_exif.get("iso") or ""),
    }


# ===== 默认参数(与旧版"恢复默认设置"一致) =====
def default_params():
    return {
        "logo_dir": DEFAULT_LOGO_DIR,
        "font_path": _default_font_path("Roboto-Regular.ttf"),
        "font_bold_path": _default_font_path("Roboto-Bold.ttf"),
        "font_italic_path": _default_font_path("Roboto-Italic.ttf"),
        "font_bold_italic_path": _default_font_path("Roboto-BoldItalic.ttf"),
        "canvas_ratio_mode": "original",
        "gap_image_top": 0.04,
        "gap_image_bottom": 0.04,
        "gap_top": 0.04,
        "gap_bottom": 0.04,
        "line_gap": 0.02,
        "image_margin": 0.015,
        "show_brand": True,
        "show_model": True,
        "show_params": True,
        "bg_color": "#FFFFFF",
        "text_color": "#383a42",
        "bg_mode": "color",
        "blur_radius": 150,
        "corner_radius": 0,
        "corner_circle": False,
        "scale_logo": 0.7,
        "scale_model": 0.5,
        "scale_params": 0.3,
        "bold_logo": True,
        "italic_logo": False,
        "bold_model": False,
        "italic_model": True,
        "bold_params": False,
        "italic_params": True,
        "min_font_size": 20,
        "low_res_enhance": True,
        "enable_image_stroke": False,
        "image_stroke_color": "#ffffff",
        "image_stroke_width": 5,
        "enable_text_stroke": False,
        "text_stroke_color": "#ffffff",
        "text_stroke_width": 1,
        "enable_image_shadow": True,
        "image_shadow_color": "#000000",
        "image_shadow_offset": 25,
        "image_shadow_blur": 30,
        "image_shadow_alpha": 0.35,
        "image_shadow_angle": 135,
        "enable_text_shadow": False,
        "text_shadow_color": "#000000",
        "text_shadow_offset": 3,
        "text_shadow_blur": 5,
        "text_shadow_alpha": 0.2,
        "text_shadow_angle": 135,
        "bg_darken_strength": 0,
        "layout_mode": "unified",
    }


# ===== 后台处理线程 =====
class RenderWorker(threading.Thread):
    def __init__(self, mode, params, events, stop_event,
                 input_path=None, output_path=None, original_name=None,
                 file_paths=None, raw_model=None, custom_model=None,
                 save_map_flag=False, manual_exif=None, batch_out_dir=None):
        super().__init__(daemon=True)
        self.name = "生成线程"        # 进日志的 %(threadName)s: 与预览/界面线程区分开(日志格式里带线程名)
        self.mode = mode
        self.params = params
        self.events = events
        self.stop_event = stop_event
        self.input_path = input_path
        self.output_path = output_path
        self.batch_out_dir = batch_out_dir or None
        self.original_name = original_name
        self.file_paths = file_paths or []
        self.raw_model = raw_model
        self.custom_model = custom_model
        self.save_map_flag = save_map_flag
        self.manual_exif = manual_exif
        self.logo_dir = params.get("logo_dir", "")

    def run(self):
        try:
            if self.mode == "single":
                self._process_single()
            else:
                # 批量里每张图都只渲染一次, 分层缓存纯空转, 还会把交互预览的层挤出去 -> 本线程关掉它。
                # thread-local 且本线程跑完这批就退出(见 RenderWorker 的用法), 无需复位;
                # 若以后改成线程池复用, 必须在 finally 里复位。
                _LAYER_CACHE_TLS.off = True
                self._process_batch()
        except Exception as e:
            logging.error(f"处理异常: {e}")
            self.events.put({"type": "log", "msg": f"处理出错: {e}", "level": "ERROR"})
            self.events.put({"type": "finished", "message": str(e), "success": False})

    def _process_single(self):
        _inject_model_map(self.params, self.raw_model, self.custom_model, ensure_map=True)
        if self.raw_model and self.custom_model and self.custom_model != self.raw_model and self.save_map_flag:
            model_map = load_model_map(self.logo_dir)
            model_map[self.raw_model] = self.custom_model
            save_model_map(self.logo_dir, model_map)

        logging.info(f"单张处理: {self.input_path}")
        self.events.put({"type": "log", "msg": "处理中...", "level": "INFO"})
        t0 = time.monotonic()
        try:
            success, final_path = process_single(self.input_path, self.output_path,
                                                 manual_exif=self.manual_exif, **self.params)
        except Exception as e:
            logging.error(f"单张处理失败: {e}")
            self.events.put({"type": "log", "msg": f"处理失败: {e}", "level": "ERROR"})
            self.events.put({"type": "finished", "message": str(e), "success": False})
            return
        if success:
            one_ms = (time.monotonic() - t0) * 1000
            logging.info(f"单张完成: {final_path} (耗时 {one_ms:.0f}ms)")
            self.events.put({"type": "log", "msg": f"已完成: {self.original_name}", "level": "INFO"})
            self.events.put({"type": "finished", "message": f"已完成: {os.path.basename(final_path)}",
                             "success": True, "download_path": final_path, "ms": one_ms, "count": 1,
                             "download_name": os.path.basename(final_path),   # PNG 回退时与真实扩展名一致
                             "out_dir": os.path.dirname(os.path.abspath(final_path))})
        else:
            self.events.put({"type": "finished", "message": "处理失败", "success": False})

    def _process_batch(self):
        files = self.file_paths  # [{path, name}, ...]
        if not files:
            self.events.put({"type": "log", "msg": "没有可处理的图片", "level": "WARNING"})
            self.events.put({"type": "finished", "message": "没有可处理的图片", "success": False})
            return

        # 与单张对称的兜底: 界面参数集恒含 model_map, 但缺失时批量会 TypeError(单张有 _inject_model_map 注入)
        self.params.setdefault("model_map", {})

        out_dir = self.batch_out_dir or PREVIEW_DIR   # 批量子目录(导出文件夹/批量水印_<时间戳>)
        total = len(files)
        success = 0
        errors = []
        used_names = set()   # 输出名去重: 不同子目录同名图片不互相覆盖
        t_batch = time.monotonic()
        logging.info(f"批量处理: 共 {total} 个文件 -> {out_dir}")
        for idx, item in enumerate(files, 1):
            if self.stop_event.is_set():
                logging.info(f"用户取消: 已完成 {success}/{total}")
                self.events.put({"type": "finished",
                                 "message": f"用户取消：已完成 {success}/{total}", "success": False,
                                 "out_dir": out_dir})   # 与成功/失败分支一致, 界面可打开已导出目录
                return
            fpath = item.get("path", "")
            fname = item.get("name", os.path.basename(fpath))
            stem = os.path.splitext(fname)[0]
            out_name = f"{stem}_watermarked.jpg"
            dup = 2
            while out_name in used_names:
                out_name = f"{stem}_watermarked_{dup}.jpg"
                dup += 1
            used_names.add(out_name)
            out_path = os.path.join(out_dir, out_name)
            logging.info(f"[{idx}/{total}] 处理: {fname} -> {out_name}")
            self.events.put({"type": "progress", "done": idx, "total": total, "name": fname})
            t_one = time.monotonic()
            try:
                process_single(fpath, out_path, **self.params)
                success += 1
                # 每张的耗时: 批量慢时一眼看出是某张特别大还是整体退化
                logging.info(f"[{idx}/{total}] 完成: {fname} (耗时 {(time.monotonic() - t_one) * 1000:.0f}ms)")
            except Exception as e:
                err_msg = f"{fname} -> {e}"
                logging.warning(f"处理失败 {fpath}: {e}")
                errors.append(err_msg)
        # 构建结果消息
        batch_ms = (time.monotonic() - t_batch) * 1000
        avg = f", 平均 {batch_ms / success:.0f}ms" if success else ""
        result_msg = f"批量处理完成：{success}/{total} -> {out_dir}"
        logging.info(f"批量总耗时 {batch_ms:.0f}ms (成功 {success}/{total}{avg})")
        if errors:
            fail_detail = "\n失败详情："
            for err in errors[:5]:
                fail_detail += f"\n  {err}"
            if len(errors) > 5:
                fail_detail += f"\n  ... 及其他 {len(errors) - 5} 个文件"
            result_msg += fail_detail
        logging.info(result_msg)
        self.events.put({"type": "log", "msg": result_msg, "level": "INFO" if success == total else "WARNING"})
        self.events.put({"type": "finished", "message": result_msg, "success": success == total,
                         "out_dir": out_dir, "ms": batch_ms, "count": success})


# ===== 应用状态 =====
class AppState:
    def __init__(self):
        self.settings = _repair_font_paths(load_settings())   # 字体路径失效时自动回退新资源目录
        self.events = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()
        self.generate_lock = threading.Lock()   # 串行化 start_generate 检查+启动, 防 TOCTOU
        self._rendering_count = 0   # 渲染中计数(多窗口并发渲染互不干扰), >0 时新预览返回旧图
        self._render_lock = threading.Lock()   # 保护 _rendering_count 原子增减(多线程桥接下防漂移)
        self._sel_lock = threading.Lock()
        self._pv_seq = 0        # 预览文件名自增序号
        # 预览缩略图缓存: {(原文件绝对路径, mtime_ns, size): (src 路径, 原图高度)}
        self._thumb_cache = {}
        self._thumb_lock = threading.Lock()
        self._thumb_gen_lock = threading.Lock()   # 串行化缩略图生成(预热线程 vs 预览请求)
        # 本次会话用户给过的输出目录(导出文件夹不再持久化, 见 Api.save_settings):
        # 供 _allowed_open_dir 白名单判断「打开所在目录」是否放行
        self.user_dirs = set()
        ensure_dir(PREVIEW_DIR)
        # _clean_preview() 已移至 main() 后台线程执行, 不阻塞窗口出现
        self.exit_reason = None  # 退出原因(页面点退出/Ctrl+C), 供退出日志打印

    def _clean_preview(self):
        try:
            if os.path.isdir(PREVIEW_DIR):
                for f in os.listdir(PREVIEW_DIR):
                    try:
                        os.remove(os.path.join(PREVIEW_DIR, f))
                    except Exception:
                        pass
        except Exception:
            pass

    def _is_allowed_input(self, p):
        """输入路径校验。

        本副本只服务调用方: 路径来自原生文件对话框或系统拖放回调, **没有"不可信调用方"
        理由是"调用方不能凭一个字符串读任意文件", 在这里不再提供任何安全价值, 反而变成功能
        约束 —— 以后想加「最近打开」「命令行传路径」都会被静默拒绝。所以只保留最基本的
        "存在且是文件"; 图片格式由 _sniff_image_type 在入口处单独判定。

        """
        try:
            return bool(p) and os.path.isfile(p)
        except Exception:
            return False

    def _sniff_image_type(self, p):
        """按文件头魔数识别图片真实类型, 返回扩展名('.jpg'/'.webp'等)或 None(非受支持图片)。
        魔数优先于扩展名, 上传时据此反推真实扩展名, 防伪造扩展名导致 Content-Type 错配"""
        if not p or not os.path.isfile(p):
            return None
        try:
            with open(p, "rb") as f:
                head = f.read(16)
        except Exception:
            return None
        if head.startswith(b"\xff\xd8\xff"):
            return ".jpg"                # JPEG
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"                # PNG
        if head.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"                # GIF
        if head.startswith(b"BM"):
            return ".bmp"                # BMP
        if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
            return ".webp"               # WEBP
        if head.startswith((b"II*\x00", b"MM\x00*")):
            return ".tif"                # TIFF
        if head.startswith(b"\x00\x00\x01\x00"):
            return ".ico"                # ICO
        return None

    def _is_supported_image(self, p):
        """校验文件头魔数是否为受支持的图片格式(与 _sniff_image_type 同一魔数表)"""
        return self._sniff_image_type(p) is not None

    def _allowed_open_dir(self, p):
        """校验 open_dir 目标目录是否在白名单内(导出目录/PREVIEW_DIR), 返回规范化绝对路径或 None。
        open_dir 可被界面「打开所在目录」调用, 若不限制可诱导打开任意路径"""
        if not p:
            return None
        try:
            absp = os.path.abspath(p)
            target = absp if os.path.isdir(absp) else os.path.dirname(absp)
        except Exception:
            return None
        roots = [PREVIEW_DIR, DEFAULT_LOGO_DIR]
        out_dir = (self.settings.get("output_dir") or "").strip()
        if out_dir:
            roots.append(out_dir)   # 历史遗留值(新版本已不再持久化 output_dir)
        with self._sel_lock:
            roots.extend(self.user_dirs)   # 本次会话实际用过的导出目录(见 start_generate)
        target_nc = os.path.normcase(target)
        for base in roots:
            if not base:
                continue
            base_nc = os.path.normcase(os.path.abspath(base))
            # 目标 == 根目录 或 目标在根目录的子目录内
            if target_nc == base_nc or target_nc.startswith(base_nc + os.sep):
                return target
        return None

    # ---------- EXIF / 型号 ----------
    def read_exif(self, path):
        if not path or not os.path.isfile(path):
            return {"make": None, "model": None}
        try:
            exif = get_exif_data_from_path(path)
        except Exception:
            exif = {"make": None, "model": None}
        return {"make": exif.get("make"), "model": exif.get("model")}

    # ---------- 型号映射 ----------
    def get_model_map(self, logo_dir):
        return {"map": load_model_map(logo_dir or DEFAULT_LOGO_DIR)}

    def save_model_map(self, logo_dir, map_dict):
        save_model_map(logo_dir or DEFAULT_LOGO_DIR, map_dict or {})
        return {"ok": True}

    # ---------- 收藏颜色(全局, 存 settings.json, 与预设互不影响) ----------
    def get_custom_colors(self):
        return {"colors": self.settings.get("custom_colors") or []}

    def save_custom_colors(self, colors):
        arr = [str(c) for c in (colors or [])][:10]
        self.settings["custom_colors"] = arr
        save_settings(self.settings)
        return {"ok": True, "colors": arr}

    # ---------- 预设 ----------
    def load_presets(self):
        presets = _load_json(PRESETS_FILE)
        # 迁移旧预设中指向 _MEI 临时目录的失效字体路径到持久目录
        changed = False
        for key, p in presets.items():
            if not isinstance(p, dict):
                continue
            for fkey, fname in FONT_SETTING_MAP:
                cur = p.get(fkey)
                if not cur or not os.path.isfile(cur):
                    newp = _default_font_path(fname)
                    if newp and p.get(fkey) != newp:
                        p[fkey] = newp
                        changed = True
        if changed:
            self.save_presets(presets)
            logging.info("presets 字体路径已迁移到持久目录")
        return presets

    def save_presets(self, presets):
        _save_json(PRESETS_FILE, presets, ensure_path=DATA_DIR)

    def preset_save(self, key, params, name):
        presets = self.load_presets()
        params = dict(params)
        params["_preset_name"] = name.strip() or f"方案 {key}"
        presets[str(key)] = params
        self.save_presets(presets)
        return {"ok": True}

    def preset_delete(self, key):
        presets = self.load_presets()
        presets.pop(str(key), None)
        self.save_presets(presets)
        return {"ok": True}

    # ---------- 处理 ----------
    def start_generate(self, req):
        self.generate_lock.acquire()   # 串行化 检查+创建+启动, 消除并发请求的 TOCTOU
        try:
            if self.worker and self.worker.is_alive():
                return {"ok": False, "error": "已有任务正在处理，请先等待完成或停止"}
            mode = req.get("mode", "single")
            params = req.get("params") or {}
            manual_exif = _format_manual_exif(req.get("manual_exif"))
            self.stop_event = threading.Event()
            self.events = queue.Queue()
            # 导出文件夹: 请求优先(界面未保存的改动即时生效), 否则取持久化设置, 最后兜底临时目录
            out_root = ((req.get("output_dir") or self.settings.get("output_dir") or "").strip()
                        or PREVIEW_DIR)
            # 记下本次会话用过的导出目录: output_dir 不再持久化, 「打开所在目录」的白名单靠它
            with self._sel_lock:
                self.user_dirs.add(os.path.normcase(os.path.abspath(out_root)))
            if mode == "single":
                in_p = (req.get("input_path") or "").strip()
                original_name = (req.get("original_name") or os.path.basename(in_p)).strip()
                if not in_p or not os.path.isfile(in_p) or not self._is_allowed_input(in_p):
                    return {"ok": False, "error": "请先选择一张输入图片"}
                # 提前建目录: 没权限/只读时立刻报错(而不是渲染完才在保存时失败), 界面弹顶部提示
                try:
                    os.makedirs(out_root, exist_ok=True)
                except Exception as e:
                    return {"ok": False, "error": f"导出目录不可写：{out_root}（{e}）"}
                stem = os.path.splitext(original_name)[0]
                out_p = _unique_output_path(out_root, stem)
                out_name = os.path.basename(out_p)
                self.worker = RenderWorker(
                    "single", params, self.events, self.stop_event,
                    input_path=in_p, output_path=out_p, original_name=out_name,
                    raw_model=req.get("raw_model"), custom_model=req.get("custom_model"),
                    save_map_flag=bool(req.get("save_map_flag")), manual_exif=manual_exif)
            else:
                file_paths = req.get("file_paths") or []
                if not file_paths:
                    return {"ok": False, "error": "请先选择要批量处理的图片"}
                # 与单张模式同等安全策略: 只接受上传副本或"用户亲手交出的"原文件(见 _is_allowed_input)
                validated = []
                for item in file_paths:
                    if not isinstance(item, dict):
                        return {"ok": False, "error": "批量文件格式错误"}
                    p = (item.get("path") or "").strip()
                    if not p or not os.path.isfile(p) or not self._is_allowed_input(p):
                        return {"ok": False, "error": "批量文件路径无效（请重新选择或拖入）"}
                    validated.append({"path": p, "name": item.get("name") or os.path.basename(p)})
                file_paths = validated
                # 批量在导出文件夹下建独立子目录(不打包 zip), 与单张成品隔离
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")   # 毫秒级, 防同一秒两次批量撞同一子目录
                batch_out = os.path.join(out_root, f"批量水印_{ts}")
                try:
                    os.makedirs(batch_out, exist_ok=True)
                except Exception as e:
                    return {"ok": False, "error": f"无法创建导出子目录: {e}"}
                self.worker = RenderWorker("batch", params, self.events, self.stop_event,
                                              file_paths=file_paths, batch_out_dir=batch_out)
            self.worker.start()
            return {"ok": True}
        finally:
            self.generate_lock.release()

    def stop(self):
        self.stop_event.set()
        return {"ok": True}

    # ---------- 预览(临时目录, 程序退出后清除) ----------
    def _write_preview_src(self, im, tag="", max_side=PREVIEW_MAX_SIDE):
        """把已打开的图缩略到 max_side 并落成 psrc_<tag>.jpg(全帧 2500) / psrcf_<tag>.jpg(先导帧 1200),
        返回 (src 路径, 原图高度)。
        文件名带 tag(原文件 mtime_size) 做内容寻址——换图不会覆盖别的图的缓存; 不带 pv:
        缩略图只由原文件内容决定, 各页面可以共享。quality 90: 预览只看构图/间距, 省编码时间。
        保存时保留原图 EXIF, 否则预览读不到相机参数(全部显示 unknown); 无 EXIF 时不传
        max_side<=0 视为不缩略(全量档), 保持原尺寸。"""
        exif = im.info.get("exif", b"")
        src_h = im.size[1]
        if max_side > 0:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        prefix = "psrc" if max_side <= 0 or max_side >= PREVIEW_MAX_SIDE else "psrcf"
        src = os.path.join(PREVIEW_DIR, f"{prefix}_{tag}.jpg" if tag else f"{prefix}_gen.jpg")
        save_kw = {"format": "JPEG", "quality": 90}
        if exif:
            save_kw["exif"] = exif
        im.convert("RGB").save(src, **save_kw)
        return src, src_h

    def _preview_src_for(self, input_path, src_image=None, max_side=PREVIEW_MAX_SIDE):
        """返回该原图对应的预览缩略图 (src 路径, 原图高度)。

        max_side 进缓存 key(现在只用 PREVIEW_MAX_SIDE 一档; 留这个形参是为了以后要分档时
        两档不会互相顶掉缓存)。
        命中缓存(原文件 mtime+size 未变)就直接复用、**不重读原图** —— 调参时同一张图会被反复预览,
        本地盘省一次解码, 网络盘省一次完整读盘; 未命中才生成并回填。
        _thumb_gen_lock: 预热线程与预览请求可能同时走到"未命中", 避免并发写同一个缩略图文件"""
        try:
            st = os.stat(input_path)
            key = (os.path.normcase(os.path.abspath(input_path)), st.st_mtime_ns, st.st_size, max_side)
            tag = f"{int(st.st_mtime)}_{st.st_size}"
        except Exception:
            key, tag = None, ""
        if key is not None:
            with self._thumb_lock:
                ent = self._thumb_cache.get(key)
            if ent and os.path.isfile(ent[0]):
                _THUMB_STATS["hit"] += 1
                logging.info(f"预览: 命中缩略图缓存 {os.path.basename(ent[0])}")
                return ent
        with self._thumb_gen_lock:
            if key is not None:   # 等锁期间可能已被预热线程生成好
                with self._thumb_lock:
                    ent = self._thumb_cache.get(key)
                if ent and os.path.isfile(ent[0]):
                    _THUMB_STATS["hit"] += 1
                    logging.info(f"预览: 命中缩略图缓存(等锁后) {os.path.basename(ent[0])}")
                    return ent
            if src_image is not None:
                src, src_h = self._write_preview_src(src_image, tag, max_side)
            else:
                with Image.open(input_path) as im:
                    src, src_h = self._write_preview_src(im, tag, max_side)
            _THUMB_STATS["miss"] += 1
            if key is not None:
                with self._thumb_lock:
                    if len(self._thumb_cache) > 8:   # 预览源通常就一张, 留少量最近项即可
                        self._thumb_cache.clear()
                    self._thumb_cache[key] = (src, src_h)
        logging.info(f"预览: 生成缩略图 {os.path.basename(src)} (src_h={src_h})")
        return src, src_h


    def _render_preview(self, input_path, params, manual_exif, raw_model, custom_model,
                        out_path, preview_scale=None, pv="", geom_out=None, src_image=None,
                        image_out=None, stage_out=None, max_side=PREVIEW_MAX_SIDE):
        """统一渲染入口: preview_scale 给定时先缩略输入图(实时预览), 否则原图全量(目前无调用方,
        为预览/其它入口保留); geom_out 给定时收集间距段几何(供界面预览图叠加标注);
        src_image 给定时复用它(调用方已打开的句柄); 缩略图本身有 (mtime,size) 缓存, 命中不读原图;
        stage_out 给定时回传"渲染分段"结果(见 process_single 的 _flush_stage_log)"""
        if preview_scale:
            src, src_h = self._preview_src_for(input_path, src_image, max_side)
            input_path = src
            # 成品全分辨率渲染的 2x 增强系数(与 process_single 内部判断一致)
            scale2 = 2 if params.get("low_res_enhance", True) and src_h < 1500 else 1
            # 预览相对成品的渲染宽度比: 所有绝对像素参数按此等比缩放, 使预览观感与成品一致
            # (圆角/模糊半径/字号下限/描边宽都是像素绝对值, 不缩放则预览小图上显得更大更粗)
            ratio = preview_scale / scale2
            params = dict(params)
            # 缩略预览分辨率低: 关闭 2x 低分辨率增强(仅正式输出需要), 显著降低实时预览耗时
            params["low_res_enhance"] = False
            params["min_font_size"] = max(8, int(params.get("min_font_size", 20) * ratio))
            params["corner_radius"] = max(0, int(params.get("corner_radius", 0) * ratio))
            params["image_stroke_width"] = max(0, int(params.get("image_stroke_width", 5) * ratio))
            params["text_stroke_width"] = max(0, int(params.get("text_stroke_width", 1) * ratio))
            params["blur_radius"] = max(0, int(params.get("blur_radius", 10) * ratio))
            params["image_shadow_offset"] = max(0, int(params.get("image_shadow_offset", 25) * ratio))
            params["image_shadow_blur"] = max(0, int(params.get("image_shadow_blur", 30) * ratio))
            params["text_shadow_offset"] = max(0, int(params.get("text_shadow_offset", 3) * ratio))
            params["text_shadow_blur"] = max(0, int(params.get("text_shadow_blur", 5) * ratio))
            # 阴影方向是角度, 与分辨率无关, 不缩放
        # 与生成一致: 注入临时型号美化映射(仅本次生效, 不写映射表)
        params = _inject_model_map(params, raw_model, custom_model, copy_on_inject=True)
        return process_single(input_path, out_path,
                              manual_exif=_format_manual_exif(manual_exif),
                              geom_out=geom_out, image_out=image_out, stage_out=stage_out,
                              **params)




    def make_preview(self, req):
        """实时预览: 输入图缩略到 PREVIEW_MAX_SIDE 快速渲染, **直接把画布 PIL 图回给调用方**。

        按名字缓存这回事, 也没有多标签页并发预览, 整套属于纯陪跑。

        """
        input_path = (req.get("input_path") or "").strip()
        if not input_path or not os.path.isfile(input_path) or not self._is_allowed_input(input_path):
            return {"ok": False, "error": "请先选择一张输入图片"}
        params = req.get("params") or {}
        geom_out = {}
        image_out = []
        stage_out = []          # 渲染分段结果: 随 resp 回给调用方(界面 tooltip 用), 成本可忽略
        with self._render_lock:
            self._rendering_count += 1
        try:
            # 只开一次原图: 尺寸计算与缩略图生成共用同一句柄(原来为拿尺寸单开一次, 网络盘上白读一遍)
            with Image.open(input_path) as im:
                w, h = im.size
                long_side = max(w, h) or 1
                # 画布固定按 2500 档渲染(原项目行为)。**不要为了"上屏 1:1"去缩小画布**:
                # 画布小的时候 PIL 得把水印字按更小的字号画上去(实测 8px 档), 再缩到屏幕上
                # 反而比"按 25px 画、再面积平均缩小"更糊 —— 大画布 + 好滤波器对文字更有利。
                #
                # PREVIEW_MAX_SIDE=0 时走全量: preview_scale=None 让 _render_preview 直接用
                # 原图渲(既不缩图也不落 JPEG 中间层), 各像素参数的 ratio 也就等于 1 ——
                # 此时预览与成品逐像素同源, 用来判断"发虚"是渲染端还是显示端造成的。
                max_side = PREVIEW_MAX_SIDE
                preview_scale = None
                if max_side > 0:
                    scale = max_side / long_side
                    preview_scale = scale if scale < 1.0 else None
                else:
                    logging.info(f"预览: 全量加载 {w}x{h}(不缩略, 单次渲染会明显变慢)")
                success, _ = self._render_preview(
                    input_path, params, req.get("manual_exif"),
                    req.get("raw_model"), req.get("custom_model"),
                    None, preview_scale=preview_scale,
                    geom_out=geom_out, src_image=im, image_out=image_out,
                    stage_out=stage_out, max_side=max_side)
        except Exception as e:
            logging.error(f"预览生成失败: {e}")
            return {"ok": False, "error": f"预览生成失败: {e}"}
        finally:
            with self._render_lock:
                self._rendering_count -= 1
        if not success or not image_out:
            return {"ok": False, "error": "预览生成失败"}
        resp = {"ok": True, "image": image_out[0],
                # 全量档(max_side=0)报真实画布边长, 免得调用方与日志看到一个 0
                "max_side": max_side or max(image_out[0].size)}
        if geom_out:
            resp["geom"] = geom_out   # 间距段坐标, 预览图叠加标注用
        if stage_out:
            resp["stage"] = stage_out[0]   # 渲染分段: 界面显示 + 请求级日志复用, 不必读日志文件
        return resp

    # ---------- 清理临时文件(程序退出时) ----------
    def cleanup(self):
        try:
            if os.path.isdir(PREVIEW_DIR):
                shutil.rmtree(PREVIEW_DIR, ignore_errors=True)
        except Exception:
            pass


# ===== 目录扫描(批量处理用) =====
def _list_images_in_dir(folder, recursive=False):
    """列出目录内的候选文件; recursive=True 时连子目录一起走。按文件名排序, 是否图片由调用方按魔数判定"""
    out = []
    try:
        if recursive:
            for root, _dirs, names in os.walk(folder):
                for nm in sorted(names):
                    out.append(os.path.join(root, nm))
        else:
            for nm in sorted(os.listdir(folder)):
                p = os.path.join(folder, nm)
                if os.path.isfile(p):
                    out.append(p)
    except Exception as e:
        logging.warning(f"列出目录失败 {folder}: {e}")
    return out
















if __name__ == "__main__":
    main()
