# PictureViewer Server

Server component for the PictureViewer iOS app. Install on your Mac, Windows PC, or Linux machine to share a folder of photos and videos with the iOS app over your local network.

## Quick Install

### macOS
```bash
bash install_mac.sh
```

### Windows (PowerShell)
```powershell
powershell -ExecutionPolicy Bypass -File install_windows.ps1
```

### Linux
```bash
bash install_linux.sh
```

## Requirements
- Python 3.8+
- Both devices on the same Wi-Fi network

## How It Works
1. Run the installer on your computer
2. Choose your media folder and set an access code
3. The server will display your IP address and port
4. Enter that address and code in the PictureViewer iOS app

## Files
| File | Description |
|------|-------------|
| `install_mac.sh` | macOS installer |
| `install_windows.ps1` | Windows PowerShell installer |
| `install_windows.bat` | Windows batch installer (legacy) |
| `install_linux.sh` | Linux installer |
| `server.py` | Main server application |
| `config.py` | Server configuration |
| `requirements.txt` | Python dependencies |
