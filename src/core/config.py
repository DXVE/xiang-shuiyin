"""内核常量: 目录与文件位置、预览与渲染阈值、缓存上限(无副作用, 只做路径计算)。"""
import os
import sys
import tempfile
from datetime import datetime


# 预览/上传临时文件目录: 由 main.py 通过 XSY_PREVIEW_DIR 指定, 程序退出时清理
PREVIEW_DIR = os.environ.get("XSY_PREVIEW_DIR") or os.path.join(tempfile.gettempdir(), "XiangShuiyin_preview_qt")

# 实时预览缩略渲染的最大边长(px): 0 表示全量加载(不缩图, 画布/字号/圆角/模糊半径均按原图尺寸计算), 仅用于画质排查
PREVIEW_MAX_SIDE = 3400

# 先导帧缩略渲染的最大边长(px): 首次看一张大图时先按这一档渲一帧低分辨率画面即时上屏, 全分辨率帧随后覆盖
PREVIEW_FAST_MAX_SIDE = 1200


# 背景模糊工作图的最小边长(px): 工作图边长为画布的 1/4, 模糊/噪声/增强在小图上完成后再放大回画布, 设下限避免小画布下画质损失过大
BLUR_WORK_MIN_SIDE = 400


# 工作图边长的量化网格(px): 让画布尺寸的细微变化(拖间距/字号)落在同一网格内, 工作图可复用而不必重做增强/模糊/噪声; 代价是工作图与画布宽高比最多差一个网格
_BLUR_WORK_STEP = 128


# 低清增强(2 倍超采样)的触发阈值(px): 输入图高度低于它时按 2 倍渲染
LOW_RES_THRESHOLD = 1500


# "偏慢"的分界线(ms): 预览一帧超过它才算慢, 核心据此决定是否为该帧落"渲染分段"日志, 界面侧引用同一个常量
SLOW_RENDER_MS = 800


if os.name == 'nt':
    _data_root = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
elif sys.platform == 'darwin':
    _data_root = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support')
else:
    _data_root = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
# 数据目录: 由 main.py 通过 XSY_BASE_DIR 指定
BASE_DIR = os.environ.get("XSY_BASE_DIR") or os.path.join(_data_root, "XiangShuiyin-QT6")
DATA_DIR = os.path.join(BASE_DIR, "watermark_data")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
# 预设目录: 10 个固定槽位, 一槽一文件(见 preset_path)
PRESETS_DIR = os.path.join(DATA_DIR, "presets")
PRESETS_FILE = os.path.join(DATA_DIR, "presets.json")   # 旧整份格式文件, 仅供一次性迁移使用。
DEFAULT_LOGO_DIR = os.path.join(DATA_DIR, "logos")

MAX_PRESET_SLOTS = 10


# 资源目录: 不在包内, 位于包目录的上一级 src/assets/(与 core、ui 平级)
# 冻结(PyInstaller)后 __file__ 指向 PYZ 内的虚拟路径, 资源定位以 _MEIPASS 兜底
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLE_ROOT = getattr(sys, "_MEIPASS", None)
ASSETS_DIR = (os.path.join(_BUNDLE_ROOT, "assets") if _BUNDLE_ROOT
              else os.path.join(os.path.dirname(SCRIPT_DIR), "assets"))
BUNDLED_FONT_DIR = os.path.join(ASSETS_DIR, "fonts")
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")


# settings 的字体路径键与其内置默认字体文件名的对应(键 -> 文件名)
FONT_SETTING_MAP = (
    ("font_path", "SarasaGothicSC-Regular.ttf"),
    ("font_bold_path", "SarasaGothicSC-Bold.ttf"),
    ("font_italic_path", "SarasaGothicSC-Italic.ttf"),
    ("font_bold_italic_path", "SarasaGothicSC-BoldItalic.ttf"),
)

# 内置字体的持久副本目录: 使 settings/presets 中保存的字体路径在冻结运行下长期有效
PERSISTENT_FONT_DIR = os.path.join(DATA_DIR, "fonts", "bundled")

_MASK_CACHE_MAX = 3          # 只留最近几项
_MASK_CACHE_MAX_PX = 16_000_000   # 超过约 16MP 的 mask 不缓存

_LAYER_CACHE_BUDGET = 128 << 20     # 层数据总量上限(字节): 超出时按最久未使用淘汰
                                    # 预览一套层约 15MB(背景/图片/阴影/描边各一张), 128MB 约可容纳 8 套
_LAYER_CACHE_MAX_PX = 16_000_000    # 单层像素上限
                                    # 取 16MP: 足以容纳各比例下的预览画布(21:9 约 11.7MP),
                                    # 同时把成品全分辨率图层(约 29.9MP)排除在缓存外。


# 日志目录: 在 import 阶段建立, 供下方 filemode='w' 写入
LOG_DIR = os.path.join(BASE_DIR, "logs")


# 当前日志文件: log-YYYYMMDD_HHMMSS.log
LOG_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"log-{LOG_TIMESTAMP}.log")
