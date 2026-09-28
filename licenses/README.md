# 随包分发的许可文本

这个目录存放对外分发时**必须随附**的第三方许可文本。分发单位是「程序 + `LICENSE` + `THIRD_PARTY_LICENSES.md` + 本目录」这一组；
打包时 `packaging/xiangshuiyin-qt.spec` 会在构建结束后把它们复制到产物目录（字体那两份从 `src/assets/fonts/` 复制过来）；对外分发的安装器会把该目录装进程序目录，用户看得到。

| 文件 | 组件 | 版本 | 许可（SPDX） | 文本来源 |
|---|---|---|---|---|
| `LGPL-3.0.txt` | PySide6 / Qt 库 | 6.11.2 | LGPL-3.0-only | GNU 官方 `lgpl-3.0.txt`（PySide6 的 wheel **不带**这份全文） |
| `GPL-3.0.txt` | 同上，LGPLv3 第 4 条 b 项要求的另一份 | — | GPL-3.0-only | GNU 官方 `gpl-3.0.txt` |
| `Qt-Notice.txt` | Qt / PySide6 的声明与源码位置 | 6.11.2 | — | 本项目撰写（见文件内说明） |
| `Pillow-MIT-CMU.txt` | Pillow | 12.3.0 | MIT-CMU | `pillow-*.dist-info/licenses/LICENSE`（含其内置第三方库的声明） |
| `ExifRead-BSD-3-Clause.txt` | exifread | 3.5.1 | BSD-3-Clause | `exifread-*.dist-info/licenses/LICENSE` |
| `Python-PSF-2.0.txt` | Python 运行时 | 3.14 | PSF-2.0 | Python 安装目录的 `LICENSE.txt` |
| `PyInstaller-GPL-2.0-with-exception.txt` | PyInstaller（引导程序会嵌入产物） | 6.22.3 | GPL-2.0-or-later + 例外 | `pyinstaller-*.dist-info/licenses/COPYING.txt` |
| `Inno-Setup-License.txt` | Inno Setup（安装引导程序会嵌入安装器） | 6.7.3 | Inno Setup License | 构建机 Inno Setup 安装目录内的 `license.txt`（原文照录，仅统一行尾） |
| `Sarasa-Gothic-OFL-1.1.txt` / `Sarasa-Gothic-NOTICE.txt` | 内置字体 | 1.0.41 子集 | SIL OFL 1.1 | 构建时从 `src/assets/fonts/OFL.txt` 与 `NOTICE.txt` 复制（仓库内的权威副本在那里） |

## 为什么是这些文件

- **LGPLv3 第 4 条（Combined Works）** 要求：a) 显著声明使用了该库且其使用受 LGPL 覆盖；b) **随附一份 GNU GPL 与一份 LGPL 文本**；d) 二者之一 —— 0) 提供库的对应源码（或书面提供），或 1) 采用可替换的共享库机制。本项目对外分发的是安装器（装出目录形态，Qt 的 DLL 就在程序目录里、用户可原地替换），因此 d 项走 **1 款**，`Qt-Notice.txt` 里同时写明各组件的源码位置，作为 0 款的兜底。`Qt-Notice.txt` 承担 a) 与 d)，`LGPL-3.0.txt` + `GPL-3.0.txt` 承担 b)。
- **MIT-CMU（Pillow）/ BSD-3-Clause（exifread）**：分发二进制时须随附版权声明与许可全文。
- **PSF-2.0（Python 运行时）**：解释器运行库随打包产物一起分发，同样须随附许可。
- **SIL OFL 1.1（字体）**：第 2 条要求随附版权声明与许可全文、不得单独出售字体；第 3 条要求修改版不得使用保留名。字体内部的 name 表已保留版权行，`OFL.txt` 与 `NOTICE.txt` 同时位于仓库与产物内（`assets/fonts/`）。

## 注意

- 这些文本是**原文照录**，请勿改写；更新依赖版本时把对应文本一并替换。
- **Inno Setup 的许可已随附**（`Inno-Setup-License.txt`）：其安装引导程序会嵌入安装器产物。该许可正文允许任何用途（含商业应用），而官网下载页与编译器启动时另提示「商业使用需购买许可」，两者的口径分歧记录在 `THIRD_PARTY_LICENSES.md`。
- Qt 自身还包含更多第三方代码，其归属清单由 Qt 官方维护（`https://doc.qt.io/qt-6/licenses.html`）。若要做正式对外发布，建议从该页（或 PySide6 源码树的 `LICENSES/`）补一份 Qt 内部第三方归属说明。
