# 第三方组件与许可

「镶 · 水印」 水印处理工具在运行、打包与对外分发时涉及的第三方组件与许可义务如下。本文件是**索引与义务清单**，不复制各许可的全文；**对外分发时（含发给朋友、内网传播），除本文件外还必须随附各组件的版权声明与许可全文** —— 这对 LGPLv3、MIT-CMU、BSD-3-Clause 都是硬要求。

## 运行时依赖

| 组件 | 版本 | 许可 | 说明 |
|---|---|---|---|
| **PySide6**（Qt for Python）与 **shiboken6** | 6.11.2 | **LGPL-3.0-only**（亦提供 GPL-2.0-only / GPL-3.0-only 与商业许可，本项目取 LGPLv3） | 以**动态链接**方式使用；运行期只用到 Essentials 范围内的模块（`QtCore` / `QtGui` / `QtWidgets` / `QtNetwork`）。LGPLv3 要求随附**许可全文**与 Qt 的版权声明，并保证使用者能够**替换**该库（见文末两条待办）。**注意**：PyPI 的 wheel 只带 `LicenseRef-Qt-Commercial.txt`（商业许可引用，0.5 KB），**不含 LGPLv3 全文**，必须另行获取。 |
| **Pillow** | 12.3.0 | MIT-CMU | 图像处理（预览、水印合成、取色）。wheel 内带 `licenses/LICENSE`（76 KB）。 |
| **exifread** | 3.5.1 | BSD-3-Clause | 读取照片 EXIF。版权行：`Copyright (c) 2002-2007 Gene Cash`、`Copyright (c) 2007-2025 Ianaré Sévi and contributors`。wheel 内带 `licenses/LICENSE`。 |
| **Python 运行时** | 3.14 | PSF-2.0 | PyInstaller 会把解释器运行库打进产物，因此也属于随包分发的内容；全文见 Python 安装目录的 `LICENSE.txt`。 |

