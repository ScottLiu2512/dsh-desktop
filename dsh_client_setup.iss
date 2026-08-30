; DSH Desktop — Inno Setup 安装脚本
; 编译：ISCC.exe dsh_client_setup.iss
; 产物：installer_output\DSH-Desktop-Setup.exe

#define MyAppName "DSH Desktop"
#define MyAppVersion "1.0.6"
#define MyAppPublisher "dsh-gui"
#define MyAppExeName "DSH-Desktop.exe"

[Setup]
AppId={{B7A4C9E1-2F3D-4E5A-8B6C-9D0E1F2A3B4C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\DSH-Desktop
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=DSH-Desktop-Setup
; 单文件 exe 内部已压缩，用 zip 更快；如需更小体积可改 lzma2
Compression=zip
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; 安装到 Program Files 需要管理员权限
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=icon.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务:"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// 检测命令是否可用（退出码 0 表示存在且能运行）
function CommandAvailable(const Cmd: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c ' + Cmd + ' >nul 2>&1', '',
                 SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

// 安装完成后检测运行依赖，缺失则提示（不强制安装）
procedure CurStepChanged(CurStep: TSetupStep);
var
  Missing: String;
begin
  if CurStep = ssPostInstall then
  begin
    Missing := '';
    if not CommandAvailable('node --version') then
      Missing := Missing + '- 未检测到 Node.js（需要 22 或更高）。请到 https://nodejs.org/ 安装。' + #13#10;
    if not CommandAvailable('dsh --version') then
      Missing := Missing + '- 未检测到 dsh。请安装后运行：npm install -g @deepseek-ai/dsh' + #13#10;

    if Missing <> '' then
      MsgBox('安装完成，但检测到缺少运行依赖：' + #13#10 + #13#10 +
             Missing + #13#10 +
             '本程序已安装，但需要补齐上述依赖后，「启动 dsh」才能正常使用。',
             mbInformation, MB_OK);
  end;
end;
