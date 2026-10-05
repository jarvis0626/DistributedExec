#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif

[Setup]
AppId={{926BDF70-AB62-4B3D-8D44-C6A5F9E1C0E1}
AppName=DistributedExec
AppVersion={#AppVersion}
AppPublisher=DistributedExec contributors
AppPublisherURL=https://github.com/jarvis0626/DistributedExec
AppSupportURL=https://github.com/jarvis0626/DistributedExec/issues
DefaultDirName={localappdata}\Programs\DistributedExec
DefaultGroupName=DistributedExec
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19045
OutputDir=..\dist
OutputBaseFilename=DistributedExec-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\DistributedExec.exe
CloseApplications=yes
CloseApplicationsFilter=DistributedExec.exe
RestartApplications=no
UsePreviousTasks=no
SetupLogging=yes

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
Name: compute; Description: "Set up compute after installation (downloads Docker if missing)"; Flags: unchecked

[Files]
Source: "..\dist\DistributedExec\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\DistributedExec"; Filename: "{app}\DistributedExec.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\DistributedExec"; Filename: "{app}\DistributedExec.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\DistributedExec.exe"; Parameters: "{code:LaunchParameters}"; Description: "Launch DistributedExec"; Flags: nowait postinstall skipifsilent

[Code]
function LaunchParameters(Param: String): String;
begin
  if WizardIsTaskSelected('compute') then
    Result := 'gui --setup-compute'
  else
    Result := 'gui';
end;

procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel2.Caption :=
    'Install DistributedExec with Python and its libraries included.' + #13#10 + #13#10 +
    'Hosting works immediately. To run computations on this computer, select compute setup on the next screen or use Set up compute inside the app.' + #13#10 + #13#10 +
    'Compute setup reuses existing Docker or downloads its signed installer. Complete Docker''s own setup screens; Windows may require virtualization setup or a restart.';
end;
