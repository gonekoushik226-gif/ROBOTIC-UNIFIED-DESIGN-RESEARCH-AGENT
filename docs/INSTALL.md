# Installing RUDRA on Windows

You need only a Windows computer. Git, GitHub accounts, Python and development tools are
**not** needed to install or use RUDRA.

## Requirements

- Windows 10 or Windows 11, 64-bit (RUDRA is tested on Windows 11).
- About 50 MB of disk space for the program, plus space for your documents and knowledge
  base.
- For OCR of scanned pages and images: an OCR language installed in Windows (most display
  languages include one; see [OCR.md](OCR.md)). Everything else works without it.

## Download

1. Open the project's **Releases** page:
   <https://github.com/gonekoushik226-gif/ROBOTIC-UNIFIED-DESIGN-RESEARCH-AGENT/releases>
2. Under the latest release, download:
   - `RUDRA-Setup-<version>-win64.exe` — the installer;
   - `SHA256SUMS.txt` — its checksum (optional, to verify the download).

## Verify the download (optional)

In PowerShell, in the folder holding both files:

```powershell
Get-FileHash .\RUDRA-Setup-<version>-win64.exe -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

The two SHA-256 values must be identical.

## Install

1. Double-click `RUDRA-Setup-<version>-win64.exe`.
2. The installer is not code-signed, so Windows SmartScreen may say it protected your PC:
   choose **More info → Run anyway**.
3. RUDRA installs for your Windows account only; no administrator rights are needed.
   Choose whether to create a desktop shortcut.
4. Finish, and start RUDRA from the Start Menu.

The program is installed in `%LOCALAPPDATA%\Programs\RUDRA\`. Your data is kept apart, in
`%LOCALAPPDATA%\RUDRA\`, created on first start.

The Start Menu also has **RUDRA Command Line** (the same functions as text commands) and
**Uninstall RUDRA**.

## First use

1. Open **Full window → Add document** and add a document (PDF, Word, PowerPoint, Excel,
   EPUB, web page, text or an image).
2. Ask a question in the assistant box, type on the **Ask** page, or click its microphone
   button and speak — for example *"What is resistance?"*.
3. Use **View Sources** on an answer to see where it came from.
4. Back up your knowledge now and then: **Full window → Settings → Back up your
   knowledge**.

## Upgrade

Download and run the newer installer. It replaces the program and keeps your data folder
and knowledge base. If the new version uses a newer database layout, RUDRA upgrades the
knowledge base on first start, after saving a copy in `%LOCALAPPDATA%\RUDRA\data\backups\`.

## Move to another computer

Back up your knowledge on the old computer (**Settings → Back up your knowledge**),
install RUDRA on the new one, and use **Settings → Restore from a backup**. Optional AI
keys are not part of backups; add your key again on the new computer if you use AI
assistance.

## Uninstall

**RUDRA's own Settings → Uninstall RUDRA**, Windows' **Settings → Apps → Installed apps →
RUDRA → Uninstall**, or **Uninstall RUDRA** in the Start Menu all run the same uninstaller.
The program and shortcuts are removed; **your data folder is kept**. To remove everything,
delete `%LOCALAPPDATA%\RUDRA\` afterwards, and, if you added an AI key, remove it in
Settings before uninstalling (or delete the `RUDRA/ai/...` entry in Windows Credential
Manager).
