"""Pure safety rules shared by the server, add_folder.py, lumina_admin.py and
the Windows installer (which calls lumina_admin.py so there is ONE rule set).

Nothing in this module touches the network, reads config, or has import-time
side effects, so it is safe to import from any tool.
"""
from __future__ import annotations

import os
import re
import stat
import unicodedata

IS_WINDOWS = os.name == "nt"

# ---------------------------------------------------------------------------
# Filenames
# ---------------------------------------------------------------------------

# Characters Windows refuses in a filename, plus path separators.
_RESERVED_CHARS = set('<>:"/\\|?*')
# DOS device names — "CON.jpg" / "nul.mov" can't be created on Windows.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
    "COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3",
}
_MAX_NAME_BYTES = 240  # NTFS allows 255 UTF-16 units, ext4 255 bytes — stay under both


def safe_filename(name: str) -> str:
    """Make a client- or disk-supplied filename safe to create, KEEPING it as
    close to the original as possible.

    Unlike werkzeug.secure_filename this never strips non-ASCII characters
    (``東京.jpg`` stays ``東京.jpg``) and never rewrites spaces. It only:
      * drops any directory part (``../../x.jpg`` -> ``x.jpg``),
      * replaces NUL / control characters and Windows-reserved ``<>:"/\\|?*``
        with ``_``,
      * strips leading/trailing spaces, trailing dots and leading dots
        (a leading dot would make the file hidden),
      * prefixes Windows device names (``CON.jpg`` -> ``_CON.jpg``),
      * caps the length (keeping the extension).
    Returns "" if nothing usable is left.
    """
    if not name:
        return ""
    name = unicodedata.normalize("NFC", str(name))
    # Keep only the final path component, whatever separator the client used.
    name = name.replace("\\", "/").split("/")[-1]
    out = []
    for ch in name:
        if ch in _RESERVED_CHARS or ord(ch) < 32 or ord(ch) == 0x7F:
            out.append("_")
        elif unicodedata.category(ch) in ("Cc", "Cf") and ch not in ("\u200d",):
            # other invisible control/format chars (e.g. RTL override U+202E,
            # which can disguise "gpj.exe" as "exe.jpg")
            out.append("_")
        else:
            out.append(ch)
    name = "".join(out).strip().rstrip(". ").lstrip(". ")
    if not name or set(name) <= {"_", "."}:
        return ""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    base_upper = stem.split(".")[0].strip().upper() if stem else ""
    if base_upper in _RESERVED_NAMES or (not stem and name.upper() in _RESERVED_NAMES):
        name = "_" + name
        stem = "_" + stem if stem else stem
    # Length cap in UTF-8 bytes, preserving the extension.
    if len(name.encode("utf-8")) > _MAX_NAME_BYTES:
        ext_part = ("." + ext) if dot and len(ext) <= 16 else ""
        stem_part = name[: len(name) - len(ext_part)] if ext_part else name
        budget = _MAX_NAME_BYTES - len(ext_part.encode("utf-8"))
        while len(stem_part.encode("utf-8")) > budget:
            stem_part = stem_part[:-1]
        name = stem_part.rstrip(". ") + ext_part
    return name


# ---------------------------------------------------------------------------
# Cloud placeholders (OneDrive / iCloud for Windows "online-only" files)
# ---------------------------------------------------------------------------

FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_CLOUD_MASK = (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN
               | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)


def is_cloud_placeholder(st) -> bool:
    """True if a stat result describes an online-only cloud file. Opening or
    moving such a file would download it (or fail) — we leave it alone.
    ``st_file_attributes`` only exists on Windows; elsewhere this is False."""
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(attrs & _CLOUD_MASK)


def is_reparse_or_link(st) -> bool:
    if stat.S_ISLNK(getattr(st, "st_mode", 0)):
        return True
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT (junctions etc.)


# ---------------------------------------------------------------------------
# Dangerous shared-folder roots
# ---------------------------------------------------------------------------

def unquote_path(p) -> str:
    """Trim whitespace and ONE pair of surrounding quotes (as pasted from
    Explorer's "Copy as path"); quotes inside a name are kept."""
    p = str(p or "").strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
        p = p[1:-1].strip()
    return p


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(p))))


