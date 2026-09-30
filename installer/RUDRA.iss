; Inno Setup script for the RUDRA Windows installer.
;
;   iscc /DAppVersion=0.1.0 installer\RUDRA.iss
;
; windows\build.py --installer runs this after building dist\RUDRA and collecting the
; license texts into build\licenses. The result is dist\installer\RUDRA-Setup-<version>-win64.exe.
;
; Per-user installation, no administrator rights:
;   program files  %LOCALAPPDATA%\Programs\RUDRA   owned by the installer, replaced on upgrade
;   user data      %LOCALAPPDATA%\RUDRA            created by RUDRA on first start; never
;                                                   written by the installer and never removed
;                                                   by upgrading or uninstalling

#ifndef AppVersion
  #error Pass the version: iscc /DAppVersion=X.Y.Z installer\RUDRA.iss
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif

#define AppName "RUDRA"
#define AppURL "https://github.com/gonekoushik226-gif/ROBOTIC-UNIFIED-DESIGN-RESEARCH-AGENT"

[Setup]
AppId={{05B1382D-1FC2-4ADB-888C-F03C39A3DF08}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
AppComments=Local-first knowledge, reasoning and calculation
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=RUDRA-Setup-{#AppVersion}-win64
SetupIconFile=..\app\ui\gui\assets\rudra.ico
UninstallDisplayIcon={app}\RUDRA.exe
UninstallDisplayName={#AppName} {#AppVersion}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An upgrade replaces the runtime completely, so no file of an older version lingers.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\RUDRA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\RUDRA.exe"; Comment: "Open RUDRA"
Name: "{group}\{#AppName} Command Line"; Filename: "{app}\RUDRA-CLI.exe"; Comment: "RUDRA's command line"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\RUDRA.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RUDRA.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=Setup has installed [name] on your computer.%n%nYour knowledge base is kept in your user profile (AppData\Local\RUDRA), separately from the program. Upgrading or uninstalling RUDRA does not remove it.
