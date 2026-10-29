"""Entry point for De-ID.

Normal use: double-click the executable (or ``python main.py``) to open the GUI.
"""

import sys


def main() -> int:
    from deid.gui.app import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
