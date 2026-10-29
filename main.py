"""Entry point for De-ID.

Normal use: double-click the executable (or ``python main.py``) to open the GUI.
"""

import sys


def _selfcheck() -> int:
    """Headless diagnostic: write step-by-step progress to selfcheck.txt so a
    crash (even a native abort) is pinpointed by the last line written."""
    from deid import paths
    out = paths.data_dir() / "selfcheck.txt"
    out.write_text("", encoding="utf-8")

    def w(line: str) -> None:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    w("start")
    try:
        import importlib.util
        present = importlib.util.find_spec("rapidocr_onnxruntime") is not None
        w(f"rapidocr spec present={present}")
    except Exception as exc:  # noqa: BLE001
        w(f"find_spec error: {exc}")
    w("raw import rapidocr...")
    try:
        import rapidocr_onnxruntime  # noqa: F401
        w("raw import OK")
    except Exception as exc:  # noqa: BLE001
        w(f"raw import EXCEPTION: {exc}")
    w("building app...")
    try:
        from deid.gui.app import App
        app = App()
        app.withdraw()
        app.update()
        w("app built; select projects")
        app.select("projects")
        app.update()
        w("select rules")
        app.select("rules")
        app.update()
        w("rules OK")
        app.destroy()
    except Exception:  # noqa: BLE001
        import traceback
        w("APP EXCEPTION:\n" + traceback.format_exc())
    w("done")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "selfcheck":
        return _selfcheck()
    from deid.gui.app import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
