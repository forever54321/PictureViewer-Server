#!/usr/bin/env python3
"""
PictureViewer Server - Windows Launcher
Standalone executable entry point. On first run, shows a setup wizard.
On subsequent runs, starts the server directly.
"""

import os
import sys
import json
import secrets
import socket
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

# Add parent dirs so we can import server modules
APP_DIR = os.path.dirname(os.path.abspath(__file__))

# When running as PyInstaller bundle, files are in _MEIPASS
if getattr(sys, '_MEIPASS', None):
    BUNDLE_DIR = sys._MEIPASS
else:
    BUNDLE_DIR = os.path.join(APP_DIR, "..", "..")

sys.path.insert(0, BUNDLE_DIR)

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PictureViewer")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
THUMB_DIR = os.path.join(CONFIG_DIR, "thumbnails")


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return None


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class SetupWizard:
    def __init__(self):
        self.result = None
        self.root = tk.Tk()
        self.root.title("PictureViewer Server Setup")
        self.root.geometry("500x480")
        self.root.resizable(False, False)

        try:
            self.root.configure(bg="#1a1a2e")
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        bg = "#1a1a2e"
        fg = "#ffffff"
        entry_bg = "#16213e"
        accent = "#7b2ff7"

        # Title
        tk.Label(self.root, text="PictureViewer Server", font=("Helvetica", 18, "bold"),
                 bg=bg, fg=fg).pack(pady=(20, 5))
        tk.Label(self.root, text="First-time setup — configure your server",
                 font=("Helvetica", 10), bg=bg, fg="#aaaaaa").pack(pady=(0, 20))

        frame = tk.Frame(self.root, bg=bg)
        frame.pack(padx=30, fill="x")

        # Media folder
        tk.Label(frame, text="Media Folder:", font=("Helvetica", 10, "bold"),
                 bg=bg, fg=fg, anchor="w").pack(fill="x")
        folder_frame = tk.Frame(frame, bg=bg)
        folder_frame.pack(fill="x", pady=(2, 12))

        default_folder = str(Path.home() / "Pictures")
        self.folder_var = tk.StringVar(value=default_folder)
        self.folder_entry = tk.Entry(folder_frame, textvariable=self.folder_var,
                                      font=("Helvetica", 10), bg=entry_bg, fg=fg,
                                      insertbackground=fg, relief="flat", bd=4)
        self.folder_entry.pack(side="left", fill="x", expand=True)

        browse_btn = tk.Button(folder_frame, text="Browse", command=self._browse_folder,
                                bg=accent, fg=fg, relief="flat", padx=10, font=("Helvetica", 9))
        browse_btn.pack(side="right", padx=(6, 0))

        # Port
        tk.Label(frame, text="Port:", font=("Helvetica", 10, "bold"),
                 bg=bg, fg=fg, anchor="w").pack(fill="x")
        self.port_var = tk.StringVar(value="8500")
        tk.Entry(frame, textvariable=self.port_var, font=("Helvetica", 10),
                 bg=entry_bg, fg=fg, insertbackground=fg, relief="flat", bd=4
                 ).pack(fill="x", pady=(2, 12))

        # Access code
        tk.Label(frame, text="Access Code:", font=("Helvetica", 10, "bold"),
                 bg=bg, fg=fg, anchor="w").pack(fill="x")
        self.code_var = tk.StringVar(value="picture123")
        tk.Entry(frame, textvariable=self.code_var, font=("Helvetica", 10),
                 bg=entry_bg, fg=fg, insertbackground=fg, relief="flat", bd=4
                 ).pack(fill="x", pady=(2, 12))

        # Auto-start checkbox
        self.autostart_var = tk.BooleanVar(value=False)
        tk.Checkbutton(frame, text="Start automatically on login",
                        variable=self.autostart_var, bg=bg, fg=fg,
                        selectcolor=entry_bg, activebackground=bg, activeforeground=fg,
                        font=("Helvetica", 9)).pack(anchor="w", pady=(0, 12))

        # Firewall checkbox
        self.firewall_var = tk.BooleanVar(value=True)
        tk.Checkbutton(frame, text="Add Windows Firewall rule",
                        variable=self.firewall_var, bg=bg, fg=fg,
                        selectcolor=entry_bg, activebackground=bg, activeforeground=fg,
                        font=("Helvetica", 9)).pack(anchor="w", pady=(0, 20))

        # Start button
        start_btn = tk.Button(self.root, text="Start Server", command=self._start,
                               bg=accent, fg=fg, font=("Helvetica", 12, "bold"),
                               relief="flat", padx=30, pady=8, cursor="hand2")
        start_btn.pack(pady=(0, 20))

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select Media Folder")
        if folder:
            self.folder_var.set(folder)

    def _start(self):
        folder = self.folder_var.get().strip()
        port = self.port_var.get().strip()
        code = self.code_var.get().strip()

        if not folder:
            messagebox.showerror("Error", "Please select a media folder")
            return
        if not port.isdigit():
            messagebox.showerror("Error", "Port must be a number")
            return
        if not code:
            messagebox.showerror("Error", "Please enter an access code")
            return

        os.makedirs(folder, exist_ok=True)

        self.result = {
            "media_folder": folder,
            "port": int(port),
            "access_code": code,
            "secret_key": secrets.token_hex(32),
            "autostart": self.autostart_var.get(),
            "firewall": self.firewall_var.get(),
        }
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result


