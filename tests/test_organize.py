#!/usr/bin/env python3
"""Self-contained checks for Lumina Gallery Server (no pytest needed, no
network sockets — Flask's test client only).

    python tests/test_organize.py

Exits non-zero if any check fails.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# --- isolated config BEFORE importing config/server -------------------------
TMP = Path(tempfile.mkdtemp(prefix="lumina_test_")).resolve()
MEDIA = TMP / "media"
MEDIA.mkdir()
CFG_DIR = TMP / "cfg"
CFG_DIR.mkdir()
CODE = "Sunset#Beach$2026"
SECRET = "s" * 64
(CFG_DIR / "config.json").write_text(json.dumps({
    "media_folder": str(MEDIA),
    "access_code": CODE,
    "secret_key": SECRET,
    "thumbnail_dir": str(TMP / "thumbs"),
    "cert_dir": str(TMP / "certs"),
    "log_dir": str(TMP / "logs"),
    "auto_organize": True,
    "organize_existing": True,
}), encoding="utf-8")
os.environ["PICTUREVIEWER_CONFIG"] = str(CFG_DIR / "config.json")
for k in list(os.environ):
    if k.startswith("PICTUREVIEWER_") and k != "PICTUREVIEWER_CONFIG":
        del os.environ[k]

import config  # noqa: E402
import safety  # noqa: E402
import server  # noqa: E402
from PIL import Image  # noqa: E402

RESULTS = []


def check(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def jpeg_bytes(color=(200, 10, 10), size=(64, 48), exif_date=None) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if exif_date:
        ex = Image.Exif()
        ex[306] = exif_date
        img.save(buf, "JPEG", exif=ex)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def mp4_bytes(n=4096) -> bytes:
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * n


def client():
    return server.app.test_client()


def token() -> str:
    r = client().post("/api/auth", json={"code": CODE})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["token"]


def auth_hdr():
    return {"Authorization": "Bearer " + token()}


def reset_media():
    shutil.rmtree(MEDIA)
    MEDIA.mkdir()
    server._auth_failures.clear()


# ---------------------------------------------------------------------------
# Filenames
# ---------------------------------------------------------------------------

@check("non-ASCII filenames are preserved")
def _():
    for n in ("東京.jpg", "Café au lait.HEIC", "фото 1.png", "صورة.mov", "😀 party.mp4"):
        assert safety.safe_filename(n) == n, (n, safety.safe_filename(n))


@check("reserved characters / separators / device names sanitized")
def _():
    f = safety.safe_filename
    assert f('a<b>:c"d|e?f*.jpg') == "a_b__c_d_e_f_.jpg", f('a<b>:c"d|e?f*.jpg')
    assert f("../../etc/passwd.jpg") == "passwd.jpg"
    assert f("..\\..\\Windows\\x.jpg") == "x.jpg"
    assert f("x\x00y\x1fz.jpg") == "x_y_z.jpg"
    assert f("CON.jpg") == "_CON.jpg" and f("nul") == "_nul" and f("Com1.tar.gz") == "_Com1.tar.gz"
    assert f("CONSOLE.jpg") == "CONSOLE.jpg"
    assert f(".hidden.jpg") == "hidden.jpg"
    assert f("trailing dots...") == "trailing dots"
    assert f("evil\u202egpj.exe") == "evil_gpj.exe"
    assert f("..") == "" and f("") == "" and f("///") == ""
    long = "é" * 300 + ".jpg"
    out = f(long)
    assert out.endswith(".jpg") and len(out.encode()) <= 240, len(out.encode())


# ---------------------------------------------------------------------------
# Dangerous roots
# ---------------------------------------------------------------------------

@check("dangerous roots rejected, normal folders accepted")
def _():
    rp = safety.root_problem
    home = str(Path.home())
    apps = [str(REPO)]
    assert rp("/", app_dirs=apps)
    assert rp("", app_dirs=apps)
    assert rp(home, app_dirs=apps), "home itself must be refused"
    assert rp(str(Path(home).parent), app_dirs=apps), "C:\\Users / /Users must be refused"
    assert rp(str(REPO), app_dirs=apps), "app dir itself"
    assert rp(str(REPO / "sub"), app_dirs=apps), "inside app dir"
    assert rp(str(REPO.parent), app_dirs=apps), "folder containing the app dir"
    if os.name != "nt":
        assert rp("/usr/share", app_dirs=apps)
        assert rp("/etc", app_dirs=apps)
        assert rp(os.path.join(home, "Library", "Photos"), app_dirs=apps)
    assert rp(str(MEDIA), app_dirs=apps) is None, rp(str(MEDIA), app_dirs=apps)
    assert rp(os.path.join(home, "Pictures"), app_dirs=apps) is None


@check("dangerous main folder stops startup; dangerous extra root is not served")
def _():
    old = config.MEDIA_FOLDER
    try:
        config.MEDIA_FOLDER = str(Path.home())
        server._root_problem_cache.clear()
        try:
            server.startup_checks()
            raise AssertionError("startup_checks accepted the home folder")
        except server.StartupError as e:
            assert "user folder" in str(e)
    finally:
        config.MEDIA_FOLDER = old
    folders = Path(config.FOLDERS_FILE)
    folders.write_text(json.dumps({"Bad": "/", "Good": str(TMP / "good")}), encoding="utf-8")
    try:
        roots = server.get_roots()
        assert "Bad" not in roots and "Good" in roots and "Library" in roots, roots
    finally:
        folders.unlink()


@check("add_folder refuses dangerous roots and writes folders.json atomically")
def _():
    import add_folder
    ok, msg = add_folder.add_folder("Home", str(Path.home()))
    assert not ok and "Refused" in msg, msg
    ok, msg = add_folder.add_folder("Wife's iPhone", str(TMP / "wife 東京"))
    assert ok, msg
    data = json.loads(Path(config.FOLDERS_FILE).read_text(encoding="utf-8"))
    assert data == {"Wife's iPhone": str(TMP / "wife 東京")}, data
    assert not [p for p in CFG_DIR.iterdir() if p.name.startswith(".cfg_")], "temp file left behind"
    Path(config.FOLDERS_FILE).unlink()


# ---------------------------------------------------------------------------
# Atomic unique destination
# ---------------------------------------------------------------------------

@check("concurrent placement never overwrites (32 threads, same name)")
def _():
    dest_dir = TMP / "race_dest"
    src_dir = TMP / "race_src"
    for d in (dest_dir, src_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
    (dest_dir / "IMG.jpg").write_bytes(b"pre-existing")
    srcs = []
    for i in range(32):
        p = src_dir / f"s{i}"
        p.write_bytes(f"payload-{i}".encode())
        srcs.append(p)
    results, errors = [], []
    barrier = threading.Barrier(len(srcs))

    def worker(p):
        try:
            barrier.wait()
            results.append(server.move_no_overwrite(p, dest_dir, "IMG.jpg"))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(p,)) for p in srcs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors
    assert len(set(results)) == 32, len(set(results))
    assert (dest_dir / "IMG.jpg").read_bytes() == b"pre-existing"
    contents = sorted(r.read_bytes() for r in results)
    assert contents == sorted(f"payload-{i}".encode() for i in range(32))
    assert not any(src_dir.iterdir())


@check("move into existing name raises FileExistsError at the atomic layer")
def _():
    d = TMP / "noreplace"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir()
    (d / "a").write_bytes(b"A")
    (d / "b").write_bytes(b"B")
    try:
        server._atomic_move_noreplace(d / "b", d / "a")
        raise AssertionError("overwrote")
    except FileExistsError:
        pass
    assert (d / "a").read_bytes() == b"A" and (d / "b").read_bytes() == b"B"


# ---------------------------------------------------------------------------
# Organizing
# ---------------------------------------------------------------------------

@check("cloud placeholder attribute detection")
def _():
    class St:
        st_file_attributes = 0
    for attr, expect in ((0, False), (0x20, False), (0x1000, True), (0x40000, True),
                         (0x400000, True), (0x400020, True)):
        s = St()
        s.st_file_attributes = attr
        assert safety.is_cloud_placeholder(s) is expect, hex(attr)
    assert safety.is_cloud_placeholder(os.stat(__file__)) is False


@check("organize: placeholders skipped (never opened), names kept, folders handled safely")
def _():
    reset_media()
    loose = MEDIA / "Trip 2019" / "June"
    loose.mkdir(parents=True)
    (loose / "東京.jpg").write_bytes(jpeg_bytes(exif_date="2019:06:15 10:00:00"))
    (loose / "notes.txt").write_text("keep me")          # non-media: folder stays
    only_media = MEDIA / "Dump"
    only_media.mkdir()
    (only_media / "IMG_0001.jpg").write_bytes(jpeg_bytes(exif_date="2021:01:02 03:04:05"))
    (MEDIA / "My Empty Album").mkdir()                    # user-created empty folder
    cloud = MEDIA / "cloud"
    cloud.mkdir()
    placeholder = cloud / "online-only.jpg"
    placeholder.write_bytes(jpeg_bytes(size=(10, 10)) + b"\x00" * 7)
    marker_size = placeholder.stat().st_size
    fake = MEDIA / "fake.jpg"
    fake.write_bytes(b"MZ not a jpeg at all")              # content mismatch: left alone

    opened = []
    orig_is_ph, orig_match = server.is_cloud_placeholder, server._content_matches_extension
    server.is_cloud_placeholder = lambda st: getattr(st, "st_size", -1) == marker_size
    server._content_matches_extension = lambda p, e: (opened.append(Path(p).name), orig_match(p, e))[1]
    try:
        # dry run first: nothing moves
        plan = []
        moved, _ = server.organize_existing(str(MEDIA), dry_run=True,
                                            report=lambda s, d: plan.append((s, d)))
        assert moved == 2 and len(plan) == 2, plan
        assert (loose / "東京.jpg").exists() and not (MEDIA / "Photos").exists()
        moved, skipped = server.organize_existing(str(MEDIA))
        moved2, _ = server.organize_existing(str(MEDIA))     # idempotent
    finally:
        server.is_cloud_placeholder, server._content_matches_extension = orig_is_ph, orig_match
    assert moved == 2, (moved, skipped)
    assert "online-only.jpg" not in opened, "placeholder was opened"
    assert placeholder.exists(), "placeholder moved"
    assert (MEDIA / "Photos" / "2019" / "06-June" / "東京.jpg").exists()
    assert (MEDIA / "Photos" / "2021" / "01-January" / "IMG_0001.jpg").exists()
    assert (loose / "notes.txt").exists(), "non-empty user folder must stay"
    assert not only_media.exists(), "folder we emptied should be removed"
    assert (MEDIA / "My Empty Album").is_dir(), "pre-existing empty folder must stay"
    assert fake.exists(), "mismatched content must not be moved"
    assert moved2 == 0, moved2


@check("organize refuses a dangerous root")
def _():
    try:
        server.organize_existing(str(Path.home()), dry_run=True)
        raise AssertionError("organized home")
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _run_config_probe(app_dir: Path, env_extra=None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PICTUREVIEWER_")}
    env.update(env_extra or {})
    code = ("import json,config; config.ensure_secret_key(); print(json.dumps({"
            "'code': config.ACCESS_CODE, 'folder': config.MEDIA_FOLDER, 'port': config.PORT,"
            "'secret': config.SECRET_KEY, 'json': config.USING_CONFIG_JSON,"
            "'org': config.ORGANIZE_EXISTING}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(app_dir), env=env,
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _fresh_app_dir(name) -> Path:
    d = TMP / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir()
    for f in ("config.py", "safety.py"):
        shutil.copy2(REPO / f, d / f)
    return d


@check(".env fallback keeps '#' and '$' (incl. BOM file), persists secret.key")
def _():
    d = _fresh_app_dir("envapp")
    (d / ".env").write_bytes(
        "\ufeffPICTUREVIEWER_MEDIA_FOLDER=C:\\Users\\Jo & Co (x)\\Pictures\n"
        "PICTUREVIEWER_ACCESS_CODE=Ab$cd${HOME}#Ef12!\n"
        "PICTUREVIEWER_SECRET_KEY=\n"
        "PICTUREVIEWER_PORT=9001\n".encode("utf-8"))
    r1 = _run_config_probe(d)
    assert r1["code"] == "Ab$cd${HOME}#Ef12!", r1["code"]
    assert r1["folder"] == "C:\\Users\\Jo & Co (x)\\Pictures", r1["folder"]
    assert r1["port"] == 9001 and r1["json"] is False and r1["org"] is True
    assert len(r1["secret"]) >= 32 and (d / "secret.key").exists()
    if os.name != "nt":
        assert oct((d / "secret.key").stat().st_mode & 0o777) == "0o600"
    r2 = _run_config_probe(d)
    assert r2["secret"] == r1["secret"], "secret must survive restarts"
    # quoted value containing ' #'
    (d / ".env").write_text("PICTUREVIEWER_ACCESS_CODE='My code #1 Rocks!'\n", encoding="utf-8")
    assert _run_config_probe(d)["code"] == "My code #1 Rocks!"


@check("config.json takes precedence over .env; secret persisted into config.json")
def _():
    d = _fresh_app_dir("jsonapp")
    (d / ".env").write_text("PICTUREVIEWER_ACCESS_CODE=FromEnv#Code123\n"
                            "PICTUREVIEWER_MEDIA_FOLDER=/env/folder\n", encoding="utf-8")
    cfgfile = TMP / "jsoncfg" / "config.json"
    cfgfile.parent.mkdir(exist_ok=True)
    cfgfile.write_text(json.dumps({"access_code": 'Js"on\\Code#1$x', "port": 8600,
                                   "organize_existing": False}), encoding="utf-8")
    r = _run_config_probe(d, {"PICTUREVIEWER_CONFIG": str(cfgfile)})
    assert r["code"] == 'Js"on\\Code#1$x', r["code"]
    assert r["folder"] == "/env/folder"          # not in json -> .env still used
    assert r["port"] == 8600 and r["json"] is True and r["org"] is False
    saved = json.loads(cfgfile.read_text(encoding="utf-8"))
    assert saved.get("secret_key") == r["secret"] and len(r["secret"]) >= 32
    assert not (d / "secret.key").exists()
    assert _run_config_probe(d, {"PICTUREVIEWER_CONFIG": str(cfgfile)})["secret"] == r["secret"]


@check("lumina_admin: base64 stdin protocol, validation, write-config, migrate-env")
def _():
    def admin(cmd, payload=None, cfg=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("PICTUREVIEWER_")}
        if cfg:
            env["PICTUREVIEWER_CONFIG"] = str(cfg)
        data = base64.b64encode(json.dumps(payload or {}).encode()).decode()
        out = subprocess.run([sys.executable, str(REPO / "lumina_admin.py"), cmd], input=data,
                             env=env, capture_output=True, text=True, encoding="utf-8")
        line = [ln for ln in out.stdout.splitlines() if ln.startswith("@@JSON ")][-1]
        return json.loads(line[7:])
    assert admin("validate-code", {"code": "short"})["ok"] is False
    assert admin("validate-code", {"code": "Ünïcødé#Pass123"})["ok"] is True
    g = admin("gen-code")["code"]
    assert admin("validate-code", {"code": g})["ok"] is True
    assert admin("validate-folder", {"path": str(Path.home())})["ok"] is False
    assert admin("validate-folder", {"path": str(TMP / "東京 & (photos)")})["ok"] is True
    cfg = TMP / "admin" / "config.json"
    r = admin("write-config", {"values": {"media_folder": str(MEDIA), "access_code": "a" + g,
                                          "port": 8500, "https_port": 8543}}, cfg)
    assert r["ok"], r
    first = json.loads(cfg.read_text(encoding="utf-8"))["secret_key"]
    r = admin("write-config", {"values": {"port": 8501}}, cfg)
    assert r["ok"] and json.loads(cfg.read_text(encoding="utf-8"))["secret_key"] == first, \
        "secret must not be regenerated on re-run"
    assert admin("write-config", {"values": {"port": 80}}, cfg)["ok"] is False
    assert admin("write-config", {"values": {"media_folder": "/"}}, cfg)["ok"] is False
    # migration from an old install folder
    old = TMP / "old install"
    (old / "certs").mkdir(parents=True, exist_ok=True)
    (old / ".env").write_text(f"PICTUREVIEWER_MEDIA_FOLDER={MEDIA}\n"
                              "PICTUREVIEWER_ACCESS_CODE=Old#Code$2024ab\n"
                              f"PICTUREVIEWER_SECRET_KEY={'k' * 64}\n", encoding="utf-8")
    (old / "folders.json").write_text('{"Wife": "/x"}', encoding="utf-8")
    (old / "certs" / "server-cert.pem").write_text("CERT")
    (old / "certs" / "server-key.pem").write_text("KEY")
    newcfg = TMP / "migrated" / "config.json"
    r = admin("migrate-env", {"env_path": str(old), "cert_dir": str(TMP / "migrated" / "certs")}, newcfg)
    assert r["ok"] and r["code_ok"] and set(r["migrated"]) >= {"access_code", "secret_key",
                                                                 "media_folder", "folders.json", "certs"}, r
    m = json.loads(newcfg.read_text(encoding="utf-8"))
    assert m["access_code"] == "Old#Code$2024ab" and m["secret_key"] == "k" * 64
    assert (TMP / "migrated" / "certs" / "server-key.pem").read_text() == "KEY"
    assert (TMP / "migrated" / "folders.json").exists()


# ---------------------------------------------------------------------------
# HTTP API (Flask test client — no sockets)
# ---------------------------------------------------------------------------

@check("token invalidated when the access code changes")
def _():
    reset_media()
    h = auth_hdr()
    assert client().get("/api/files", headers=h).status_code == 200
    old = config.ACCESS_CODE
    try:
        config.ACCESS_CODE = "Another#Code2026"
        r = client().get("/api/files", headers=h)
        assert r.status_code == 401, r.status_code
    finally:
        config.ACCESS_CODE = old
    assert client().get("/api/files", headers=h).status_code == 200
    # tokens from the old server (no 'cv' claim) are rejected too
    import jwt as pyjwt
    import datetime as dt
    legacy = pyjwt.encode({"exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
                           "iat": dt.datetime.now(dt.timezone.utc)}, SECRET, algorithm="HS256")
    assert client().get("/api/files", headers={"Authorization": "Bearer " + legacy}).status_code == 401


@check("/api/auth: refused over HTTP when HTTPS is running, allowed over HTTPS")
def _():
    server._auth_failures.clear()
    server._runtime["https_port"] = 8543
    try:
        r = client().post("/api/auth", json={"code": CODE})
        assert r.status_code == 403 and r.get_json() == {"error": "use https"}
        r = client().post("/api/auth", json={"code": CODE}, base_url="https://192.168.1.5:8543")
        assert r.status_code == 200 and "token" in r.get_json()
    finally:
        server._runtime["https_port"] = None
    assert client().post("/api/auth", json={"code": CODE}).status_code == 200  # HTTP-only server


@check("login throttle: 5 failures then 429")
def _():
    server._auth_failures.clear()
    for _ in range(5):
        assert client().post("/api/auth", json={"code": "wrong"}).status_code == 401
    assert client().post("/api/auth", json={"code": CODE}).status_code == 429
    server._auth_failures.clear()


@check("/api/status hides folder names without a token; new fields present")
def _():
    r = client().get("/api/status").get_json()
    assert r["roots"] == [] and r["media_folder"] == "" and r["media_folder_name"] == ""
    assert r["root_count"] == 1 and "auto_organize" in r and "tls_fingerprint_full" in r
    assert str(MEDIA) not in json.dumps(r) and MEDIA.name not in json.dumps(r["roots"])
    r = client().get("/api/status", headers=auth_hdr()).get_json()
    assert r["roots"][0]["name"] == "Library" and r["roots"][0]["path"] == str(MEDIA)
    assert r["media_folder_name"] == "media"


@check("/api/file: originals by default, ?display=1 JPEG, range, refuses non-media")
def _():
    reset_media()
    h = auth_hdr()
    (MEDIA / ".env").write_text("SECRET=1")
    (MEDIA / "server.py").write_text("print(1)")
    (MEDIA / "key.pem").write_text("-----BEGIN")
    for name in (".env", "server.py", "key.pem", "../cfg/config.json"):
        r = client().get("/api/file", query_string={"path": name}, headers=h)
        assert r.status_code in (403, 404), (name, r.status_code)
        assert b"SECRET" not in r.data and b"BEGIN" not in r.data
    png = io.BytesIO()
    Image.new("RGB", (20, 10), (0, 255, 0)).save(png, "PNG")
    (MEDIA / "pic.png").write_bytes(png.getvalue())
    r = client().get("/api/file", query_string={"path": "pic.png"}, headers=h)
    assert r.status_code == 200 and r.data == png.getvalue() and r.mimetype == "image/png"
    r = client().get("/api/file", query_string={"path": "pic.png", "display": "1"}, headers=h)
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:3] == b"\xff\xd8\xff"
    vid = mp4_bytes(10000)
    (MEDIA / "clip.mp4").write_bytes(vid)
    r = client().get("/api/file", query_string={"path": "clip.mp4"}, headers={**h, "Range": "bytes=0-99"})
    assert r.status_code == 206 and r.data == vid[:100] and r.mimetype == "video/mp4"


@check("/api/thumbnail: size validation (400 on junk, clamped otherwise)")
def _():
    h = auth_hdr()
    (MEDIA / "big.jpg").write_bytes(jpeg_bytes(size=(1600, 1200)))
    for bad in ("abc", "1e3", "12px", "None"):
        r = client().get("/api/thumbnail", query_string={"path": "big.jpg", "size": bad}, headers=h)
        assert r.status_code == 400, (bad, r.status_code)
    for size, expect in (("-5", 128), ("150", 256), ("300", 400), ("800", 800), ("99999", 1024)):
        r = client().get("/api/thumbnail", query_string={"path": "big.jpg", "size": size}, headers=h)
        assert r.status_code == 200 and r.mimetype == "image/jpeg", (size, r.status_code)
        w, hgt = Image.open(io.BytesIO(r.data)).size
        assert max(w, hgt) == expect, (size, w, hgt)
    assert server._thumb_size(None) == 400


@check("/api/thumbnail never returns video bytes (no ffmpeg -> 204; bad video -> 204)")
def _():
    h = auth_hdr()
    vid = mp4_bytes(50000)
    (MEDIA / "movie.mp4").write_bytes(vid)
    orig = server._ffmpeg_path
    try:
        server._ffmpeg_path = lambda: None
        r = client().get("/api/thumbnail", query_string={"path": "movie.mp4"}, headers=h)
        assert r.status_code == 204 and r.data == b"", r.status_code
    finally:
        server._ffmpeg_path = orig
    if shutil.which("ffmpeg"):
        r = client().get("/api/thumbnail", query_string={"path": "movie.mp4"}, headers=h)
        assert r.status_code == 204 and vid[:64] not in r.data
        # a real (tiny) video gets a JPEG poster frame
        real = MEDIA / "real.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=320x240:d=2", "-pix_fmt", "yuv420p", str(real)], check=True)
        r = client().get("/api/thumbnail", query_string={"path": "real.mp4", "size": "300"}, headers=h)
        assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:3] == b"\xff\xd8\xff"
        assert max(Image.open(io.BytesIO(r.data)).size) <= 400


@check("thumbnail cache prune evicts least-recently-used files down to the cap")
def _():
    folder = Path(config.THUMBNAIL_FOLDER)
    folder.mkdir(exist_ok=True)
    for f in folder.iterdir():
        f.unlink()
    import time as _t
    now = _t.time()
    for i in range(10):
        p = folder / f"t{i}.jpg"
        p.write_bytes(b"x" * 1000)
        os.utime(p, (now - 1000 + i, now - 1000 + i))
    removed = server.prune_thumbnail_cache(cap_bytes=5000)
    left = sorted(p.name for p in folder.iterdir())
    assert removed >= 5 and "t9.jpg" in left and "t0.jpg" not in left, (removed, left)


@check("upload keeps the original (non-ASCII) name, organizes, rejects spoofed content")
def _():
    reset_media()
    h = auth_hdr()
    from urllib.parse import quote
    data = jpeg_bytes(exif_date="2024:03:09 12:00:00")
    r = client().post("/api/upload", data=data, content_type="application/octet-stream",
                      headers={**h, "X-Upload-Filename": quote("東京 タワー.jpg")})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["name"] == "東京 タワー.jpg", j
    assert (MEDIA / "Photos" / "2024" / "03-March" / "東京 タワー.jpg").read_bytes() == data
    r = client().post("/api/upload", data=data, content_type="application/octet-stream",
                      headers={**h, "X-Upload-Filename": quote("東京 タワー.jpg")})
    assert r.get_json()["name"] == "東京 タワー_1.jpg"
    r = client().post("/api/upload", data=b"MZ\x90\x00evil", content_type="application/octet-stream",
                      headers={**h, "X-Upload-Filename": "x.jpg"})
    assert r.status_code == 400
    r = client().post("/api/upload", data=b"#!/bin/sh", content_type="application/octet-stream",
                      headers={**h, "X-Upload-Filename": "x.sh"})
    assert r.status_code == 400
    # multipart (legacy) path
    r = client().post("/api/upload", headers=h, content_type="multipart/form-data",
                      data={"path": "", "file": (io.BytesIO(data), "../../evil<>.jpg")})
    assert r.status_code == 200 and r.get_json()["name"] == "evil__.jpg", r.get_data(as_text=True)
    assert not list((MEDIA / ".incoming").iterdir())


@check("upload refused with 507 when the disk is nearly full")
def _():
    h = auth_hdr()
    orig = server._free_bytes
    try:
        server._free_bytes = lambda p: 100 * 1024 * 1024       # 100 MB free < 1 GB reserve
        r = client().post("/api/upload", data=jpeg_bytes(), content_type="application/octet-stream",
                          headers={**h, "X-Upload-Filename": "a.jpg"})
        assert r.status_code == 507, r.status_code
    finally:
        server._free_bytes = orig


@check("stale .incoming uploads older than 24h are cleaned")
def _():
    inc = MEDIA / ".incoming"
    inc.mkdir(exist_ok=True)
    old, new = inc / "up_old.jpg", inc / "up_new.jpg"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    import time as _t
    os.utime(old, (_t.time() - 2 * 86400,) * 2)
    server.cleanup_stale_incoming()
    assert not old.exists() and new.exists()


@check("DNS-rebinding guard + ALLOWED_HOSTS")
def _():
    r = client().get("/api/status", headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    assert client().get("/api/status", headers={"Host": "192.168.1.20:8500"}).status_code == 200
    assert client().get("/api/status", headers={"Host": "[::1]:8500"}).status_code == 200
    config.ALLOWED_HOSTS = ["pc.tail1234.ts.net", ".home.arpa"]
    try:
        assert client().get("/api/status", headers={"Host": "pc.tail1234.ts.net"}).status_code == 200
        assert client().get("/api/status", headers={"Host": "nas.home.arpa:8543"}).status_code == 200
        assert client().get("/api/status", headers={"Host": "evilhome.arpa"}).status_code == 403
    finally:
        config.ALLOWED_HOSTS = []


@check("magic-byte checks for every allowed extension")
def _():
    ok = {
        ".heic": b"\x00\x00\x00\x18ftypheic" + b"\x00" * 200,
        ".mov": b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 200,
        ".dng": b"II*\x00" + b"\x00" * 200,
        ".avi": b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 200,
        ".webp": b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 200,
        ".3gp": b"\x00\x00\x00\x14ftyp3gp4" + b"\x00" * 200,
        ".m2ts": (b"\x00\x00\x00\x00\x47" + b"\x00" * 187) * 3,
    }
    for ext, head in ok.items():
        assert server._sniff_ok(head, ext), ext
    assert not server._sniff_ok(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 50, ".heic")
    assert not server._sniff_ok(b"RIFF\x00\x00\x00\x00WAVE", ".avi")
    assert not server._sniff_ok(b"\x00\x00\x00\x00junk", ".mp4")
    assert not server._sniff_ok(b"anything", ".exe")
    for ext in config.ALL_EXTENSIONS:
        assert not server._sniff_ok(b"", ext), ext


@check("HEIC thumbnail via optional pillow-heif (skipped if not installed)")
def _():
    if not server.HEIF_AVAILABLE:
        return
    h = auth_hdr()
    buf = io.BytesIO()
    Image.new("RGB", (900, 600), (9, 9, 200)).save(buf, "HEIF")
    (MEDIA / "iphone.HEIC").write_bytes(buf.getvalue())
    r = client().get("/api/thumbnail", query_string={"path": "iphone.HEIC", "size": "256"}, headers=h)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert max(Image.open(io.BytesIO(r.data)).size) == 256


def main() -> int:
    failed = 0
    for name, fn in RESULTS:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed"
          f" (pillow-heif {'present' if server.HEIF_AVAILABLE else 'absent'},"
          f" ffmpeg {'present' if shutil.which('ffmpeg') else 'absent'})")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
