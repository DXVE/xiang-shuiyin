; ===========================================================================
; 「镶 · 水印」 水印处理工具 · 绿色版安装器脚本（Inno Setup）
;
; 行为：把目录形态产物解压到用户选定的常驻目录，并按勾选创建桌面快捷方式（不写开始菜单）；
;       全程免管理员，不装卸载器，不写注册表，不进「应用和功能」。
; 卸载：删除程序目录与快捷方式即可；用户数据位于独立数据目录，不受影响。
; 前提：先以目录模式打包（环境变量 XSY_ONEDIR=1）产出 dist\xiangshuiyin-qt\，
;       同时生成版本头文件 build\version.iss；构建机安装 Inno Setup 6.3 或更高版本。
; 编译：ISCC.exe packaging\xiangshuiyin-qt.iss
;
; 语法：注释须整行以分号开头（行内分号是参数分隔符，不是注释）。
;       脚本为 UTF-8 编码，Inno Setup 6.3 起无需 BOM。
; ===========================================================================

; 版本号来自打包脚本生成的头文件，本脚本不含任何版本字面量：
; 唯一来源是 src/core/__init__.py 的 __version__，改版本只改那一处。
#include "..\build\version.iss"

[Setup]
; ---------------------------------------------------------------------------
; 应用身份
; ---------------------------------------------------------------------------