def add_firewall_rule(port):
    try:
        import subprocess
        subprocess.run([
            "netsh", "advfirewall", "firewall", "add", "rule",
            "name=PictureViewer Server", "dir=in", "action=allow",
            "protocol=TCP", f"localport={port}",
        ], capture_output=True)
    except Exception:
        pass


def add_autostart():
    try:
        import subprocess
        exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        subprocess.run([
            "schtasks", "/create", "/tn", "PictureViewerServer",
            "/tr", f'"{exe_path}"', "/sc", "onlogon", "/rl", "highest", "/f"
        ], capture_output=True)
    except Exception:
        pass


def show_running_window(ip, port, code, cfg):
    """Show a small status window while server runs."""
    root = tk.Tk()
    root.title("PictureViewer Server")
    root.geometry("460x360")
    root.resizable(False, False)

    bg = "#1a1a2e"
    fg = "#ffffff"
    root.configure(bg=bg)

    tk.Label(root, text="PictureViewer Server", font=("Helvetica", 16, "bold"),
             bg=bg, fg=fg).pack(pady=(20, 5))

    tk.Label(root, text="Running", font=("Helvetica", 11),
             bg=bg, fg="#00ff88").pack(pady=(0, 15))

    info_frame = tk.Frame(root, bg="#16213e", padx=15, pady=15)
    info_frame.pack(padx=20, fill="x")

    url = f"http://{ip}:{port}"
    folder_label = tk.Label(info_frame, text="", font=("Helvetica", 9),
                            bg="#16213e", fg=fg, anchor="w", wraplength=300, justify="left")

    def render_rows():
        for w in info_frame.winfo_children():
            w.destroy()
        for label, value in [("Server URL:", url),
                             ("Access Code:", code),
                             ("Media Folder:", cfg["media_folder"])]:
            row = tk.Frame(info_frame, bg="#16213e")
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, font=("Helvetica", 9, "bold"),
                     bg="#16213e", fg="#aaaaaa", width=14, anchor="w").pack(side="left")
            tk.Label(row, text=value, font=("Helvetica", 9),
                     bg="#16213e", fg=fg, anchor="w", wraplength=290, justify="left").pack(
                         side="left", fill="x")

    render_rows()

    tk.Label(root, text="Enter the URL and access code in the iOS app to connect.",
             font=("Helvetica", 8), bg=bg, fg="#888888", wraplength=380).pack(pady=(15, 5))

    def change_folder():
        new_folder = filedialog.askdirectory(
            title="Choose Media Folder",
            initialdir=cfg["media_folder"] if os.path.isdir(cfg["media_folder"]) else os.path.expanduser("~")
        )
        if not new_folder:
            return
        if not os.path.isdir(new_folder):
            messagebox.showerror("Folder not accessible",
                                 f"Cannot access:\n{new_folder}")
            return
        cfg["media_folder"] = new_folder
        save_config(cfg)

        # Apply live so the user doesn't have to restart the server
        try:
            import config as srv_config
            srv_config.MEDIA_FOLDER = new_folder
            os.environ["PICTUREVIEWER_MEDIA_FOLDER"] = new_folder
        except Exception:
            pass

        render_rows()
        messagebox.showinfo("Folder updated",
                            "Media folder updated. Connected iPhones may need to refresh.")

    def on_close():
        root.destroy()
        os._exit(0)

    root.protocol("WM_DELETE_WINDOW", on_close)

    btn_row = tk.Frame(root, bg=bg)
    btn_row.pack(pady=(10, 15))

    tk.Button(btn_row, text="Change Folder…", command=change_folder,
              bg="#7b2ff7", fg=fg, relief="flat", padx=14, pady=4,
              font=("Helvetica", 9)).pack(side="left", padx=4)

    tk.Button(btn_row, text="Stop Server", command=on_close,
              bg="#e74c3c", fg=fg, relief="flat", padx=14, pady=4,
              font=("Helvetica", 9)).pack(side="left", padx=4)

    root.mainloop()


