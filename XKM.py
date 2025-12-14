#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-Kernel Manager — XanMod + Liquorix + Mainline
ULTIMATE FINAL VERSION — DECEMBER 2025 — ABSOLUTELY PERFECT
"""

import os
import sys
import ctypes
import ctypes.util

os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["DEBIAN_FRONTEND"] = "noninteractive"
os.environ["APT_LISTCHANGES_FRONTEND"] = "none"
os.environ["NEEDRESTART_MODE"] = "l"
os.environ["G_MESSAGES_DEBUG"] = ""
os.environ["GTK_CSD"] = "0"
os.environ["GDK_BACKEND"] = "x11"

_devnull = os.open("/dev/null", os.O_WRONLY)
_old_stderr = os.dup(2)
os.dup2(_devnull, 2)

try:
    libapt = ctypes.CDLL(ctypes.util.find_library("apt-pkg"))
    libapt.pkgInitialize(0)
    for k, v in {
        b"Quiet": b"2",
        b"APT::Get::Assume-Yes": b"true",
        b"Dir::Log::Terminal": b"/dev/null",
        b"APT::Status-Fd": b"2",
    }.items():
        try: libapt.pkgSetConfigString(k, v)
        except: pass
except:
    pass

import apt
import apt_pkg

os.dup2(_old_stderr, 2)
os.close(_old_stderr)
os.close(_devnull)

apt_pkg.init()
apt_pkg.config.set("Quiet", "2")
apt_pkg.config.set("APT::Get::Assume-Yes", "true")
apt_pkg.config.set("Dir::Log::Terminal", "/dev/null")
apt_pkg.config.set("APT::Status-Fd", "2")

import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger().setLevel(1000)

_original_cache = apt.Cache
class SilentCache(apt.Cache):
    def __init__(self, *args, **kwargs):
        os.dup2(os.open("/dev/null", os.O_WRONLY), 2)
        super().__init__(*args, **kwargs)
        os.dup2(1, 2)
apt.Cache = SilentCache

import json
import threading
import subprocess
import platform
from pathlib import Path
from functools import cmp_to_key
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gio, GLib, Adw, GObject

APP_ID = "com.xanmod.kernel.manager"

def is_liquorix_name(name: str) -> bool:
    return name.startswith(("linux-image-liquorix-", "linux-headers-liquorix-"))

def is_xanmod_name(name: str) -> bool:
    return any(name.lower().startswith(p) for p in (
        "linux-xanmod", "linux-image-xanmod", "linux-headers-xanmod", "linux-modules-xanmod"
    ))

def is_generic_kernel_name(name: str) -> bool:
    if is_xanmod_name(name) or is_liquorix_name(name):
        return False
    return name.startswith((
        "linux-image-", "linux-headers-", "linux-modules-",
        "linux-image-unsigned-", "linux-headers-unsigned-"
    ))

XANMOD_KEYRING = "/usr/share/keyrings/xanmod-archive-keyring.gpg"
XANMOD_SOURCE_FILE = "/etc/apt/sources.list.d/xanmod-kernel.list"
LIQUORIX_PPA_FILES = [
    "/etc/apt/sources.list.d/damentz-liquorix.sources",
    "/etc/apt/sources.list.d/damentz-liquorix.list",
]

AUTO_OFFER_ADD_REPO = True

CONFIG_DIR = Path.home() / ".config" / "xanmod-kernel-manager"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_DIR = CONFIG_DIR / "logs"
DEFAULT_CONFIG = {
    "auto_check_hours": 6,
    "auto_remove_after_install": False,
    "win_size": [980, 620],
    "dark_mode": True,
}

Adw.init()

class KernelRow(GObject.Object):
    __gtype_name__ = "KernelRow"
    is_selected = GObject.Property(type=bool, default=False)
    markup = GObject.Property(type=str, default="")
    name = GObject.Property(type=str, default="")
    version = GObject.Property(type=str, default="")
    size = GObject.Property(type=str, default="")
    status = GObject.Property(type=str, default="")
    is_installed = GObject.Property(type=bool, default=False)
    is_active = GObject.Property(type=bool, default=False)
    def __init__(self, **kwargs): super().__init__(**kwargs)

class KernelManagerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.mgr = None
    def do_activate(self):
        if not self.mgr:
            self.mgr = KernelManagerWindow(application=self)
        self.mgr.present()

class KernelManagerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Multi-Kernel Manager")
        cfg = self._load_config()
        self.set_default_size(*cfg.get("win_size", [980, 620]))
        self.manager = KernelManager(self)
        self.set_content(self.manager.main_box)
        self.style_manager = Adw.StyleManager.get_default()
        self.apply_color_scheme()
        self.connect("close-request", self._on_close)
        self.connect("realize", self._on_window_realized)

        # BLOCK RIGHT-CLICK POPOVERMENU WARNING — GTK4 CORRECT WAY
        click_ctrl = Gtk.GestureClick()
        click_ctrl.set_button(3)  # right mouse button
        click_ctrl.connect("pressed", lambda *_: True)
        self.add_controller(click_ctrl)

    def _on_window_realized(self, _):
        self.manager._reload_kernels_async()
        if AUTO_OFFER_ADD_REPO:
            GLib.idle_add(self.manager._maybe_offer_add_repos)

    def _load_config(self):
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return {**DEFAULT_CONFIG, **json.load(f)}
        except: pass
        return DEFAULT_CONFIG.copy()

    def apply_color_scheme(self):
        dark = self._load_config().get("dark_mode", True)
        self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)

    def _on_close(self, *_):
        cfg = self._load_config()
        w, h = self.get_default_size()
        cfg["win_size"] = [w, h]
        cfg["auto_remove_after_install"] = self.manager.chk_auto_rm.get_active()
        self.manager._save_config(cfg)
        self.manager._end_log_session()

class KernelManager:
    def __init__(self, win):
        super().__init__() # Added for consistency, though not strictly needed for this class
        self.win = win
        self.cache = None
        self.kernels = []
        self.running_release = platform.uname().release
        self._pre_modules = set()
        self.busy = False
        self._log_handle = None

        self.store_xanmod   = Gio.ListStore(item_type=KernelRow)
        self.store_liquorix = Gio.ListStore(item_type=KernelRow)
        self.store_generic  = Gio.ListStore(item_type=KernelRow)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._build_ui()

        hours = self._load_config().get("auto_check_hours", 6)
        # Fix: Return True to make the periodic check repeat
        GLib.timeout_add_seconds(int(max(1, hours)) * 3600, self._periodic_check) 

    def _load_config(self):
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return {**DEFAULT_CONFIG, **json.load(f)}
        except: pass
        return DEFAULT_CONFIG.copy()

    def _save_config(self, data):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _xanmod_repo_present(self):
        if Path(XANMOD_SOURCE_FILE).exists() and Path(XANMOD_KEYRING).exists():
            return True
        patterns = ["deb.xanmod.org", "http://deb.xanmod.org", "https://deb.xanmod.org", "cdn-mirror.chaotic.cx/xanmod"]
        try:
            sources = ["/etc/apt/sources.list"] + list(Path("/etc/apt/sources.list.d").glob("*"))
            for src in sources:
                try:
                    if any(p in Path(src).read_text() for p in patterns):
                        return True
                except: continue
        except: pass
        return False

    def _liquorix_repo_present(self):
        if any(Path(p).exists() for p in LIQUORIX_PPA_FILES):
            return True
        patterns = ["damentz/liquorix", "ppa.launchpad.net/damentz/liquorix", "ppa.launchpadcontent.net/damentz/liquorix", "liquorix.net"]
        try:
            sources = ["/etc/apt/sources.list"] + list(Path("/etc/apt/sources.list.d").glob("*"))
            for src in sources:
                try:
                    if any(p.lower() in Path(src).read_text().lower() for p in patterns):
                        return True
                except: continue
            # Check if any liquorix package is present, indicating the repo was added
            cache = apt.Cache()
            for pkg in cache:
                if pkg.name.startswith(("linux-image-liquorix-", "linux-headers-liquorix-")):
                    return True
        except: pass
        return False

    def _maybe_offer_add_repos(self):
        if hasattr(self, "_repo_offer_shown"):
            return False
        self._repo_offer_shown = True
        missing = []
        if not self._xanmod_repo_present():
            missing.append("XanMod")
        if not self._liquorix_repo_present():
            missing.append("Liquorix")
        if not missing:
            return False
        dlg = Adw.MessageDialog(
            transient_for=self.win,
            heading="Missing Kernel Repositories",
            body=f"<b>{' • '.join(missing)}</b>\n\nAdd them now?"
        )
        dlg.set_body_use_markup(True)
        dlg.add_response("cancel", "Not Now")
        dlg.add_response("ok", "Add Repositories")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: (self._add_missing_repos() if r == "ok" else None) or d.close())
        dlg.present()
        return False

    def _add_missing_repos(self):
        if not self._xanmod_repo_present():
            self._add_xanmod_repo_silent()
        if not self._liquorix_repo_present():
            self._add_liquorix_ppa_official()

    # FIX: Restructured PPA addition to correctly chain: Add PPA -> apt update -> Reload kernels
    def _add_liquorix_ppa_official(self):
        self.btn_details.set_active(True)
        self._clear_log()
        self._start_log_session("liquorix-ppa")
        self._set_busy(True, "Adding official Liquorix PPA…")
        cmd_add_ppa = ["pkexec", "add-apt-repository", "-y", "ppa:damentz/liquorix"]

        def on_update_done(rc, _):
            self._set_busy(False)
            if rc == 0:
                self.status_push("Liquorix PPA added and sources updated.")
            else:
                self.status_push("Failed to update sources after PPA add.")
            self._reload_kernels_async()
            self._end_log_session()

        def on_add_done(rc, _):
            if rc == 0:
                self._set_busy(True, "Updating package list…")
                cmd_update = ["pkexec", "apt", "update", "-qq"]
                self._stream_subprocess(cmd_update, on_update_done)
            else:
                self._set_busy(False, "Failed to add Liquorix PPA.")
                self._end_log_session()

        self._stream_subprocess(cmd_add_ppa, on_add_done)

    def _add_xanmod_repo_silent(self):
        self.btn_details.set_active(True)
        self._clear_log()
        self._start_log_session("xanmod-repo")
        self._set_busy(True, "Adding XanMod repository…")
        cmd = ["pkexec", "bash", "-c", """
