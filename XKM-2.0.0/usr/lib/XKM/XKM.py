#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-Kernel Manager — XanMod + Liquorix + Mainline
v2.0.0 — Purge/Remove choice, apt-mark hold support, mainline metapackages,
          update-grub safety net, XanMod flavor selection.
"""

APP_VERSION = "2.0.0"
RELEASES_API_URL = "https://api.github.com/repos/bobbycomet/XKM-Multi-Kernel-Manager/releases/latest"

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
os.close(_devnull)

try:
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
    except Exception:
        pass

    import apt
    import apt_pkg
finally:
    # Always restore real stderr — even if an import throws
    os.dup2(_old_stderr, 2)
    os.close(_old_stderr)

apt_pkg.init()
apt_pkg.config.set("Quiet", "2")
apt_pkg.config.set("APT::Get::Assume-Yes", "true")
apt_pkg.config.set("Dir::Log::Terminal", "/dev/null")
apt_pkg.config.set("APT::Status-Fd", "2")

import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger().setLevel(1000)

class SilentCache(apt.Cache):
    def __init__(self, *args, **kwargs):
        # Suppress apt's stderr chatter during cache open.
        # We save the real stderr fd and restore it afterward rather than
        # redirecting to fd 1 (stdout), which would be wrong if stdout has
        # also been redirected by the caller.
        _null = os.open("/dev/null", os.O_WRONLY)
        _saved = os.dup(2)
        os.dup2(_null, 2)
        os.close(_null)
        try:
            super().__init__(*args, **kwargs)
        finally:
            os.dup2(_saved, 2)
            os.close(_saved)
apt.Cache = SilentCache

import json
import re
import threading
import subprocess
import platform
import urllib.request
import urllib.error
from pathlib import Path
from functools import cmp_to_key
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gio, GLib, Adw, GObject

APP_ID = "com.xanmod.kernel.manager"

# ─── GPU Detection ───────────────────────────────────────────────────────────

def detect_gpus():
    """Returns a set of detected GPU vendors: 'nvidia', 'amd', 'intel'"""
    gpus = set()
    try:
        out = subprocess.check_output(["lspci"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            l = line.lower()
            if "vga" in l or "3d" in l or "display" in l:
                if "nvidia" in l:
                    gpus.add("nvidia")
                if "amd" in l or "radeon" in l or "advanced micro" in l:
                    gpus.add("amd")
                if "intel" in l:
                    gpus.add("intel")
    except Exception:
        pass
    return gpus

GPU_VENDORS = detect_gpus()

# Package name → GPU relevance map (patterns)
NVIDIA_PKG_PATTERNS = ("linux-modules-nvidia", "linux-modules-extra-nvidia",
                        "linux-modules-extra-gep")
AMD_PKG_PATTERNS    = ("linux-modules-amd",)
INTEL_PKG_PATTERNS  = ("linux-modules-intel",)

def gpu_relevant(pkg_name: str) -> bool:
    """Return True if this package is relevant for installed GPU hardware."""
    n = pkg_name.lower()
    if any(p in n for p in NVIDIA_PKG_PATTERNS) and "nvidia" not in GPU_VENDORS:
        return False
    if any(p in n for p in AMD_PKG_PATTERNS) and "amd" not in GPU_VENDORS:
        return False
    if any(p in n for p in INTEL_PKG_PATTERNS) and "intel" not in GPU_VENDORS:
        return False
    return True

# ─── Package Classification ───────────────────────────────────────────────────

def is_liquorix_name(name: str) -> bool:
    return name.startswith(("linux-image-liquorix-", "linux-headers-liquorix-"))

def is_xanmod_name(name: str) -> bool:
    """
    Return True for any XanMod kernel package.

    The XanMod apt repo ships two kinds of packages:
      • Meta/tracking:  linux-xanmod-x64v3, linux-xanmod-edge, linux-xanmod …
      • Versioned:      linux-image-6.18.3-x64v3-xanmod1,
                        linux-headers-6.18.3-x64v3-xanmod1
                        linux-image-6.12.68-x64v2-xanmod1-lts …

    The versioned packages do NOT start with "linux-image-xanmod"; the version
    number comes first and "xanmod" appears as an infix/suffix.
    """
    n = name.lower()
    # Meta-packages: linux-xanmod*, linux-image-xanmod*, linux-headers-xanmod*
    if any(n.startswith(p) for p in (
        "linux-xanmod", "linux-image-xanmod", "linux-headers-xanmod",
        "linux-modules-xanmod",
    )):
        return True
    # Versioned packages: linux-image-<ver>-x64vN-xanmodN[…]
    #                     linux-headers-<ver>-x64vN-xanmodN[…]
    if any(n.startswith(p) for p in ("linux-image-", "linux-headers-")):
        if "xanmod" in n:
            return True
    return False

# XanMod flavors in display order.  "any" means show all.
XANMOD_FLAVORS = ["any", "v1", "v2", "v3", "v4", "edge", "lts", "rt"]

def xanmod_flavor(pkg_name: str) -> str:
    """
    Extract the XanMod CPU optimisation level from a package name.

    Meta-packages:   linux-xanmod-x64v3       → 'v3'
                     linux-xanmod-edge         → 'edge'
                     linux-xanmod              → 'generic'
    Versioned:       linux-image-6.18.3-x64v3-xanmod1       → 'v3'
                     linux-image-6.12.68-x64v2-xanmod1-lts  → 'v2'
                     linux-image-6.1.0-rt-xanmod1           → 'rt'
                     linux-image-6.18.3-xanmod1             → 'generic'
    """
    n = pkg_name.lower()
    # x64vN wins over bare -vN; check longest/most-specific tokens first.
    for token, mapped in (
        ("x64v4", "v4"), ("x64v3", "v3"), ("x64v2", "v2"), ("x64v1", "v1"),
        ("-edge", "edge"), ("-lts",  "lts"),  ("-rt",   "rt"),
        ("-v4",   "v4"),  ("-v3",   "v3"),  ("-v2",   "v2"),  ("-v1",   "v1"),
    ):
        if token in n:
            return mapped
    return "generic"

def is_generic_kernel_name(name: str) -> bool:
    if is_xanmod_name(name) or is_liquorix_name(name):
        return False
    return name.startswith((
        "linux-image-", "linux-headers-", "linux-modules-",
        "linux-image-unsigned-", "linux-image-uc-", "linux-image-oem-",
    ))

# ─── Mainline Kernel Version Grouping ────────────────────────────────────────

# Pattern to extract the kernel version number from a package name
# e.g. linux-image-6.14.0-37-generic → 6.14.0-37
# e.g. linux-modules-extra-6.14.0-37-generic → 6.14.0-37
_KVER_RE = re.compile(
    r"(?:linux-image-unsigned-|linux-image-uc-|linux-image-oem-|"
    r"linux-image-|linux-headers-|linux-modules-extra-|linux-modules-nvidia-\S+-|"
    r"linux-modules-extra-gep-|linux-modules-)(\d+\.\d+\.\d+-\d+)"
)

def extract_kernel_version(pkg_name: str):
    """
    Extract the numeric kernel version from a mainline package name.
    Returns e.g. '6.14.0-37' or None if not found.
    """
    m = _KVER_RE.search(pkg_name)
    return m.group(1) if m else None

def pkg_category(pkg_name: str) -> str:
    """Return a human-readable category for a mainline package."""
    n = pkg_name.lower()
    if "linux-image-unsigned-" in n or "linux-image-uc-" in n:
        return "Image (unsigned/uc)"
    if "linux-image-oem-" in n:
        return "Image (OEM)"
    if "linux-image-" in n:
        return "Image"
    if "linux-headers-" in n:
        return "Headers"
    if "linux-modules-extra-gep" in n:
        return "Modules Extra (GEP)"
    if "linux-modules-nvidia" in n:
        return "Modules NVIDIA"
    if "linux-modules-extra" in n:
        return "Modules Extra"
    if "linux-modules-" in n:
        return "Modules"
    return "Other"


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
    "win_size": [1080, 680],
    "dark_mode": True,
}

Adw.init()

# ─── Shared Config (single source of truth) ──────────────────────────────────

def load_config() -> dict:
    """Load config from disk, merging with defaults. Safe to call from any class."""
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()

def save_config(data: dict) -> None:
    """Persist config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ─── GObject Models ──────────────────────────────────────────────────────────

class KernelRow(GObject.Object):
    __gtype_name__ = "KernelRow"
    is_selected  = GObject.Property(type=bool, default=False)
    markup       = GObject.Property(type=str,  default="")
    name         = GObject.Property(type=str,  default="")
    version      = GObject.Property(type=str,  default="")
    size         = GObject.Property(type=str,  default="")
    status       = GObject.Property(type=str,  default="")
    is_installed = GObject.Property(type=bool, default=False)
    is_active    = GObject.Property(type=bool, default=False)
    is_held      = GObject.Property(type=bool, default=False)
    category     = GObject.Property(type=str,  default="")
    kver         = GObject.Property(type=str,  default="")  # e.g. "6.14.0-37"
    gpu_relevant = GObject.Property(type=bool, default=True)
    flavor       = GObject.Property(type=str,  default="")  # xanmod flavor or ""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

