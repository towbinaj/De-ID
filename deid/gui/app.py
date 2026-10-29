"""Main window: a left nav-rail + swappable, project-centric views.

The app holds one *active Project*. The nav-rail has a project selector at the
top (so every view operates on the same project) and buttons that switch focused
views. Views are built lazily and switched with ``tkraise`` (no re-render).
"""

from __future__ import annotations

import customtkinter as ctk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except Exception:  # noqa: BLE001 - drag-drop is optional
    TkinterDnD = None
    DND_FILES = None
    _HAS_DND = False

from .. import APP_NAME, __version__, config, paths
from ..logging_setup import setup as setup_logging
from ..project import ProjectStore
from ..engine.pseudonyms import PseudonymIndex
from .theme import MUTED, apply_scale, view_color
from .view_projects import ProjectsView
from .view_rules import RulesView
from .view_run import RunView
from .view_pseudonyms import PseudonymsView
from .view_queue import QueueView
from .view_settings import SettingsView

# Order of the nav-rail.
NAV_VIEWS = [ProjectsView, RulesView, RunView, PseudonymsView, QueueView]
CONFIG_VIEWS = [SettingsView]

_APP_BASES = (ctk.CTk, TkinterDnD.DnDWrapper) if _HAS_DND else (ctk.CTk,)


