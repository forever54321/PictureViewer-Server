# PictureViewer Server

Server companion for the **PictureViewer** iOS app. Runs on your Mac, Windows PC, or Linux machine and shares one or more folders of photos and videos with your iPhone over your local Wi-Fi.

---

## Quickest install (no commands)

Download the pre-built installer for your computer from the **[Releases page →](../../releases/latest)**

| If you have… | Download | Then |
|---|---|---|
| **Windows** | `PictureViewerServer.exe` | Double-click. Click *More info → Run anyway* if SmartScreen warns. The setup wizard appears. |
| **macOS** | `PictureViewerServer.dmg` | Open it, drag the app into **Applications**, then launch it. |

That's it — no Python, no terminal, no `pip`. The setup wizard asks for your photo folder and an access code, then shows the URL/code to type into the iPhone app.

After it's running, the status window has a **Manage Folders…** button so you can add multiple folders that the iPhone can switch between.

---

## Requirements
- iPhone and the computer on the **same Wi-Fi**.
- The iOS app: search for **PictureViewer / Lumina Gallery** on the App Store.

---

## Install from source (developers / Linux)

If you want to run from the Python source instead of using the installer:

| Platform | Command |
|---|---|
| macOS | `bash install_mac.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File install_windows.ps1` |
| Linux | `bash install_linux.sh` |

Requires Python 3.8 or newer. The script creates a virtual environment, installs dependencies, runs a setup prompt, and creates a launcher.

---

## Building the installers yourself

Each push of a `vX.Y.Z` tag to this repo triggers GitHub Actions ([`.github/workflows/build-installers.yml`](.github/workflows/build-installers.yml)) which builds and uploads `PictureViewerServer.exe` and `PictureViewerServer.dmg` to a new Release.

To build locally:

**Windows** (requires Python on Windows):
```bash
pip install pyinstaller
pip install --no-cache-dir -r requirements.txt
cd installer/windows
pyinstaller pictureviewer.spec --noconfirm
# → installer/windows/dist/PictureViewerServer.exe
```

**macOS**:
```bash
pip3 install Pillow
python3 installer/icon/generate_icons.py
bash installer/macos/build_macos.sh
# → installer/macos/PictureViewerServer.dmg
```

---

## Files
| File | What it does |
|------|---|
| `server.py` | Flask server — handles auth, listing, thumbnails, uploads. |
| `config.py` | Defaults for media folder, port, access code, allowed extensions. |
| `installer/windows/launcher.py` | GUI launcher with setup wizard + **Manage Folders…**. |
| `installer/windows/pictureviewer.spec` | PyInstaller spec → `.exe`. |
| `installer/macos/build_macos.sh` | Builds the macOS `.app` and `.dmg`. |
| `install_*.sh` / `install_windows.*` | Source-install convenience scripts. |
