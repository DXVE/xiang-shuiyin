"""主窗常量: 布局节奏、预设槽位、撤销上限与运行期阈值。"""
from PySide6.QtCore import QT_TRANSLATE_NOOP
from core.config import MAX_PRESET_SLOTS, SLOW_RENDER_MS

# 间距标注的心跳周期(ms): 周期过短会持续唤醒事件循环, 过长则响应迟滞
_HOVER_POLL_MS = 80

# 预览换帧的淡入时长(ms): 换帧统一按这个时长补间, 不宜过长, 否则等于让用户看着旧帧慢慢消失
_PV_FADE_MS = 200

# 槽位 1..10, 每槽一个文件(presets1..10): 文件名只承载槽位号, 回避非法字符与重名
_PRESET_SLOTS = MAX_PRESET_SLOTS
# 预设中的绝对路径键：本机存储时保留（同机存取可还原字体），仅在导出时剥离，使导出文件与机器无关。
_PRESET_MACHINE_KEYS = ("logo_dir", "font_path", "font_bold_path",
                        "font_italic_path", "font_bold_italic_path")
# 导出文件里的格式标记: _apply_params 会忽略未知键, 所以它不影响加载, 只用来让将来能识别。
_PRESET_FORMAT = "xiangshuiyin.preset/1"
# 仅用于生成导出时的默认文件名（用户可修改），不参与任何识别。
ILLEGAL_FN_CHARS = set(chr(92) + '/:*?"<>|')

# 撤销/重做: 只保留最近 N 步; 同一控件的连续改动在停手 _UNDO_COALESCE_MS 后合并为一步
_UNDO_LIMIT = 100
_UNDO_COALESCE_MS = 600

TABS = [("files", QT_TRANSLATE_NOOP("MainWindow", "文件与美化")),
        ("font", QT_TRANSLATE_NOOP("MainWindow", "字体与画布")),
        ("effects", QT_TRANSLATE_NOOP("MainWindow", "水印与效果"))]

# 持久化时剥离的键：输入/输出路径不落盘（model_map 存放于 logo_dir 下的 model_map.json）。
_NOT_PERSISTED = {"logo_dir", "input_path", "output_path", "batch_dir", "output_dir", "model_map"}
# 竖向滚动条占位宽度, 必须与 theme.QSS 里 QScrollBar:vertical 的 width 一致(_segbar_row 据此留占位)
_SB_W = 14

# 初始尺寸按屏幕可用区(已扣任务栏)的百分比算, 不写死像素; 留边 _WIN_EDGE px 保证四边不贴边
_WIN_W_RATIO, _WIN_H_RATIO, _WIN_EDGE = 0.8, 0.9, 100
# 左列占比(左 4.5 : 右 5.5)。窗口缩放时按此比例重排; 用户手动拖过分隔条后不再自动分配。
_SPLIT_LEFT = 0.45

# 慢调用阈值(ms): 超过则把请求日志降级为 WARNING; 这条线只定义在核心(SLOW_RENDER_MS), 两处不得各写一个数
_SLOW_RENDER_MS = SLOW_RENDER_MS

# 字体样式：每行一种文字(显示 / 粗体 / 斜体 三列)
FONT_STYLE_COLS = ("显示", "粗体", "斜体")
FONT_STYLE_GROUPS = [
    ("Logo 文字", ("show_brand", "bold_logo", "italic_logo")),
    ("型号文字", ("show_model", "bold_model", "italic_model")),
    ("参数文字", ("show_params", "bold_params", "italic_params")),
]