def ensure_media_folder(cfg):
    """Validate the saved media folder before starting the server. If missing
    (e.g. external drive not mounted, folder renamed), prompt the user instead
    of silently fabricating an empty default folder."""
    folder = cfg.get("media_folder", "")
    if folder and os.path.isdir(folder):
        return cfg

    root = tk.Tk()
    root.withdraw()
    msg = (f"Saved media folder is not accessible:\n\n{folder}\n\n"
           "Choose a new folder, or quit the server.")
    if not messagebox.askokcancel("PictureViewer — Folder Missing", msg,
                                  icon="warning", default="ok"):
        root.destroy()
        sys.exit(0)

    new_folder = filedialog.askdirectory(
        title="Choose Media Folder",
        initialdir=os.path.expanduser("~")
    )
    root.destroy()

    if not new_folder or not os.path.isdir(new_folder):
        sys.exit(0)

    cfg["media_folder"] = new_folder
    save_config(cfg)
    return cfg


def run_server(cfg):
    """Start the Flask server with the saved config."""
    cfg = ensure_media_folder(cfg)

    os.environ["PICTUREVIEWER_MEDIA_FOLDER"] = cfg["media_folder"]
    os.environ["PICTUREVIEWER_ACCESS_CODE"] = cfg["access_code"]
    os.environ["PICTUREVIEWER_SECRET_KEY"] = cfg["secret_key"]

    # Patch config module
    import config as srv_config
    srv_config.MEDIA_FOLDER = cfg["media_folder"]
    srv_config.ACCESS_CODE = cfg["access_code"]
    srv_config.SECRET_KEY = cfg["secret_key"]
    srv_config.PORT = cfg.get("port", 8500)
    srv_config.THUMBNAIL_FOLDER = THUMB_DIR

    os.makedirs(THUMB_DIR, exist_ok=True)
    # Intentionally do NOT mkdir cfg["media_folder"] — ensure_media_folder
    # already verified it exists. Silent creation here used to mask drive-not-
    # mounted errors and reset users to an empty default folder.

    import server as srv
    ip = get_local_ip()
    port = cfg.get("port", 8500)

    # Run Flask in background thread
    flask_thread = threading.Thread(
        target=lambda: srv.app.run(host="0.0.0.0", port=port, debug=False, threaded=True),
        daemon=True
    )
    flask_thread.start()

    # Show status window on main thread
    show_running_window(ip, port, cfg["access_code"], cfg)


def main():
    cfg = load_config()

    if cfg is None:
        wizard = SetupWizard()
        cfg = wizard.run()
        if cfg is None:
            sys.exit(0)

        save_config(cfg)

        if cfg.get("firewall"):
            add_firewall_rule(cfg["port"])
        if cfg.get("autostart"):
            add_autostart()

    run_server(cfg)


if __name__ == "__main__":
    main()
