"""渲染内核：相机参数水印渲染。

仅依赖 Pillow 与 exifread，不依赖界面框架；界面层位于 `src/ui/`，单向依赖本包。
字体与图标资源位于 `src/assets/`（与包目录平级），路径见 app.ASSETS_DIR / app.BUNDLED_FONT_DIR。
"""

__version__ = "1.0.0"