# Mainline meta-package name prefixes — no numeric version in name.
# These act as "tracking" packages: install once, upgrade automatically.
MAINLINE_META_NAMES = {
    "linux-generic", "linux-generic-hwe-22.04", "linux-generic-hwe-24.04",
    "linux-lowlatency", "linux-lowlatency-hwe-22.04", "linux-lowlatency-hwe-24.04",
    "linux-virtual", "linux-cloud-tools-generic",
    "linux-oem-22.04", "linux-oem-24.04",
    "linux-headers-generic", "linux-headers-generic-hwe-22.04",
    "linux-headers-generic-hwe-24.04",
}

def is_mainline_meta(name: str) -> bool:
    """True for metapackages that track a kernel flavour without a version number."""
    return name in MAINLINE_META_NAMES or any(
        name.startswith(p) for p in ("linux-oem-", "linux-generic-hwe-", "linux-lowlatency-hwe-")
    )

# ─── App & Window ─────────────────────────────────────────────────────────────

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
        cfg = load_config()
        self.set_default_size(*cfg.get("win_size", [1080, 680]))
        self.manager = KernelManager(self)
        self.set_content(self.manager.toast_overlay)
        self.style_manager = Adw.StyleManager.get_default()
        self.apply_color_scheme()
        self.connect("close-request", self._on_close)
        self.connect("realize", self._on_window_realized)
        click_ctrl = Gtk.GestureClick()
        click_ctrl.set_button(3)
        click_ctrl.connect("pressed", lambda *_: True)
        self.add_controller(click_ctrl)

    def _on_window_realized(self, _):
        # Show a loading indicator in the mainline tab immediately
        loading = Gtk.Label(label="Loading package cache…")
        loading.set_margin_top(40)
        self.manager._mainline_box.append(loading)
        self.manager._reload_kernels_async()
        if AUTO_OFFER_ADD_REPO:
            GLib.idle_add(self.manager._maybe_offer_add_repos)
        # Check for updates a couple of seconds after window appears
        GLib.timeout_add_seconds(3, self.manager._check_for_app_update)

    def apply_color_scheme(self):
        dark = load_config().get("dark_mode", True)
        self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)

    def _on_close(self, *_):
        cfg = load_config()
        w, h = self.get_default_size()
        cfg["win_size"] = [w, h]
        cfg["auto_remove_after_install"] = self.manager.chk_auto_rm.get_active()
        save_config(cfg)
        self.manager._end_log_session()

# ─── Kernel Manager Core ──────────────────────────────────────────────────────

