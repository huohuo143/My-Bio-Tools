#define MyAppName "My Bio Tools"
#define MyAppVersion "1.7.2"
#define MyAppPublisher "Wu Lab"
#define MyAppExeName "My Bio Tools.exe"
#define WebViewProductKey "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

#ifndef SourceDir
  #define SourceDir "..\..\dist\windows\app"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\windows"
#endif
#ifndef IconFile
  #define IconFile "..\MyBioTools.Windows\Assets\AppIcon.ico"
#endif

[Setup]
AppId={{E75EC10E-88DE-4A48-8F89-96FF68838DFB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=My-Bio-Tools-{#MyAppVersion}-win-x64-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion=1.7.2.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Windows x64 installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\prerequisites\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装 Microsoft Edge WebView2 Runtime…"; Flags: waituntilterminated; Check: not WebView2RuntimeInstalled
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function VersionIsUsable(const Version: String): Boolean;
begin
  Result := (Version <> '') and (CompareText(Version, '0.0.0.0') <> 0);
end;

function WebView2RuntimeInstalled(): Boolean;
var
  Version: String;
  Key: String;
begin
  Key := 'Software\Microsoft\EdgeUpdate\Clients\{#WebViewProductKey}';
  Result := RegQueryStringValue(HKCU, Key, 'pv', Version) and VersionIsUsable(Version);
  if not Result then
    Result := RegQueryStringValue(HKLM32, Key, 'pv', Version) and VersionIsUsable(Version);
end;
