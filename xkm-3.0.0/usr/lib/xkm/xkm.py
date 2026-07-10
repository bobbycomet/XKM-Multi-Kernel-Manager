#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-Kernel Manager — XanMod + Liquorix + Mainline
v2.0.0 — Purge/Remove choice, apt-mark hold support, mainline metapackages,
          update-grub safety net, XanMod flavor selection.
v2.0.1 — Privileged apt/dkms/grub/reboot operations now go through a
          dedicated pkexec helper with its own polkit action. Fixed a
          mainline meta-package classifier that quietly misfiled ~38% of
          real Ubuntu kernel-family packages (including linux-image-generic
          itself, and every linux-oem-6.x / cloud-flavour branch tracker)
          into a bogus "ungrouped" card instead of Meta/Tracking. Fixed
          group-header checkboxes (Mainline version/flavor cards, XanMod,
          Liquorix, Meta) silently swallowing clicks landing on the
          checkbox glyph itself instead of triggering the one-click
          select-everything-needed behavior, and added matching tooltips
          on those checkboxes.
v2.0.2 — Fixed extract_kernel_flavor() mislabeling every meta/tracking
          package (linux-oem-6.14, linux-headers-aws-lts-24.04, ...) as
          "Generic" — the version-relative suffix logic it used had
          nothing to anchor on for packages with no numeric version, so
          it silently fell through to the "no suffix -> Generic" default
          for all of them. Added a Mainline flavor filter (defaulting to
          Generic) so users pick Generic/OEM/AWS/Azure/etc up front
          instead of opening every kernel-version card to find out what's
          inside, and added flavor badges to each version card's header
          for the same reason. Made the Meta/XanMod/Liquorix cards
          collapsed-by-default with lazily-built package rows, matching
          the pattern Mainline version cards already used — this also
          reduces the live widget count on those tabs, which should
          meaningfully cut down the lag when switching tabs or toggling
          light/dark mode.

