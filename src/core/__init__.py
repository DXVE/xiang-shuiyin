"""渲染内核: 相机参数水印渲染。

仅依赖 Pillow 与 exifread, 不依赖界面框架; 字体与图标在包外的 src/assets/, 路径见 config.ASSETS_DIR。
"""

from . import paths   # 导入本包即完成环境初始化(建日志目录、轮转旧日志、清理临时残留、配置日志)

__version__ = "1.0"
