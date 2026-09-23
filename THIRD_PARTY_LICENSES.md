# 第三方组件与许可

「镶 · 水印 Qt 版」随包分发的第三方组件如下。**分发（含发给朋友、内网传播）时必须连本文件
一起带上** —— 其中 PySide6 的 LGPLv3 与内置字体的 SIL OFL 1.1 都明确要求"随附版权声明与许可全文"。

## 运行时依赖

| 组件 | 版本 | 许可 | 说明 |
|---|---|---|---|
| **PySide6**（Qt for Python） | 6.11.x | **LGPLv3**（或 GPL） | 以**动态链接**方式使用。LGPLv3 要求随附许可全文与 Qt 的版权声明，并保证使用者能替换该库 —— 打包时注意：`PySide6` 元包会带上 `PySide6-Addons`（约 77 MB，本程序用不到），应改用 `PySide6-Essentials`；另外 PyInstaller `onefile` 会把 Qt 打进单文件包，"可替换"这一条要另想办法（目录模式，或提供替换说明）。 |
| **Pillow** | 12.x | MIT-CMU | 图像处理（预览、水印合成、取色） |
| **exifread** | 3.5.x | BSD-3-Clause | 读取照片 EXIF |

> 上面三者的许可全文随各自的发行包提供（`pip download` 的 wheel 里带）；本文件只作索引与提示，
> 不复制它们的全文。若将来要打正式分发包，建议把三份全文也一起收进发行物。

## 内置字体

| 文件 | 许可 | 来源 |
|---|---|---|
| `src/xiangshuiyin/assets/fonts/SarasaGothicSC-{Regular,Bold,Italic,BoldItalic}.ttf` | **SIL OFL 1.1** | 由「更纱黑体 Sarasa Gothic」1.0.41（Sarasa Gothic SC）**子集化**而来（**名称与版权原样保留**） |

- **OFL 全文**：`src/xiangshuiyin/assets/fonts/OFL.txt`（原样取自上游仓库的 `LICENSE`）
- **来源与改动说明**：`src/xiangshuiyin/assets/fonts/NOTICE.txt`（子集范围、改名理由、重建方法）
- **版权行**（子集版本一并保留）：

      Copyright (c) 2015-2025, Renzhi Li (aka. Belleve Invis, belleve@typeof.net).
      Portions Copyright (c) 2016 The Inter Project Authors.
      Portions Copyright (c) 2014-2021 Adobe Systems Incorporated (http://www.adobe.com/),
      with Reserved Font Name 'Source'.
      Portions Copyright (c) 2012 Google Inc.

- **OFL 义务要点**：随软件分发/嵌入/与软件一起出售都允许；**必须附版权声明与许可全文**（就是上面
  那两个文件）；**不得单独出售**字体本身；**修改版不得使用保留名**（这份 OFL 声明的保留名是
  `'Source'`，来自 Adobe 的 Source Han Sans —— 本字体名与版权都保留原样，没有碰它）。

## 历史

- 2026-09-23：把原来内置的 Roboto 四变体（SIL OFL 1.1）**整体替换**为上面这套更纱黑体子集，
  原因是 Roboto 只有拉丁字形 —— 水印文案里一出现中文就渲染成方框（FreeType 的 `.notdef`）。
  同时删掉了 Roboto 的许可文件 `OFL.txt`，换成 Sarasa 的 LICENSE；并新建本文件（此前 README 与
  「关于」弹窗都在指向一个并不存在的 `THIRD_PARTY_LICENSES`）。