PyQt6 port — all logic (package classification, apt/pkexec workflows, hold,
DKMS handling, update checks, etc.) is unchanged from the GTK4/Libadwaita
original. Only the UI toolkit has changed.
"""

APP_VERSION = "3.0.0"
RELEASES_API_URL = "https://api.github.com/repos/bobbycomet/XKM-Multi-Kernel-Manager/releases/latest"

import os
import sys

# ── Privileged helper ────────────────────────────────────────────────────────
# All root-requiring operations (apt, apt-mark, update-grub, dkms, reboot,
# repo setup) are routed through a single pkexec target: xkm-helper. This
# lets polkit show a proper app-specific auth prompt (via the
# com.xanmod.kernel.manager.helper action / .policy file) instead of the
# generic "Run apt as root?" prompt you get from calling pkexec on system
# binaries directly, and it means the AppImage never needs to ask pkexec to
# run something out of a squashfs mountpoint. XKM never talks to apt
# directly as root; every privileged call is
# ["pkexec", HELPER_PATH, "<subcommand>", ...args].
HELPER_PATH = os.environ.get("XKM_HELPER_PATH", "/usr/lib/xkm/xkm-helper")
if not os.path.exists(HELPER_PATH):
    _dev_helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xkm-helper")
    if os.path.exists(_dev_helper):
        HELPER_PATH = _dev_helper
import ctypes
import ctypes.util

os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["DEBIAN_FRONTEND"] = "noninteractive"
os.environ["APT_LISTCHANGES_FRONTEND"] = "none"
os.environ["NEEDRESTART_MODE"] = "l"

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

from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QTextCursor, QCursor, QPalette, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QCheckBox, QLineEdit, QComboBox, QTabWidget, QScrollArea,
    QFrame, QTextEdit, QProgressBar, QMessageBox, QToolButton, QSizePolicy,
    QStackedLayout,
)

APP_ID = "com.xanmod.kernel.manager"

# ─── Pango-markup → Qt rich-text helper ──────────────────────────────────────
# Package markup strings were originally authored for GTK/Pango
# (`foreground='green'`). Qt's rich-text engine understands a similar subset
# of HTML but wants `style='color:green'` instead. This lets all the label
# markup constructed elsewhere in this file work unmodified.

_FOREGROUND_RE = re.compile(r"foreground='([^']+)'")

def _to_richtext(markup: str) -> str:
    return _FOREGROUND_RE.sub(r"style='color:\1'", markup)


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

# Human explanations shown in tooltips / info dialog for each XanMod variant.
# (title, description) — psABI levels per the x86-64 microarchitecture levels
# reference; "no kernel benefit" note on v4 reflects that AVX-512 mostly
# matters for userspace workloads, not the kernel itself.
XANMOD_FLAVOR_INFO = {
    "v1": (
        "x86-64-v1 — baseline (circa 2003+)",
        "Runs on essentially any 64-bit x86 CPU: AMD K8/K10/Barcelona, Intel "
        "Pentium 4/Xeon (Nocona) through Core 2, and everything since. The "
        "safe default if you're not sure what your CPU supports."
    ),
    "v2": (
        "x86-64-v2 (circa 2009+)",
        "Needs SSE4.2/POPCNT support: AMD Bobcat/Jaguar/Bulldozer through "
        "Steamroller, Intel Nehalem through Ivy Bridge, and Silvermont/"
        "Goldmont-family Atoms."
    ),
    "v3": (
        "x86-64-v3 (circa 2015+)",
        "Needs AVX2/BMI2/FMA support: AMD Excavator and Zen through Zen 3, "
        "Intel Haswell through 14th-gen (Raptor Lake Refresh). This is the "
        "best match for most modern desktop/laptop CPUs from the last decade."
    ),
    "v4": (
        "x86-64-v4 / AVX-512",
        "Needs AVX-512 support (Zen 4/5, Ice/Cascade/Cooper/Tiger/Sapphire/"
        "Emerald Rapids Xeons, some Skylake-X chips). AVX-512 mostly benefits "
        "specific userspace workloads, not the kernel itself — on most "
        "consumer CPUs this gives no real advantage over v3, and some chips "
        "even downclock when AVX-512 is used. Only pick this if you have a "
        "specific, confirmed reason to."
    ),
    "edge": (
        "Edge — bleeding-edge",
        "Tracks the newest upstream kernel changes before they've had time "
        "to mature. Expect more frequent updates and a higher chance of "
        "regressions or driver/module incompatibilities. Choose this only if "
        "you specifically want the latest kernel features and don't mind "
        "troubleshooting the occasional issue."
    ),
    "lts": (
        "LTS — long-term support",
        "Tracks an upstream long-term-support kernel branch: slower-moving "
        "and more conservative. Generally the safest choice for a stable "
        "daily-driver system."
    ),
    "rt": (
        "RT — PREEMPT_RT real-time",
        "Real-time preemption patches for low, predictable latency. Mainly "
        "useful for audio production, industrial control, or other latency-"
        "sensitive workloads — not needed for typical desktop or gaming use."
    ),
    "generic": (
        "Generic",
        "XanMod's main variant with no specific CPU optimization level or "
        "extra patchset applied."
    ),
}


def detect_cpu_psabi_level():
    """
    Best-effort detection of the highest x86-64 psABI level (v1-v4) the
    running CPU supports, based on /proc/cpuinfo flags — the same flag
    sets glibc/gcc use to define the x86-64-v2/v3/v4 microarchitecture
    levels. Returns None if it can't be determined (e.g. not on Linux, or
    /proc/cpuinfo is unreadable) so callers can just skip the recommendation.
    """
    try:
        with open("/proc/cpuinfo") as f:
            text = f.read()
    except Exception:
        return None

    flags = set()
    for line in text.splitlines():
        if line.startswith(("flags", "Features")):
            flags = set(line.split(":", 1)[1].split())
            break
    if not flags:
        return None

    has_lzcnt = "abm" in flags or "lzcnt" in flags  # AMD reports "abm", Intel reports "lzcnt"
    v2 = {"cx16", "popcnt", "sse4_1", "sse4_2", "ssse3"} <= flags
    v3 = v2 and has_lzcnt and {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "movbe"} <= flags
    v4 = v3 and {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"} <= flags

    if v4:
        return "v4"
    if v3:
        return "v3"
    if v2:
        return "v2"
    return "v1"

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

# Prefixes that mark a package as belonging to Ubuntu's mainline/archive
# kernel family — whether it's a specific versioned build
# (linux-image-6.14.0-37-generic) or a bare tracking meta-package
# (linux-generic, linux-oem-24.04, linux-headers-aws-lts-24.04). This
# intentionally covers every flavour Ubuntu currently ships — generic,
# low-latency, OEM, virtual, and the cloud flavours (aws, azure, gcp,
# oracle, ibm, kvm, gke/gkeop, intel-iotg, nvidia) — plus linux-modules-*
# and linux-cloud-tools-*, so a new HWE point release or a flavour family
# not explicitly named below still gets picked up correctly as long as it
# follows Ubuntu's linux-<component>[-<flavour>] naming convention.
_MAINLINE_KERNEL_PREFIXES = (
    "linux-image-", "linux-headers-", "linux-modules-", "linux-cloud-tools-",
    "linux-generic", "linux-lowlatency", "linux-virtual", "linux-oem-",
    "linux-aws", "linux-azure", "linux-gcp", "linux-oracle", "linux-ibm",
    "linux-kvm", "linux-gke", "linux-gkeop", "linux-intel-iotg", "linux-nvidia",
)

def is_generic_kernel_name(name: str) -> bool:
    """True for a *versioned* mainline package — one with an extractable
    numeric kernel version, e.g. linux-image-6.14.0-37-generic or
    linux-headers-6.11.0-1027-oem. Bare tracking packages like
    linux-generic or linux-oem-24.04 have no version in their name — see
    is_mainline_meta for those."""
    if is_xanmod_name(name) or is_liquorix_name(name):
        return False
    if not name.startswith(_MAINLINE_KERNEL_PREFIXES):
        return False
    return extract_kernel_version(name) is not None


# ─── Mainline Kernel Version Grouping ────────────────────────────────────────

# Pattern to extract the kernel version number from a package name
# e.g. linux-image-6.14.0-37-generic → 6.14.0-37
# e.g. linux-modules-extra-6.14.0-37-generic → 6.14.0-37
_KVER_RE = re.compile(
    r"(?:linux-image-unsigned-|linux-image-uc-|linux-image-oem-|"
    r"linux-image-|linux-headers-|linux-modules-extra-|linux-modules-nvidia-\S+-|"
    r"linux-modules-extra-gep-|linux-modules-|linux-cloud-tools-)(\d+\.\d+\.\d+-\d+)"
)

def extract_kernel_version(pkg_name: str):
    """
    Extract the numeric kernel version from a mainline package name.
    Returns e.g. '6.14.0-37' or None if not found.
    """
    m = _KVER_RE.search(pkg_name)
    return m.group(1) if m else None

# Flavor display order used when sorting sub-groups within a version card.
FLAVOR_ORDER = [
    "Generic", "Generic (64k pages)", "Low Latency",
    "OEM", "Virtual", "AWS", "Azure", "GCP", "Oracle", "IBM", "KVM",
    "GKE", "GKE OP", "Intel IoTG", "NVIDIA", "Other",
]

# Mainline flavor filter options — mirrors FLAVOR_ORDER but with an "Any"
# catch-all at the front. Selecting one of these hides every version card
# (and meta-package row) that isn't that flavor, so instead of opening
# each kernel-version card to see what's inside, the user picks the
# flavor family up front and only ever sees relevant results. "Generic"
# is the default on the theory that most people want the plain kernel,
# not a cloud-provider or OEM-hardware build.
MAINLINE_FLAVOR_FILTERS = ["Any"] + FLAVOR_ORDER
MAINLINE_DEFAULT_FLAVOR_FILTER = "Generic"

def _flavor_sort_key(label: str) -> int:
    for i, base in enumerate(FLAVOR_ORDER):
        if label.startswith(base):
            return i
    return len(FLAVOR_ORDER)

def _mainline_flavor_matches(label: str, filt: str) -> bool:
    """Does this row's flavor label satisfy the currently selected Mainline
    flavor filter? "Any" always matches. Otherwise this deliberately
    matches on base-name prefix, so picking "Generic" also includes
    "Generic (64k pages)" and "Generic (HWE 24.04)" variants — those are
    still fundamentally generic kernels a "just give me generic" user
    wants to see. "Other" is a genuine catch-all: anything that didn't
    match any of the named families above it in FLAVOR_ORDER."""
    if filt == "Any":
        return True
    if filt == "Other":
        return not any(label.startswith(b) for b in FLAVOR_ORDER[:-1])
    return label.startswith(filt)

def extract_kernel_flavor(pkg_name: str) -> str:
    """
    Extract a human-readable "flavor" label for a mainline kernel package —
    the part of the name that distinguishes generic vs low-latency vs OEM vs
    cloud (AWS/Azure/GCP/KVM) variants, and HWE point releases, from one
    another. Packages sharing the same numeric version AND the same flavor
    belong to a single installable kernel (image + headers + modules) and
    should be grouped/installed together; packages that merely share a
    version number but are a different flavor should NOT be lumped in.

    Examples:
      linux-image-6.14.0-37-generic              -> "Generic"
      linux-headers-6.14.0-37-generic             -> "Generic"
      linux-modules-extra-6.14.0-37-generic       -> "Generic"
      linux-image-6.14.0-37-lowlatency            -> "Low Latency"
      linux-image-6.14.0-1015-aws                 -> "AWS"
      linux-image-6.14.0-1009-oem                 -> "OEM"
      linux-image-6.14.0-37-generic-hwe-22.04     -> "Generic (HWE 22.04)"
      linux-modules-nvidia-570-6.14.0-37-generic  -> "Generic"
      linux-image-generic                         -> "Generic"
      linux-oem-6.14                              -> "OEM"
      linux-headers-aws-lts-24.04                 -> "AWS"
    """
    n = pkg_name.lower()
    m = _KVER_RE.search(n)
    if m:
        suffix = n[m.end():].strip("-")
    else:
        # Meta/tracking packages (linux-generic, linux-oem-24.04,
        # linux-headers-aws-lts-24.04, ...) have no numeric version to
        # anchor the "what comes after it" logic above on — the version IS
        # the flavor-ish part here. Fall back to stripping the leading
        # component prefix instead and scanning what's left. Without this
        # branch every meta package fell through to `suffix = ""`, which
        # unconditionally labeled ALL of them "Generic" regardless of their
        # actual flavor — i.e. linux-oem-6.14 and linux-headers-aws-lts-24.04
        # both silently became "Generic".
        suffix = n
        for prefix in (
            "linux-image-unsigned-", "linux-image-uc-", "linux-image-",
            "linux-headers-", "linux-cloud-tools-", "linux-modules-", "linux-",
        ):
            if suffix.startswith(prefix):
                suffix = suffix[len(prefix):]
                break

    if not suffix or "generic" in suffix:
        base = "Generic"
    elif "lowlatency" in suffix:
        base = "Low Latency"
    elif "64k" in suffix:
        base = "Generic (64k pages)"
    elif "oem" in suffix:
        base = "OEM"
    elif "virtual" in suffix:
        base = "Virtual"
    elif "aws" in suffix:
        base = "AWS"
    elif "azure" in suffix:
        base = "Azure"
    elif "gcp" in suffix:
        base = "GCP"
    elif "oracle" in suffix:
        base = "Oracle"
    elif "ibm" in suffix:
        base = "IBM"
    elif "gkeop" in suffix:
        base = "GKE OP"
    elif "gke" in suffix:
        base = "GKE"
    elif "iotg" in suffix:
        base = "Intel IoTG"
    elif "nvidia" in suffix:
        base = "NVIDIA"
    elif "kvm" in suffix:
        base = "KVM"
    else:
        base = suffix.split("-")[0].replace("_", " ").title() or "Other"

    hwe_m = re.search(r"hwe-(\d+\.\d+)", suffix)
    if hwe_m:
        base += f" (HWE {hwe_m.group(1)})"
    return base

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
    if "linux-cloud-tools-" in n:
        return "Cloud Tools"
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

# ─── Kernel Row (plain data object — replaces the GObject.Object model) ──────

class KernelRow:
    """Plain data holder for one package row. No GObject signals are needed
    here because in the Qt port each widget owns a direct reference to its
    row and mutates/reads it directly."""
    def __init__(self, is_selected=False, markup="", name="", version="",
                 size="", status="", is_installed=False, is_active=False,
                 is_held=False, category="", kver="", gpu_relevant=True,
                 flavor=""):
        self.is_selected = is_selected
        self.markup = markup
        self.name = name
        self.version = version
        self.size = size
        self.status = status
        self.is_installed = is_installed
        self.is_active = is_active
        self.is_held = is_held
        self.category = category
        self.kver = kver
        self.gpu_relevant = gpu_relevant
        self.flavor = flavor

# ─── Mainline Meta / Tracking Packages ───────────────────────────────────────
# "Meta" packages track a kernel flavour without pinning a specific build —
# e.g. linux-generic, linux-oem-24.04, linux-headers-aws-lts-24.04,
# linux-image-generic-hwe-24.04. Ubuntu adds new ones fairly often (a new
# HWE point release, a new -6.14/-6.17 branch tracker, a new cloud
# flavour), so this used to be a hardcoded set of literal names — which
# quietly went stale and made several real current packages (including
# linux-image-generic itself, and every linux-oem-6.x / linux-*-hwe branch
# tracker) invisible to the app entirely, since packages that matched
# neither the old hardcoded set NOR the versioned-package check were
# silently dropped in _collect_kernels. Recognizing the *shape* instead —
# a known kernel-family prefix with no extractable numeric version — keeps
# this correct without needing to enumerate every flavour by hand.
def is_mainline_meta(name: str) -> bool:
    """True for metapackages that track a kernel flavour without a pinned
    numeric kernel version."""
    if is_xanmod_name(name) or is_liquorix_name(name):
        return False
    if not name.startswith(_MAINLINE_KERNEL_PREFIXES):
        return False
    return extract_kernel_version(name) is None


# ─── Cross-thread dispatch helper ────────────────────────────────────────────
# Replaces GLib.idle_add() for marshaling calls from background worker threads
# onto the Qt main/GUI thread. Qt's queued signal/slot connections are
# thread-safe for this purpose.

class MainThreadDispatcher(QObject):
    _sig = pyqtSignal(object, tuple, dict)

    def __init__(self):
        super().__init__()
        self._sig.connect(self._exec, Qt.ConnectionType.QueuedConnection)

    def _exec(self, fn, args, kwargs):
        fn(*args, **kwargs)

    def call(self, fn, *args, **kwargs):
        """Schedule fn(*args, **kwargs) to run on the GUI thread."""
        self._sig.emit(fn, args, kwargs)


# ─── Clickable card/header widget (stand-in for Gtk.Button.set_child) ───────

class ClickableFrame(QFrame):
    clicked = pyqtSignal()

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ClickableRow(QWidget):
    """A package row that toggles its checkbox when clicked anywhere on the
    row — mirrors the GTK ListView 'activate' behaviour on row click."""
    def __init__(self, checkbox, *a, **kw):
        super().__init__(*a, **kw)
        self._checkbox = checkbox
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._checkbox is not None and self._checkbox.isEnabled():
            self._checkbox.setChecked(not self._checkbox.isChecked())
        super().mouseReleaseEvent(event)


# ─── Toast overlay (stand-in for Adw.ToastOverlay / Adw.Toast) ──────────────

class ToastWidget(QFrame):
    def __init__(self, message, button_label=None, on_button=None, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setStyleSheet(
            "QFrame#toast { background-color: #2a2a2a; border-radius: 8px; }"
            "QFrame#toast QLabel { color: #f0f0f0; }"
            "QFrame#toast QPushButton { color: #66c0f4; font-weight: bold; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 10, 10)
        lay.setSpacing(10)
        lbl = QLabel(message)
        lay.addWidget(lbl)
        if button_label and on_button:
            btn = QPushButton(button_label)
            btn.setFlat(True)
            btn.clicked.connect(on_button)
            lay.addWidget(btn)
        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setStyleSheet("QToolButton { color: #aaaaaa; border: none; }")
        close_btn.clicked.connect(self.deleteLater)
        lay.addWidget(close_btn)


class ToastOverlay(QWidget):
    """Wraps a content widget and floats dismissible toast notifications over
    its bottom edge, similar to Adw.ToastOverlay.

    NOTE: an earlier version of this class stacked a full-window-sized
    "toast layer" on top of the content via QStackedLayout and relied on
    WA_TransparentForMouseEvents to let clicks pass through the empty parts
    of it. That attribute turns out to suppress mouse delivery to the ENTIRE
    subtree it's set on — including the toast's own buttons — so any toast
    with a button (e.g. the "Open" button on the update-available toast)
    became unclickable, and a persistent toast (timeout=0) blocked the whole
    window until dismissed.

    Instead, the toast layer here is a manually-positioned floating child
    that is always resized to exactly its own content's sizeHint (via
    _reposition). With zero toasts its height collapses to ~0, so it never
    overlaps — let alone blocks — the rest of the UI, and no click-through
    hack is needed at all: toast buttons are ordinary, fully clickable
    widgets.
    """
    def __init__(self, content_widget, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content_widget)

        self._toast_layer = QWidget(self)
        self._toast_layer.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        toast_layout = QVBoxLayout(self._toast_layer)
        toast_layout.setContentsMargins(24, 0, 24, 24)
        toast_layout.setSpacing(8)
        self._toast_vbox = toast_layout
        self._toast_layer.show()
        self._reposition()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()

    def _reposition(self):
        """Shrink-wrap the toast layer to its current content and pin it to
        the bottom, then ensure it's painted above the main content."""
        # With zero toasts, QVBoxLayout.sizeHint() still includes the
        # layout's own margins (top+bottom = 0+24 = 24px here), so the
        # "empty" toast layer doesn't actually collapse to 0 height — it
        # sits as an invisible strip over whatever is at the bottom of the
        # content widget (e.g. the status bar), silently eating clicks on
        # the portion of it that overlaps. Force a true 0 when there are no
        # toasts instead of trusting sizeHint() here.
        if self._toast_vbox.count() == 0:
            h = 0
        else:
            h = self._toast_layer.sizeHint().height()
        self._toast_layer.setGeometry(0, max(0, self.height() - h), self.width(), h)
        self._toast_layer.raise_()

    def add_toast(self, message, timeout=3000, button_label=None, on_button=None):
        toast = ToastWidget(message, button_label, on_button)
        self._toast_vbox.addWidget(toast, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._reposition()
        if timeout and timeout > 0:
            QTimer.singleShot(timeout, lambda: self._remove_toast(toast))
        return toast

    def _remove_toast(self, toast):
        toast.setParent(None)
        toast.deleteLater()
        QTimer.singleShot(0, self._reposition)


# ─── Simple list panel (stand-in for Gtk.ListView + factory, XanMod/Liquorix) ─

class SimpleListPanel(QScrollArea):
    """Displays a flat, checkable list of KernelRow objects. Filtering is done
    by hiding/showing row widgets rather than swapping list models, since the
    lists involved here are small (dozens, not thousands, of packages)."""

    def __init__(self, on_toggle, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._container = QWidget()
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(0, 0, 0, 0)
        self._vbox.setSpacing(0)
        self._vbox.addStretch(1)
        self.setWidget(self._container)
        self.on_toggle = on_toggle
        self.row_widgets = {}  # KernelRow -> QWidget

    def set_rows(self, rows):
        while self._vbox.count() > 1:
            item = self._vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.row_widgets = {}
        for row in rows:
            w = self._make_row_widget(row)
            self._vbox.insertWidget(self._vbox.count() - 1, w)
            self.row_widgets[row] = w

    def _make_row_widget(self, row):
        chk = QCheckBox()
        chk.setChecked(row.is_selected)
        chk.toggled.connect(lambda checked, r=row: self.on_toggle(r, checked))

        outer = ClickableRow(chk)
        h = QHBoxLayout(outer)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(12)
        h.addWidget(chk)

        labels = QVBoxLayout()
        labels.setSpacing(2)
        name_lbl = QLabel()
        name_lbl.setTextFormat(Qt.TextFormat.RichText)
        name_lbl.setText(_to_richtext(row.markup))
        size_lbl = QLabel(row.size)
        size_lbl.setStyleSheet("color: gray; font-size: 9pt;")
        labels.addWidget(name_lbl)
        labels.addWidget(size_lbl)
        h.addLayout(labels, 1)

        status_lbl = QLabel(row.status)
        status_lbl.setStyleSheet("font-size: 9pt;")
        h.addWidget(status_lbl)

        outer._row = row
        return outer

    def apply_filter(self, predicate):
        for row, w in self.row_widgets.items():
            w.setVisible(predicate(row))


# ─── App & Window ─────────────────────────────────────────────────────────────

class KernelManagerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Multi-Kernel Manager")
        cfg = load_config()
        w, h = cfg.get("win_size", [1080, 680])
        self.resize(w, h)

        self.manager = KernelManager(self)
        self.setCentralWidget(self.manager.toast_overlay)
        self.apply_color_scheme()

        # Kick things off shortly after the window is shown, mirroring the
        # GTK version's "realize" handler.
        QTimer.singleShot(0, self._on_window_realized)

    def _on_window_realized(self):
        loading = QLabel("Loading package cache…")
        loading.setStyleSheet("margin-top: 40px;")
        loading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.manager._mainline_box.addWidget(loading)
        self.manager._reload_kernels_async()
        if AUTO_OFFER_ADD_REPO:
            QTimer.singleShot(0, self.manager._maybe_offer_add_repos)
        # Check for updates a couple of seconds after window appears
        QTimer.singleShot(3000, self.manager._check_for_app_update)

    def apply_color_scheme(self):
        dark = load_config().get("dark_mode", True)
        app = QApplication.instance()
        # Base window/text/button colors are set via QPalette rather than a
        # QSS "QWidget { ... }" rule. A universal QWidget selector forces Qt
        # to re-polish EVERY widget in the app on every stylesheet change —
        # with the hundreds of small labels/rows the Mainline tab can build,
        # that's what caused the multi-second lag when toggling themes.
        # QPalette changes propagate to all widgets without that per-widget
        # CSS re-matching cost, so toggling is effectively instant.
        app.setPalette(_build_palette(dark))
        app.setStyleSheet(_DARK_QSS if dark else _LIGHT_QSS)

    def closeEvent(self, event):
        cfg = load_config()
        cfg["win_size"] = [self.width(), self.height()]
        cfg["auto_remove_after_install"] = self.manager.chk_auto_rm.isChecked()
        save_config(cfg)
        self.manager._end_log_session()
        super().closeEvent(event)


def _build_palette(dark: bool) -> QPalette:
    """Base window/text/button colors, applied via QPalette instead of a
    stylesheet so theme switches stay cheap regardless of widget count."""
    pal = QPalette()
    if dark:
        window, base, text = QColor("#1b2838"), QColor("#2a475e"), QColor("#c7d5e0")
        button, highlight, disabled = QColor("#2a475e"), QColor("#355978"), QColor("#6a7f8f")
    else:
        window, base, text = QColor("#f4f6f8"), QColor("#ffffff"), QColor("#1c1c1c")
        button, highlight, disabled = QColor("#e8ebee"), QColor("#3a7bd5"), QColor("#a0a0a0")
    pal.setColor(QPalette.ColorRole.Window, window)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, window)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, button)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.ToolTipBase, base)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Highlight, highlight)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8a9aa8" if dark else "#8a8a8a"))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return pal