def _inside(child: str, parent: str) -> bool:
    """child == parent or child is below parent (both already _norm'ed)."""
    if child == parent:
        return True
    parent_sep = parent if parent.endswith(os.sep) else parent + os.sep
    return child.startswith(parent_sep)


def _protected_dirs(home: str):
    """(equal_only, inside_too) lists of system locations."""
    equal_only, inside_too = [], []
    home_parent = os.path.dirname(home.rstrip("\\/")) if home else ""
    if home_parent:
        equal_only.append(home_parent)                  # C:\Users, /Users, /home
    if IS_WINDOWS:
        env = os.environ
        for var in ("WINDIR", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)",
                    "ProgramW6432", "ProgramData", "APPDATA", "LOCALAPPDATA"):
            v = env.get(var)
            if v:
                inside_too.append(v)
        if home:
            inside_too.append(os.path.join(home, "AppData"))
        sysdrive = env.get("SystemDrive", "C:") + "\\"
        inside_too.extend([os.path.join(sysdrive, "Windows"),
                           os.path.join(sysdrive, "Program Files"),
                           os.path.join(sysdrive, "Program Files (x86)"),
                           os.path.join(sysdrive, "ProgramData"),
                           os.path.join(sysdrive, "$Recycle.Bin"),
                           os.path.join(sysdrive, "System Volume Information")])
    else:
        inside_too.extend(["/bin", "/sbin", "/boot", "/dev", "/etc", "/lib",
                           "/lib64", "/proc", "/sys", "/usr", "/System",
                           "/Library", "/Applications", "/private/etc",
                           "/private/var/db"])
        equal_only.extend(["/var", "/private", "/private/var", "/tmp",
                           "/private/tmp", "/opt", "/srv", "/mnt", "/media",
                           "/Volumes", "/home", "/Users", "/root"])
        if home:
            inside_too.append(os.path.join(home, "Library"))
    return equal_only, inside_too


def root_problem(path: str, *, app_dirs=(), home: str | None = None) -> str | None:
    """Return a human-readable reason why `path` must NOT be used as a shared
    (and auto-organized) folder, or None if it is acceptable.

    Refused: empty paths, drive/filesystem roots and mount points, the user's
    home folder itself (and its parent, e.g. C:\\Users), system folders
    (Windows, Program Files, ProgramData, AppData, /usr, /System, ~/Library …),
    and any folder that contains — or is inside — the server's own app/config
    directories.
    """
    if not path or not str(path).strip():
        return "no folder was given"
    raw = str(path).strip()
    if IS_WINDOWS and re.fullmatch(r"[A-Za-z]:", raw):
        return f"'{raw}' is a whole drive — pick a folder on it instead (e.g. {raw}\\Photos)"
    p = _norm(raw)
    head, tail = os.path.split(p.rstrip("\\/") or p)
    if not tail or p == os.path.dirname(p) or os.path.splitdrive(p)[1] in ("\\", "/", ""):
        return f"'{raw}' is the root of a drive — pick a folder on it instead (e.g. a 'Photos' folder)"
    try:
        if os.path.ismount(p):
            return (f"'{raw}' is the top of a drive/volume — create a folder on it "
                    "(e.g. 'Photos') and choose that instead")
    except OSError:
        pass
    home_n = _norm(home if home is not None else os.path.expanduser("~"))
    if p == home_n:
        return ("that is your whole user folder — choose a folder inside it, "
                "such as Pictures")
    equal_only, inside_too = _protected_dirs(home_n)
    for d in equal_only:
        if d and p == _norm(d):
            return f"'{raw}' is a system folder — choose a folder of your own photos"
    for d in inside_too:
        if d and _inside(p, _norm(d)):
            return f"'{raw}' is (inside) a system/app-data folder — choose a folder of your own photos"
    for a in app_dirs:
        if not a:
            continue
        an = _norm(a)
        if _inside(an, p):
            return ("that folder contains the server's own program/config files — "
                    "choose a different folder")
        if _inside(p, an):
            return "that folder is inside the server's own program/config folder"
    return None
