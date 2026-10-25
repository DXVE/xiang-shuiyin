; 「镶 · 水印」 水印处理工具 · 绿色版安装器脚本(Inno Setup)
; 注释须整行以分号开头(行内分号是参数分隔符, 不是注释); 脚本为 UTF-8 编码, Inno Setup 6.3 起无需 BOM

#include "..\build\version.iss"

[Setup]
; 应用标识, 用于判定安装器之间能否互相覆盖; 本配置不含卸载器与注册表写入, 该值当前不被读取
; 固定一个 GUID, 以便日后切换为标准安装档时标识不变
AppId={{8E1F0C24-6D5B-4E3A-9C77-2B4D9A10F3C6}

; 应用显示名: 用于向导标题, 并作为默认目录名的来源
AppName=「镶 · 水印」 水印处理工具

; 版本号, 仅用于显示; 本配置不做版本比较, 升级方式为重新运行新版安装器覆盖安装
AppVersion={#AppVer}

; 向导中显示的名称与版本组合文本; 默认值为「AppName AppVersion」, 此处显式写出以带上 v 前缀
AppVerName=「镶 · 水印」 水印处理工具 v{#AppVer}

; 安装器可执行文件的二进制文件版本(Windows 版本资源最多四段数字, 不足四段时系统补零)
VersionInfoVersion={#AppVer}

; 资源管理器「文件版本」一栏显示的文本版本
VersionInfoTextVersion=v{#AppVer}

; 二进制产品版本, 同为数字形式
VersionInfoProductVersion={#AppVer}

; 资源管理器「产品版本」一栏显示的文本版本
VersionInfoProductTextVersion=v{#AppVer}

; 安装器可执行文件的描述, 显示于文件属性
VersionInfoDescription=「镶 · 水印」 水印处理工具 安装程序

; 默认解压位置; 输入栏只读(见末尾 [Code]), 用户只能通过「浏览」改位置
; 位于程序数据根目录 %LOCALAPPDATA%\XiangShuiyin-QT6 下的 app 子目录, 与同根目录内的 watermark_data(设置与预设)和 logs 互不影响: 删除 app 目录不影响用户数据, 删除数据根目录则会一并删除程序
DefaultDirName={localappdata}\XiangShuiyin-QT6\app

; 开始菜单文件夹默认名(用户可在向导中修改)
DefaultGroupName=「镶 · 水印」 水印处理工具

; 以非管理员模式安装: 不请求提权, 不触发用户账户控制提示
PrivilegesRequired=lowest

; 保留「选择目标位置」向导页: 该指令默认值为 auto, 会查询注册表判断是否已安装过
; 本配置不写注册表, 因此必须显式设为 no, 以保证每次安装均可选择路径
DisableDirPage=no

; 按帮助文档, 不含卸载器时安装器依赖的注册表项不会被创建
Uninstallable=no

CreateUninstallRegKey=no

; 不查询注册表沿用上一次的安装目录(该指令默认值为 yes)
UsePreviousAppDir=no

; 不查询注册表沿用上一次的开始菜单文件夹名(该指令默认值为 yes)
UsePreviousGroup=no

; 不通知系统刷新文件关联(本程序不注册文件关联)
ChangesAssociations=no

; 不通知系统重新加载环境变量(本程序不修改环境变量)
ChangesEnvironment=no

; 覆盖安装前检测占用待更新文件的程序, 并提示用户关闭; 本程序带单实例机制, 运行期间会占用安装目录, 因此保留该项
CloseApplications=yes

; 未启用的相关项: 不写 [Registry] 段; 无卸载入口, 故不设 UninstallDisplayName / UninstallDisplayIcon / UninstallDisplaySize
; 也不启用 SetupLogging / UninstallLogging(两者的默认值为 no)

DisableWelcomePage=yes

DisableReadyPage=yes

; 保留「选择开始菜单文件夹」向导页, 默认组名见 DefaultGroupName
DisableProgramGroupPage=no

; 向导配色: classic 为灰底, modern 为白底, 二者改此一行即可切换
WizardStyle=classic

; 安装器图标, 取自项目的图标资源
SetupIconFile=..\src\assets\icons\exif-watermark-icon.ico

; 向导许可页引用的文本文件, 与安装后目录中的许可全文为同一份, 不产生重复副本
; 帮助文档要求该文件为 .txt 或 .rtf, 此处引用无扩展名的 LICENSE: 编译器该分支只做文本读取并按内容识别 RTF, 不校验扩展名
; 若后续版本改为校验扩展名, 需把仓库根的 LICENSE 复制为 packaging\license.txt, 并同步修改此处文件名
LicenseFile=..\dist\xiangshuiyin-qt\LICENSE

; 安装器输出目录, 相对本脚本所在目录
OutputDir=..\dist

; 安装器文件名: 版本号取自打包生成的 build/version.iss(AppVer), 与源码 __version__ 同源;
; 不使用 setup 命名: 帮助文档指出 Windows 会对所有名为 setup.exe 的程序施加兼容性 shim
OutputBaseFilename=xiangshuiyin-v{#AppVer}-portable

; 压缩算法与等级: lzma2 最高档, 安装包体积最小
Compression=lzma2/max

; 固实压缩: 全部文件一并压缩以获得更高压缩率, 代价是安装时解压耗时增加
SolidCompression=yes

; 允许运行的架构: 仅 64 位(产物与 Qt 库均为 x64)
ArchitecturesAllowed=x64compatible

ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinese"; MessagesFile: "i18n\ChineseSimplified.isl"

; 英文界面: 编译器自带的默认语言文件
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 桌面快捷方式默认勾选(checkedonce), 用户可在向导中取消
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce

[Files]
; 把最新一次目录模式打包的产物整棵复制到目标目录; ignoreversion 表示升级时同名文件直接覆盖
Source: "..\dist\xiangshuiyin-qt\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单项: 始终创建; {group} = {autoprograms}\<开始菜单组名>
Name: "{group}\「镶 · 水印」 水印处理工具"; Filename: "{app}\xiangshuiyin-qt.exe"

; 桌面快捷方式: 由向导的附加任务决定(默认勾选)
Name: "{autodesktop}\「镶 · 水印」 水印处理工具"; Filename: "{app}\xiangshuiyin-qt.exe"; Tasks: desktopicon

[InstallDelete]
; 覆盖安装时先清空 {app} 整个目录, 避免上一版删掉的模块/文件残留(本段是安装第一步, 随后由 [Files] 重新铺开)
; {app} 被 [Code] 固定在数据根目录下的 app 子目录, 故清空不波及其父目录(watermark_data 与 logs)
Type: filesandordirs; Name: "{app}"
; 清理早期版本残留的旧快捷方式(旧版无开始菜单组、桌面项用旧名)
Type: files; Name: "{autoprograms}\「镶 · 水印」 水印处理工具.lnk"
Type: files; Name: "{autoprograms}\镶 · 水印.lnk"
Type: files; Name: "{autodesktop}\镶 · 水印.lnk"

[Run]
; 完成页的可选动作: nowait 不等待程序退出, postinstall 显示于完成页, skipifsilent 静默安装时不启动
Filename: "{app}\xiangshuiyin-qt.exe"; Description: "立即运行"; Flags: nowait postinstall skipifsilent

[Code]
var
  DirEditing: Boolean;

// 「选择目标位置」页的路径输入栏设为只读, 禁手输或粘贴绕过 app 子目录, 只能通过「浏览」选位置
// 浏览后立即把 app 子目录补回, 使输入栏与最终安装路径都恒为数据根下的独立子目录
procedure DirEditChanged(Sender: TObject);
begin
  if DirEditing then
    Exit;
  if CompareText(ExtractFileName(WizardForm.DirEdit.Text), 'app') = 0 then
    Exit;
  DirEditing := True;
  WizardForm.DirEdit.Text := AddBackslash(WizardForm.DirEdit.Text) + 'app';
  DirEditing := False;
end;

procedure InitializeWizard;
begin
  WizardForm.DirEdit.ReadOnly := True;
  WizardForm.DirEdit.OnChange := @DirEditChanged;
end;