# Only widget-CLASS selectors here (QPushButton, QLineEdit, ...) — never a
# bare "QWidget { ... }" rule. Class selectors only match instances of that
# class, so re-polishing on theme switch stays cheap no matter how many
# widgets the Mainline tab has built.
_DARK_QSS = """
QLineEdit, QTextEdit, QComboBox { background-color: #2a475e; color: #c7d5e0;
    border: 1px solid #3a5a75; border-radius: 4px; padding: 3px; }
QPushButton { background-color: #2a475e; color: #c7d5e0; border: 1px solid #3a5a75;
    border-radius: 4px; padding: 5px 10px; }
QPushButton:hover { background-color: #355978; }
QPushButton:disabled { color: #6a7f8f; }
QTabWidget::pane { border-top: 1px solid #3a5a75; }
QTabBar::tab { background: #1b2838; color: #c7d5e0; padding: 6px 14px; }
QTabBar::tab:selected { background: #2a475e; }
QFrame#card { background-color: #22384a; border: 1px solid #3a5a75; border-radius: 6px; }
QToolButton#flavorHeader { background-color: #2a475e; border: 1px solid #3a5a75; border-radius: 4px; }
QToolButton#flavorHeader:hover { background-color: #355978; }
QCheckBox { spacing: 6px; }
"""

_LIGHT_QSS = """
QLineEdit, QTextEdit, QComboBox { background-color: #ffffff; color: #1c1c1c;
    border: 1px solid #b8c0c8; border-radius: 4px; padding: 3px; }
QPushButton { background-color: #e8ebee; color: #1c1c1c; border: 1px solid #b8c0c8;
    border-radius: 4px; padding: 5px 10px; }
QPushButton:hover { background-color: #d8dee4; }
QPushButton:disabled { color: #a0a0a0; }
QTabWidget::pane { border-top: 1px solid #b8c0c8; }
QTabBar::tab { background: #e8ebee; color: #1c1c1c; padding: 6px 14px; }
QTabBar::tab:selected { background: #ffffff; }
QFrame#card { background-color: #ffffff; border: 1px solid #b8c0c8; border-radius: 6px; }
QToolButton#flavorHeader { background-color: #eef1f4; border: 1px solid #b8c0c8; border-radius: 4px; }
QToolButton#flavorHeader:hover { background-color: #dfe4e8; }
QCheckBox { spacing: 6px; }
"""


