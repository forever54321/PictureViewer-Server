#!/usr/bin/env python3
"""macOS Setup Wizard for PictureViewer Server — Tkinter GUI."""

import os
import sys
import json
import secrets
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

APP_SUPPORT = os.path.expanduser("~/Library/Application Support/PictureViewer")
CONFIG_FILE = os.path.join(APP_SUPPORT, "config.json")


class SetupWizard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PictureViewer Server Setup")
        self.root.geometry("480x420")
        self.root.resizable(False, False)

        bg = "#1e1e2e"
        fg = "#cdd6f4"
        entry_bg = "#313244"
        accent = "#cba6f7"

        self.root.configure(bg=bg)

        tk.Label(self.root, text="PictureViewer Server", font=("SF Pro Display", 18, "bold"),
                 bg=bg, fg=fg).pack(pady=(20, 5))
        tk.Label(self.root, text="Configure your media server",
                 font=("SF Pro Display", 11), bg=bg, fg="#6c7086").pack(pady=(0, 20))

        frame = tk.Frame(self.root, bg=bg)
        frame.pack(padx=30, fill="x")

        # Media folder
        tk.Label(frame, text="Media Folder:", font=("SF Pro Display", 10, "bold"),
                 bg=bg, fg=fg, anchor="w").pack(fill="x")
        folder_frame = tk.Frame(frame, bg=bg)
        folder_frame.pack(fill="x", pady=(2, 14))

        self.folder_var = tk.StringVar(value=str(Path.home() / "Pictures"))
        tk.Entry(folder_frame, textvariable=self.folder_var, font=("SF Mono", 10),
                 bg=entry_bg, fg=fg, insertbackground=fg, relief="flat", bd=4
                 ).pack(side="left", fill="x", expand=True)
        tk.Button(folder_frame, text="Browse", command=self._browse,
                  bg=accent, fg="#1e1e2e", relief="flat", padx=10,
                  font=("SF Pro Display", 9, "bold")).pack(side="right", padx=(6, 0))

        # Port
        tk.Label(frame, text="Port:", font=("SF Pro Display", 10, "bold"),
                 bg=bg, fg=fg, anchor="w").pack(fill="x")
        self.port_var = tk.StringVar(value="8500")
        tk.Entry(frame, textvariable=self.port_var, font=("SF Mono", 10),
                 bg=entry_bg, fg=fg, insertbackground=fg, relief="flat", bd=4
                 ).pack(fill="x", pady=(2, 14))

        # Access code
        tk.Label(frame, text="Access Code:", font=("SF Pro Display", 10, "bold"),
                 bg=bg, fg=fg, anchor="w").pack(fill="x")
        self.code_var = tk.StringVar(value="picture123")
        tk.Entry(frame, textvariable=self.code_var, font=("SF Mono", 10),
                 bg=entry_bg, fg=fg, insertbackground=fg, relief="flat", bd=4
                 ).pack(fill="x", pady=(2, 14))

        # Auto-start
        self.autostart_var = tk.BooleanVar(value=True)
        tk.Checkbutton(frame, text="Start automatically on login",
                        variable=self.autostart_var, bg=bg, fg=fg,
                        selectcolor=entry_bg, activebackground=bg, activeforeground=fg,
                        font=("SF Pro Display", 10)).pack(anchor="w", pady=(0, 20))

        # Save button
        tk.Button(self.root, text="Save & Start Server", command=self._save,
                  bg=accent, fg="#1e1e2e", font=("SF Pro Display", 12, "bold"),
                  relief="flat", padx=30, pady=8, cursor="hand2").pack(pady=(0, 20))

    def _browse(self):
        folder = filedialog.askdirectory(title="Select Media Folder")
        if folder:
            self.folder_var.set(folder)

    def _save(self):
        folder = self.folder_var.get().strip()
        port = self.port_var.get().strip()
        code = self.code_var.get().strip()

        if not folder or not port.isdigit() or not code:
            messagebox.showerror("Error", "Please fill in all fields correctly.")
            return

        os.makedirs(folder, exist_ok=True)
        os.makedirs(APP_SUPPORT, exist_ok=True)

        config = {
            "media_folder": folder,
            "port": int(port),
            "access_code": code,
            "secret_key": secrets.token_hex(32),
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

        # Register LaunchAgent if requested
        if self.autostart_var.get():
            self._register_launch_agent(config)

        self.root.destroy()

    def _register_launch_agent(self, config):
        try:
            venv_python = os.path.join(APP_SUPPORT, "venv", "bin", "python3")
            resources = os.path.dirname(os.path.abspath(__file__))
            server_py = os.path.join(resources, "server.py")

            plist_dir = os.path.expanduser("~/Library/LaunchAgents")
            os.makedirs(plist_dir, exist_ok=True)
            plist_path = os.path.join(plist_dir, "com.pictureviewer.server.plist")

            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.pictureviewer.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>{venv_python}</string>
        <string>{server_py}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{resources}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PICTUREVIEWER_MEDIA_FOLDER</key>
        <string>{config['media_folder']}</string>
        <key>PICTUREVIEWER_ACCESS_CODE</key>
        <string>{config['access_code']}</string>
        <key>PICTUREVIEWER_SECRET_KEY</key>
        <string>{config['secret_key']}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{APP_SUPPORT}/server.log</string>
    <key>StandardErrorPath</key>
    <string>{APP_SUPPORT}/server_error.log</string>
</dict>
</plist>"""

            with open(plist_path, "w") as f:
                f.write(plist_content)

            os.system(f"launchctl load '{plist_path}' 2>/dev/null")
        except Exception:
            pass


if __name__ == "__main__":
    wizard = SetupWizard()
    wizard.root.mainloop()
