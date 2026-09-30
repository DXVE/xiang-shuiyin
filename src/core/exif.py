"""EXIF 与型号: exifread 主路径加 Pillow 兜底, 型号映射注入、展示串格式化与 Logo 查找。"""
from PIL import Image
import exifread
import logging
import os


def _parse_ratio(value):
    # 把 EXIF 有理数解析为 (分子, 分母, 浮点值): 非分数形式返回 (None, None, float), 无法解析返回 (None, None, None)
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


# 用 exifread 解析品牌/型号/快门/光圈/ISO, 结果原地写入 result 字典。
def _read_exif_exifread(image_path, result, debug=False):
    try:
        with open(image_path, 'rb') as f:
            tags = exifread.process_file(f, details=True)
        if debug:
            print("=== All EXIF tags ===")
            for tag in sorted(tags.keys()):
                print(f"{tag}: {tags[tag]}")


        if 'Image Make' in tags:
            result['make'] = str(tags['Image Make']).strip()
        if 'Image Model' in tags:
            result['model'] = str(tags['Image Model']).strip()


        if 'EXIF FNumber' in tags:
            num, den, fl = _parse_ratio(tags['EXIF FNumber'])
            if fl is not None:
                result['fnumber'] = fl
                if debug:
                    print(f"FNumber: {fl}")

        # 优先采用 ShutterSpeedValue(APEX 值)换算快门, 精度最高。
        if 'EXIF ShutterSpeedValue' in tags:
            _, _, apex = _parse_ratio(tags['EXIF ShutterSpeedValue'])
            if apex is not None and apex != 0:
                exp_time = 2 ** (-apex)
                result['exposure_time'] = exp_time
                if debug:
                    print(f"ShutterSpeedValue -> {exp_time} ({1/exp_time:.1f})")

        # 无 ShutterSpeedValue 时回退到 ExposureTime。
        if result['exposure_time'] is None and 'EXIF ExposureTime' in tags:
            num, den, fl = _parse_ratio(tags['EXIF ExposureTime'])
            if fl is not None:
                result['exposure_time'] = fl
                if num is not None and den is not None:
                    result['exposure_num'] = num
                    result['exposure_den'] = den
                if debug:
                    print(f"ExposureTime: {fl} ({num}/{den})")


        if 'EXIF ISOSpeedRatings' in tags:
            try:
                result['iso'] = int(float(str(tags['EXIF ISOSpeedRatings'])))
                if debug:
                    print(f"ISO: {result['iso']}")
            except Exception:
                pass

        if debug:
            print("=== exifread extracted ===", result)
        # 此处不提前返回: 始终继续执行下文的 Pillow 补漏, 避免 exifread 部分成功(缺光圈/ISO 等)时字段丢失。
    except Exception as e:
        # 须记录到日志而非 print: 否则 EXIF 读取失败时错误信息不可见, 相应字段会静默保持 unknown。
        logging.warning(f"EXIF: exifread 读取失败({type(e).__name__}: {e}), 走 Pillow 回退")


# Pillow 兜底补全: 仅填 None, 不覆盖已有值
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


# 解码 bytes 并剔除不可打印字符; 其它类型返回 None
def _str(val):
    if isinstance(val, bytes):
        s = val.decode("utf-8", errors="ignore")
        s = ''.join(ch for ch in s if ch.isprintable() or ch == ' ')
        return s.strip()
    if isinstance(val, str):
        s = ''.join(ch for ch in val if ch.isprintable() or ch == ' ')
        return s.strip()
    return None


# 转换失败返回 None
def _number(val):
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None


def exposure_to_str(exp=None, num=None, den=None):
    # 快门显示口径与 Windows 文件属性一致: 优先用 ShutterSpeedValue 换算, 缺省时用 ExposureTime 求值
    # >=1s 保留一位小数; <1s 转为 round(1/value) 的 1/x 形式; 取不到或非正值返回空串
    if exp is not None:
        value = exp
    elif num is not None and den is not None and den != 0:
        value = num / den
    else:
        return ""

    if value <= 0:
        return ""
    if value >= 1:
        return f"{value:.1f}s"
    else:
        x = int(round(1.0 / value))
        if x <= 0:
            return ""
        return f"1/{x}s"


def aperture_to_str(f):
    if f is None:
        return ""
    if abs(f - round(f)) < 0.01:
        return f"f/{int(round(f))}"
    else:
        return f"f/{f:.1f}"


def iso_to_str(i):
    if i is None:
        return ""
    return f"ISO{int(i)}"


# 注入临时型号映射(仅本次生效, 不写映射表): copy_on_inject 先拷贝以免污染调用方对象, ensure_map 保证键存在
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


def prettify_model(model, model_map):
    if not model:
        return model
    return model_map.get(model, model)


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


def load_logo(path, max_height):
    try:
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        new_h = int(max_height)
        new_w = int(w * new_h / h)
        return img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    except Exception:
        return None
