---
name: xsy-cli
description: 用「镶 · 水印」的命令行入口给照片加水印：单张或整目录批量出图、套用预设、覆盖任意内核参数、输出机器可读结果。需要给图片加 EXIF 水印或批量导出水印图时使用。
---

# 「镶 · 水印」命令行出图

把一个或多个图片（或目录）交给渲染内核，逐张生成带 EXIF 水印的成品图。

## 何时使用

- 用：批量给照片加水印、按预设出图、把出图接进脚本或自动化流程、需要机器可读的处理结果。
- 不用：实时预览调参、取色、编辑型号映射。这些只在桌面界面里做，命令行不含。

## 前置

解释器用项目自带的虚拟环境，不用全局 Python：

- Windows：`<项目根>\.venv\Scripts\python.exe`
- POSIX：`<项目根>/.venv/bin/python`

入口，两条等价，都在项目根执行：

- `<解释器> src/cli/render.py <图片或目录>… <选项>`
- `cd src` 后 `<解释器> -m cli <图片或目录>… <选项>`

只依赖 Pillow 与渲染内核，不需要界面框架与显示器。

## 三段式调用

1. 查可用参数：`<解释器> src/cli/render.py --list-params --json`
   输出内核接受的键、默认值与类型。**键名以这条命令的结果为准**，不要凭记忆写。
2. 只试不渲染：`<解释器> src/cli/render.py <入参> --dry-run --json`
   输出将处理的文件、跳过项、生效参数来源与非默认参数，不写任何成品（这套 JSON 的字段是 `out_dir`、`count`、`files`、`skipped`、`param_source`、`logo_dir`、`changed`）。
3. 正式出图：`<解释器> src/cli/render.py <入参> --json`

套用预设前可先看槽位：`<解释器> src/cli/render.py --list-presets`

## 选项

| 用途 | 选项 |
|---|---|
| 输入输出 | `inputs`（图片或目录，至少一个）、`--out <目录>`、`--recursive` |
| 外观 | `--bg color\|blur`、`--bg-color <#RRGGBB>`、`--text-color <#RRGGBB>`、`--blur-radius <整数>`、`--darken <0~1>`、`--corner-radius <整数>` |
| 布局与间距 | `--layout unified\|split_top_bottom\|split_left_right`、`--ratio <档位>`、`--margin <比例>`、`--line-gap <比例>`、`--gap-inner <比例>`、`--gap-outer <比例>` |
| 预设与素材 | `--preset <槽位号或方案名>`、`--logo-dir <目录>` |
| 任意参数 | `--set KEY=VALUE`，可重复 |
| 水印信息 | `--exif KEY=VALUE`，可重复 |
| 诊断 | `--json`、`--stage`、`--dry-run`、`--list-params`、`--list-presets`、`--quiet` |

`--ratio` 的档位：`original`、`1:1`、`3:2`、`2:3`、`4:3`、`3:4`、`16:9`、`9:16`、`21:9`、`9:21`。

`--set` 用于覆盖参数表里没有专用选项的参数，键取 `--list-params` 的结果，值按该键默认值的类型转换（布尔接受 `true` / `false` / `1` / `0` / `yes` / `no` / `on` / `off`）。

## 参数优先级

内核默认值 → `--preset` → 专用选项（`--bg` / `--layout` / `--ratio` / `--exif` 等）→ `--set`。`--set` 最后生效，可覆盖前面任何来源。

## 输出与退出码

- 默认输出目录：首张输入图所在目录下的 `watermarked/`；`--out` 覆盖，目录不存在会创建。
- 成品命名：`<原文件名>_watermarked.jpg`；同名已存在时依次 `_watermarked_2.jpg`、`_watermarked_3.jpg`。
- 成品格式固定 JPEG（质量 100），沿用原图 EXIF 并去掉方向标记；JPEG 写不出时回退为同名 `.png`，此时路径以 JSON 里的 `output` 为准。
- `--json` 顶层字段：`ok`、`total`、`success`、`out_dir`、`param_source`、`changed`、`skipped`、`results`。每张结果含 `input`、`output`、`ok`、`ms`，失败时是 `error`，加了 `--stage` 才有 `stages`。
- 退出码：`0` 全部成功；`1` 有失败的图（其余仍会继续处理）；`2` 参数错误、没有输入、输入不存在或没有受支持的图片。
- 批量是串行执行，单张失败不中断其余。

## 易错点

1. **画布比例**用 `--ratio <档位>` 或 `--set canvas_ratio=<数值>`，数值 `0` 表示跟随原图。界面里的档位字符串（如 `original`）不是内核参数，写成 `--set` 会报未知参数。
2. **`--set` 的键只能来自 `--list-params`**。预览与调试用的内部参数不在其中，命令行无法设置。
3. **目录入参默认只看当前层**，要含子目录加 `--recursive`；图片按文件头判定而不看扩展名，不支持的文件计入 `skipped`，不算失败。
4. **`--exif` 的键固定五个**：`make`、`model`、`exposure`、`fnumber`、`iso`，必须写成 `键=值`。值会自动补格式：`exposure=1/250` → `1/250s`、`fnumber=2.8` → `f/2.8`、`iso=400` → `ISO400`。品牌与型号逐字段以自定义值优先、缺省回退原图 EXIF；曝光 / 光圈 / ISO 只要给了任一项，参数行整行采用自定义值，未给的项不显示；留空等于不覆盖。
5. **`--gap-inner` 同时写图片上下的内侧间距，`--gap-outer` 同时写画布上下的外侧间距**。上下分离布局里两段可见距离都可能大于设定值（矮侧余量内外各半，居中与镜像语义），这是既定行为而不是缺陷。

## 副作用与隔离

导入内核会创建数据目录与日志目录、轮转旧日志，并清理数据目录里的临时残留。不想碰用户数据时，在启动解释器前把这两个变量指到临时目录：

- `XSY_BASE_DIR`：数据根目录（设置、预设、型号映射、日志）。
- `XSY_PREVIEW_DIR`：预览临时目录，退出时整目录清空。

两者都在导入内核时读取，必须在进程启动前设好。

## Logo 与型号映射

`--logo-dir` 指向品牌图与型号映射所在的目录：品牌图按厂商名在该目录里查找，找不到就退回文字水印；型号显示名取自该目录下的 `model_map.json`，该文件缺失只告警、不中断出图。不传时用内核默认参数里的 Logo 目录。

## 自检

1. `<解释器> src/cli/render.py --list-params --json` 能列出键与默认值。
2. `<解释器> src/cli/render.py <一张图> --dry-run --json` 的 `changed` 里能看到本次要改的参数。
3. `<解释器> src/cli/render.py <一张图> --json` 退出码为 `0`，且 `success` 等于 `total`。

原理与运行形态见 `wiki.md`，开发规则见 `AGENTS.md`。