class App(*_APP_BASES):
    def __init__(self):
        super().__init__()
        self._dnd_ok = False
        if _HAS_DND:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self._dnd_ok = True
            except Exception:  # noqa: BLE001
                self._dnd_ok = False
        setup_logging()
        self.settings = config.Settings.load()
        self.projects = ProjectStore()
        self.pseudonyms = PseudonymIndex()
        self.destinations = config.load_destinations()

        # Resolve the active project (remembered, else first/default).
        self.active_project = (self.projects.load(self.settings.active_project)
                               or self.projects.ensure_default())

        ctk.set_appearance_mode(self.settings.appearance)
        ctk.set_default_color_theme("blue")
        apply_scale(self.settings.ui_scale)

        self.title(f"{APP_NAME}  v{__version__}")
        self.minsize(880, 600)
        if self.settings.window_geometry and self._geometry_on_screen(
                self.settings.window_geometry):
            self.geometry(self.settings.window_geometry)
        else:
            self.geometry("1060x740")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        icon = paths.resource_dir() / "assets" / "icon.ico"
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:  # noqa: BLE001
                pass

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()

        # Content area ---------------------------------------------------
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.views: dict[str, ctk.CTkFrame] = {}
        self._view_classes: dict[str, type] = {}
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._register_views()

        self._build_statusbar()

        self._current = ""
        last = self.settings.last_view
        self.select(last if last in self._view_classes
                    else next(iter(self._view_classes)))

    # -- sidebar ---------------------------------------------------------
    def _build_sidebar(self) -> None:
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        ctk.CTkLabel(self.sidebar, text="De-ID",
                     font=ctk.CTkFont(size=26, weight="bold"),
                     justify="left").pack(anchor="w", padx=16, pady=(16, 2))
        ctk.CTkLabel(self.sidebar, text="de-identification",
                     text_color=MUTED, font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=16, pady=(0, 10))

        # Active-project selector: the one project every view operates on.
        ctk.CTkLabel(self.sidebar, text="ACTIVE PROJECT",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED).pack(anchor="w", padx=16, pady=(6, 2))
        self.project_menu = ctk.CTkOptionMenu(
            self.sidebar, width=188, dynamic_resizing=False,
            command=self._on_project_selected)
        self.project_menu.pack(anchor="w", padx=16, pady=(0, 12))
        self.refresh_project_menu()

        self._nav_host = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self._nav_host.pack(fill="both", expand=True, padx=0, pady=0)

    def _build_statusbar(self) -> None:
        status = ctk.CTkFrame(self, height=24, corner_radius=0)
        status.grid(row=1, column=1, sticky="ew")
        ctk.CTkLabel(status, text=f"Data: {paths.data_dir()}",
                     text_color=MUTED,
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=8)
        self.proj_status = ctk.CTkLabel(
            status, text="", text_color=MUTED, font=ctk.CTkFont(size=11))
        self.proj_status.pack(side="right", padx=8)
        ctk.CTkButton(status, text="Report a Bug", height=20, width=100,
                      fg_color="#b5651d", hover_color="#c8761f",
                      font=ctk.CTkFont(size=11, weight="bold"),
                      command=self._report_bug).pack(side="right", padx=6)
        self._update_proj_status()

    def _register_views(self) -> None:
        def add_group(label, classes):
            ctk.CTkLabel(self._nav_host, text=label,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=MUTED).pack(anchor="w", padx=16,
                                                pady=(10, 2))
            for cls in classes:
                key = cls.key
                self._view_classes[key] = cls
                item = ctk.CTkFrame(self._nav_host, fg_color="transparent")
                item.pack(fill="x", padx=8, pady=2)
                ctk.CTkFrame(item, width=5, height=30, corner_radius=3,
                             fg_color=view_color(key)).pack(side="left",
                                                            padx=(0, 6))
                btn = ctk.CTkButton(
                    item, text=cls.title, anchor="w", height=34,
                    font=ctk.CTkFont(size=14), fg_color="transparent",
                    text_color=("gray10", "gray90"),
                    hover_color=("gray75", "gray25"),
                    command=lambda k=key: self.select(k))
                btn.pack(side="left", fill="x", expand=True)
                self._buttons[key] = btn

        add_group("WORKFLOW", NAV_VIEWS)
        add_group("CONFIG", CONFIG_VIEWS)

    # -- view switching --------------------------------------------------
    def _ensure_view(self, key: str):
        view = self.views.get(key)
        if view is not None:
            return view
        cls = self._view_classes.get(key)
        if cls is None:
            return None
        view = cls(self.content, self)
        view.grid(row=0, column=0, sticky="nsew")
        view.lower()
        self.views[key] = view
        return view

    def select(self, key: str) -> None:
        self._current = key
        view = self._ensure_view(key)
        if view is None:
            return
        view.tkraise()
        if hasattr(view, "on_show"):
            view.on_show()
        for k, btn in self._buttons.items():
            btn.configure(fg_color=("gray80", "gray28") if k == key
                          else "transparent")

    # -- active project --------------------------------------------------
    def refresh_project_menu(self) -> None:
        names = self.projects.names() or [self.active_project.name]
        self.project_menu.configure(values=names)
        if self.active_project.name in names:
            self.project_menu.set(self.active_project.name)
        elif names:
            self.project_menu.set(names[0])

    def _on_project_selected(self, name: str) -> None:
        self.set_active_project(name)

    def set_active_project(self, name: str) -> None:
        proj = self.projects.load(name)
        if proj is None:
            return
        self.active_project = proj
        self.settings.active_project = name
        self.settings.save()
        self.refresh_project_menu()
        self._update_proj_status()
        self.notify_project_changed()

    def notify_project_changed(self) -> None:
        """Tell every built view the active project changed/was edited."""
        for view in self.views.values():
            if hasattr(view, "on_project_changed"):
                view.on_project_changed()

    def save_active_project(self) -> None:
        """Persist the active project and refresh dependent UI."""
        self.projects.save(self.active_project)
        self.settings.active_project = self.active_project.name
        self.settings.save()
        self.refresh_project_menu()
        self._update_proj_status()

    def _update_proj_status(self) -> None:
        self.proj_status.configure(
            text=f"Project: {self.active_project.name}")

    # -- destinations ----------------------------------------------------
    def remember_destination(self, key: str, name: str) -> None:
        if self.settings.last_destinations.get(key) != name:
            self.settings.last_destinations[key] = name
            self.settings.save()

    def broadcast_destinations_changed(self) -> None:
        for view in self.views.values():
            if hasattr(view, "on_destinations_changed"):
                view.on_destinations_changed()

    # -- helpers ---------------------------------------------------------
    def _report_bug(self) -> None:
        from .bugreport import BugReportDialog
        BugReportDialog(self)

    def _geometry_on_screen(self, geo: str) -> bool:
        import re
        m = re.match(r"\d+x\d+([+-]\d+)([+-]\d+)$", geo)
        if not m:
            return False
        x, y = int(m.group(1)), int(m.group(2))
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        return -50 <= x <= sw - 100 and -20 <= y <= sh - 100

    def _on_close(self) -> None:
        try:
            self.settings.window_geometry = self.geometry()
            self.settings.last_view = getattr(self, "_current", "")
            self.settings.save()
        except Exception:  # noqa: BLE001
            pass
        # Stop the receive/export queue if it's running, so the process exits.
        qv = self.views.get("queue")
        if qv is not None and getattr(qv, "controller", None) is not None:
            try:
                qv.controller.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.pseudonyms.close()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    def enable_drop(self, widget, on_paths, on_enter=None, on_leave=None) -> bool:
        """Register a widget as a file/folder drop target."""
        if not self._dnd_ok:
            return False
        try:
            widget.drop_target_register(DND_FILES)

            def _drop(e):
                if on_leave:
                    on_leave()
                on_paths(list(self.tk.splitlist(e.data)))

            widget.dnd_bind("<<Drop>>", _drop)
            if on_enter:
                widget.dnd_bind("<<DropEnter>>", lambda e: on_enter())
            if on_leave:
                widget.dnd_bind("<<DropLeave>>", lambda e: on_leave())
            return True
        except Exception:  # noqa: BLE001
            return False


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
