"""路径与持久化: settings / 预设 / 型号映射的读写与目录准备, 以及导入期的环境初始化。"""
from .config import (BUNDLED_FONT_DIR, DATA_DIR, FONT_SETTING_MAP, LOG_DIR, LOG_FILE,
                     PERSISTENT_FONT_DIR, PRESETS_DIR, SETTINGS_FILE)
import json
import logging
import os
import shutil
import subprocess
import sys
import uuid


def preset_path(slot):
    # 返回槽位(1..10)对应的预设文件路径
    return os.path.join(PRESETS_DIR, f"presets{int(slot)}.json")


# 系统字体目录列表(按平台惯例), 字体解析顺序的最后兜底
def _system_font_dirs():
    if os.name == 'nt':
        return [r"C:\Windows\Fonts"]
    if sys.platform == 'darwin':
        return ["/System/Library/Fonts", "/Library/Fonts",
                os.path.join(os.path.expanduser('~'), 'Library', 'Fonts')]

    xdg_root = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return [os.path.join(xdg_root, 'fonts'),
            os.path.join(os.path.expanduser('~'), '.fonts'),
            "/usr/share/fonts", "/usr/local/share/fonts"]


# 均未命中时返回空串, 由调用方回退默认字体
def _default_font_path(filename):
    for base in (PERSISTENT_FONT_DIR, BUNDLED_FONT_DIR) + tuple(_system_font_dirs()):
        p = os.path.join(base, filename)
        if os.path.isfile(p):
            return p
    return ""


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def open_path(path):
    # 跨平台打开目录/文件: Windows 使用资源管理器, macOS 使用 Finder, Linux 使用 xdg-open
    if os.name == 'nt':
        os.startfile(path)
    elif sys.platform == 'darwin':
        subprocess.run(['open', path])
    else:
        subprocess.run(['xdg-open', path])


# 读取 JSON 文件: 文件不存在或解析失败时返回 {}, check_file=True 时只认普通文件, log_func=None 时失败静默
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


# 写入 JSON 文件(自动创建父目录): 先写带 pid 与 uuid 的临时文件, 再用 os.replace 原子替换, 并发写以最后一次为准
# 不做 fsync: 写频率低, 可接受进程崩溃时丢失最后一次写入
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
        # 原子替换失败时(临时文件写失败/权限/跨分区): 清理残留临时文件并降级为直接写入。
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


# 加载设置文件; 文件不存在或解析失败时返回空字典。
def load_settings():
    return _load_json(SETTINGS_FILE, log_func=logging.error, fail_prefix="加载设置文件失败")


# 保存设置字典到 JSON 文件。
def save_settings(settings):
    _save_json(SETTINGS_FILE, settings, ensure_path=DATA_DIR)


def _ensure_persistent_fonts():
    # 把内置字体从 BUNDLED_FONT_DIR 同步到 PERSISTENT_FONT_DIR, 返回是否至少确保了一个字体存在
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
    # 先确保内置字体已落到持久化目录, 使后续修复指向稳定路径。
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


# 加载指定 Logo 目录下的型号映射文件 model_map.json; 文件不存在或解析失败时返回空字典。
def load_model_map(logo_dir):
    map_path = os.path.join(logo_dir, "model_map.json")
    return _load_json(map_path, check_file=True, log_func=logging.warning,
                      fail_prefix="加载型号映射文件失败")


# 保存型号映射字典到指定 Logo 目录下的 model_map.json。
def save_model_map(logo_dir, map_dict):
    _save_json(os.path.join(logo_dir, "model_map.json"), map_dict, ensure_path=logo_dir)


# 清理旧日志: 只保留最近 3 份 log-*.log
def _cleanup_old_logs():
    log_files = []
    for fname in os.listdir(LOG_DIR):
        if fname.startswith("log-") and fname.endswith(".log"):
            fpath = os.path.join(LOG_DIR, fname)
            if os.path.isfile(fpath):
                log_files.append((os.path.getmtime(fpath), fpath))
    log_files.sort(key=lambda x: x[0], reverse=True)  # 最新的在前
    for _, fpath in log_files[3:]:
        try:
            os.remove(fpath)
        except Exception:
            pass


# 启动期清理: 扫 DATA_DIR 下的 *.tmp* 残留(_save_json 的临时文件命名)
def _cleanup_tmp_files():
    if not os.path.isdir(DATA_DIR):
        return
    for name in os.listdir(DATA_DIR):
        if ".tmp" in name:
            try:
                os.remove(os.path.join(DATA_DIR, name))
            except Exception:
                pass


# 导入期初始化: 导入本包时执行一次(见 core/__init__.py 对本模块的导入)
# Windows 控制台默认编码为 GBK, 重定向后中文输出会乱码, 故统一改为 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
ensure_dir(LOG_DIR)
_cleanup_old_logs()
_cleanup_tmp_files()
# 时间戳带毫秒, 便于对齐时序问题; 格式含线程名, 便于区分四个写日志线程的来源
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s.%(msecs)03d [%(levelname)s] [%(threadName)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    filename=LOG_FILE, filemode='w', encoding='utf-8')
