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

> **Access code requirements:** the code that connects the app to your server must be **at least 12 characters** and include a **lowercase letter, an uppercase letter, a number, and a special character** (e.g. `Sunset#Beach2026`). The server refuses to start with a weaker code.

After it's running, the status window has a **Manage Folders…** button so you can add multiple folders that the iPhone can switch between.

### Automatic organization

The server automatically sorts your photos and videos into a tidy structure:

```
Pictures/2026/June/IMG_0001.jpg
Videos/2026/June/VID_0001.mov
```

- Photos go under **`Pictures/<Year>/<Month>/`**, videos under **`Videos/<Year>/<Month>/`**.
- The date comes from the photo's EXIF capture date / the video's metadata, falling back to the filename date, then the file's modified time.
- This runs on every uploaded file, **and** once at startup it sorts any loose files already sitting in your shared folder (existing files are only ever *moved*, never deleted, and never overwritten — name clashes get a `_1`, `_2` suffix).

To turn this off and keep uploads in whatever folder the app chooses, set the environment variable `PICTUREVIEWER_AUTO_ORGANIZE=0` (or edit `AUTO_ORGANIZE = False` in `config.py`).

---

## Requirements
- iPhone and the computer on the **same Wi-Fi**.
- The iOS app: search for **PictureViewer / Lumina Gallery** on the App Store.

---

## Install from source (developers / Linux)

If you want to run from the Python source instead of using the installer:

| Platform | How |
|---|---|
| macOS | `bash install_mac.sh` |
| **Windows** | **Double-click `install_windows.bat`** (in File Explorer) — or run `install_windows.bat` in a terminal |
| Linux | `bash install_linux.sh` |

Requires Python 3.8 or newer. The script creates a virtual environment, installs dependencies, runs a setup prompt, and creates a launcher.

> **Windows note:** Use **`install_windows.bat`** — it runs in Command Prompt and is **not** affected by PowerShell's script-execution policy. (Running `install_windows.ps1` directly fails with *"…is not digitally signed… cannot be loaded"* unless you prefix it with `powershell -ExecutionPolicy Bypass -File install_windows.ps1`. The `.bat` avoids that entirely — just double-click it.)

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