class KernelManager:
    def __init__(self, win):
        self.win = win
        self.cache = None
        self.kernels = []
        self.running_release = platform.uname().release
        self._pre_modules = set()
        self.busy = False
        self._log_handle = None
        self._pkexec_fail_count = 0
        self._search_debounce_id = None  # GLib timeout ID for search debounce
        self._rebuild_generation = 0     # incremented on each rebuild request to cancel stale ones

        self.store_xanmod   = Gio.ListStore(item_type=KernelRow)
        self.store_liquorix = Gio.ListStore(item_type=KernelRow)
        # NOTE: Mainline/generic packages are NOT stored in a flat ListStore.
        # They live in _mainline_groups (dict[kver → list[KernelRow]]) so the
        # grouped UI can render them as collapsible version cards. store_generic
        # is intentionally absent — any future flat-list fallback can add it here.

        # Mainline: keyed by kver string, value = list of KernelRow
        self._mainline_groups: dict[str, list] = {}
        # Mainline metapackages (linux-generic, linux-lowlatency, etc.)
        self.store_meta = Gio.ListStore(item_type=KernelRow)
        # Currently selected XanMod flavor filter ("any" means show all)
        self._xanmod_flavor_filter: str = "any"

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.main_box)
        self._build_ui()

        hours = load_config().get("auto_check_hours", 6)
        GLib.timeout_add_seconds(int(max(1, hours)) * 3600, self._periodic_check)

    # ── Config ───────────────────────────────────────────────────────────────
    # All config I/O goes through the module-level load_config() / save_config()
    # functions directly. No instance wrappers — there are no subclasses and no
    # external callers that depend on these as instance methods.

    # ── Repo Detection ────────────────────────────────────────────────────────

    def _xanmod_repo_present(self):
        if Path(XANMOD_SOURCE_FILE).exists() and Path(XANMOD_KEYRING).exists():
            return True
        patterns = ["deb.xanmod.org", "http://deb.xanmod.org", "https://deb.xanmod.org"]
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
        patterns = ["damentz/liquorix", "liquorix.net"]
        try:
            sources = ["/etc/apt/sources.list"] + list(Path("/etc/apt/sources.list.d").glob("*"))
            for src in sources:
                try:
                    if any(p.lower() in Path(src).read_text().lower() for p in patterns):
                        return True
                except: continue
            try:
                cache = apt.Cache()
                for pkg in cache:
                    if pkg.name.startswith(("linux-image-liquorix-", "linux-headers-liquorix-")):
                        return True
            except Exception:
                pass
        except Exception:
            pass
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

    def _add_liquorix_ppa_official(self):
        self.btn_details.set_active(True)
        self._clear_log()
        self._start_log_session("liquorix-ppa")
        self._set_busy(True, "Adding official Liquorix PPA…")

        def on_update_done(rc, _):
            self._set_busy(False)
            self.status_push("Liquorix PPA added." if rc == 0 else "Failed to update sources.")
            self._reload_kernels_async()
            self._end_log_session()

        def on_add_done(rc, _):
            if rc == 0:
                self._set_busy(True, "Updating package list…")
                self._stream_subprocess(["pkexec", "apt", "update", "-qq"], on_update_done)
            else:
                self._set_busy(False, "Failed to add Liquorix PPA.")
                self._end_log_session()

        self._stream_subprocess(["pkexec", "add-apt-repository", "-y", "ppa:damentz/liquorix"], on_add_done)

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
        self._stream_subprocess(cmd, self._on_xanmod_repo_added)

    def _on_xanmod_repo_added(self, rc, _output):
        self._set_busy(False, "XanMod repository added." if rc == 0 else "Failed to add XanMod repository.")
        self._reload_kernels_async()
        self._end_log_session()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        """Top-level UI assembly — delegates to focused sub-builders.
        NOTE: _build_search_bar() must run before _build_stack() because the
        filtered models connect to self.search_entry at construction time.
        """
        self.main_box.append(self._build_header())
        self._build_toolbar()
        self._build_search_bar()   # ← search_entry must exist before _build_stack
        self._build_stack()
        self._build_log_panel()
        self._update_buttons()

    def _build_header(self) -> Adw.HeaderBar:
        """Construct the Adw.HeaderBar with title, dark-mode switch, and GPU badge."""
        header = Adw.HeaderBar()
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title.append(Gtk.Label(label="Multi-Kernel Manager"))
        title.append(Gtk.Label(label="XanMod • Liquorix • Mainline"))
        header.set_title_widget(title)

        self.mode_switch = Gtk.Switch()
        self.mode_switch.set_active(load_config().get("dark_mode", True))
        self.mode_switch.connect("notify::active", self.on_mode_toggled)
        mode_box = Gtk.Box(spacing=8)
        mode_box.append(Gtk.Label(label="Light"))
        mode_box.append(self.mode_switch)
        mode_box.append(Gtk.Label(label="Dark"))
        mode_box.set_valign(Gtk.Align.CENTER)
        header.pack_end(mode_box)

        if GPU_VENDORS:
            badge_text = " + ".join(sorted(v.upper() for v in GPU_VENDORS)) + " Detected"
            badge = Gtk.Label(label=badge_text)
            badge.add_css_class("caption")
            badge.add_css_class("success")
            header.pack_start(badge)

        return header

    def _build_toolbar(self):
        """Build the action toolbar (install, remove, hold, unhold, auto-remove, spinner)."""
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_top(8)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        self.main_box.append(toolbar)

        self.btn_refresh = Gtk.Button(label="↺  Refresh")
        self.btn_refresh.connect("clicked", self._action_check_updates)
        toolbar.append(self.btn_refresh)

        self.btn_install = Gtk.Button(label="⬇  Install Selected")
        self.btn_install.connect("clicked", self._install_selected)
        self.btn_install.add_css_class("suggested-action")
        toolbar.append(self.btn_install)

        self.btn_install_hold = Gtk.Button(label="⬇🔒 Install + Hold")
        self.btn_install_hold.set_tooltip_text(
            "Install selected packages and immediately apply apt-mark hold\n"
            "so they are never auto-upgraded (popular for pinning XanMod versions)."
        )
        self.btn_install_hold.connect("clicked", self._install_and_hold_selected)
        toolbar.append(self.btn_install_hold)

        self.btn_remove = Gtk.Button(label="✕  Remove Selected")
        self.btn_remove.connect("clicked", self._remove_selected)
        self.btn_remove.add_css_class("destructive-action")
        toolbar.append(self.btn_remove)

        self.btn_hold = Gtk.Button(label="🔒 Hold")
        self.btn_hold.set_tooltip_text("Prevent selected packages from being upgraded or removed (apt-mark hold)")
        self.btn_hold.connect("clicked", self._hold_selected)
        toolbar.append(self.btn_hold)

        self.btn_unhold = Gtk.Button(label="🔓 Unhold")
        self.btn_unhold.set_tooltip_text("Release hold on selected packages (apt-mark unhold)")
        self.btn_unhold.connect("clicked", self._unhold_selected)
        toolbar.append(self.btn_unhold)

        self.btn_autorm = Gtk.Button(label="Auto-Remove Old")
        self.btn_autorm.connect("clicked", self._auto_remove_old_kernels)
        toolbar.append(self.btn_autorm)

        self.chk_auto_rm = Gtk.CheckButton(label="Auto-remove after install")
        self.chk_auto_rm.set_active(load_config().get("auto_remove_after_install", False))
        toolbar.append(self.chk_auto_rm)

        self.spinner = Gtk.Spinner()
        toolbar.append(self.spinner)

        # Warning banner — shown when pkexec fails repeatedly (non-root context).
        self._pkexec_warn_bar = Gtk.InfoBar()
        self._pkexec_warn_bar.set_message_type(Gtk.MessageType.WARNING)
        self._pkexec_warn_bar.add_child(
            Gtk.Label(label="⚠  Privilege escalation (pkexec) failed multiple times.  "
                            "Are you running as a non-privileged user without sudo rights?")
        )
        self._pkexec_warn_bar.set_show_close_button(True)
        self._pkexec_warn_bar.set_visible(False)
        self._pkexec_warn_bar.connect("response", lambda bar, _: bar.set_visible(False))
        self.main_box.append(self._pkexec_warn_bar)

    def _build_search_bar(self):
        """Build the package search / filter row."""
        search_box = Gtk.Box(spacing=6)
        search_box.set_margin_top(4)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        self.main_box.append(search_box)
        search_box.append(Gtk.Label(label="Filter:"))
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search all kernels…")
        self.search_entry.connect("search-changed", self._on_search_changed)
        search_box.append(self.search_entry)

    def _build_stack(self):
        """Build the XanMod / Liquorix / Mainline tab stack."""
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(8)
        switcher.set_margin_bottom(4)
        self.main_box.append(switcher)
        self.main_box.append(self.stack)

        self.filtered_xanmod   = self._create_xanmod_filtered_model()
        self.filtered_liquorix = self._create_filtered_model(self.store_liquorix)

        simple_factory = self._create_simple_factory()

        def make_simple_listview(model):
            sc = Gtk.ScrolledWindow(vexpand=True)
            sel = Gtk.SingleSelection(model=model)
            lv = Gtk.ListView(model=sel, factory=simple_factory)
            lv.connect("activate", self._on_list_activated)
            sc.set_child(lv)
            return sc

        self.stack.add_titled(self._build_xanmod_tab(make_simple_listview), "xanmod", "XanMod")
        self.stack.add_titled(make_simple_listview(self.filtered_liquorix), "liquorix", "Liquorix")
        self.stack.add_titled(self._build_mainline_tab(make_simple_listview), "mainline", "Mainline")

    def _build_xanmod_tab(self, make_simple_listview) -> Gtk.Box:
        """Build the XanMod tab contents (flavor filter bar + list)."""
        xanmod_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        flavor_bar = Gtk.Box(spacing=6, margin_top=8, margin_bottom=4,
                             margin_start=12, margin_end=12)
        flavor_bar.append(Gtk.Label(label="Flavor:"))
        self.flavor_combo = Gtk.DropDown()
        flavor_strings = Gtk.StringList.new(XANMOD_FLAVORS)
        self.flavor_combo.set_model(flavor_strings)
        self.flavor_combo.set_selected(0)
        self.flavor_combo.set_tooltip_text(
            "Filter XanMod packages by CPU optimization level.\n"
            "v1=baseline  v2=SSE4  v3=AVX2  v4=AVX-512  edge=latest unstable"
        )
        self.flavor_combo.connect("notify::selected", self._on_flavor_changed)
        flavor_bar.append(self.flavor_combo)

        flavor_hint = Gtk.Label(
            label="ℹ  v1=baseline · v2=SSE4 · v3=AVX2 · v4=AVX-512 · edge=latest",
            xalign=0, hexpand=True
        )
        flavor_hint.add_css_class("caption")
        flavor_hint.add_css_class("dim-label")
        flavor_bar.append(flavor_hint)

        xanmod_outer.append(flavor_bar)
        xanmod_outer.append(make_simple_listview(self.filtered_xanmod))
        return xanmod_outer

    def _build_mainline_tab(self, make_simple_listview) -> Gtk.Box:
        """Build the Mainline tab — grouped versioned packages + meta-packages,
        all rendered by _rebuild_mainline_ui into a single scrollable widget.
        The old flat meta ListView has been removed; meta-packages now appear as
        a pinned group at the top of the grouped view."""
        mainline_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._mainline_scroll = Gtk.ScrolledWindow(vexpand=True)
        self._mainline_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._mainline_scroll.set_child(self._mainline_box)
        mainline_outer.append(self._mainline_scroll)

        return mainline_outer

    def _build_log_panel(self):
        """Build the status bar, Details revealer, and log text view."""
        controls = Gtk.Box(spacing=8)
        controls.set_margin_start(12)
        controls.set_margin_end(12)
        controls.set_margin_top(4)
        self.btn_details = Gtk.ToggleButton(label="Show Details")
        self.btn_details.connect("toggled", lambda b: self.revealer.set_reveal_child(b.get_active()))
        self.status_label = Gtk.Label(xalign=0, hexpand=True)
        controls.append(self.btn_details)
        controls.append(self.status_label)
        self.main_box.append(controls)

        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.main_box.append(self.revealer)

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                          margin_start=12, margin_end=12, margin_bottom=12)
        self.textview = Gtk.TextView(editable=False, monospace=True)
        self.textbuf = self.textview.get_buffer()
        log_click_ctrl = Gtk.GestureClick()
        log_click_ctrl.set_button(3)
        log_click_ctrl.connect("pressed", lambda *_: True)
        self.textview.add_controller(log_click_ctrl)
        log_scroll = Gtk.ScrolledWindow(min_content_height=180)
        log_scroll.set_child(self.textview)
        log_box.append(log_scroll)
        self.revealer.set_child(log_box)

    # ── Filtered Models (XanMod / Liquorix / Meta) ──────────────────────────

    def _create_filtered_model(self, store):
        """Standard text filter on the markup property."""
        filter_model = Gtk.FilterListModel(model=store)
        string_filter = Gtk.StringFilter.new(Gtk.PropertyExpression.new(KernelRow, None, "markup"))
        string_filter.set_ignore_case(True)
        filter_model.set_filter(string_filter)
        self.search_entry.connect("search-changed",
                                  lambda e: string_filter.set_search(e.get_text().lower()))
        return filter_model

    def _create_xanmod_filtered_model(self):
        """
        Filter for the XanMod store that combines text search AND flavor selection.
        Uses a Gtk.CustomFilter so we can apply both predicates in one pass.
        """
        def match_fn(row, user_data):
            q = self.search_entry.get_text().lower()
            if q and q not in row.markup.lower():
                return False
            if self._xanmod_flavor_filter != "any":
                if row.flavor != self._xanmod_flavor_filter:
                    return False
            return True

        custom = Gtk.CustomFilter.new(match_fn, None)
        filter_model = Gtk.FilterListModel(model=self.store_xanmod)
        filter_model.set_filter(custom)
        # Store reference so we can call changed() from both triggers
        self._xanmod_custom_filter = custom
        self.search_entry.connect("search-changed",
                                  lambda _: custom.changed(Gtk.FilterChange.DIFFERENT))
        return filter_model

    def _on_flavor_changed(self, combo, _):
        selected = combo.get_selected()
        self._xanmod_flavor_filter = XANMOD_FLAVORS[selected]
        if hasattr(self, "_xanmod_custom_filter"):
            self._xanmod_custom_filter.changed(Gtk.FilterChange.DIFFERENT)

    def _on_search_changed(self, _entry):
        """Debounce search input — only refilter 250 ms after the user stops typing."""
        if self._search_debounce_id is not None:
            GLib.source_remove(self._search_debounce_id)
        self._search_debounce_id = GLib.timeout_add(250, self._do_refilter)

    def _do_refilter(self):
        """Actual filter execution, called after debounce delay."""
        self._search_debounce_id = None
        self._refilter_all()
        return False  # one-shot

    def _refilter_all(self):
        for m in (self.filtered_xanmod, self.filtered_liquorix):
            if m.get_filter():
                m.get_filter().changed(Gtk.FilterChange.DIFFERENT)
        # Mainline tab (including meta card) is fully rebuilt on each filter change
        q = self.search_entry.get_text().lower()
        self._rebuild_mainline_ui(query=q)

    # ── Simple Factory (XanMod / Liquorix) ───────────────────────────────────

    def _create_simple_factory(self):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._factory_setup)
        factory.connect("bind",  self._factory_bind)
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

    # ── Mainline Grouped UI ───────────────────────────────────────────────────

    def _rebuild_mainline_ui(self, query: str = ""):
        """
        Build a grouped Mainline view — the single source of truth for the Mainline tab.
        Uses chunked GLib.idle_add scheduling so the GTK main loop stays responsive
        while building potentially hundreds of widgets. Each call increments a generation
        counter; stale generators (from superseded searches) abort early.
        """
        self._rebuild_generation += 1
        gen = self._rebuild_generation

        # Clear all existing children immediately
        child = self._mainline_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._mainline_box.remove(child)
            child = nxt

        query = (query or "").strip().lower()

        # Build the list of "chunks" to render: each chunk is a callable that
        # appends one card (meta or versioned) to _mainline_box.
        chunks = []

        # ── 1. Meta-packages pinned card ──────────────────────────────────────
        meta_rows_all = [
            self.store_meta.get_item(i)
            for i in range(self.store_meta.get_n_items())
        ]
        meta_visible = meta_rows_all if not query else [
            r for r in meta_rows_all if query in r.name.lower()
        ]
        if meta_visible:
            chunks.append(lambda mv=meta_visible: self._mainline_box.append(
                self._build_meta_card(mv)
            ))

        # ── 2. Versioned kernel cards sorted newest-first ─────────────────────
        sorted_vers = sorted(
            self._mainline_groups.keys(),
            key=cmp_to_key(lambda a, b: -self._version_cmp(a, b))
        )

        for kver in sorted_vers:
            rows = self._mainline_groups[kver]
            if not rows:
                continue
            visible_rows = rows if not query else [
                r for r in rows if query in r.name.lower() or query in kver
            ]
            if not visible_rows:
                continue
            # Capture loop variables in default args
            chunks.append(lambda kv=kver, vr=visible_rows, allr=rows: (
                self._mainline_box.append(self._build_version_card(kv, vr, allr))
            ))

        if not chunks:
            empty = Gtk.Label(label="No mainline kernels found in apt cache.\nTry clicking Refresh.")
            empty.set_margin_top(40)
            self._mainline_box.append(empty)
            return

        # Dispatch chunks one-per-idle-cycle so GTK can process events between each card
        chunk_iter = iter(chunks)

        def dispatch_next():
            if gen != self._rebuild_generation:
                return False  # superseded — abort
            try:
                next(chunk_iter)()
            except StopIteration:
                return False  # done
            return True  # schedule next idle

        GLib.idle_add(dispatch_next)

    def _build_version_card(self, kver: str, visible_rows: list, all_rows: list) -> Gtk.Frame:
        """
        Build a single versioned kernel card (e.g. 6.14.0-37).
        Extracted from _rebuild_mainline_ui so chunked dispatch can call it cleanly.
        """
        frame = Gtk.Frame()
        frame.set_margin_top(8)
        frame.set_margin_start(10)
        frame.set_margin_end(10)
        frame.set_margin_bottom(2)
        frame.add_css_class("card")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.set_child(vbox)

        # ── Header row (clickable) ──
        header_btn = Gtk.Button()
        header_btn.add_css_class("flat")
        header_btn.set_margin_top(0)
        header_btn.set_margin_bottom(0)

        header_inner = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10,
                               margin_start=12, margin_end=12)

        grp_check = Gtk.CheckButton()
        grp_check.add_css_class("selection-mode")
        header_inner.append(grp_check)

        ver_label = Gtk.Label(xalign=0, hexpand=True)
        any_active    = any(r.is_active    for r in all_rows)
        any_installed = any(r.is_installed for r in all_rows)

        if any_active:
            ver_label.set_markup(
                f"<b>Kernel {kver}</b>  "
                f"<span foreground='green'><b>[Active]</b></span>"
                f"  <small>({len(all_rows)} packages)</small>"
            )
        elif any_installed:
            ver_label.set_markup(
                f"<b>Kernel {kver}</b>  "
                f"<span foreground='gray'>[Installed]</span>"
                f"  <small>({len(all_rows)} packages)</small>"
            )
        else:
            ver_label.set_markup(
                f"<b>Kernel {kver}</b>  "
                f"<span foreground='#88cc88'>[Available]</span>"
                f"  <small>({len(all_rows)} packages)</small>"
            )
        ver_label.add_css_class("heading")
        header_inner.append(ver_label)

        gpu_rows = [r for r in all_rows if not r.gpu_relevant]
        if gpu_rows:
            gpu_hint = Gtk.Label(label="⚠ Some GPU pkgs hidden (no matching GPU)")
            gpu_hint.add_css_class("caption")
            gpu_hint.add_css_class("warning")
            header_inner.append(gpu_hint)

        header_btn.set_child(header_inner)
        vbox.append(header_btn)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        vbox.append(sep)

        # ── Package rows grouped by category ──
        cats: dict[str, list] = {}
        for r in visible_rows:
            cats.setdefault(r.category, []).append(r)

        CAT_ORDER = [
            "Image", "Image (unsigned/uc)", "Image (OEM)",
            "Headers", "Modules", "Modules Extra",
            "Modules Extra (GEP)", "Modules NVIDIA", "Other"
        ]
        ordered_cats = sorted(cats.keys(), key=lambda c: CAT_ORDER.index(c) if c in CAT_ORDER else 99)

        pkg_check_map = {}

        for cat in ordered_cats:
            cat_rows = cats[cat]

            cat_label = Gtk.Label(label=cat, xalign=0)
            cat_label.add_css_class("caption")
            cat_label.add_css_class("dim-label")
            cat_label.set_margin_start(16)
            cat_label.set_margin_top(6)
            cat_label.set_margin_bottom(2)
            vbox.append(cat_label)

            for r in cat_rows:
                pkg_box = Gtk.Box(spacing=10,
                                  margin_top=4, margin_bottom=4,
                                  margin_start=24, margin_end=12)

                chk = Gtk.CheckButton()
                chk.set_active(r.is_selected)
                pkg_check_map[r] = chk

                def _on_pkg_check(btn, row=r, kver=kver):
                    row.is_selected = btn.get_active()
                    self._update_group_header_check(kver, pkg_check_map, grp_check)
                    self._update_buttons()

                chk._toggled_handler_id = chk.connect("toggled", _on_pkg_check)
                pkg_box.append(chk)

                name_lbl = Gtk.Label(xalign=0, hexpand=True)
                if not r.gpu_relevant:
                    name_lbl.set_markup(
                        f"<span foreground='gray'><s>{r.name}</s></span>"
                        f"  <small><span foreground='orange'>no matching GPU</span></small>"
                    )
                    chk.set_sensitive(False)
                else:
                    name_lbl.set_markup(r.markup)
                pkg_box.append(name_lbl)

                size_lbl = Gtk.Label(label=r.size, xalign=1)
                size_lbl.add_css_class("dim-label")
                pkg_box.append(size_lbl)

                status_lbl = Gtk.Label(label=r.status, xalign=1)
                status_lbl.add_css_class("caption")
                pkg_box.append(status_lbl)

                vbox.append(pkg_box)

        # ── Wire header button → select/deselect entire group ──
        def _on_header_clicked(btn, kver=kver, rows=all_rows, check_map=pkg_check_map, grp_chk=grp_check):
            eligible = [r for r in rows if r.gpu_relevant and not r.is_active]
            all_sel = all(r.is_selected for r in eligible)
            new_state = not all_sel
            for r in eligible:
                r.is_selected = new_state
            for row, chk in check_map.items():
                if row.gpu_relevant and not row.is_active:
                    handler_id = getattr(chk, "_toggled_handler_id", None)
                    if handler_id is not None:
                        chk.handler_block(handler_id)
                    try:
                        chk.set_active(row.is_selected)
                    finally:
                        if handler_id is not None:
                            chk.handler_unblock(handler_id)
            grp_chk.set_active(new_state)
            self._update_buttons()

        header_btn.connect("clicked", _on_header_clicked)
        self._update_group_header_check(kver, pkg_check_map, grp_check)

        return frame

    def _build_meta_card(self, meta_rows: list) -> Gtk.Frame:
        """
        Render the meta/tracking packages (linux-generic, linux-lowlatency, etc.)
        as a card at the top of the mainline grouped view.
        The header button selects/deselects all meta-packages in one click.
        """
        frame = Gtk.Frame()
        frame.set_margin_top(8)
        frame.set_margin_start(10)
        frame.set_margin_end(10)
        frame.set_margin_bottom(2)
        frame.add_css_class("card")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.set_child(vbox)

        # Header button
        header_btn = Gtk.Button()
        header_btn.add_css_class("flat")

        header_inner = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10,
                               margin_start=12, margin_end=12)

        grp_check = Gtk.CheckButton()
        grp_check.add_css_class("selection-mode")
        header_inner.append(grp_check)

        hdr_label = Gtk.Label(xalign=0, hexpand=True)
        hdr_label.set_markup(
            f"<b>Meta / Tracking packages</b>"
            f"  <small><span foreground='#88aaff'>install once, upgrade automatically</span>"
            f"  ({len(meta_rows)} packages)</small>"
        )
        hdr_label.add_css_class("heading")
        header_inner.append(hdr_label)
        header_btn.set_child(header_inner)
        vbox.append(header_btn)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        vbox.append(sep)

        pkg_check_map = {}

        for r in meta_rows:
            pkg_box = Gtk.Box(spacing=10, margin_top=4, margin_bottom=4,
                              margin_start=24, margin_end=12)

            chk = Gtk.CheckButton()
            chk.set_active(r.is_selected)
            pkg_check_map[r] = chk

            def _on_meta_check(btn, row=r):
                row.is_selected = btn.get_active()
                self._update_meta_header_check(pkg_check_map, grp_check)
                self._update_buttons()

            chk._toggled_handler_id = chk.connect("toggled", _on_meta_check)
            pkg_box.append(chk)

            name_lbl = Gtk.Label(xalign=0, hexpand=True)
            name_lbl.set_markup(r.markup)
            pkg_box.append(name_lbl)

            size_lbl = Gtk.Label(label=r.size, xalign=1)
            size_lbl.add_css_class("dim-label")
            pkg_box.append(size_lbl)

            status_lbl = Gtk.Label(label=r.status, xalign=1)
            status_lbl.add_css_class("caption")
            pkg_box.append(status_lbl)

            vbox.append(pkg_box)

        def _on_meta_header_clicked(btn, check_map=pkg_check_map, grp_chk=grp_check):
            eligible = [r for r in check_map if not r.is_active]
            all_sel = all(r.is_selected for r in eligible)
            new_state = not all_sel
            for r in eligible:
                r.is_selected = new_state
            for row, chk in check_map.items():
                if not row.is_active:
                    handler_id = getattr(chk, "_toggled_handler_id", None)
                    if handler_id is not None:
                        chk.handler_block(handler_id)
                    try:
                        chk.set_active(row.is_selected)
                    finally:
                        if handler_id is not None:
                            chk.handler_unblock(handler_id)
            grp_chk.set_active(new_state)
            self._update_buttons()

        header_btn.connect("clicked", _on_meta_header_clicked)
        self._update_meta_header_check(pkg_check_map, grp_check)
        return frame

    def _update_meta_header_check(self, pkg_check_map, grp_check):
        eligible = [r for r in pkg_check_map if not r.is_active]
        if not eligible:
            grp_check.set_active(False)
            grp_check.set_inconsistent(False)
            return
        n_sel = sum(1 for r in eligible if r.is_selected)
        if n_sel == 0:
            grp_check.set_active(False)
            grp_check.set_inconsistent(False)
        elif n_sel == len(eligible):
            grp_check.set_active(True)
            grp_check.set_inconsistent(False)
        else:
            grp_check.set_active(False)
            grp_check.set_inconsistent(True)

    def _update_group_header_check(self, kver, pkg_check_map, grp_check):
        eligible = [r for r in pkg_check_map if r.gpu_relevant and not r.is_active]
        if not eligible:
            grp_check.set_active(False)
            grp_check.set_inconsistent(False)
            return
        n_sel = sum(1 for r in eligible if r.is_selected)
        if n_sel == 0:
            grp_check.set_active(False)
            grp_check.set_inconsistent(False)
        elif n_sel == len(eligible):
            grp_check.set_active(True)
            grp_check.set_inconsistent(False)
        else:
            grp_check.set_active(False)
            grp_check.set_inconsistent(True)

    # ── Data Collection ───────────────────────────────────────────────────────

    def _collect_kernels(self):
        self._open_cache()
        items = []
        run = platform.uname().release

        # Fetch held packages once via dpkg (faster than per-package apt query)
        held_pkgs: set[str] = set()
        try:
            out = subprocess.check_output(
                ["dpkg", "--get-selections"],
                text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == "hold":
                    held_pkgs.add(parts[0])
        except Exception:
            pass

        # Pre-split running release for faster active-kernel matching
        run_parts = run.split("-")

        for pkg in self.cache:
            name = pkg.name
            is_meta = is_mainline_meta(name)
            if not (is_xanmod_name(name) or is_liquorix_name(name)
                    or is_generic_kernel_name(name) or is_meta):
                continue
            cand = pkg.candidate
            if not cand:
                continue
            version   = cand.version or ""
            installed = pkg.is_installed
            # Active check: version prefix must match running release
            active    = installed and (run.startswith(version) or version in run)
            held      = name in held_pkgs
            status    = "Active" if active else ("Held" if held and installed else ("Installed" if installed else "Available"))
            size      = self._fmt_bytes(getattr(cand, "installed_size", 0) or 0)
            kver      = extract_kernel_version(name) if (is_generic_kernel_name(name) and not is_meta) else ""
            category  = pkg_category(name) if (is_generic_kernel_name(name) and not is_meta) else ""
            relevant  = gpu_relevant(name)
            flavor    = xanmod_flavor(name) if is_xanmod_name(name) else ""

            held_tag = "  <span foreground='orange'><b>[Held]</b></span>" if held else ""
            if active:
                markup = (f"<b>{name}</b> <small>({version})</small>"
                          f"  <span foreground='green'><b>[Active]</b></span>{held_tag}")
            elif installed:
                markup = (f"<span foreground='gray'>{name} <small>({version})</small>"
                          f"  [Installed]</span>{held_tag}")
            else:
                markup = (f"<b>{name}</b> <small>({version})</small>"
                          f"  <span foreground='#88cc88'>[Available]</span>{held_tag}")

            items.append({
                "name": name, "version": version, "installed": installed,
                "active": active, "held": held, "status": status, "size": size,
                "markup": markup, "kver": kver, "category": category,
                "gpu_relevant": relevant, "flavor": flavor, "is_meta": is_meta,
            })

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
                # Log the technical detail but show a friendly message in the dialog
                import traceback
                detail = traceback.format_exc()
                GLib.idle_add(self._append_log, f"\n[ERROR] {detail}\n")
                GLib.idle_add(
                    self._error_dialog,
                    "Could not load kernel list",
                    "An error occurred while reading the package cache.\n\n"
                    "Click 'Show Details' for the full error, then try clicking Refresh.\n\n"
                    f"Summary: {type(e).__name__}: {e}"
                )
        threading.Thread(target=worker, daemon=True).start()

    def _on_kernels_loaded(self, items):
        self.kernels = items
        self._populate_models()

    def _populate_models(self):
        for store in (self.store_xanmod, self.store_liquorix, self.store_meta):
            store.remove_all()
        self._mainline_groups = {}

        for k in self.kernels:
            row = KernelRow(
                is_selected=False, markup=k["markup"], name=k["name"],
                version=k["version"], size=k["size"], status=k["status"],
                is_installed=k["installed"], is_active=k["active"],
                is_held=k.get("held", False),
                kver=k.get("kver", ""), category=k.get("category", ""),
                gpu_relevant=k.get("gpu_relevant", True),
                flavor=k.get("flavor", ""),
            )
            if is_xanmod_name(k["name"]):
                self.store_xanmod.append(row)
            elif is_liquorix_name(k["name"]):
                self.store_liquorix.append(row)
            elif k.get("is_meta"):
                self.store_meta.append(row)
            elif is_generic_kernel_name(k["name"]):
                kv = k.get("kver") or "ungrouped"
                self._mainline_groups.setdefault(kv, []).append(row)

        self._refilter_all()
        # Defer the mainline rebuild to the next idle cycle so the store updates
        # and filter invalidations can flush first — keeps the UI snappy.
        GLib.idle_add(self._rebuild_mainline_ui, self.search_entry.get_text())
        self._update_buttons()

    # ── Subprocess Helper ─────────────────────────────────────────────────────

    def _stream_subprocess(self, cmd, on_done):
        def worker():
            combined = []
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1)
                for line in proc.stdout:
                    combined.append(line)
                    GLib.idle_add(self._append_log, line)
                rc = proc.wait()
            except Exception as e:
                rc = 1
                combined.append(f"\nERROR: {e}\n")
                GLib.idle_add(self._append_log, combined[-1])

            output = "".join(combined)

            # apt exits 100 when there are unmet dependencies or the package
            # is not found, but NOT when nothing needed upgrading (that's 0).
            # Treat rc=100 for apt/apt-get as a real failure — log it clearly.
            is_apt_cmd = len(cmd) > 1 and cmd[-1] != "update" and any(
                "apt" in str(c) for c in cmd
            )
            if rc == 100 and is_apt_cmd:
                GLib.idle_add(self._append_log,
                              "\n⚠  apt exited with code 100 — package not found or "
                              "dependency conflict. Check the log above.\n")

            # Track consecutive pkexec authentication failures so we can show
            # the warning banner after a threshold is reached.
            if "pkexec" in str(cmd[0]):
                if rc != 0:
                    GLib.idle_add(self._on_pkexec_fail)
                else:
                    # Successful pkexec — reset counter and hide banner.
                    GLib.idle_add(self._on_pkexec_success)

            GLib.idle_add(on_done, rc, output)
        threading.Thread(target=worker, daemon=True).start()

    def _on_pkexec_fail(self):
        """Increment the pkexec failure counter and show the warning banner at threshold."""
        self._pkexec_fail_count += 1
        _THRESHOLD = 2
        if self._pkexec_fail_count >= _THRESHOLD and hasattr(self, "_pkexec_warn_bar"):
            self._pkexec_warn_bar.set_visible(True)
        return False

    def _on_pkexec_success(self):
        """Reset pkexec failure counter and hide the warning banner."""
        self._pkexec_fail_count = 0
        if hasattr(self, "_pkexec_warn_bar"):
            self._pkexec_warn_bar.set_visible(False)
        return False

    def _action_check_updates(self, *_):
        self._set_busy(True, "Updating sources…")
        self._stream_subprocess(
            ["pkexec", "apt-get", "update", "-qq"],
            self._on_update_sources_done
        )

    def _on_update_sources_done(self, rc, _output):
        self._set_busy(False, "Sources updated." if rc == 0 else "Update failed — see log.")
        self._reload_kernels_async()

    # ── Install / Remove ──────────────────────────────────────────────────────

    # Allowlist for valid Debian/Ubuntu package name characters.
    # Per policy: lowercase letters, digits, plus, minus, dots.
    _PKG_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9+\-.]*$')

    @classmethod
    def _sanitize_pkg_name(cls, name: str) -> str:
        """
        Validate a package name against the Debian naming policy.
        Raises ValueError for names that do not match, preventing shell-injection
        style attacks if a malformed name ever ends up in the selection.
        """
        if not cls._PKG_NAME_RE.match(name):
            raise ValueError(f"Unsafe or invalid package name rejected: {name!r}")
        return name

    def _get_selected_packages(self, only_installed=None):
        pkgs = []
        bad = []
        for store in (self.store_xanmod, self.store_liquorix, self.store_meta):
            for i in range(store.get_n_items()):
                row = store.get_item(i)
                if row.is_selected and (only_installed is None or row.is_installed == only_installed):
                    try:
                        pkgs.append(self._sanitize_pkg_name(row.name))
                    except ValueError as e:
                        bad.append(str(e))
        # Also collect from mainline groups
        for rows in self._mainline_groups.values():
            for row in rows:
                if row.is_selected and (only_installed is None or row.is_installed == only_installed):
                    try:
                        pkgs.append(self._sanitize_pkg_name(row.name))
                    except ValueError as e:
                        bad.append(str(e))
        if bad:
            GLib.idle_add(self._error_dialog, "Invalid package name(s)",
                          "The following package names were rejected:\n" + "\n".join(bad))
        return list(dict.fromkeys(pkgs))  # deduplicate, preserve order

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
            self._error_dialog("Install failed", "See the Details log for more information.")
            self._end_log_session()
            return
        if self.chk_auto_rm.get_active():
            self._auto_remove_old_kernels()
        self._run_update_grub_silent()
        self._dkms_for_new_kernels_then_reboot()
        self._reload_kernels_async()

    def _install_and_hold_selected(self, *_):
        """
        Install selected packages and immediately apply apt-mark hold on them.
        This is a single atomic user action: install → hold → done.
        Popular for XanMod users who want to pin a specific version and
        prevent automatic upgrades via unattended-upgrades or apt upgrade.
        """
        pkgs = self._get_selected_packages(only_installed=False)
        if not pkgs:
            self._error_dialog("Nothing selected", "Select kernels to install and hold.")
            return

        self._pre_modules = set(os.listdir("/usr/lib/modules")) if os.path.isdir("/usr/lib/modules") else set()
        self.btn_details.set_active(True)
        self._clear_log()
        self._start_log_session("install-hold")
        self._set_busy(True, "Installing kernels…")

        def on_install_done(rc, _):
            if rc != 0:
                self._set_busy(False, "Installation failed.")
                self._error_dialog("Install failed", "See the Details log for more information.")
                self._end_log_session()
                return
            # Installation succeeded — now apply hold
            self._set_busy(True, f"Applying hold to {len(pkgs)} package(s)…")
            self._append_log(f"\n=== Applying apt-mark hold to {len(pkgs)} package(s) ===\n")

            def on_hold_done(rc2, _):
                ok = rc2 == 0
                self._set_busy(False, "Install + Hold complete." if ok else "Hold failed after install.")
                if ok:
                    self._show_toast(f"Installed and held: {', '.join(pkgs)}")
                else:
                    self._error_dialog("Hold failed",
                                       "Packages were installed but hold could not be applied.\n"
                                       "Run 'sudo apt-mark hold " + " ".join(pkgs) + "' manually.")
                if self.chk_auto_rm.get_active():
                    self._auto_remove_old_kernels()
                self._run_update_grub_silent()
                self._dkms_for_new_kernels_then_reboot()
                self._reload_kernels_async()

            self._stream_subprocess(["pkexec", "apt-mark", "hold"] + pkgs, on_hold_done)

        self._stream_subprocess(["pkexec", "apt", "install", "-y"] + pkgs, on_install_done)

    # ── Hold / Unhold ─────────────────────────────────────────────────────────

    def _hold_selected(self, *_):
        pkgs = self._get_selected_packages(only_installed=True)
        if not pkgs:
            self._error_dialog("Nothing to hold", "Select installed kernels to hold.")
            return
        self.btn_details.set_active(True)
        self._clear_log()
        self._start_log_session("hold")
        self._set_busy(True, "Applying hold…")
        self._stream_subprocess(
            ["pkexec", "apt-mark", "hold"] + pkgs,
            self._make_hold_done_cb(pkgs, "hold")
        )

    def _make_hold_done_cb(self, pkgs, action):
        """Return a named callback for hold/unhold completion (avoids tuple-lambda pattern)."""
        def on_done(rc, _output):
            ok = rc == 0
            if action == "hold":
                self._set_busy(False, "Hold applied." if ok else "Hold failed.")
                self._show_toast(f"Held: {', '.join(pkgs)}" if ok else "Hold failed — see log.")
            else:
                self._set_busy(False, "Hold released." if ok else "Unhold failed.")
                self._show_toast(f"Unheld: {', '.join(pkgs)}" if ok else "Unhold failed — see log.")
            self._reload_kernels_async()
            self._end_log_session()
        return on_done

    def _unhold_selected(self, *_):
        pkgs = self._get_selected_packages(only_installed=True)
        if not pkgs:
            self._error_dialog("Nothing to unhold", "Select held kernels to release.")
            return
        self.btn_details.set_active(True)
        self._clear_log()
        self._start_log_session("unhold")
        self._set_busy(True, "Releasing hold…")
        self._stream_subprocess(
            ["pkexec", "apt-mark", "unhold"] + pkgs,
            self._make_hold_done_cb(pkgs, "unhold")
        )

    # ── update-grub safety net ────────────────────────────────────────────────

    def _run_update_grub_silent(self, on_complete=None):
        """
        Run update-grub after install or remove as a safety net.
        Kernel postinst scripts normally handle this, but running it explicitly
        ensures the bootloader is consistent when packages are removed manually
        or when GRUB has been customised.  Output is appended to the current log
        session.  If `on_complete` is provided it is called after grub finishes,
        allowing callers to close the log session once all output is captured.
        """
        self._append_log("\n=== Running update-grub (safety net) ===\n")

        def on_done(rc, _):
            self._append_log(
                "✓ update-grub completed.\n" if rc == 0
                else "⚠ update-grub returned a non-zero exit code — check log.\n"
            )
            if on_complete:
                on_complete()

        self._stream_subprocess(["pkexec", "update-grub"], on_done)

    def _remove_selected(self, *_):
        # Pass 1: flat XanMod / Liquorix stores.
        # Pass 2: mainline groups (separate data structure, not a ListStore).
        # Both passes run before any early-return so the guard fires exactly
        # once, regardless of which tab holds the active kernel's packages.
        all_selected_rows = []
        for store in (self.store_xanmod, self.store_liquorix, self.store_meta):
            for i in range(store.get_n_items()):
                row = store.get_item(i)
                if row.is_selected:
                    all_selected_rows.append(row)
        for rows in self._mainline_groups.values():
            for row in rows:
                if row.is_selected:
                    all_selected_rows.append(row)

        if any(r.is_active for r in all_selected_rows):
            self._error_dialog("Cannot Remove Active Kernel",
                               "The currently running kernel cannot be removed.")
            return

        pkgs = self._get_selected_packages(only_installed=True)
        if not pkgs:
            return

        # ── Ask Purge vs Remove ──
        dlg = Adw.MessageDialog(
            transient_for=self.win,
            heading="Remove or Purge?",
            body=(
                "Remove: uninstalls the packages but keeps configuration files.\n\n"
                "Purge: removes packages and deletes all associated configuration files.\n\n"
                f"Packages to remove: {len(pkgs)}"
            ),
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
        dlg.add_response("purge",  "Purge")
        dlg.set_response_appearance("purge",  Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.SUGGESTED)

        def on_choice(d, response):
            d.close()
            if response not in ("remove", "purge"):
                return
            # "Remove" uses plain `apt remove` without --auto-remove.
            # --auto-remove can cascade to removing shared dependencies
            # (e.g. linux-firmware, linux-base) that other kernels still need,
            # which is surprising and hard to undo. Users who want dependency
            # cleanup can run `apt autoremove` manually afterward.
            apt_flag = "--purge" if response == "purge" else None
            cmd = ["pkexec", "apt", "remove", "-y"]
            if apt_flag:
                cmd.append(apt_flag)
            cmd += pkgs
            self.btn_details.set_active(True)
            self._clear_log()
            self._start_log_session(response)
            self._set_busy(True, f"{'Purging' if response == 'purge' else 'Removing'} kernels…")
            self._stream_subprocess(cmd, self._on_remove_done)

        dlg.connect("response", on_choice)
        dlg.present()

    def _on_remove_done(self, rc, _):
        self._set_busy(False, "Done." if rc == 0 else "Remove failed.")
        if rc == 0:
            # Run update-grub first so its output lands inside this log session,
            # then formally close the session once grub is done.
            self._run_update_grub_silent(on_complete=self._end_log_session)
        else:
            self._error_dialog("Remove failed", "See the Details log for more information.")
            self._end_log_session()  # always close on failure too
        self._reload_kernels_async()

    def _auto_remove_old_kernels(self, *_):
        installed_rows = []
        for store in (self.store_xanmod, self.store_liquorix, self.store_meta):
            for i in range(store.get_n_items()):
                r = store.get_item(i)
                if r.is_installed:
                    installed_rows.append(r)
        for rows in self._mainline_groups.values():
            for r in rows:
                if r.is_installed:
                    installed_rows.append(r)
        if not installed_rows:
            return
        versions = {}
        for row in installed_rows:
            versions.setdefault(row.version, []).append(row)
        ver_list = sorted(versions.keys(), key=cmp_to_key(self._version_cmp), reverse=True)
        active = next((r.version for r in installed_rows if r.is_active), None)
        keep = {active} if active else set()
        keep.update(ver_list[:2])
        to_remove = [r.name for v in versions if v not in keep for r in versions[v]
                     if not r.is_held]  # never auto-remove held packages
        if not to_remove:
            self._show_toast("Nothing to auto-remove.")
            return

        dlg = Adw.MessageDialog(
            transient_for=self.win,
            heading="Auto-Remove Old Kernels",
            body=(
                f"Found {len(to_remove)} package(s) to remove.\n\n"
                "Remove: keep config files.\n"
                "Purge: also delete config files."
            ),
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
        dlg.add_response("purge",  "Purge")
        dlg.set_response_appearance("purge",  Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.SUGGESTED)

        def on_choice(d, response):
            d.close()
            if response not in ("remove", "purge"):
                return
            # Same reasoning as _remove_selected: avoid --auto-remove cascade.
            apt_flag = "--purge" if response == "purge" else None
            cmd = ["pkexec", "apt", "remove", "-y"]
            if apt_flag:
                cmd.append(apt_flag)
            cmd += to_remove
            self.btn_details.set_active(True)
            self._clear_log()
            self._start_log_session(f"autoremove-{response}")
            self._set_busy(True, "Auto-removing old kernels…")
            self._stream_subprocess(cmd, self._on_remove_done)

        dlg.connect("response", on_choice)
        dlg.present()

    # ── Improved DKMS Logic ───────────────────────────────────────────────────

    def _dkms_for_new_kernels_then_reboot(self):
        """
        Robust DKMS rebuild:
          1. Detect newly installed kernel module directories.
          2. For each new kernel version, run `dkms autoinstall`.
          3. After completion, verify each module was built successfully.
          4. Report any failures before prompting for reboot.
        """
        after = set(os.listdir("/usr/lib/modules")) if os.path.isdir("/usr/lib/modules") else set()
        new_kernels = [k for k in (after - self._pre_modules) if k != self.running_release]

        if not new_kernels:
            self._show_reboot_dialog()
            self._end_log_session()
            return

        self._set_busy(True, f"Rebuilding DKMS modules for {len(new_kernels)} kernel(s)…")
        self._append_log(f"\n=== DKMS: Processing {len(new_kernels)} new kernel(s) ===\n")
        for k in new_kernels:
            self._append_log(f"  → {k}\n")

        # Build the DKMS script: autoinstall + verify each module built OK
        dkms_script = r"""
set -euo pipefail

KERNELS=( """ + " ".join(f'"{k}"' for k in new_kernels) + r""" )
FAILED_KERNELS=()
DKMS_REPORT=""

for kver in "${KERNELS[@]}"; do
    echo ""
    echo "━━━ DKMS autoinstall for: $kver ━━━"

    # Check if dkms has anything to do for this kernel
    MODULES=$(dkms status 2>/dev/null | awk -F',' '{print $1}' | sort -u)

    if [ -z "$MODULES" ]; then
        echo "  ✓ No DKMS modules registered — nothing to do for $kver"
        continue
    fi

    # Run autoinstall
    if dkms autoinstall -k "$kver" 2>&1; then
        echo "  ✓ dkms autoinstall succeeded for $kver"
    else
        echo "  ✗ dkms autoinstall reported errors for $kver"
        FAILED_KERNELS+=("$kver")
    fi

    # Verify: check dkms status for each module on this kernel
    echo ""
    echo "  Verifying DKMS module status for $kver:"
    while IFS= read -r line; do
        mod=$(echo "$line" | awk -F',' '{print $1}')
        state=$(echo "$line" | grep -oP '(installed|built|not installed|disabled|error)' | head -1)
        if [[ "$line" == *"$kver"* ]]; then
            if [[ "$state" == "installed" || "$state" == "built" ]]; then
                echo "    ✓  $mod — $state"
            else
                echo "    ✗  $mod — ${state:-unknown} (may need attention)"
                DKMS_REPORT+="WARN: $mod on $kver is '${state:-unknown}'\n"
            fi
        fi
    done < <(dkms status 2>/dev/null)
done

if [ -n "$DKMS_REPORT" ]; then
    echo ""
    echo "━━━ DKMS Warning Summary ━━━"
    echo -e "$DKMS_REPORT"
fi

if [ ${#FAILED_KERNELS[@]} -gt 0 ]; then
    echo ""
    echo "━━━ DKMS ERRORS ━━━"
    echo "The following kernels had DKMS failures:"
    for k in "${FAILED_KERNELS[@]}"; do
        echo "  ✗ $k"
    done
    echo "You may need to manually run: sudo dkms autoinstall -k <version>"
    exit 1
fi

echo ""
echo "✓ All DKMS modules processed successfully."
exit 0
"""

        self._stream_subprocess(
            ["pkexec", "bash", "-c", dkms_script],
            self._on_dkms_done
        )

    def _on_dkms_done(self, rc, output):
        self._set_busy(False)
        if rc != 0:
            # Show warning but still offer reboot — kernel is installed even if DKMS had issues
            dlg = Adw.MessageDialog(
                transient_for=self.win,
                heading="DKMS Warning",
                body="Some DKMS modules may not have built correctly.\n\n"
                     "Check the Details log for specifics.\n\n"
                     "The new kernel was installed — you can still reboot, but some "
                     "driver modules (e.g. NVIDIA) may not be available until DKMS is fixed."
            )
            dlg.add_response("log",    "Stay & Review Log")
            dlg.add_response("reboot", "Reboot Anyway")
            dlg.set_response_appearance("reboot", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.connect("response", lambda d, r: (
                self._show_reboot_dialog() if r == "reboot" else None,
                d.close()
            ))
            dlg.present()
        else:
            self._show_reboot_dialog()
        self._end_log_session()

    def _show_reboot_dialog(self):
        dlg = Adw.MessageDialog(
            transient_for=self.win,
            heading="Reboot Required",
            body="Your new kernel is ready. Reboot now to use it?"
        )
        dlg.add_response("cancel", "Later")
        dlg.add_response("ok",     "Reboot Now")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: r == "ok" and subprocess.Popen(["pkexec", "systemctl", "reboot"]))
        dlg.present()

    # ── Button State ──────────────────────────────────────────────────────────

    def _update_buttons(self):
        can_install = can_remove = can_hold = can_unhold = False
        for store in (self.store_xanmod, self.store_liquorix, self.store_meta):
            for i in range(store.get_n_items()):
                row = store.get_item(i)
                if row.is_selected:
                    if not row.is_installed:                    can_install = True
                    if row.is_installed and not row.is_active:  can_remove  = True
                    if row.is_installed and not row.is_held:    can_hold    = True
                    if row.is_installed and row.is_held:        can_unhold  = True
        for rows in self._mainline_groups.values():
            for row in rows:
                if row.is_selected:
                    if not row.is_installed:                    can_install = True
                    if row.is_installed and not row.is_active:  can_remove  = True
                    if row.is_installed and not row.is_held:    can_hold    = True
                    if row.is_installed and row.is_held:        can_unhold  = True
        self.btn_install.set_sensitive(can_install  and not self.busy)
        self.btn_install_hold.set_sensitive(can_install and not self.busy)
        self.btn_remove.set_sensitive(can_remove   and not self.busy)
        self.btn_hold.set_sensitive(can_hold      and not self.busy)
        self.btn_unhold.set_sensitive(can_unhold    and not self.busy)
        self.btn_refresh.set_sensitive(not self.busy)
        self.btn_autorm.set_sensitive(not self.busy)

    # ── Update Check ──────────────────────────────────────────────────────────

    def _check_for_app_update(self):
        """
        Fetch the latest release tag from GitHub in a background thread.
        If a newer version is available, show a toast notification.
        Called once ~3 seconds after window realise; returns False so the
        GLib.timeout_add_seconds one-shot doesn't repeat.
        """
        def worker():
            try:
                req = urllib.request.Request(
                    RELEASES_API_URL,
                    headers={"User-Agent": f"XKM-Multi-Kernel-Manager/{APP_VERSION}"},
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                tag = data.get("tag_name", "").lstrip("v")
                if tag and self._version_newer(tag, APP_VERSION):
                    GLib.idle_add(self._on_update_available, tag, data.get("html_url", ""))
            except Exception:
                pass  # Network unavailable or API error — silently skip

        threading.Thread(target=worker, daemon=True).start()
        return False  # one-shot

    def _version_newer(self, remote: str, local: str) -> bool:
        """Return True if remote version tuple is strictly greater than local."""
        def to_tuple(v):
            try:
                return tuple(int(x) for x in re.split(r"[.\-]", v) if x.isdigit())
            except Exception:
                return (0,)
        return to_tuple(remote) > to_tuple(local)

    def _on_update_available(self, new_version: str, url: str):
        toast = Adw.Toast(
            title=f"Update available: v{new_version}  —  click to open release page",
            timeout=0,  # stay until dismissed
            button_label="Open",
        )
        toast.connect("button-clicked", lambda _: self._open_url(url))
        self.toast_overlay.add_toast(toast)
        return False

    def _show_toast(self, message: str, timeout: int = 3):
        """Display a brief informational toast."""
        toast = Adw.Toast(title=message, timeout=timeout)
        self.toast_overlay.add_toast(toast)

    @staticmethod
    def _open_url(url: str):
        try:
            subprocess.Popen(["xdg-open", url])
        except Exception:
            pass

    # ── Misc ──────────────────────────────────────────────────────────────────

    def _periodic_check(self):
        self._reload_kernels_async()
        return True

    def on_mode_toggled(self, switch, _):
        dark = switch.get_active()
        cfg = load_config()
        cfg["dark_mode"] = dark
        save_config(cfg)
        self.win.style_manager.set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
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
        """Open (or re-open) the apt cache with proper error recovery."""
        try:
            if self.cache is None:
                self.cache = apt.Cache()
            else:
                # open(None) re-reads from disk — needed after apt operations.
                # Pass progress=None to suppress any progress output.
                self.cache.open(None)
        except Exception as e:
            # Drop the stale cache object so the next call starts fresh.
            self.cache = None
            raise RuntimeError(
                f"Failed to open apt cache: {e}\n\n"
                "Try running 'sudo apt-get update' in a terminal and then "
                "click Refresh."
            ) from e

    def _version_cmp(self, a, b):
        try: return apt_pkg.version_compare(a or "0", b or "0")
        except: return (a > b) - (a < b)

    def _fmt_bytes(self, n):
        if not n or n <= 0:
            return "—"
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                # Format to 1 decimal place, then strip a trailing ".0" cleanly.
                formatted = f"{n:.1f}"
                if formatted.endswith(".0"):
                    formatted = formatted[:-2]
                return f"{formatted} {unit}"
            n /= 1024
        formatted = f"{n:.1f}"
        if formatted.endswith(".0"):
            formatted = formatted[:-2]
        return f"{formatted} TB"

    def _error_dialog(self, title, message):
        dlg = Adw.MessageDialog(transient_for=self.win, heading=title, body=message)
        dlg.add_response("ok", "OK")
        dlg.present()



# ─── Unit Tests ───────────────────────────────────────────────────────────────
# Run with:  python3 XKM --test

def _run_tests():
    """Lightweight self-tests for classification and version logic."""
    import unittest

    class TestClassification(unittest.TestCase):

        # ── is_xanmod_name ────────────────────────────────────────────────────
        def test_xanmod_versioned_image(self):
            self.assertTrue(is_xanmod_name("linux-image-6.18.3-x64v3-xanmod1"))

        def test_xanmod_versioned_headers(self):
            self.assertTrue(is_xanmod_name("linux-headers-6.12.68-x64v2-xanmod1-lts"))

        def test_xanmod_meta(self):
            self.assertTrue(is_xanmod_name("linux-xanmod-x64v3"))
            self.assertTrue(is_xanmod_name("linux-xanmod-edge"))
            self.assertTrue(is_xanmod_name("linux-xanmod"))

        def test_xanmod_meta_image(self):
            self.assertTrue(is_xanmod_name("linux-image-xanmod"))

        def test_not_xanmod(self):
            self.assertFalse(is_xanmod_name("linux-image-6.14.0-37-generic"))
            self.assertFalse(is_xanmod_name("linux-image-liquorix-amd64"))
            self.assertFalse(is_xanmod_name("linux-generic"))

        # ── xanmod_flavor ─────────────────────────────────────────────────────
        def test_flavor_x64v3(self):
            self.assertEqual(xanmod_flavor("linux-image-6.18.3-x64v3-xanmod1"), "v3")

        def test_flavor_x64v2_lts(self):
            self.assertEqual(xanmod_flavor("linux-headers-6.12.68-x64v2-xanmod1-lts"), "v2")

        def test_flavor_edge(self):
            self.assertEqual(xanmod_flavor("linux-xanmod-edge"), "edge")

        def test_flavor_rt(self):
            self.assertEqual(xanmod_flavor("linux-image-6.1.0-rt-xanmod1"), "rt")

        def test_flavor_lts(self):
            self.assertEqual(xanmod_flavor("linux-xanmod-lts"), "lts")

        def test_flavor_generic(self):
            self.assertEqual(xanmod_flavor("linux-image-6.18.3-xanmod1"), "generic")

        def test_flavor_meta_v3(self):
            self.assertEqual(xanmod_flavor("linux-xanmod-x64v3"), "v3")

        # ── extract_kernel_version ────────────────────────────────────────────
        def test_extract_generic(self):
            self.assertEqual(extract_kernel_version("linux-image-6.14.0-37-generic"), "6.14.0-37")

        def test_extract_unsigned(self):
            self.assertEqual(extract_kernel_version("linux-image-unsigned-6.14.0-37-generic"), "6.14.0-37")

        def test_extract_headers(self):
            self.assertEqual(extract_kernel_version("linux-headers-6.14.0-37-generic"), "6.14.0-37")

        def test_extract_modules_extra(self):
            self.assertEqual(extract_kernel_version("linux-modules-extra-6.14.0-37-generic"), "6.14.0-37")

        def test_extract_none(self):
            self.assertIsNone(extract_kernel_version("linux-generic"))

        # ── is_liquorix_name ──────────────────────────────────────────────────
        def test_liquorix_image(self):
            self.assertTrue(is_liquorix_name("linux-image-liquorix-amd64"))

        def test_liquorix_headers(self):
            self.assertTrue(is_liquorix_name("linux-headers-liquorix-amd64"))

        def test_not_liquorix(self):
            self.assertFalse(is_liquorix_name("linux-image-6.14.0-37-generic"))

        # ── is_generic_kernel_name ────────────────────────────────────────────
        def test_generic_image(self):
            self.assertTrue(is_generic_kernel_name("linux-image-6.14.0-37-generic"))

        def test_generic_headers(self):
            self.assertTrue(is_generic_kernel_name("linux-headers-6.14.0-37-generic"))

        def test_generic_excludes_xanmod(self):
            self.assertFalse(is_generic_kernel_name("linux-image-6.18.3-x64v3-xanmod1"))

        def test_generic_excludes_liquorix(self):
            self.assertFalse(is_generic_kernel_name("linux-image-liquorix-amd64"))

    class TestVersionCompare(unittest.TestCase):
        """Test _version_newer used by the update checker."""

        def setUp(self):
            # Instantiate a minimal mock so we can call _version_newer
            class _Stub:
                _version_newer = KernelManagerWindow.__dict__.get("_version_newer", None)

            # _version_newer is a plain method on KernelManager; test it directly
            self._newer = lambda r, l: KernelManager._version_newer(None, r, l)

        def test_newer_patch(self):
            self.assertTrue(self._newer("2.0.1", "2.0.0"))

        def test_newer_minor(self):
            self.assertTrue(self._newer("2.2.0", "2.0.0"))

        def test_same(self):
            self.assertFalse(self._newer("2.0.0", "2.0.0"))

        def test_older(self):
            self.assertFalse(self._newer("2.0.9", "2.0.0"))

        def test_malformed(self):
            # Should not raise
            self.assertFalse(self._newer("", "2.0.0"))

    suite = unittest.TestLoader().loadTestsFromTestCase(TestClassification)
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestVersionCompare))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        _run_tests()
        return
    app = KernelManagerApp()
    app.run(sys.argv)

if __name__ == "__main__":
    main()
