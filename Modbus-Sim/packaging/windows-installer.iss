#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "Modbus-Sim"
#define MyAppPublisher "Uni-Lab"
#define MyAppExeName "Modbus-Sim.exe"
#define Com0comInstaller "Setup_com0com_v3.0.0.0_W7_x64_signed.exe"

[Setup]
AppId={{FA20E9A4-9EC2-4A10-84E8-2019A00E4F0B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#SourcePath}\..\artifacts
OutputBaseFilename=Modbus-Sim-Setup-Windows-x64-v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#SourcePath}\assets\uni-lab-sim.ico
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："; Flags: unchecked
Name: "com0com"; Description: "安装 com0com 虚拟串口驱动（可选，需要 UAC 管理员授权）"; GroupDescription: "附加选项："; Flags: unchecked

[Files]
Source: "{#SourcePath}\..\dist\Modbus-Sim\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\vendor\com0com\*"; DestDir: "{app}\third_party\com0com"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "gui"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "gui"; Tasks: desktopicon

[Run]
Filename: "{app}\third_party\com0com\{#Com0comInstaller}"; Parameters: "/S"; Verb: "runas"; StatusMsg: "正在安装 com0com 虚拟串口驱动…"; Flags: shellexec waituntilterminated runhidden; Tasks: com0com; Check: not Com0comInstalled
Filename: "{app}\{#MyAppExeName}"; Parameters: "gui"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function Com0comInstalled: Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{pf64}\com0com\setupc.exe')) or
    FileExists(ExpandConstant('{pf64}\com0com\setupc\setupc.exe')) or
    FileExists(ExpandConstant('{pf32}\com0com\setupc.exe')) or
    FileExists(ExpandConstant('{pf32}\com0com\setupc\setupc.exe'));
end;
