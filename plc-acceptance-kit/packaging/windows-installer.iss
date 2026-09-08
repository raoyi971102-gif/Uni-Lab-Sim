#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "SZLab PLC 自动验收"
#define MyAppDirName "SZLab-PLC-Acceptance"
#define MyAppPublisher "Uni-Lab"
#define MyAppExeName "SZLab-PLC-Acceptance.exe"

[Setup]
AppId={{5B915297-6E74-43B8-9A7C-96CB5D2466C0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={localappdata}\Programs\{#MyAppDirName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#SourcePath}\..\artifacts
OutputBaseFilename=SZLab-PLC-Acceptance-Setup-Windows-x64-v{#MyAppVersion}
SetupIconFile={#SourcePath}\Uni-Lab-Sim.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
MinVersion=10.0.10240
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："; Flags: unchecked

[Files]
Source: "{#SourcePath}\..\dist\SZLab-PLC-Acceptance\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\FRAMEWORK.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\SZLab PLC 自动验收"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\SZLab PLC 自动验收"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 SZLab PLC 自动验收"; Flags: nowait postinstall skipifsilent