; 应用标识，用于判定安装器之间能否互相覆盖。本配置不含卸载器与注册表写入，
; 该值当前不被读取；固定一个 GUID，以便日后切换为标准安装档时标识不变。
AppId={{8E1F0C24-6D5B-4E3A-9C77-2B4D9A10F3C6}

; 应用显示名：用于向导标题，并作为默认目录名的来源。
AppName=「镶 · 水印」 水印处理工具

; 版本号，仅用于显示。本配置不做版本比较，升级方式为重新运行新版安装器覆盖安装。
; 取值来自上面 include 的版本头文件，不在本脚本中写死。
AppVersion={#AppVer}

; 向导中显示的名称与版本组合文本；默认值为「AppName AppVersion」，此处显式写出以带上 v 前缀。
AppVerName=「镶 · 水印」 水印处理工具 v{#AppVer}

; 安装器可执行文件的二进制文件版本（Windows 版本资源最多四段数字；不足四段时系统补零）。
VersionInfoVersion={#AppVer}

; 资源管理器「文件版本」一栏显示的文本版本。
VersionInfoTextVersion=v{#AppVer}

; 二进制产品版本，同样为数字形式。
VersionInfoProductVersion={#AppVer}

; 资源管理器「产品版本」一栏显示的文本版本。
VersionInfoProductTextVersion=v{#AppVer}

; 安装器可执行文件的描述，显示于文件属性。
VersionInfoDescription=「镶 · 水印」 水印处理工具 安装程序

; ---------------------------------------------------------------------------
; 解压位置与权限
; ---------------------------------------------------------------------------

; 默认解压位置，用户可在向导中修改。
; 位于程序数据根目录 %LOCALAPPDATA%\XiangShuiyin-QT6 下的 app 子目录，与该根目录内的
; watermark_data（设置与预设）和 logs 互不影响：删除 app 目录不影响用户数据，
; 删除数据根目录则会一并删除程序。
DefaultDirName={localappdata}\XiangShuiyin-QT6\app

; 以非管理员模式安装：不请求提权，不触发用户账户控制提示。
PrivilegesRequired=lowest

; 保留「选择目标位置」向导页。
; 该指令默认值为 auto，会查询注册表判断是否已安装过；本配置不写注册表，
; 因此必须显式设为 no，以保证每次安装均可选择路径。
DisableDirPage=no

; ---------------------------------------------------------------------------
; 零注册表与无卸载器
; ---------------------------------------------------------------------------

; 不包含卸载器。按帮助文档，该设置下安装器依赖的注册表项不会被创建。
Uninstallable=no

; 不在「应用和功能」中建立条目。
CreateUninstallRegKey=no

; 不查询注册表沿用上一次的安装目录（默认值为 yes）。
UsePreviousAppDir=no

; 不查询注册表沿用上一次的开始菜单文件夹名（默认值为 yes）。
UsePreviousGroup=no

; 不通知系统刷新文件关联（本程序不注册文件关联）。
ChangesAssociations=no

; 不通知系统重新加载环境变量（本程序不修改环境变量）。
ChangesEnvironment=no

; 覆盖安装前检测占用待更新文件的程序，并提示用户关闭。
; 本程序带单实例机制，运行期间会占用安装目录，因此保留该项。
CloseApplications=yes

; 未启用的相关项：不写 [Registry] 段；不设 UninstallDisplayName / UninstallDisplayIcon /
; UninstallDisplaySize（无卸载入口）；不启用 SetupLogging / UninstallLogging（默认 no）。

; ---------------------------------------------------------------------------
; 向导与外观
; ---------------------------------------------------------------------------

; 不显示「欢迎」页。
DisableWelcomePage=yes

; 不显示「准备安装」页。
DisableReadyPage=yes

; 不显示「选择开始菜单文件夹」页：本安装器不创建开始菜单快捷方式，该页没有意义（保持关闭）。
DisableProgramGroupPage=yes

; 向导配色：classic 为灰底，modern 为白底；二者改此一行即可切换。
WizardStyle=classic

; 安装器图标，取自项目的图标资源。
SetupIconFile=..\src\assets\icons\exif-watermark-icon.ico

; 向导许可页的文本文件。
; 指向随包分发的许可全文，与安装后目录中的文件为同一份，不产生重复副本。
; 帮助文档要求该文件为 .txt 或 .rtf；此处引用无扩展名的 LICENSE，依据是编译器源码中
; 该分支仅做文本读取并按内容识别 RTF，不校验扩展名（已实测编译通过）。
; 若后续版本改为校验扩展名，将仓库根的 LICENSE 复制为 packaging\license.txt，
; 并同步修改此处文件名。
LicenseFile=..\dist\xiangshuiyin-qt\LICENSE

; ---------------------------------------------------------------------------
; 产物与压缩
; ---------------------------------------------------------------------------

; 安装器输出目录，相对本脚本所在目录。
OutputDir=..\dist

; 安装器文件名。不使用 setup 命名：帮助文档指出 Windows 会对所有名为 setup.exe 的
; 程序施加兼容性 shim。
OutputBaseFilename=xiangshuiyin-qt-portable

; 压缩算法与等级：lzma2 最高档，安装包体积最小。
Compression=lzma2/max

; 固实压缩：全部文件一并压缩以获得更高压缩率，代价是安装时解压耗时增加。
SolidCompression=yes

; 允许运行的架构：仅 64 位（产物与 Qt 库均为 x64）。
ArchitecturesAllowed=x64compatible

; 在 64 位系统上以 64 位安装模式安装。
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; 简体中文界面。使用仓库内的官方翻译副本（i18n\ChineseSimplified.isl）而非
; compiler:Languages\ChineseSimplified.isl：官方安装包未捆绑中文，使用后者在干净环境上
; 会编译失败。该副本取自 Inno Setup 官方仓库的 Files\Languages\，内容未改动；
; 升级方式为以官方仓库的同名文件覆盖。
Name: "chinese"; MessagesFile: "i18n\ChineseSimplified.isl"

; 英文界面：编译器自带的默认语言文件。
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 桌面快捷方式：默认勾选（checkedonce），用户可在向导中取消。
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce

[Files]
; 安装主体：把目录形态产物整棵复制到目标目录。
;   Source 为最新一次目录模式打包的产物目录；
;   recursesubdirs / createallsubdirs 递归复制并创建子目录；
;   ignoreversion 表示同名文件直接覆盖，用于升级。
Source: "..\dist\xiangshuiyin-qt\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 桌面快捷方式：仅在桌面任务被勾选时创建。安装器**不创建开始菜单快捷方式**（按需求去掉）。
Name: "{autodesktop}\「镶 · 水印」 水印处理工具"; Filename: "{app}\xiangshuiyin-qt.exe"; Tasks: desktopicon

[InstallDelete]
; 清理早期版本留下的开始菜单快捷方式：本安装器不再创建它，早期版本装过的话会一直残留在开始菜单里。
Type: files; Name: "{autoprograms}\「镶 · 水印」 水印处理工具.lnk"
; 改名之前的旧命名，同样清掉，避免开始菜单里同时留着新旧两条。
Type: files; Name: "{autoprograms}\镶 · 水印.lnk"
; 桌面同理：早期版本的桌面快捷方式是旧名字，本安装器会用新名字再建一条，不清掉会变成两条。
Type: files; Name: "{autodesktop}\镶 · 水印.lnk"

[Run]
; 完成页的可选动作：立即启动程序。
;   nowait 不等待程序退出；postinstall 显示于完成页；skipifsilent 静默安装时不启动。
Filename: "{app}\xiangshuiyin-qt.exe"; Description: "立即运行"; Flags: nowait postinstall skipifsilent