set -e
install -m 0755 -d /usr/share/keyrings
curl -fsSL https://dl.xanmod.org/gpg.key | gpg --dearmor -o /usr/share/keyrings/xanmod-archive-keyring.gpg
echo 'deb [signed-by=/usr/share/keyrings/xanmod-archive-keyring.gpg] http://deb.xanmod.org releases main' > /etc/apt/sources.list.d/xanmod-kernel.list
apt-get update -qq
"""]
        self._stream_subprocess(cmd, lambda *_: (self._set_busy(False), self._reload_kernels_async(), self._end_log_session()))

    def _build_ui(self):
        header = Adw.HeaderBar()
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title.append(Gtk.Label(label="Multi-Kernel Manager"))
        title.append(Gtk.Label(label="XanMod • Liquorix • Mainline"))
        header.set_title_widget(title)

        self.mode_switch = Gtk.Switch()
        self.mode_switch.set_active(self._load_config().get("dark_mode", True))
        self.mode_switch.connect("notify::active", self.on_mode_toggled)
        mode_box = Gtk.Box(spacing=8)
        mode_box.append(Gtk.Label(label="Light"))
        mode_box.append(self.mode_switch)
        mode_box.append(Gtk.Label(label="Dark"))
        mode_box.set_valign(Gtk.Align.CENTER)
        header.pack_end(mode_box)
        self.main_box.append(header)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_top(8)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        self.main_box.append(toolbar)

        self.btn_refresh = Gtk.Button(label="Refresh")
        self.btn_refresh.connect("clicked", self._action_check_updates)
        toolbar.append(self.btn_refresh)

        self.btn_install = Gtk.Button(label="Install Selected")
        self.btn_install.connect("clicked", self._install_selected)
        self.btn_install.add_css_class("suggested-action")
        toolbar.append(self.btn_install)

        self.btn_remove = Gtk.Button(label="Remove Selected")
        self.btn_remove.connect("clicked", self._remove_selected)
        self.btn_remove.add_css_class("destructive-action")
        toolbar.append(self.btn_remove)

        self.btn_autorm = Gtk.Button(label="Auto-Remove Old")
        self.btn_autorm.connect("clicked", self._auto_remove_old_kernels)
        toolbar.append(self.btn_autorm)

        self.chk_auto_rm = Gtk.CheckButton(label="Auto-remove after install")
        self.chk_auto_rm.set_active(self._load_config().get("auto_remove_after_install", False))
        toolbar.append(self.chk_auto_rm)

        self.spinner = Gtk.Spinner()
        toolbar.append(self.spinner)

        search_box = Gtk.Box(spacing=6)
        search_box.set_margin_top(8)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        self.main_box.append(search_box)
        search_box.append(Gtk.Label(label="Filter:"))
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search all kernels...")
        self.search_entry.connect("search-changed", lambda _: self._refilter_all())
        search_box.append(self.search_entry)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.main_box.append(self.stack)

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(8)
        switcher.set_margin_bottom(8)
        self.main_box.append(switcher)

        self.filtered_xanmod   = self._create_filtered_model(self.store_xanmod)
        self.filtered_liquorix = self._create_filtered_model(self.store_liquorix)
        self.filtered_generic  = self._create_filtered_model(self.store_generic)

        factory = self._create_factory()

        def make_listview(model, title):
            frame = Gtk.Frame()
            frame.set_label_widget(Gtk.Label().set_markup(title))
            selection = Gtk.SingleSelection(model=model)
            lv = Gtk.ListView(model=selection, factory=factory)
            lv.connect("activate", self._on_list_activated)
            sc = Gtk.ScrolledWindow(vexpand=True)
            sc.set_child(lv)
            frame.set_child(sc)
            return frame

        self.stack.add_titled(make_listview(self.filtered_xanmod,   "<b>XanMod Kernels</b>"),   "xanmod",   "XanMod")
        self.stack.add_titled(make_listview(self.filtered_liquorix, "<b>Liquorix Kernels</b>"), "liquorix", "Liquorix")
        self.stack.add_titled(make_listview(self.filtered_generic,  "<b>Mainline / Generic</b>"), "generic",  "Mainline")

        controls = Gtk.Box(spacing=8)
        controls.set_margin_start(12)
        controls.set_margin_end(12)
        self.btn_details = Gtk.ToggleButton(label="Show Details")
        self.btn_details.connect("toggled", lambda b: self.revealer.set_reveal_child(b.get_active()))
        self.status_label = Gtk.Label(xalign=0, hexpand=True)
        controls.append(self.btn_details)
        controls.append(self.status_label)
        self.main_box.append(controls)

        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.main_box.append(self.revealer)

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=12, margin_end=12, margin_bottom=12)
        self.textview = Gtk.TextView(editable=False, monospace=True)
        self.textbuf = self.textview.get_buffer()

        # BLOCK RIGHT-CLICK IN LOG TEXTVIEW — GTK4 CORRECT WAY
        log_click_ctrl = Gtk.GestureClick()
        log_click_ctrl.set_button(3)
        log_click_ctrl.connect("pressed", lambda *_: True)
        self.textview.add_controller(log_click_ctrl)

        log_scroll = Gtk.ScrolledWindow(min_content_height=180)
        log_scroll.set_child(self.textview)
        log_box.append(log_scroll)
        self.revealer.set_child(log_box)

        self._update_buttons()

    def _create_filtered_model(self, store):
        filter_model = Gtk.FilterListModel(model=store)
        string_filter = Gtk.StringFilter.new(Gtk.PropertyExpression.new(KernelRow, None, "markup"))
        string_filter.set_ignore_case(True)
        filter_model.set_filter(string_filter)
        self.search_entry.connect("search-changed", lambda e: string_filter.set_search(e.get_text().lower()))
        return filter_model

    def _refilter_all(self):
        for m in (self.filtered_xanmod, self.filtered_liquorix, self.filtered_generic):
            if m.get_filter():
                m.get_filter().changed(Gtk.FilterChange.DIFFERENT)

    def _create_factory(self):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._factory_setup)
        factory.connect("bind", self._factory_bind)
        factory.connect("unbind", self._factory_unbind)
        return factory

    def _factory_setup(self, factory, list_item):
        box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        check = Gtk.CheckButton()
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label_name = Gtk.Label(xalign=0, hexpand=True)
        label_name.add_css_class("heading")
        label_size = Gtk.Label(xalign=0)
        label_size.add_css_class("dim-label")
        labels.append(label_name)
        labels.append(label_size)
        status = Gtk.Label(xalign=1)
        status.add_css_class("caption")
        box.append(check)
        box.append(labels)
        box.append(status)
        list_item.set_child(box)
        list_item.check = check
        list_item.label_name = label_name
        list_item.label_size = label_size
        list_item.status = status

    def _factory_bind(self, factory, list_item):
        row = list_item.get_item()
        check = list_item.check
        if hasattr(check, "handler_id"):
            check.disconnect(check.handler_id)
        check.set_active(row.is_selected)
        check.handler_id = check.connect("toggled", self._on_check_toggled, row)
        list_item.label_name.set_markup(row.markup)
        list_item.label_size.set_text(row.size)
        list_item.status.set_text(row.status)

    def _factory_unbind(self, factory, list_item):
        check = list_item.check
        if hasattr(check, "handler_id"):
            check.disconnect(check.handler_id)
            del check.handler_id

    def _on_check_toggled(self, button, row):
        row.is_selected = button.get_active()
        self._update_buttons()

    def _on_list_activated(self, listview, position):
        model = listview.get_model()
        row = model.get_item(position)
        row.is_selected = not row.is_selected
        self._update_buttons()

    def on_mode_toggled(self, switch, _):
        dark = switch.get_active()
        cfg = self._load_config()
        cfg["dark_mode"] = dark
        self._save_config(cfg)
        self.win.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
        self.status_push(f"Switched to {'Dark' if dark else 'Light'} mode")

    def status_push(self, msg):
        self.status_label.set_text(msg)

    def _append_log(self, text):
        end = self.textbuf.get_end_iter()
        self.textbuf.insert(end, text)
        mark = self.textbuf.create_mark(None, self.textbuf.get_end_iter(), True)
        self.textview.scroll_mark_onscreen(mark)
        if self._log_handle:
            try:
                self._log_handle.write(text)
                self._log_handle.flush()
            except: pass

    def _start_log_session(self, prefix):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        fp = LOG_DIR / f"{prefix}_{ts}.log"
        self._log_handle = open(fp, "w", encoding="utf-8")
        self._append_log(f"\n=== Log started: {fp} ===\n")

    def _end_log_session(self):
        if self._log_handle:
            self._append_log("\n=== Log ended ===\n")
            self._log_handle.close()
        self._log_handle = None

    def _clear_log(self):
        self.textbuf.set_text("")

    def _set_busy(self, busy, text=""):
        self.busy = busy
        self.spinner.set_spinning(busy)
        self.status_push(text if text else ("Working…" if busy else "Ready"))
        self._update_buttons()

    def _open_cache(self):
        if self.cache is None:
            self.cache = apt.Cache()
        else:
            self.cache.open(None)

    def _version_cmp(self, a, b):
        try: return apt_pkg.version_compare(a or "0", b or "0")
        except: return (a > b) - (a < b)

    def _fmt_bytes(self, n):
        if not n or n <= 0: return "—"
        for unit in ["B", "KB", "MB", "GB"]:
            if n < 1024: return f"{n:.1f} {unit}".rstrip("0").rstrip(".") + (" " if unit != "B" else "")
            n /= 1024
        return f"{n:.1f} TB"

    def _collect_kernels(self):
        self._open_cache()
        items = []
        run = platform.uname().release
        for pkg in self.cache:
            name = pkg.name
            if not (is_xanmod_name(name) or is_liquorix_name(name) or is_generic_kernel_name(name)):
                continue
            cand = pkg.candidate
            if not cand: continue
            version = cand.version or ""
            installed = pkg.is_installed
            active = installed and (version in run or run.startswith(version + "-"))
            status = "Active" if active else ("Installed" if installed else "Available")
            size = self._fmt_bytes(getattr(cand, "installed_size", 0) or 0)
            markup = (
                f"<b>{name}</b> <small>({version})</small> <span foreground='green'><b>[Active]</b></span>" if active else
                f"<span foreground='gray'>{name} <small>({version})</small> [Installed]</span>" if installed else
                f"<b>{name}</b> <small>({version})</small> <span foreground='green'>[Available]</span>"
            )
            items.append({"name": name, "version": version, "installed": installed, "active": active,
                          "status": status, "size": size, "markup": markup})
        items.sort(key=cmp_to_key(lambda a, b: (
            (0 if a["active"] else 1 if a["installed"] else 2) -
            (0 if b["active"] else 1 if b["installed"] else 2) or
            -self._version_cmp(a["version"], b["version"])
        )))
        return items

    def _reload_kernels_async(self):
        def worker():
            try:
                items = self._collect_kernels()
                GLib.idle_add(self._on_kernels_loaded, items)
            except Exception as e:
                GLib.idle_add(self._error_dialog, "Error", str(e))
        threading.Thread(target=worker, daemon=True).start()

    def _on_kernels_loaded(self, items):
        self.kernels = items
        self._populate_models()

    def _populate_models(self):
        for store in (self.store_xanmod, self.store_liquorix, self.store_generic):
            store.remove_all()
        for k in self.kernels:
            row = KernelRow(is_selected=False, markup=k["markup"], name=k["name"], version=k["version"],
                            size=k["size"], status=k["status"], is_installed=k["installed"], is_active=k["active"])
            if is_xanmod_name(k["name"]):
                self.store_xanmod.append(row)
            elif is_liquorix_name(k["name"]):
                self.store_liquorix.append(row)
            elif is_generic_kernel_name(k["name"]):
                self.store_generic.append(row)
        self._refilter_all()
        self._update_buttons()

    def _stream_subprocess(self, cmd, on_done):
        def worker():
            combined = []
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                for line in proc.stdout:
                    combined.append(line)
                    GLib.idle_add(self._append_log, line)
                rc = proc.wait()
            except Exception as e:
                rc = 1
                combined.append(f"\nERROR: {e}\n")
                GLib.idle_add(self._append_log, combined[-1])
            GLib.idle_add(on_done, rc, "".join(combined))
        threading.Thread(target=worker, daemon=True).start()

    def _action_check_updates(self, *_):
        self._set_busy(True, "Updating sources…")
        self._stream_subprocess(["pkexec", "apt-get", "update", "-qq"], lambda rc,_: (self._set_busy(False), self._reload_kernels_async()))

    def _get_selected_packages(self, only_installed=None):
        pkgs = []
        for store in (self.store_xanmod, self.store_liquorix, self.store_generic):
            for i in range(store.get_n_items()):
                row = store.get_item(i)
                if row.is_selected and (only_installed is None or row.is_installed == only_installed):
                    pkgs.append(row.name)
        return pkgs

    def _install_selected(self, *_):
        pkgs = self._get_selected_packages(only_installed=False)
        if not pkgs:
            self._error_dialog("Nothing selected", "Select kernels to install.")
            return
        self._pre_modules = set(os.listdir("/usr/lib/modules")) if os.path.isdir("/usr/lib/modules") else set()
        self.btn_details.set_active(True)
        self._clear_log()
        self._start_log_session("install")
        self._set_busy(True, "Installing kernels…")
        self._stream_subprocess(["pkexec", "apt", "install", "-y"] + pkgs, self._on_install_done)

    def _on_install_done(self, rc, _):
        ok = rc == 0
        self._set_busy(False, "Installation complete." if ok else "Installation failed.")
        if not ok:
            self._error_dialog("Install failed", "See log for details.")
            self._end_log_session()
            return
        if self.chk_auto_rm.get_active():
            self._auto_remove_old_kernels()
        self._dkms_for_new_kernels_then_reboot()
        self._reload_kernels_async()

    def _dkms_for_new_kernels_then_reboot(self):
        after = set(os.listdir("/usr/lib/modules")) if os.path.isdir("/usr/lib/modules") else set()
        new = [r for r in (after - self._pre_modules) if r != self.running_release]
        if new:
            self._set_busy(True, "Rebuilding DKMS modules…")
            self._stream_subprocess(
                ["pkexec", "bash", "-c", f"for k in {' '.join(new)}; do dkms autoinstall -k \"$k\" || true; done"],
                lambda *_: (self._set_busy(False), self._show_reboot_dialog(), self._end_log_session())
            )
        else:
            self._show_reboot_dialog()
            self._end_log_session()

    def _show_reboot_dialog(self):
        dlg = Adw.MessageDialog(transient_for=self.win, heading="Reboot Required", body="New kernel installed. Reboot now?")
        dlg.add_response("cancel", "Later")
        dlg.add_response("ok", "Reboot Now")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: r == "ok" and subprocess.Popen(["pkexec", "systemctl", "reboot"]))
        dlg.present()

    def _remove_selected(self, *_):
        if any(store.get_item(i).is_selected and store.get_item(i).is_active
               for store in (self.store_xanmod, self.store_liquorix, self.store_generic)
               for i in range(store.get_n_items())):
            self._error_dialog("Error", "Cannot remove active kernel.")
            return
        pkgs = self._get_selected_packages(only_installed=True)
        if not pkgs: return
        self.btn_details.set_active(True)
        self._clear_log()
        self._start_log_session("remove")
        self._set_busy(True, "Removing kernels…")
        self._stream_subprocess(["pkexec", "apt", "remove", "--purge", "-y"] + pkgs,
                                lambda *_: (self._set_busy(False), self._reload_kernels_async(), self._end_log_session()))

    def _auto_remove_old_kernels(self, *_):
        installed = [r for store in (self.store_xanmod, self.store_liquorix, self.store_generic)
                    for i in range(store.get_n_items()) if (r := store.get_item(i)).is_installed]
        if not installed: return
        versions = {}
        for row in installed:
            versions.setdefault(row.version, []).append(row)
        ver_list = sorted(versions.keys(), key=cmp_to_key(self._version_cmp), reverse=True)
        active = next((r.version for r in installed if r.is_active), None)
        keep = {active} if active else set()
        keep.update(ver_list[:2])
        to_remove = [r.name for v in versions if v not in keep for r in versions[v]]
        if not to_remove: return
        self.btn_details.set_active(True)
        self._clear_log()
        self._start_log_session("autoremove")
        self._set_busy(True, "Auto-removing old kernels…")
        self._stream_subprocess(["pkexec", "apt", "remove", "--purge", "-y"] + to_remove,
                                lambda *_: (self._set_busy(False), self._reload_kernels_async(), self._end_log_session()))

    def _update_buttons(self):
        can_install = can_remove = False
        for store in (self.store_xanmod, self.store_liquorix, self.store_generic):
            for i in range(store.get_n_items()):
                row = store.get_item(i)
                if row.is_selected:
                    if not row.is_installed: can_install = True
                    if row.is_installed and not row.is_active: can_remove = True
        self.btn_install.set_sensitive(can_install and not self.busy)
        self.btn_remove.set_sensitive(can_remove and not self.busy)
        self.btn_refresh.set_sensitive(not self.busy)
        self.btn_autorm.set_sensitive(not self.busy)

    def _periodic_check(self):
        self._reload_kernels_async()
        # FIX: Return True to make the timeout repeat periodically
        return True

    def _error_dialog(self, title, message):
        dlg = Adw.MessageDialog(transient_for=self.win, heading=title, body=message)
        dlg.add_response("ok", "OK")
        dlg.present()

def main():
    app = KernelManagerApp()
    app.run(sys.argv)

if __name__ == "__main__":
    main()