class KernelManagerApp(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.setApplicationName(APP_ID)
        self.win = None

    def start(self):
        self.win = KernelManagerWindow()
        self.win.show()


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
        self._search_debounce_timer = QTimer()
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.timeout.connect(self._do_refilter)
        self._rebuild_generation = 0  # incremented on each rebuild request to cancel stale ones

        self._dispatch = MainThreadDispatcher()

        # Flat row lists (replace Gio.ListStore)
        self.rows_xanmod = []
        self.rows_liquorix = []
        self.rows_meta = []
        # Mainline: keyed by kver string, value = list of KernelRow
        self._mainline_groups = {}
        # Currently selected XanMod flavor filter ("any" means show all)
        self._xanmod_flavor_filter = "any"
        # Currently selected Mainline flavor filter — defaults to Generic
        # (see MAINLINE_DEFAULT_FLAVOR_FILTER) since that's what most
        # people are looking for; "Any" shows everything, unfiltered.
        self._mainline_flavor_filter = MAINLINE_DEFAULT_FLAVOR_FILTER

        self.main_box = QWidget()
        self.main_layout = QVBoxLayout(self.main_box)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        self.toast_overlay = ToastOverlay(self.main_box)

        self._build_ui()

        hours = load_config().get("auto_check_hours", 6)
        self._periodic_timer = QTimer()
        self._periodic_timer.setInterval(int(max(1, hours)) * 3600 * 1000)
        self._periodic_timer.timeout.connect(self._periodic_check)
        self._periodic_timer.start()

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
            return
        self._repo_offer_shown = True
        missing = []
        if not self._xanmod_repo_present():
            missing.append("XanMod")
        if not self._liquorix_repo_present():
            missing.append("Liquorix")
        if not missing:
            return
        box = QMessageBox(self.win)
        box.setWindowTitle("Missing Kernel Repositories")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<b>{' • '.join(missing)}</b><br><br>Add them now?")
        btn_cancel = box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        btn_ok = box.addButton("Add Repositories", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is btn_ok:
            self._add_missing_repos()

    def _add_missing_repos(self):
        if not self._xanmod_repo_present():
            self._add_xanmod_repo_silent()
        if not self._liquorix_repo_present():
            self._add_liquorix_ppa_official()

    def _add_liquorix_ppa_official(self):
        self.btn_details.setChecked(True)
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
                self._stream_subprocess(["pkexec", HELPER_PATH, "update-sources"], on_update_done)
            else:
                self._set_busy(False, "Failed to add Liquorix PPA.")
                self._error_dialog(
                    "Failed to add Liquorix PPA",
                    "See the Details log for more information.\n\n"
                    "If the log mentions 'add-apt-repository' or "
                    "'software-properties-common', that package may have "
                    "failed to install — check your network connection and "
                    "try again."
                )
                self._end_log_session()

        self._stream_subprocess(["pkexec", HELPER_PATH, "add-repo-liquorix"], on_add_done)

    def _add_xanmod_repo_silent(self):
        self.btn_details.setChecked(True)
        self._clear_log()
        self._start_log_session("xanmod-repo")
        self._set_busy(True, "Adding XanMod repository…")
        cmd = ["pkexec", HELPER_PATH, "add-repo-xanmod"]
        self._stream_subprocess(cmd, self._on_xanmod_repo_added)

    def _on_xanmod_repo_added(self, rc, _output):
        self._set_busy(False, "XanMod repository added." if rc == 0 else "Failed to add XanMod repository.")
        if rc != 0:
            self._error_dialog(
                "Failed to add XanMod repository",
                "See the Details log for more information.\n\n"
                "This usually means the GPG key download from "
                "dl.xanmod.org failed — check your network connection "
                "and try again."
            )
        self._reload_kernels_async()
        self._end_log_session()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        """Top-level UI assembly — delegates to focused sub-builders.
        NOTE: _build_search_bar() must run before _build_stack() because the
        filtered panels reference self.search_entry at construction time."""
        self.main_layout.addWidget(self._build_header())
        self._build_toolbar()
        self._build_search_bar()   # ← search_entry must exist before _build_stack
        self._build_stack()
        self._build_log_panel()
        self._update_buttons()

    def _build_header(self) -> QWidget:
        """Construct the header bar with title, dark-mode switch, and GPU badge."""
        header = QFrame()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 10, 12, 10)

        if GPU_VENDORS:
            badge_text = " + ".join(sorted(v.upper() for v in GPU_VENDORS)) + " Detected"
            badge = QLabel(badge_text)
            badge.setStyleSheet("color: #6fcf6f; font-size: 9pt;")
            layout.addWidget(badge)

        layout.addStretch(1)

        title_box = QVBoxLayout()
        title_lbl = QLabel("Multi-Kernel Manager")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13pt;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle_lbl = QLabel("XanMod • Liquorix • Mainline")
        subtitle_lbl.setStyleSheet("color: gray; font-size: 9pt;")
        subtitle_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title_box.addWidget(title_lbl)
        title_box.addWidget(subtitle_lbl)
        layout.addLayout(title_box)

        layout.addStretch(1)

        self.mode_switch = QCheckBox("Dark Mode")
        self.mode_switch.setChecked(load_config().get("dark_mode", True))
        self.mode_switch.toggled.connect(self.on_mode_toggled)
        layout.addWidget(self.mode_switch)

        return header

    def _build_toolbar(self):
        """Build the action toolbar (install, remove, hold, unhold, auto-remove, spinner)."""
        toolbar_widget = QWidget()
        toolbar = QHBoxLayout(toolbar_widget)
        toolbar.setContentsMargins(12, 8, 12, 0)
        toolbar.setSpacing(8)
        self.main_layout.addWidget(toolbar_widget)

        self.btn_refresh = QPushButton("↺  Refresh")
        self.btn_refresh.clicked.connect(self._action_check_updates)
        toolbar.addWidget(self.btn_refresh)

        self.btn_install = QPushButton("⬇  Install Selected")
        self.btn_install.clicked.connect(self._install_selected)
        self.btn_install.setStyleSheet("QPushButton { font-weight: bold; }")
        toolbar.addWidget(self.btn_install)

        self.btn_install_hold = QPushButton("⬇🔒 Install + Hold")
        self.btn_install_hold.setToolTip(
            "Install selected packages and immediately apply apt-mark hold\n"
            "so they are never auto-upgraded (popular for pinning XanMod versions)."
        )
        self.btn_install_hold.clicked.connect(self._install_and_hold_selected)
        toolbar.addWidget(self.btn_install_hold)

        self.btn_remove = QPushButton("✕  Remove Selected")
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_remove.setStyleSheet("QPushButton { color: #e05050; font-weight: bold; }")
        toolbar.addWidget(self.btn_remove)

        self.btn_hold = QPushButton("🔒 Hold")
        self.btn_hold.setToolTip("Prevent selected packages from being upgraded or removed (apt-mark hold)")
        self.btn_hold.clicked.connect(self._hold_selected)
        toolbar.addWidget(self.btn_hold)

        self.btn_unhold = QPushButton("🔓 Unhold")
        self.btn_unhold.setToolTip("Release hold on selected packages (apt-mark unhold)")
        self.btn_unhold.clicked.connect(self._unhold_selected)
        toolbar.addWidget(self.btn_unhold)

        self.btn_autorm = QPushButton("Auto-Remove Old")
        self.btn_autorm.clicked.connect(self._auto_remove_old_kernels)
        toolbar.addWidget(self.btn_autorm)

        self.chk_auto_rm = QCheckBox("Auto-remove after install")
        self.chk_auto_rm.setChecked(load_config().get("auto_remove_after_install", False))
        toolbar.addWidget(self.chk_auto_rm)

        toolbar.addStretch(1)

        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)  # indeterminate / "spinning"
        self.spinner.setMaximumWidth(80)
        self.spinner.setTextVisible(False)
        self.spinner.setVisible(False)
        toolbar.addWidget(self.spinner)

        # Warning banner — shown when pkexec fails repeatedly (non-root context).
        # This banner always keeps its own fixed warm/dark colors regardless of
        # the app's light/dark theme, so its text color must be set explicitly
        # here too — otherwise in light mode the label falls back to the
        # theme's near-black text on this dark background and becomes
        # unreadable.
        self._pkexec_warn_bar = QFrame()
        self._pkexec_warn_bar.setStyleSheet(
            "QFrame { background-color: #5a4a1a; border: 1px solid #a08030; border-radius: 4px; }"
            "QFrame QLabel { color: #fdf0c8; }"
        )
        warn_layout = QHBoxLayout(self._pkexec_warn_bar)
        warn_layout.addWidget(QLabel(
            "⚠  Privilege escalation (pkexec) failed multiple times.  "
            "Are you running as a non-privileged user without sudo rights?"
        ))
        warn_close = QToolButton()
        warn_close.setText("✕")
        warn_close.setStyleSheet("QToolButton { color: #fdf0c8; border: none; }")
        warn_close.clicked.connect(lambda: self._pkexec_warn_bar.setVisible(False))
        warn_layout.addWidget(warn_close)
        self._pkexec_warn_bar.setVisible(False)
        self.main_layout.addWidget(self._pkexec_warn_bar)

    def _build_search_bar(self):
        """Build the package search / filter row."""
        search_widget = QWidget()
        search_box = QHBoxLayout(search_widget)
        search_box.setContentsMargins(12, 4, 12, 0)
        search_box.setSpacing(6)
        self.main_layout.addWidget(search_widget)
        search_box.addWidget(QLabel("Filter:"))
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("Search all kernels…")
        self.search_entry.textChanged.connect(self._on_search_changed)
        search_box.addWidget(self.search_entry)

    def _build_stack(self):
        """Build the XanMod / Liquorix / Mainline tab stack."""
        self.stack = QTabWidget()

        self._detected_psabi_level = detect_cpu_psabi_level()

        self.stack.addTab(self._build_xanmod_tab(), "XanMod")
        self.stack.addTab(self._build_liquorix_tab(), "Liquorix")
        self.stack.addTab(self._build_mainline_tab(), "Mainline")

        self.main_layout.addWidget(self.stack, 1)

    def _build_xanmod_tab(self) -> QWidget:
        """Build the XanMod tab contents (flavor filter bar + CPU
        recommendation banner + grouped, one-click-installable kernel cards)."""
        xanmod_outer = QWidget()
        v = QVBoxLayout(xanmod_outer)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        flavor_bar = QWidget()
        flavor_layout = QHBoxLayout(flavor_bar)
        flavor_layout.setContentsMargins(12, 8, 12, 4)
        flavor_layout.setSpacing(6)
        flavor_layout.addWidget(QLabel("Flavor:"))

        self.flavor_combo = QComboBox()
        self.flavor_combo.addItems(XANMOD_FLAVORS)
        self.flavor_combo.setCurrentIndex(0)
        self.flavor_combo.setToolTip(
            "Filter XanMod packages by CPU optimization level.\n"
            "v1=baseline  v2=SSE4  v3=AVX2  v4=AVX-512  edge=latest unstable"
        )
        self.flavor_combo.currentIndexChanged.connect(self._on_flavor_changed)
        flavor_layout.addWidget(self.flavor_combo)

        flavor_hint = QLabel("ℹ  v1=baseline · v2=SSE4 · v3=AVX2 · v4=AVX-512 · edge=latest")
        flavor_hint.setStyleSheet("color: gray; font-size: 9pt;")
        flavor_layout.addWidget(flavor_hint, 1)

        info_btn = QToolButton()
        info_btn.setText("ⓘ  x86-64 levels")
        info_btn.setToolTip("Show the full x86-64-v1..v4 CPU reference table")
        info_btn.clicked.connect(self._show_psabi_info_dialog)
        flavor_layout.addWidget(info_btn)

        v.addWidget(flavor_bar)

        # CPU auto-detection banner — makes the v1/v2/v3/v4 choice easy by
        # telling the user which level their own CPU actually supports.
        rec_bar = QWidget()
        rec_layout = QHBoxLayout(rec_bar)
        rec_layout.setContentsMargins(12, 0, 12, 8)
        rec_label = QLabel()
        if self._detected_psabi_level:
            title, _desc = XANMOD_FLAVOR_INFO.get(self._detected_psabi_level, ("", ""))
            rec_label.setText(
                f"★ Your CPU supports up to <b>{title}</b> — kernels marked "
                f"<span style='color:#88cc88'>★ recommended</span> below are the best match."
            )
        else:
            rec_label.setText("Couldn't auto-detect your CPU's supported x86-64 level.")
        rec_label.setTextFormat(Qt.TextFormat.RichText)
        rec_label.setStyleSheet("color: gray; font-size: 9pt;")
        rec_label.setWordWrap(True)
        rec_layout.addWidget(rec_label, 1)
        v.addWidget(rec_bar)

        self._xanmod_scroll = QScrollArea()
        self._xanmod_scroll.setWidgetResizable(True)
        self._xanmod_scroll.setFrameShape(QFrame.Shape.NoFrame)
        xanmod_container = QWidget()
        self._xanmod_box = QVBoxLayout(xanmod_container)
        self._xanmod_box.setContentsMargins(0, 0, 0, 0)
        self._xanmod_box.addStretch(1)
        self._xanmod_scroll.setWidget(xanmod_container)
        v.addWidget(self._xanmod_scroll, 1)
        return xanmod_outer

    def _build_liquorix_tab(self) -> QWidget:
        """Build the Liquorix tab — grouped, one-click-installable kernel cards."""
        liquorix_outer = QWidget()
        v = QVBoxLayout(liquorix_outer)
        v.setContentsMargins(0, 0, 0, 0)

        self._liquorix_scroll = QScrollArea()
        self._liquorix_scroll.setWidgetResizable(True)
        self._liquorix_scroll.setFrameShape(QFrame.Shape.NoFrame)
        liquorix_container = QWidget()
        self._liquorix_box = QVBoxLayout(liquorix_container)
        self._liquorix_box.setContentsMargins(0, 0, 0, 0)
        self._liquorix_box.addStretch(1)
        self._liquorix_scroll.setWidget(liquorix_container)
        v.addWidget(self._liquorix_scroll)
        return liquorix_outer

    def _show_psabi_info_dialog(self):
        """Show the full x86-64-v1..v4 CPU microarchitecture reference table."""
        box = QMessageBox(self.win)
        box.setWindowTitle("x86-64 psABI Levels")
        box.setTextFormat(Qt.TextFormat.RichText)
        rows = []
        for flavor in ("v1", "v2", "v3", "v4"):
            title, desc = XANMOD_FLAVOR_INFO[flavor]
            rows.append(f"<p><b>{title}</b><br>{desc}</p>")
        detected = self._detected_psabi_level
        if detected:
            det_title, _ = XANMOD_FLAVOR_INFO.get(detected, ("", ""))
            rows.insert(0, f"<p style='color:#88cc88'><b>Your CPU supports up to: {det_title}</b></p><hr>")
        box.setText("".join(rows))
        box.addButton("Close", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    def _build_mainline_tab(self) -> QWidget:
        """Build the Mainline tab — a flavor filter bar (so users pick
        Generic/OEM/AWS/etc up front instead of opening every kernel-
        version card to find out what's inside), then grouped versioned
        packages + meta-packages, all rendered by _rebuild_mainline_ui."""
        mainline_outer = QWidget()
        v = QVBoxLayout(mainline_outer)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        flavor_bar = QWidget()
        flavor_layout = QHBoxLayout(flavor_bar)
        flavor_layout.setContentsMargins(12, 8, 12, 4)
        flavor_layout.setSpacing(6)
        flavor_layout.addWidget(QLabel("Flavor:"))

        self.mainline_flavor_combo = QComboBox()
        self.mainline_flavor_combo.addItems(MAINLINE_FLAVOR_FILTERS)
        self.mainline_flavor_combo.setCurrentIndex(
            MAINLINE_FLAVOR_FILTERS.index(self._mainline_flavor_filter)
        )
        self.mainline_flavor_combo.setToolTip(
            "Show only this kernel flavor — Generic is the plain desktop/server "
            "kernel most people want; OEM is for newer hardware on an older Ubuntu "
            "release; AWS/Azure/GCP/Oracle/IBM/KVM/GKE are cloud-provider-tuned "
            "builds; Low Latency trades some throughput for responsiveness."
        )
        self.mainline_flavor_combo.currentIndexChanged.connect(self._on_mainline_flavor_changed)
        flavor_layout.addWidget(self.mainline_flavor_combo)

        flavor_hint = QLabel(
            "ℹ  Generic is the standard kernel — pick a cloud/OEM flavor only if "
            "you're specifically running on that platform."
        )
        flavor_hint.setStyleSheet("color: gray; font-size: 9pt;")
        flavor_layout.addWidget(flavor_hint, 1)

        v.addWidget(flavor_bar)

        self._mainline_scroll = QScrollArea()
        self._mainline_scroll.setWidgetResizable(True)
        self._mainline_scroll.setFrameShape(QFrame.Shape.NoFrame)
        mainline_container = QWidget()
        self._mainline_box = QVBoxLayout(mainline_container)
        self._mainline_box.setContentsMargins(0, 0, 0, 0)
        self._mainline_box.addStretch(1)
        self._mainline_scroll.setWidget(mainline_container)
        v.addWidget(self._mainline_scroll, 1)

        return mainline_outer

    def _on_mainline_flavor_changed(self, index):
        self._mainline_flavor_filter = MAINLINE_FLAVOR_FILTERS[index]
        self._rebuild_mainline_ui(query=self.search_entry.text())

    def _build_log_panel(self):
        """Build the status bar, Details toggle, and log text view."""
        controls_widget = QWidget()
        controls = QHBoxLayout(controls_widget)
        controls.setContentsMargins(12, 4, 12, 0)
        controls.setSpacing(8)
        self.btn_details = QPushButton("Show Details")
        self.btn_details.setCheckable(True)
        self.btn_details.toggled.connect(lambda checked: self._log_container.setVisible(checked))
        self.status_label = QLabel()
        controls.addWidget(self.btn_details)
        controls.addWidget(self.status_label, 1)
        self.main_layout.addWidget(controls_widget)

        self._log_container = QWidget()
        log_box = QVBoxLayout(self._log_container)
        log_box.setContentsMargins(12, 4, 12, 12)
        self.textedit = QTextEdit()
        self.textedit.setReadOnly(True)
        self.textedit.setFontFamily("monospace")
        self.textedit.setMinimumHeight(180)
        log_box.addWidget(self.textedit)
        self._log_container.setVisible(False)
        self.main_layout.addWidget(self._log_container)

    # ── Filtering (XanMod / Liquorix) ─────────────────────────────────────────

    def _on_flavor_changed(self, index):
        self._xanmod_flavor_filter = XANMOD_FLAVORS[index]
        self._rebuild_xanmod_ui(query=self.search_entry.text())

    def _on_search_changed(self, _text):
        """Debounce search input — only refilter 250 ms after the user stops typing."""
        self._search_debounce_timer.start(250)

    def _do_refilter(self):
        self._refilter_all()

    def _refilter_all(self):
        q = self.search_entry.text()
        self._rebuild_xanmod_ui(query=q)
        self._rebuild_liquorix_ui(query=q)
        # Mainline tab (including meta card) is fully rebuilt on each filter change
        self._rebuild_mainline_ui(query=q)

    # ── Mainline Grouped UI ───────────────────────────────────────────────────

    def _clear_layout(self, layout):
        """Remove all widgets from a layout except a trailing stretch item."""
        while layout.count() > 1:
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _rebuild_mainline_ui(self, query: str = ""):
        """
        Build a grouped Mainline view — the single source of truth for the Mainline tab.
        Uses chunked QTimer scheduling so the Qt event loop stays responsive while
        building potentially hundreds of widgets. Each call increments a generation
        counter; stale generators (from superseded searches) abort early.
        """
        self._rebuild_generation += 1
        gen = self._rebuild_generation

        # Clear all existing children immediately
        self._clear_layout(self._mainline_box)

        query = (query or "").strip().lower()
        flavor_filt = self._mainline_flavor_filter

        # Build the list of "chunks" to render: each chunk is a callable that
        # appends one card (meta or versioned) to _mainline_box.
        chunks = []

        # ── 1. Meta-packages pinned card ──────────────────────────────────────
        # Flavor-filtered too (a "Generic" filter shouldn't leave every OEM/
        # AWS/Azure/... tracking meta-package cluttering the top of the tab).
        meta_rows_all = [
            r for r in self.rows_meta
            if _mainline_flavor_matches(extract_kernel_flavor(r.name), flavor_filt)
        ]
        meta_visible = meta_rows_all if not query else [
            r for r in meta_rows_all if query in r.name.lower()
        ]
        if meta_visible:
            chunks.append(lambda mv=meta_visible: self._mainline_box.insertWidget(
                self._mainline_box.count() - 1, self._build_meta_card(mv)
            ))

        # ── 2. Versioned kernel cards sorted newest-first ─────────────────────
        sorted_vers = sorted(
            self._mainline_groups.keys(),
            key=cmp_to_key(lambda a, b: -self._version_cmp(a, b))
        )

        for kver in sorted_vers:
            rows_all_flavors = self._mainline_groups[kver]
            if not rows_all_flavors:
                continue
            # Flavor filter first — a version with no Generic-flavored
            # packages at all (e.g. a kernel Ubuntu only ever shipped as
            # -aws) simply doesn't show up while "Generic" is selected,
            # instead of showing up with an empty/irrelevant card.
            rows = [
                r for r in rows_all_flavors
                if _mainline_flavor_matches(extract_kernel_flavor(r.name), flavor_filt)
            ]
            if not rows:
                continue
            visible_rows = rows if not query else [
                r for r in rows if query in r.name.lower() or query in kver
            ]
            if not visible_rows:
                continue
            # Capture loop variables in default args
            chunks.append(lambda kv=kver, vr=visible_rows, allr=rows: (
                self._mainline_box.insertWidget(
                    self._mainline_box.count() - 1, self._build_version_card(kv, vr, allr)
                )
            ))

        if not chunks:
            if flavor_filt != "Any":
                empty = QLabel(
                    f"No {flavor_filt} mainline kernels found.\n"
                    "Try a different flavor filter above, or click Refresh."
                )
            else:
                empty = QLabel("No mainline kernels found in apt cache.\nTry clicking Refresh.")
            empty.setStyleSheet("margin-top: 40px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self._mainline_box.insertWidget(self._mainline_box.count() - 1, empty)
            return

        # Dispatch chunks one-per-cycle so Qt can process events between each card
        chunk_iter = iter(chunks)

        def dispatch_next():
            if gen != self._rebuild_generation:
                return  # superseded — abort
            try:
                next(chunk_iter)()
            except StopIteration:
                return  # done
            QTimer.singleShot(0, dispatch_next)

        QTimer.singleShot(0, dispatch_next)

    # ── Shared group-selection helpers (version cards, flavor sub-groups,
    #    and the meta-package card all use these) ──────────────────────────

    def _split_group_targets(self, rows):
        """Split a group of rows into (need_install, need_removal) — i.e.
        packages that aren't installed yet vs. installed-but-inactive ones."""
        need_install = [r for r in rows if r.gpu_relevant and not r.is_active and not r.is_installed]
        need_removal = [r for r in rows if r.gpu_relevant and not r.is_active and r.is_installed]
        return need_install, need_removal

    def _group_click_targets(self, rows):
        """What a single click on a group header should select: the
        not-yet-installed packages if there are any (so one click installs
        exactly what's missing and never re-touches anything already
        installed); otherwise the installed/inactive ones, so the same
        click is still useful for a one-shot removal once everything in
        the group is already installed."""
        need_install, need_removal = self._split_group_targets(rows)
        return need_install if need_install else need_removal

    def _update_group_tristate(self, rows, check_map, grp_check):
        target = self._group_click_targets(rows)
        if not target:
            grp_check.setCheckState(Qt.CheckState.Unchecked)
            return
        n_sel = sum(1 for r in target if r.is_selected)
        if n_sel == 0:
            grp_check.setCheckState(Qt.CheckState.Unchecked)
        elif n_sel == len(target):
            grp_check.setCheckState(Qt.CheckState.Checked)
        else:
            grp_check.setCheckState(Qt.CheckState.PartiallyChecked)

    def _make_group_header_click(self, rows, check_map, grp_check):
        def _on_click():
            target = self._group_click_targets(rows)
            if not target:
                return
            new_state = not all(r.is_selected for r in target)
            for r in target:
                r.is_selected = new_state
                chk = check_map.get(r)
                if chk is not None:
                    chk.blockSignals(True)
                    try:
                        chk.setChecked(new_state)
                    finally:
                        chk.blockSignals(False)
            self._update_group_tristate(rows, check_map, grp_check)
            self._update_buttons()
        return _on_click

    def _group_tooltip(self, label, rows):
        need_install, need_removal = self._split_group_targets(rows)
        cats = sorted({pkg_category(r.name) for r in rows})
        cat_text = " + ".join(cats) if cats else "packages"
        if need_install:
            return (
                f"{label}\nClick to select {len(need_install)} package(s) not yet "
                f"installed ({cat_text}).\nAlready-installed packages are left alone."
            )
        if need_removal:
            return (
                f"{label}\nEverything here is already installed.\n"
                f"Click to select all {len(need_removal)} package(s) for removal."
            )
        return f"{label}\nNothing to install or remove — this is up to date."

    def _build_version_card(self, kver: str, visible_rows: list, all_rows: list) -> QFrame:
        """
        Build a single versioned kernel card (e.g. 6.14.0-37).

        Packages sharing this numeric version can still be entirely
        different installable kernels — generic vs low-latency vs OEM vs
        AWS/Azure/GCP vs an HWE point release. So instead of dumping every
        Image/Headers/Modules row for the whole version into one flat list,
        this groups them by *flavor* (see extract_kernel_flavor) into its
        own collapsible section. Clicking a flavor's header selects/installs
        exactly the packages for THAT flavor that aren't already installed
        — never everything under the version number.

        IMPORTANT — this is also the fix for the multi-second lag when
        toggling light/dark mode: with a real mainline archive (which keeps
        years of historical builds), eagerly building every flavor's package
        rows for every version — even hidden ones — can add up to tens of
        thousands of live widgets. Hidden widgets still get touched by Qt's
        style repolish on every theme switch, so the cost scaled with total
        widget count regardless of what was actually visible. Both the
        flavor sub-groups AND their package rows below are now built lazily,
        the first time each is expanded, and cached after that — so a
        collapsed Mainline tab stays down to a handful of widgets per
        version no matter how many historical kernels are listed.
        """
        frame = QFrame()
        frame.setObjectName("card")
        frame.setContentsMargins(0, 8, 0, 2)

        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # ── Header row: chevron (expand/collapse) + clickable select area ──
        header_row = QWidget()
        header_row_lay = QHBoxLayout(header_row)
        header_row_lay.setContentsMargins(0, 0, 0, 0)
        header_row_lay.setSpacing(0)

        chevron_btn = QToolButton()
        chevron_btn.setText("▸")
        chevron_btn.setToolTip("Expand to show kernel flavors (Generic, Low Latency, OEM, …)")
        chevron_btn.setAutoRaise(True)
        chevron_btn.setStyleSheet("QToolButton { border: none; font-size: 11pt; padding: 0 8px; }")
        header_row_lay.addWidget(chevron_btn)

        header_btn = ClickableFrame()
        header_inner = QHBoxLayout(header_btn)
        header_inner.setContentsMargins(0, 10, 12, 10)
        header_inner.setSpacing(12)

        grp_check = QCheckBox()
        grp_check.setEnabled(False)  # visual indicator only — header click drives selection
        # Disabled widgets never receive mouse events in Qt, and critically
        # those events do NOT bubble up to the parent ClickableFrame either —
        # they are simply dropped. Without this, clicking directly on the
        # checkbox glyph (the most natural place to click) silently does
        # nothing, even though clicking the label right next to it works.
        # Making it transparent for mouse events lets clicks (and tooltip
        # hover) fall through to header_btn underneath.
        grp_check.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        grp_check.setTristate(True)
        header_inner.addWidget(grp_check)

        ver_label = QLabel()
        ver_label.setTextFormat(Qt.TextFormat.RichText)
        any_active    = any(r.is_active    for r in all_rows)
        any_installed = any(r.is_installed for r in all_rows)

        # Flavor badges — so "which of these is OEM vs Azure vs Generic?"
        # is answered right here in the collapsed header, instead of
        # requiring the user to expand the card and search through it.
        flavor_labels = sorted(
            {extract_kernel_flavor(r.name) for r in all_rows},
            key=_flavor_sort_key
        )
        # Strip the "(HWE 22.04)" / "(64k pages)" parenthetical for the
        # badge line — it's still visible in the flavor sub-section itself,
        # but at a glance "Generic, Generic, OEM" from two HWE variants
        # reads worse than just "Generic, OEM".
        badge_bases = list(dict.fromkeys(re.sub(r"\s*\(.*?\)", "", f) for f in flavor_labels))
        badges_html = " · ".join(badge_bases)

        if any_active:
            ver_label.setText(_to_richtext(
                f"<b>Kernel {kver}</b>  "
                f"<span style='color:green'><b>[Active]</b></span>"
                f"  <small>({len(all_rows)} packages · {badges_html})</small>"
            ))
        elif any_installed:
            ver_label.setText(_to_richtext(
                f"<b>Kernel {kver}</b>  "
                f"<span style='color:gray'>[Installed]</span>"
                f"  <small>({len(all_rows)} packages · {badges_html})</small>"
            ))
        else:
            ver_label.setText(_to_richtext(
                f"<b>Kernel {kver}</b>  "
                f"<span style='color:#88cc88'>[Available]</span>"
                f"  <small>({len(all_rows)} packages · {badges_html})</small>"
            ))
        header_inner.addWidget(ver_label, 1)

        gpu_rows = [r for r in all_rows if not r.gpu_relevant]
        if gpu_rows:
            gpu_hint = QLabel("⚠ Some GPU pkgs hidden (no matching GPU)")
            gpu_hint.setStyleSheet("color: orange; font-size: 9pt;")
            header_inner.addWidget(gpu_hint)

        header_row_lay.addWidget(header_btn, 1)
        vbox.addWidget(header_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        vbox.addWidget(sep)

        # ── Body: one collapsible section per kernel flavor — built lazily ──
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        vbox.addWidget(body)
        body.setVisible(False)  # collapsed by default — expand via chevron

        pkg_check_map = {}  # ALL rows in this card, for the version-level header
        state = {"built": False}

        def _build_flavor_groups():
            if state["built"]:
                return
            state["built"] = True

            flavors = {}
            for r in visible_rows:
                flavors.setdefault(extract_kernel_flavor(r.name), []).append(r)
            ordered_flavors = sorted(flavors.keys(), key=_flavor_sort_key)

            for flavor in ordered_flavors:
                frows = flavors[flavor]
                body_lay.addWidget(self._build_flavor_section(kver, flavor, frows, all_rows, pkg_check_map, grp_check))

        def _toggle_body(checked=False):
            _build_flavor_groups()
            expand = not body.isVisible()
            body.setVisible(expand)
            chevron_btn.setText("▾" if expand else "▸")
            chevron_btn.setToolTip(
                "Collapse" if expand else
                "Expand to show kernel flavors (Generic, Low Latency, OEM, …)"
            )
        chevron_btn.clicked.connect(_toggle_body)

        # ── Version-level header click selects across ALL flavors at once ──
        # (Works correctly even before the body is expanded/built: selection
        # state lives on the KernelRow objects themselves, not the widgets —
        # any not-yet-built checkboxes simply pick up r.is_selected once
        # they're eventually created.)
        header_btn.clicked.connect(
            self._make_group_header_click(all_rows, pkg_check_map, grp_check)
        )
        _kver_tip = self._group_tooltip(f"Kernel {kver} — every flavor below", all_rows)
        header_btn.setToolTip(_kver_tip)
        grp_check.setToolTip(_kver_tip)  # belt-and-braces alongside WA_TransparentForMouseEvents
        self._update_group_tristate(all_rows, pkg_check_map, grp_check)

        return frame

    def _build_flavor_section(self, kver, flavor, frows, all_rows, pkg_check_map, grp_check) -> QWidget:
        """One collapsible flavor sub-group (Generic / Low Latency / OEM / …)
        within a version card. Its package rows are themselves built lazily,
        the first time this flavor is expanded."""
        flavor_any_active = any(r.is_active for r in frows)

        flavor_section = QWidget()
        flavor_lay = QVBoxLayout(flavor_section)
        flavor_lay.setContentsMargins(0, 0, 0, 0)
        flavor_lay.setSpacing(0)

        f_header_row = QWidget()
        f_header_lay = QHBoxLayout(f_header_row)
        f_header_lay.setContentsMargins(24, 6, 12, 6)
        f_header_lay.setSpacing(8)

        f_chevron = QToolButton()
        f_chevron.setText("▸")
        f_chevron.setAutoRaise(True)
        f_chevron.setToolTip("Show the individual packages in this flavor")
        f_chevron.setStyleSheet("QToolButton { border: none; }")
        f_header_lay.addWidget(f_chevron)

        f_grp_check = QCheckBox()
        f_grp_check.setEnabled(False)
        # See the matching comment in _build_version_card — disabled widgets
        # swallow mouse events instead of passing them to the parent frame.
        f_grp_check.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        f_grp_check.setTristate(True)
        f_header_lay.addWidget(f_grp_check)

        f_header_btn = ClickableFrame()
        f_inner = QHBoxLayout(f_header_btn)
        f_inner.setContentsMargins(0, 0, 0, 0)
        f_inner.setSpacing(8)

        status_tag = ""
        if flavor_any_active:
            status_tag = "  <span style='color:green'><b>[Active]</b></span>"
        elif all(r.is_installed for r in frows):
            status_tag = "  <span style='color:gray'>[Installed]</span>"
        f_name_lbl = QLabel()
        f_name_lbl.setTextFormat(Qt.TextFormat.RichText)
        f_name_lbl.setText(_to_richtext(
            f"<b>{flavor}</b>{status_tag}  <small>({len(frows)} packages)</small>"
        ))
        f_inner.addWidget(f_name_lbl, 1)
        f_header_lay.addWidget(f_header_btn, 1)

        flavor_lay.addWidget(f_header_row)

        # Package rows — built lazily, the first time this flavor is expanded.
        f_rows_container = QWidget()
        f_rows_lay = QVBoxLayout(f_rows_container)
        f_rows_lay.setContentsMargins(0, 0, 0, 0)
        f_rows_lay.setSpacing(0)
        f_rows_container.setVisible(False)
        flavor_lay.addWidget(f_rows_container)

        flavor_check_map = {}
        state = {"built": False}

        CAT_ORDER = [
            "Image", "Image (unsigned/uc)", "Image (OEM)",
            "Headers", "Modules", "Modules Extra",
            "Modules Extra (GEP)", "Modules NVIDIA", "Cloud Tools", "Other"
        ]

        def _build_pkg_rows():
            if state["built"]:
                return
            state["built"] = True
            cats = {}
            for r in frows:
                cats.setdefault(r.category, []).append(r)
            ordered_cats = sorted(cats.keys(), key=lambda c: CAT_ORDER.index(c) if c in CAT_ORDER else 99)

            for cat in ordered_cats:
                cat_label = QLabel(cat)
                cat_label.setStyleSheet("color: gray; font-size: 8pt; margin-left: 16px; margin-top: 6px;")
                f_rows_lay.addWidget(cat_label)

                for r in cats[cat]:
                    pkg_widget = QWidget()
                    pkg_box = QHBoxLayout(pkg_widget)
                    pkg_box.setContentsMargins(24, 4, 12, 4)
                    pkg_box.setSpacing(10)

                    chk = QCheckBox()
                    chk.setChecked(r.is_selected)
                    flavor_check_map[r] = chk
                    pkg_check_map[r] = chk

                    tip = (
                        f"{r.name}\n{r.version or ''}\n"
                        f"{'Installed' if r.is_installed else 'Not installed'}"
                        + (" (running)" if r.is_active else "")
                        + ("\nHeld — won't auto-upgrade" if r.is_held else "")
                    )

                    def _on_pkg_check(checked, row=r, fcm=flavor_check_map, fgc=f_grp_check,
                                       acm=pkg_check_map, agc=grp_check, arows=all_rows, frows_=frows):
                        row.is_selected = checked
                        self._update_group_tristate(frows_, fcm, fgc)
                        self._update_group_tristate(arows, acm, agc)
                        self._update_buttons()

                    chk.toggled.connect(_on_pkg_check)
                    chk.setToolTip(tip)
                    pkg_box.addWidget(chk)

                    name_lbl = QLabel()
                    name_lbl.setTextFormat(Qt.TextFormat.RichText)
                    name_lbl.setToolTip(tip)
                    if not r.gpu_relevant:
                        name_lbl.setText(_to_richtext(
                            f"<span style='color:gray'><s>{r.name}</s></span>"
                            f"  <small><span style='color:orange'>no matching GPU</span></small>"
                        ))
                        chk.setEnabled(False)
                    else:
                        name_lbl.setText(_to_richtext(r.markup))
                    pkg_box.addWidget(name_lbl, 1)

                    size_lbl = QLabel(r.size)
                    size_lbl.setStyleSheet("color: gray; font-size: 9pt;")
                    pkg_box.addWidget(size_lbl)

                    status_lbl = QLabel(r.status)
                    status_lbl.setStyleSheet("font-size: 9pt;")
                    pkg_box.addWidget(status_lbl)

                    f_rows_lay.addWidget(pkg_widget)

        def _toggle_flavor(checked=False, *, container=f_rows_container, btn=f_chevron):
            _build_pkg_rows()
            expand = not container.isVisible()
            container.setVisible(expand)
            btn.setText("▾" if expand else "▸")
            btn.setToolTip("Hide packages" if expand else "Show the individual packages in this flavor")
        f_chevron.clicked.connect(_toggle_flavor)

        f_header_btn.clicked.connect(
            self._make_group_header_click(frows, flavor_check_map, f_grp_check)
        )
        _flavor_tip = self._group_tooltip(f"{flavor} — kernel {kver}", frows)
        f_header_btn.setToolTip(_flavor_tip)
        f_grp_check.setToolTip(_flavor_tip)  # belt-and-braces alongside WA_TransparentForMouseEvents
        self._update_group_tristate(frows, flavor_check_map, f_grp_check)

        return flavor_section

    def _build_simple_group_card(self, title_html: str, rows: list, tooltip: str) -> QFrame:
        """
        A card with one clickable header (selects what's needed with a
        single click) and a flat list of package rows below. Used for the
        Mainline meta-package card, and for XanMod/Liquorix groups — those
        don't need a second level of flavor nesting the way Mainline version
        cards do, because each group here already IS one specific,
        installable combination (e.g. "XanMod 6.18.3 [v3]" or "Liquorix
        6.9-1").

        Collapsed by default, with the package rows built lazily on first
        expand — same as _build_version_card, and for the same reason: with
        a large local Meta/XanMod/Liquorix set, eagerly building every
        row for every group (most of which the user will never open) is
        what made switching tabs and toggling light/dark mode noticeably
        laggy. Collapsed, a tab full of these cards costs one header widget
        each instead of a header-plus-N-rows.
        """
        frame = QFrame()
        frame.setObjectName("card")
        frame.setContentsMargins(0, 8, 0, 2)

        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # ── Header row: chevron (expand/collapse) + clickable select area ──
        header_row = QWidget()
        header_row_lay = QHBoxLayout(header_row)
        header_row_lay.setContentsMargins(0, 0, 0, 0)
        header_row_lay.setSpacing(0)

        chevron_btn = QToolButton()
        chevron_btn.setText("▸")
        chevron_btn.setToolTip("Expand to show individual packages")
        chevron_btn.setAutoRaise(True)
        chevron_btn.setStyleSheet("QToolButton { border: none; font-size: 11pt; padding: 0 8px; }")
        header_row_lay.addWidget(chevron_btn)

        header_btn = ClickableFrame()
        header_inner = QHBoxLayout(header_btn)
        header_inner.setContentsMargins(0, 10, 12, 10)
        header_inner.setSpacing(12)

        grp_check = QCheckBox()
        grp_check.setEnabled(False)
        # See the matching comment in _build_version_card — disabled widgets
        # swallow mouse events instead of passing them to the parent frame.
        grp_check.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        grp_check.setTristate(True)
        header_inner.addWidget(grp_check)

        hdr_label = QLabel()
        hdr_label.setTextFormat(Qt.TextFormat.RichText)
        hdr_label.setText(_to_richtext(title_html))
        header_inner.addWidget(hdr_label, 1)

        header_row_lay.addWidget(header_btn, 1)
        vbox.addWidget(header_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        vbox.addWidget(sep)

        # ── Body: package rows, built lazily on first expand ──────────────
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        vbox.addWidget(body)
        body.setVisible(False)  # collapsed by default — expand via chevron

        pkg_check_map = {}
        state = {"built": False}

        def _build_rows():
            if state["built"]:
                return
            state["built"] = True

            for r in rows:
                pkg_widget = QWidget()
                pkg_box = QHBoxLayout(pkg_widget)
                pkg_box.setContentsMargins(24, 4, 12, 4)
                pkg_box.setSpacing(10)

                chk = QCheckBox()
                chk.setChecked(r.is_selected)
                pkg_check_map[r] = chk
                tip = (
                    f"{r.name}\n{r.version or ''}\n"
                    f"{'Installed' if r.is_installed else 'Not installed'}"
                    + (" (running)" if r.is_active else "")
                    + ("\nHeld — won't auto-upgrade" if r.is_held else "")
                )
                chk.setToolTip(tip)

                def _on_check(checked, row=r, check_map=pkg_check_map, grp_chk=grp_check, rs=rows):
                    row.is_selected = checked
                    self._update_group_tristate(rs, check_map, grp_chk)
                    self._update_buttons()

                chk.toggled.connect(_on_check)
                pkg_box.addWidget(chk)

                name_lbl = QLabel()
                name_lbl.setTextFormat(Qt.TextFormat.RichText)
                name_lbl.setText(_to_richtext(r.markup))
                name_lbl.setToolTip(tip)
                pkg_box.addWidget(name_lbl, 1)

                size_lbl = QLabel(r.size)
                size_lbl.setStyleSheet("color: gray; font-size: 9pt;")
                pkg_box.addWidget(size_lbl)

                status_lbl = QLabel(r.status)
                status_lbl.setStyleSheet("font-size: 9pt;")
                pkg_box.addWidget(status_lbl)

                body_lay.addWidget(pkg_widget)

            # Any checkbox states set programmatically (e.g. via a group
            # click) before this card was ever expanded need to be applied
            # to the checkboxes we just built.
            for r, chk in pkg_check_map.items():
                chk.blockSignals(True)
                try:
                    chk.setChecked(r.is_selected)
                finally:
                    chk.blockSignals(False)

        def _toggle_body(checked=False):
            _build_rows()
            expand = not body.isVisible()
            body.setVisible(expand)
            chevron_btn.setText("▾" if expand else "▸")
            chevron_btn.setToolTip("Collapse" if expand else "Expand to show individual packages")
        chevron_btn.clicked.connect(_toggle_body)

        # ── Header click selects across the whole group at once — works
        # correctly even before the body is expanded/built, since selection
        # state lives on the KernelRow objects, not the widgets.
        header_btn.clicked.connect(
            self._make_group_header_click(rows, pkg_check_map, grp_check)
        )
        header_btn.setToolTip(tooltip)
        grp_check.setToolTip(tooltip)  # belt-and-braces alongside WA_TransparentForMouseEvents
        self._update_group_tristate(rows, pkg_check_map, grp_check)
        return frame

    def _build_meta_card(self, meta_rows: list) -> QFrame:
        """Meta/tracking packages (linux-generic, linux-lowlatency, etc.) as
        a single card at the top of the Mainline grouped view."""
        title = (
            f"<b>Meta / Tracking packages</b>"
            f"  <small><span style='color:#88aaff'>install once, upgrade automatically</span>"
            f"  ({len(meta_rows)} packages)</small>"
        )
        return self._build_simple_group_card(
            title, meta_rows, self._group_tooltip("Meta / Tracking packages", meta_rows)
        )

    # ── XanMod / Liquorix grouped UI ──────────────────────────────────────────
    #
    # Both were previously a flat, individually-checkable list (SimpleListPanel)
    # with no way to install "one whole kernel" in a single click — and for
    # XanMod specifically, packages that merely share a kernel NUMBER but are
    # actually different builds (different x86-64-vN level, edge/lts/rt) could
    # never be told apart by version number alone. Grouping by the package's
    # own (version, flavor) — using the real apt version string, not a
    # name-guessing regex — fixes both: each card is one exact, installable
    # kernel, and its header selects only what that exact kernel needs.

    def _build_xanmod_group_card(self, key, rows: list) -> QFrame:
        version, flavor = key
        any_active    = any(r.is_active    for r in rows)
        any_installed = any(r.is_installed for r in rows)

        status_tag = ""
        if any_active:
            status_tag = "  <span style='color:green'><b>[Active]</b></span>"
        elif any_installed:
            status_tag = "  <span style='color:gray'>[Installed]</span>"

        warn_tag = ""
        if flavor == "v4":
            warn_tag = "  <span style='color:orange'>⚠ AVX-512 — usually no kernel benefit</span>"
        elif flavor == "edge":
            warn_tag = "  <span style='color:orange'>⚠ bleeding-edge</span>"

        rec_tag = ""
        if flavor == getattr(self, "_detected_psabi_level", None):
            rec_tag = "  <span style='color:#88cc88'>★ recommended for your CPU</span>"

        title = (
            f"<b>XanMod {version}</b>  <span style='color:#88aaff'>[{flavor}]</span>"
            f"{status_tag}{warn_tag}{rec_tag}  <small>({len(rows)} packages)</small>"
        )

        tooltip = self._group_tooltip(f"XanMod {version} [{flavor}]", rows)
        info = XANMOD_FLAVOR_INFO.get(flavor)
        if info:
            tooltip += f"\n\n{info[0]}:\n{info[1]}"

        return self._build_simple_group_card(title, rows, tooltip)

    def _build_liquorix_version_card(self, version: str, rows: list) -> QFrame:
        any_active    = any(r.is_active    for r in rows)
        any_installed = any(r.is_installed for r in rows)
        status_tag = ""
        if any_active:
            status_tag = "  <span style='color:green'><b>[Active]</b></span>"
        elif any_installed:
            status_tag = "  <span style='color:gray'>[Installed]</span>"

        title = f"<b>Liquorix {version}</b>{status_tag}  <small>({len(rows)} packages)</small>"
        return self._build_simple_group_card(
            title, rows, self._group_tooltip(f"Liquorix {version}", rows)
        )

    def _rebuild_xanmod_ui(self, query: str = ""):
        self._clear_layout(self._xanmod_box)
        query = (query or "").strip().lower()

        groups = {}
        for r in self.rows_xanmod:
            if self._xanmod_flavor_filter != "any" and r.flavor != self._xanmod_flavor_filter:
                continue
            if query and query not in r.name.lower():
                continue
            groups.setdefault((r.version, r.flavor), []).append(r)

        if not groups:
            empty = QLabel("No XanMod kernels found (or none match the current filter).\nTry clicking Refresh.")
            empty.setStyleSheet("margin-top: 40px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self._xanmod_box.insertWidget(self._xanmod_box.count() - 1, empty)
            return

        def sort_key(k):
            version, flavor = k
            rank = XANMOD_FLAVORS.index(flavor) if flavor in XANMOD_FLAVORS else 99
            return (rank, version)

        for key in sorted(groups.keys(), key=sort_key):
            self._xanmod_box.insertWidget(
                self._xanmod_box.count() - 1, self._build_xanmod_group_card(key, groups[key])
            )

    def _rebuild_liquorix_ui(self, query: str = ""):
        self._clear_layout(self._liquorix_box)
        query = (query or "").strip().lower()

        groups = {}
        for r in self.rows_liquorix:
            if query and query not in r.name.lower():
                continue
            groups.setdefault(r.version, []).append(r)

        if not groups:
            empty = QLabel("No Liquorix kernels found (or none match the current filter).\nTry clicking Refresh.")
            empty.setStyleSheet("margin-top: 40px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self._liquorix_box.insertWidget(self._liquorix_box.count() - 1, empty)
            return

        for version in sorted(groups.keys(), key=cmp_to_key(lambda a, b: -self._version_cmp(a, b))):
            self._liquorix_box.insertWidget(
                self._liquorix_box.count() - 1, self._build_liquorix_version_card(version, groups[version])
            )

    # ── Data Collection ───────────────────────────────────────────────────────

    def _collect_kernels(self):
        self._open_cache()
        items = []
        run = platform.uname().release

        # Fetch held packages once via dpkg (faster than per-package apt query)
        held_pkgs = set()
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
                self._dispatch.call(self._on_kernels_loaded, items)
            except Exception as e:
                import traceback
                detail = traceback.format_exc()
                self._dispatch.call(self._append_log, f"\n[ERROR] {detail}\n")
                self._dispatch.call(
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
        self.rows_xanmod = []
        self.rows_liquorix = []
        self.rows_meta = []
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
                self.rows_xanmod.append(row)
            elif is_liquorix_name(k["name"]):
                self.rows_liquorix.append(row)
            elif k.get("is_meta"):
                self.rows_meta.append(row)
            elif is_generic_kernel_name(k["name"]):
                kv = k.get("kver") or "ungrouped"
                self._mainline_groups.setdefault(kv, []).append(row)

        self._refilter_all()
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
                    self._dispatch.call(self._append_log, line)
                rc = proc.wait()
            except Exception as e:
                rc = 1
                combined.append(f"\nERROR: {e}\n")
                self._dispatch.call(self._append_log, combined[-1])

            output = "".join(combined)

            # apt exits 100 when there are unmet dependencies or the package
            # is not found, but NOT when nothing needed upgrading (that's 0).
            # Treat rc=100 for apt/apt-get as a real failure — log it clearly.
            # These are the xkm-helper subcommands that shell out to apt-get
            # under the hood (see xkm-helper's case statement); check the
            # actual subcommand token (cmd[2] in a "pkexec HELPER <sub> ..."
            # invocation) rather than substring-matching the name, since
            # subcommand names don't necessarily contain "apt".
            _APT_BACKED_SUBCOMMANDS = {
                "install", "remove", "update-sources", "hold", "unhold",
                "add-repo-liquorix", "add-repo-xanmod",
            }
            is_apt_cmd = len(cmd) > 2 and cmd[2] in _APT_BACKED_SUBCOMMANDS
            if rc == 100 and is_apt_cmd:
                self._dispatch.call(self._append_log,
                              "\n⚠  apt exited with code 100 — package not found or "
                              "dependency conflict. Check the log above.\n")

            # Track consecutive pkexec authentication failures so we can show
            # the warning banner after a threshold is reached.
            if "pkexec" in str(cmd[0]):
                if rc != 0:
                    self._dispatch.call(self._on_pkexec_fail)
                else:
                    # Successful pkexec — reset counter and hide banner.
                    self._dispatch.call(self._on_pkexec_success)

            self._dispatch.call(on_done, rc, output)
        threading.Thread(target=worker, daemon=True).start()

    def _on_pkexec_fail(self):
        """Increment the pkexec failure counter and show the warning banner at threshold."""
        self._pkexec_fail_count += 1
        _THRESHOLD = 2
        if self._pkexec_fail_count >= _THRESHOLD and hasattr(self, "_pkexec_warn_bar"):
            self._pkexec_warn_bar.setVisible(True)

    def _on_pkexec_success(self):
        """Reset pkexec failure counter and hide the warning banner."""
        self._pkexec_fail_count = 0
        if hasattr(self, "_pkexec_warn_bar"):
            self._pkexec_warn_bar.setVisible(False)

    def _action_check_updates(self, *_):
        self._set_busy(True, "Updating sources…")
        self._stream_subprocess(
            ["pkexec", HELPER_PATH, "update-sources"],
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
        for rows in (self.rows_xanmod, self.rows_liquorix, self.rows_meta):
            for row in rows:
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
            self._dispatch.call(self._error_dialog, "Invalid package name(s)",
                          "The following package names were rejected:\n" + "\n".join(bad))
        return list(dict.fromkeys(pkgs))  # deduplicate, preserve order

    def _install_selected(self, *_):
        pkgs = self._get_selected_packages(only_installed=False)
        if not pkgs:
            self._error_dialog("Nothing selected", "Select kernels to install.")
            return
        self._pre_modules = set(os.listdir("/usr/lib/modules")) if os.path.isdir("/usr/lib/modules") else set()
        self.btn_details.setChecked(True)
        self._clear_log()
        self._start_log_session("install")
        self._set_busy(True, "Installing kernels…")
        self._stream_subprocess(["pkexec", HELPER_PATH, "install"] + pkgs, self._on_install_done)

    def _on_install_done(self, rc, _):
        ok = rc == 0
        self._set_busy(False, "Installation complete." if ok else "Installation failed.")
        if not ok:
            self._error_dialog("Install failed", "See the Details log for more information.")
            self._end_log_session()
            return
        if self.chk_auto_rm.isChecked():
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
        self.btn_details.setChecked(True)
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
                if self.chk_auto_rm.isChecked():
                    self._auto_remove_old_kernels()
                self._run_update_grub_silent()
                self._dkms_for_new_kernels_then_reboot()
                self._reload_kernels_async()

            self._stream_subprocess(["pkexec", HELPER_PATH, "hold"] + pkgs, on_hold_done)

        self._stream_subprocess(["pkexec", HELPER_PATH, "install"] + pkgs, on_install_done)

    # ── Hold / Unhold ─────────────────────────────────────────────────────────

    def _hold_selected(self, *_):
        pkgs = self._get_selected_packages(only_installed=True)
        if not pkgs:
            self._error_dialog("Nothing to hold", "Select installed kernels to hold.")
            return
        self.btn_details.setChecked(True)
        self._clear_log()
        self._start_log_session("hold")
        self._set_busy(True, "Applying hold…")
        self._stream_subprocess(
            ["pkexec", HELPER_PATH, "hold"] + pkgs,
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
        self.btn_details.setChecked(True)
        self._clear_log()
        self._start_log_session("unhold")
        self._set_busy(True, "Releasing hold…")
        self._stream_subprocess(
            ["pkexec", HELPER_PATH, "unhold"] + pkgs,
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

        self._stream_subprocess(["pkexec", HELPER_PATH, "update-grub"], on_done)

    def _remove_selected(self, *_):
        # Pass 1: flat XanMod / Liquorix / Meta rows.
        # Pass 2: mainline groups (separate data structure, not a flat list).
        # Both passes run before any early-return so the guard fires exactly
        # once, regardless of which tab holds the active kernel's packages.
        all_selected_rows = []
        for rows in (self.rows_xanmod, self.rows_liquorix, self.rows_meta):
            for row in rows:
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
        box = QMessageBox(self.win)
        box.setWindowTitle("Remove or Purge?")
        box.setText(
            "Remove: uninstalls the packages but keeps configuration files.\n\n"
            "Purge: removes packages and deletes all associated configuration files.\n\n"
            f"Packages to remove: {len(pkgs)}"
        )
        btn_cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        btn_remove = box.addButton("Remove", QMessageBox.ButtonRole.AcceptRole)
        btn_purge  = box.addButton("Purge", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        response = "remove" if clicked is btn_remove else "purge" if clicked is btn_purge else "cancel"
        if response not in ("remove", "purge"):
            return

        # "Remove" uses plain `apt remove` without --auto-remove.
        # --auto-remove can cascade to removing shared dependencies
        # (e.g. linux-firmware, linux-base) that other kernels still need,
        # which is surprising and hard to undo. Users who want dependency
        # cleanup can run `apt autoremove` manually afterward.
        apt_flag = "--purge" if response == "purge" else None
        cmd = ["pkexec", HELPER_PATH, "remove"]
        if apt_flag:
            cmd.append(apt_flag)
        cmd += pkgs
        self.btn_details.setChecked(True)
        self._clear_log()
        self._start_log_session(response)
        self._set_busy(True, f"{'Purging' if response == 'purge' else 'Removing'} kernels…")
        self._stream_subprocess(cmd, self._on_remove_done)

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
        for rows in (self.rows_xanmod, self.rows_liquorix, self.rows_meta):
            for r in rows:
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

        box = QMessageBox(self.win)
        box.setWindowTitle("Auto-Remove Old Kernels")
        box.setText(
            f"Found {len(to_remove)} package(s) to remove.\n\n"
            "Remove: keep config files.\n"
            "Purge: also delete config files."
        )
        btn_cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        btn_remove = box.addButton("Remove", QMessageBox.ButtonRole.AcceptRole)
        btn_purge  = box.addButton("Purge", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        response = "remove" if clicked is btn_remove else "purge" if clicked is btn_purge else "cancel"
        if response not in ("remove", "purge"):
            return

        # Same reasoning as _remove_selected: avoid --auto-remove cascade.
        apt_flag = "--purge" if response == "purge" else None
        cmd = ["pkexec", HELPER_PATH, "remove"]
        if apt_flag:
            cmd.append(apt_flag)
        cmd += to_remove
        self.btn_details.setChecked(True)
        self._clear_log()
        self._start_log_session(f"autoremove-{response}")
        self._set_busy(True, "Auto-removing old kernels…")
        self._stream_subprocess(cmd, self._on_remove_done)

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

        # The autoinstall-then-verify logic lives in xkm-helper's
        # "dkms-autoinstall" subcommand now, so we just hand it the list of
        # newly installed kernel versions as arguments (each validated
        # helper-side against a strict version-token pattern).
        self._stream_subprocess(
            ["pkexec", HELPER_PATH, "dkms-autoinstall"] + new_kernels,
            self._on_dkms_done
        )

    def _on_dkms_done(self, rc, output):
        self._set_busy(False)
        if rc != 0:
            # Show warning but still offer reboot — kernel is installed even if DKMS had issues
            box = QMessageBox(self.win)
            box.setWindowTitle("DKMS Warning")
            box.setText(
                "Some DKMS modules may not have built correctly.\n\n"
                "Check the Details log for specifics.\n\n"
                "The new kernel was installed — you can still reboot, but some "
                "driver modules (e.g. NVIDIA) may not be available until DKMS is fixed."
            )
            btn_log = box.addButton("Stay & Review Log", QMessageBox.ButtonRole.RejectRole)
            btn_reboot = box.addButton("Reboot Anyway", QMessageBox.ButtonRole.DestructiveRole)
            box.exec()
            if box.clickedButton() is btn_reboot:
                self._show_reboot_dialog()
        else:
            self._show_reboot_dialog()
        self._end_log_session()

    def _show_reboot_dialog(self):
        box = QMessageBox(self.win)
        box.setWindowTitle("Reboot Required")
        box.setText("Your new kernel is ready. Reboot now to use it?")
        btn_cancel = box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        btn_ok = box.addButton("Reboot Now", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is btn_ok:
            subprocess.Popen(["pkexec", HELPER_PATH, "reboot"])

    # ── Button State ──────────────────────────────────────────────────────────

    def _update_buttons(self):
        can_install = can_remove = can_hold = can_unhold = False
        for rows in (self.rows_xanmod, self.rows_liquorix, self.rows_meta):
            for row in rows:
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
        self.btn_install.setEnabled(can_install  and not self.busy)
        self.btn_install_hold.setEnabled(can_install and not self.busy)
        self.btn_remove.setEnabled(can_remove   and not self.busy)
        self.btn_hold.setEnabled(can_hold      and not self.busy)
        self.btn_unhold.setEnabled(can_unhold    and not self.busy)
        self.btn_refresh.setEnabled(not self.busy)
        self.btn_autorm.setEnabled(not self.busy)

    # ── Update Check ──────────────────────────────────────────────────────────

    def _check_for_app_update(self):
        """
        Fetch the latest release tag from GitHub in a background thread.
        If a newer version is available, show a toast notification.
        Called once ~3 seconds after window realise; runs only once.
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
                    self._dispatch.call(self._on_update_available, tag, data.get("html_url", ""))
            except Exception:
                pass  # Network unavailable or API error — silently skip

        threading.Thread(target=worker, daemon=True).start()

    def _version_newer(self, remote: str, local: str) -> bool:
        """Return True if remote version tuple is strictly greater than local."""
        def to_tuple(v):
            try:
                return tuple(int(x) for x in re.split(r"[.\-]", v) if x.isdigit())
            except Exception:
                return (0,)
        return to_tuple(remote) > to_tuple(local)

    def _on_update_available(self, new_version: str, url: str):
        self.toast_overlay.add_toast(
            f"Update available: v{new_version}  —  click to open release page",
            timeout=0,  # stay until dismissed
            button_label="Open",
            on_button=lambda: self._open_url(url),
        )

    def _show_toast(self, message: str, timeout: int = 3):
        """Display a brief informational toast."""
        self.toast_overlay.add_toast(message, timeout=timeout * 1000)

    @staticmethod
    def _open_url(url: str):
        try:
            subprocess.Popen(["xdg-open", url])
        except Exception:
            pass

    # ── Misc ──────────────────────────────────────────────────────────────────

    def _periodic_check(self):
        self._reload_kernels_async()

    def on_mode_toggled(self, checked):
        dark = checked
        cfg = load_config()
        cfg["dark_mode"] = dark
        save_config(cfg)
        self.win.apply_color_scheme()
        self.status_push(f"Switched to {'Dark' if dark else 'Light'} mode")

    def status_push(self, msg):
        self.status_label.setText(msg)

    def _append_log(self, text):
        self.textedit.moveCursor(QTextCursor.MoveOperation.End)
        self.textedit.insertPlainText(text)
        self.textedit.ensureCursorVisible()
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
        self.textedit.clear()

    def _set_busy(self, busy, text=""):
        self.busy = busy
        self.spinner.setVisible(busy)
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
        box = QMessageBox(self.win)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(message)
        box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


# ─── Unit Tests ───────────────────────────────────────────────────────────────
# Run with:  python3 XKM.py --test

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
            self._newer = lambda r, l: KernelManager._version_newer(None, r, l)

        def test_newer_patch(self):
            self.assertTrue(self._newer("2.0.1", "2.0.0"))

        def test_newer_minor(self):
            self.assertTrue(self._newer("2.2.0", "2.0.0"))

        def test_same(self):
            self.assertFalse(self._newer("2.0.0", "2.0.0"))

        def test_older(self):
            self.assertFalse(self._newer("2.0.0", "2.0.9"))

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
    app = KernelManagerApp(sys.argv)
    app.start()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