> 版本号取自实际安装环境（`*.dist-info/METADATA`）；`PySide6` 的许可标识为 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`。

## 打包期工具与产物内的第三方代码

| 组件 | 版本 | 许可 | 说明 |
|---|---|---|---|
| **PyInstaller** | 6.22.3 | GPL-2.0-or-later + 特殊例外 | 只作构建工具，不进运行时依赖；但它的 **bootloader 会被嵌入生成的 exe**，例外条款允许用它打包非自由程序，随附其 `COPYING.txt` 更稳妥。 |
| **Inno Setup** | 6.7.3 | Inno Setup License（正文允许任何用途，含商业应用） | 只作构建工具；但**产物（Windows 安装器）内含其安装引导程序**，属随包分发的第三方代码，其许可文本已随附于 `licenses/Inno-Setup-License.txt`。官网在下载页与编译器启动时另提示「商业使用需购买许可」，与该许可正文不完全一致，商业使用口径需自行确认。仓库内另存有官方简体中文语言文件（`packaging/i18n/ChineseSimplified.isl`），仅用于编译安装器。 |
| **Windows VC/UCRT 运行库** | 随 Python | 微软可再分发条款 | 产物内可能含 `vcruntime140.dll` 等，按微软再分发条款随产物分发即可。 |

## 内置字体

| 文件 | 许可 | 来源 |
|---|---|---|
| `src/assets/fonts/SarasaGothicSC-{Regular,Bold,Italic,BoldItalic}.ttf` | **SIL OFL 1.1** | 由「更纱黑体 Sarasa Gothic」1.0.41（Sarasa Gothic SC）**子集化**而来（名称与版权原样保留） |

- **OFL 全文**：`src/assets/fonts/OFL.txt` —— 与上游仓库 `be5invis/Sarasa-Gothic` 的 `LICENSE` **逐字符一致**（113 行，已核对）。
- **来源与改动说明**：`src/assets/fonts/NOTICE.txt`（子集范围、名称保留理由、重建方法）。
- **版权行**（子集版本一并保留，也写在字体自身的 name 表里）：

      Copyright (c) 2015-2025, Renzhi Li (aka. Belleve Invis, belleve@typeof.net).
      Portions Copyright (c) 2016 The Inter Project Authors.
      Portions Copyright (c) 2014-2021 Adobe Systems Incorporated (http://www.adobe.com/),
      with Reserved Font Name 'Source'.
      Portions Copyright (c) 2012 Google Inc.

- **OFL 义务要点**：随软件分发 / 嵌入 / 与软件一起出售都允许；**必须附版权声明与许可全文**（即上面两个文件）；**不得单独出售**字体本身；**修改版不得使用保留名**。上游 OFL 声明的保留名只有 `'Source'`（来自 Adobe 的 Source Han Sans），**不含** `Sarasa Gothic`，因此子集化后沿用族名 `Sarasa Gothic SC` 合规。
- 这两个文件位于 `src/assets/fonts/`，处于打包 spec 的 `datas` 映射范围（`src/assets` → 产物内 `assets/`）内，因此**打包产物里同样带有** OFL 全文与来源说明。

## 程序图标与标识

- `src/assets/icons/*.svg`、`src/assets/icons/exif-watermark-icon.ico`、`src/assets/icons/exif-watermark-logo-1024.png` 为本项目自制（根目录 `LICENSE` 说明图标与 logo 由 AI 辅助生成），不是第三方素材，无额外随附义务。
- `src/assets/icons/github.svg` 复刻的是 **GitHub 的标识**：它是 GitHub, Inc. 的商标，本项目只在「关于」弹窗中用它链接到本仓库，属指代性使用，不表示任何背书关系。

## 分发时必须随附的文本

这些文本已收在仓库根的 `licenses/`，源码分发时就在仓库里；打包时 `packaging/xiangshuiyin-qt.spec` 会在构建结束后把它们复制到产物目录：单文件形态放在 exe 旁边，目录形态（安装器分发的那一形态）则随安装器装进程序目录，用户看得到。分发单位 = 程序 + `LICENSE` + 本文件 + `licenses/`（安装器装出的程序目录里已经包含这些文本）。

| 文本 | 组件 | 来源 |
|---|---|---|
| `licenses/LGPL-3.0.txt`、`licenses/GPL-3.0.txt` | PySide6 / Qt | GNU 官方文本；PySide6 的 wheel **不含**，这两份是 LGPLv3 第 4 条 b 项要求的随附文本 |
| `licenses/Qt-Notice.txt` | PySide6 / Qt | 本项目撰写：第 4 条 a 项的显著声明、第 4 条 d 项的源码位置与"可替换"说明 |
| `licenses/Pillow-MIT-CMU.txt` | Pillow | `.venv/Lib/site-packages/pillow-*.dist-info/licenses/LICENSE` |
| `licenses/ExifRead-BSD-3-Clause.txt` | exifread | `.venv/Lib/site-packages/exifread-*.dist-info/licenses/LICENSE` |
| `licenses/Python-PSF-2.0.txt` | Python 运行时 | Python 安装目录的 `LICENSE.txt` |
| `licenses/PyInstaller-GPL-2.0-with-exception.txt` | PyInstaller | `.venv/Lib/site-packages/pyinstaller-*.dist-info/licenses/COPYING.txt` |
| `licenses/Inno-Setup-License.txt` | Inno Setup | 构建机 Inno Setup 安装目录内的 `license.txt`（随附的是原文，仅统一行尾） |
| `licenses/Sarasa-Gothic-OFL-1.1.txt`、`licenses/Sarasa-Gothic-NOTICE.txt` | 内置字体 | 构建时从 `src/assets/fonts/OFL.txt` 与 `NOTICE.txt` 复制（权威副本在字体目录里） |
| `LICENSE` | 本项目 | 仓库根目录 |

> Inno Setup 的许可正文允许任何用途（含商业应用），而其下载页与编译器启动时另提示「商业使用需购买许可」，两者口径不一致：本项目按其许可正文随附 `Inno-Setup-License.txt`，商业使用口径需自行确认。

## 打包形态与「可替换该库」

对外分发的形态是**安装器**（`dist\xiangshuiyin-qt-portable.exe`）：它装出的是目录形态，Qt 的 DLL 就在程序目录里、用户可以直接替换，因此走 LGPLv3 第 4 条 d 项 **1 款**（「可替换该库」）。`licenses/Qt-Notice.txt` 同时给出各组件的源码位置，作为 0 款的兜底。

只做目录形态构建时（安装器的输入）：

```bat
set XSY_ONEDIR=1
.venv\Scripts\pyinstaller.exe packaging\xiangshuiyin-qt.spec
```

产物为 `dist\xiangshuiyin-qt\` 文件夹，Qt 的 DLL 在文件夹里，可以整体替换。默认的单文件产物把 Qt 库封在 exe 内、运行时解包到临时目录，用户无法替换，只能走 0 款，因此不作为对外分发形态。

## 已处理的依赖项

- 运行时依赖已从 `PySide6` 元包改为 **`PySide6-Essentials`**（`pyproject.toml`）：本程序只用 `QtCore` / `QtGui` / `QtWidgets` / `QtNetwork`，元包会额外带上 `PySide6-Addons`（安装体积 435 MB 量级，实测），既无用途也没有必要一起分发。

## 历史

- 2026-09-23：把原先内置的 Roboto 四变体（SIL OFL 1.1）**整体替换**为上面这套更纱黑体子集 —— Roboto 只有拉丁字形，水印文案里一出现中文就渲染成方框（FreeType 的 `.notdef`）。同时把 Roboto 的许可文件换成 Sarasa 的 `LICENSE`（现为 `src/assets/fonts/OFL.txt`），并新建本文件（此前 README 与「关于」弹窗指向的 `THIRD_PARTY_LICENSES` 并不存在）。
