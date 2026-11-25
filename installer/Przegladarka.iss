#define MyAppName "Przeglądarka"
#define MyAppVersion "0.3.1"
#define MyAppPublisher "Przeglądarka"
#define MyAppExeName "Przegladarka.exe"

[Setup]
AppId={{8C38DDF2-6E50-4D3B-9D12-4B7D1F6E9031}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Przeglądarka
DefaultGroupName={#MyAppName}
OutputDir=..\dist-installer
OutputBaseFilename=Przegladarka-Setup-Windows-v0.3.1
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "desktopicon"; Description: "Utwórz ikonę na pulpicie"; GroupDescription: "Dodatkowe skróty:"; Flags: unchecked

[Files]
Source: "..\dist\Przegladarka.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Przeglądarka"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Przeglądarka"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Uruchom Przeglądarkę"; Flags: postinstall nowait skipifsilent
