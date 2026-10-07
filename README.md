# Lumina Gallery Server

Server companion for the **Lumina Gallery** iPhone app. It runs on your Windows PC, Mac, or Linux computer and shares a folder of photos and videos with your iPhone over your home Wi-Fi — and receives backups from the phone.

Everything stays on your own network: no accounts, no cloud.

---

## Install on Windows

1. Download the Windows ZIP (`lumina-server-windows-<version>.zip`, or **Code → Download ZIP** on GitHub).
2. Right-click the ZIP → **Extract All…** (don't run it from inside the ZIP).
3. Open the extracted folder and **double-click `install_windows.bat`**.

The installer explains each step and asks before doing anything that needs permission:

| Step | What happens |
|---|---|
| Program | Copied to `%LOCALAPPDATA%\LuminaServer\app` (so a download folder with spaces, `&` or `( )` in its name doesn't matter). Settings live in `%LOCALAPPDATA%\LuminaServer\config\config.json`, logs in `…\LuminaServer\logs`. |
| Python | Uses an installed Python 3.10–3.14 (64-bit). If none is found it offers `winget install Python.Python.3.13 --scope user` (no admin), or opens python.org. |
| Packages | A private environment in `%LOCALAPPDATA%\LuminaServer\venv` with pinned, pre-built packages (no compiler needed). HEIC thumbnail support is optional. |
| Photo folder | Pick it in a folder window. A whole drive (`D:\`), your user folder, Windows/Program Files/AppData, and the server's own folder are refused. |
| Access code | Let the installer generate a strong code (recommended) or type your own twice (12+ characters with lower case, UPPER case, a number and a symbol). |
| Ports | HTTP **8500** and HTTPS **8543** unless you change them. |
| Firewall | The **only** step that needs administrator permission (one Windows prompt): an inbound rule named *Lumina Gallery Server* for **Private** networks and your **local subnet** only. |
| Auto-start | A per-user scheduled task starts the server in the background when you sign in (no admin). |
| Start Menu | **Lumina Server – Start / Stop / Status / Add Folder / Uninstall**. |

At the end the installer starts the server, checks that it answers, and shows the address to type into the app (e.g. `http://192.168.1.20:8500`) plus your access code if it generated one.

### Everyday use (Windows)

- **Lumina Server – Status**: running or not, the address(es) for the app, ports, the full certificate fingerprint, the last 20 log lines, and — only if you ask — the access code.
- **Lumina Server – Stop / Start**: stop or start the background server (it starts again at your next sign-in).
- **Lumina Server – Add Folder**: add another backup folder, e.g. one per phone (same access code; each phone picks its folder in the app).
- **Lumina Server – Uninstall**: removes the program, task, shortcuts and (if you agree) the firewall rule and settings. **Your photos are never touched.**

### "The phone can't connect"

- Phone and PC must be on the **same Wi-Fi/network**.
- Windows must treat your home network as **Private**: *Settings → Network & internet → Wi-Fi (or Ethernet) → your network → Network profile type → Private*. On a *Public* network the firewall rule does not apply (by design). The installer detects this and can switch it for you.
- If you declined the administrator prompt, run `install_windows.bat` again (settings are kept) and accept it.
- Open **Status** and try the address shown there.

### Updating

Download the new version, extract it, and double-click `install_windows.bat` again. It stops the server, updates the program and packages, shows your current settings and defaults to **Keep current settings**. Your access code, secret key and certificate are kept, so phones stay connected.

**Coming from an older version** (the one that kept a `.env` file next to `server.py`)? Run the new installer from that old folder, or point it to the old folder when it asks: your photo folder, access code, secret key, extra folders and TLS certificate are imported into the new `config.json`, and the old `PictureViewerServer` startup task is removed so the two don't fight over the port. The old folder itself is not changed — delete it once everything works. (Keeping the old way — `git pull` and running `server.py` with your `.env` — also still works.)

---

## Install on macOS

1. Download `lumina-server-macos-<version>.zip` and double-click it to extract.
2. Double-click **`install_mac.command`**. The first time, macOS may say it can't be opened because it's from the internet: **right-click → Open** (on macOS 15 and later: *System Settings → Privacy & Security → Open Anyway*). Or run it in Terminal: `bash install_mac.command`.

It installs per user (no administrator rights) to `~/Library/Application Support/LuminaServer`, needs Python 3.10–3.14 (it tells you how to get one: the python.org installer, or `brew install python@3.13`), asks the same questions as on Windows, and installs a **LaunchAgent** (`~/Library/LaunchAgents/com.lumina.server.plist`) so the server starts at login and restarts if it crashes. Double-click helpers (**Start / Stop / Status / Add Folder / Uninstall**) are put in `~/Applications/Lumina Server`.

If the macOS firewall is on, macOS asks once whether Python may accept incoming connections — click **Allow** (or let the installer allow it, which asks for your password). Folders in Desktop/Documents/Downloads or on external drives can be blocked by macOS privacy protection for background programs; a folder such as `~/Pictures/Lumina` avoids that.

## Install on Linux

```bash
tar xzf lumina-server-linux-<version>.tar.gz
cd lumina-server-linux-<version>
bash install_linux.sh
```

Installs per user to `~/.local/share/lumina-server` (no sudo), needs Python 3.10+ with `venv` (on Debian/Ubuntu: `sudo apt install python3 python3-venv`), and sets up a **systemd user service** (`lumina-server.service`). Run `sudo loginctl enable-linger $USER` if it should run while you're logged out. Control it with `lumina-server start | stop | restart | status | add-folder | uninstall` (linked into `~/.local/bin`). If you use ufw, the installer prints — and can run — a rule that allows only your LAN subnet.

On every platform, re-running the installer updates the server and keeps your settings.

---

## Automatic organization

Uploads from the phone are sorted into:

```
Photos/2026/01-January/IMG_0001.HEIC
Videos/2026/01-January/IMG_0002.MOV
```

- The date comes from the photo's EXIF capture date / the video's metadata, then a date in the filename, then the Year/Month folder the file already sits in, then the file's modified time.
- **Original filenames are kept** (including non-English names like `東京.jpg`). Only characters Windows can't store (`< > : " / \ | ? *`, control characters) are replaced with `_`. If a name is taken, `_1`, `_2`, … is added — nothing is ever overwritten.
- **Existing files**: the installer asks *"Organize photos already in this folder…?"*. If yes, files already in the folder are **moved** (never deleted, never overwritten) into the same structure each time the server starts. Online-only OneDrive/iCloud files, files inside app libraries (Photos Library, Lightroom catalogs …), files whose contents don't match their extension, and symlinks are left alone. Only folders that the organizer itself emptied are removed; folders you created empty stay.
- **Preview first**: `python server.py --organize-dry-run` lists every planned move and moves nothing. (Windows: run it with `%LOCALAPPDATA%\LuminaServer\venv\Scripts\python.exe` from `%LOCALAPPDATA%\LuminaServer\app` with `PICTUREVIEWER_CONFIG` pointing at your `config.json`.)

Settings in `config.json` (or `.env` for older installs):

| Setting | `config.json` key | `.env` / environment | Default |
|---|---|---|---|
| Sort uploads | `auto_organize` | `PICTUREVIEWER_AUTO_ORGANIZE` | `true` |
| Also sort existing files at startup | `organize_existing` | `PICTUREVIEWER_ORGANIZE_EXISTING` | `true` |

Shared folders are checked at startup: a whole drive, your home folder, system folders, or a folder containing the server itself are refused with a clear error.

---

## Configuration reference

`config.json` is written by the installers (values are JSON, so special characters in codes and paths need no escaping). Its location is `PICTUREVIEWER_CONFIG`, else `config.json` next to `config.py`. Keys: `media_folder`, `access_code`, `secret_key`, `port`, `https_port`, `auto_organize`, `organize_existing`, `allowed_hosts`, `log_dir`, `cert_dir`, `thumbnail_dir`, `max_upload_size_mb`, `media_roots`. Extra backup folders live in `folders.json` beside it.

Older installs keep working with their `.env` file (`PICTUREVIEWER_MEDIA_FOLDER`, `PICTUREVIEWER_ACCESS_CODE`, `PICTUREVIEWER_SECRET_KEY`, optional `PICTUREVIEWER_PORT` / `PICTUREVIEWER_HTTPS_PORT`). `config.json` wins over the environment, which wins over `.env`. `.env` values are read literally (no `${VAR}` expansion); **if a value contains a space followed by `#`, wrap it in single quotes** (`PICTUREVIEWER_ACCESS_CODE='My code #1 Rocks!'`), otherwise the rest is treated as a comment.

If no strong `secret_key` is configured, the server generates one and saves it (into `config.json`, or a `secret.key` file readable only by you) so restarts don't sign phones out. Changing the access code signs out all phones.

## Security notes

- The app learns the server's HTTPS port and certificate fingerprint on first contact and then talks only HTTPS, pinned to that certificate. When HTTPS is running, the access code is refused over plain HTTP.
- Unauthenticated callers can see that a server is there, but not folder names or paths.
- `/api/file` serves only photo/video types — never `.env`, keys or program files, even inside a shared folder.
- Never forward the server's ports on your router. For access away from home, use a VPN such as Tailscale — see [REMOTE_ACCESS.md](REMOTE_ACCESS.md).
- Logs (in the logs folder) never contain the access code or login tokens.

## Optional extras

- **HEIC thumbnails**: `pillow-heif` (installed automatically when available; `requirements-heic.txt`).
- **Video thumbnails**: install `ffmpeg` (Windows: `winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`; Linux: your package manager). Without it, videos show a placeholder.

---

## For developers

- `python tests/test_organize.py` — self-contained checks (no pytest, no network).
- `packaging/build_release.sh` — builds `dist/lumina-server-{windows,macos,linux}-<VERSION>` archives (the version comes from `VERSION`) and prints their sizes and SHA-256 (also written to `dist/SHA256SUMS`).
- Run from source: `python3 -m venv venv && venv/bin/pip install -r requirements.txt && venv/bin/python server.py` with a `config.json` next to `config.py`.

| File | What it does |
|---|---|
| `server.py` | Flask server: auth, listing, originals, thumbnails, uploads, organizing. |
| `config.py` | Settings loader (`config.json` → environment → `.env`). |
| `safety.py` | Shared rules: safe filenames, dangerous-folder checks, cloud-placeholder detection. |
| `lumina_admin.py` | Helper the installers call (values via stdin; one rule set). |
| `run.py` | Entry point used by the auto-start task / LaunchAgent / systemd unit. |
| `add_folder.py` | Add an extra backup folder (also via the Add Folder shortcut). |
| `install_windows.bat`, `windows/*.ps1` | Windows installer and Start Menu tools. |
| `install_mac.command`, `install_linux.sh`, `unix/*.sh` | macOS/Linux installer and `lumina` control script. |
| `installer/` | Legacy GUI launcher / app-bundle build scripts (unmaintained; not part of releases). |
