#!/usr/bin/env python3
"""Entry point used by the Windows scheduled task (and handy everywhere).

* runs from this file's folder, whatever the task's working directory is;
* points PICTUREVIEWER_CONFIG at the installed config.json when it isn't set
  (Windows: %LOCALAPPDATA%\\LuminaServer\\config\\config.json);
* works under pythonw.exe (no console): output goes to the rotating log file
  in the configured logs folder.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

if not os.environ.get("PICTUREVIEWER_CONFIG") and os.name == "nt":
    _la = os.environ.get("LOCALAPPDATA")
    if _la:
        _cand = os.path.join(_la, "LuminaServer", "config", "config.json")
        if os.path.isfile(_cand):
            os.environ["PICTUREVIEWER_CONFIG"] = _cand

# pythonw.exe has no console: sys.stdout/stderr are None and any print would
# crash. stderr goes to the bit bucket; stdout stays None so the server skips
# its console log handler and logs only to its log file.
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _main() -> int:
    try:
        import server
    except Exception as e:  # e.g. a corrupt config.json — make sure it is logged
        import logging
        import traceback
        try:
            import config  # may itself be the failure
            log_dir = config.LOG_DIR
        except Exception:
            la = os.environ.get("LOCALAPPDATA") or HERE
            log_dir = os.path.join(la, "LuminaServer", "logs") if os.name == "nt" else os.path.join(HERE, "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "server.log"), "a", encoding="utf-8") as fh:
            fh.write("FATAL: server failed to start: %s\n%s\n" % (e, traceback.format_exc()))
        logging.shutdown()
        return 2
    return server.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
