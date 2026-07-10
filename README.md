<p align="center">
  <img width="500" height="500" alt="com xanmod kernel manager" src="https://github.com/user-attachments/assets/0ab6fac5-b4ef-4be4-8450-049b7d278098" />
</p>

[Griffin blog for updates and releases](https://griffin-linux.blogspot.com/)

## What's new in 3.0.0

XKM has been rewritten from the ground up in PyQt6 with the Griffin dark theme, and it's smarter about picking the right kernel for your hardware:

- **CPU detection** — XKM detects your processor and suggests the best XanMod build (v1, v2, v3, or v4) for it automatically. You can still override the pick if you know what you're doing.
- **Better kernel groupings** — clicking a kernel now selects everything that kernel actually needs; individual pieces can still be unchecked.
- **Mainline variant select button** — a new selector on the Mainline tab lets you filter by build type (generic, low-latency, OEM, and so on) instead of scrolling through everything.
- **Collapsible sections** — Mainline kernels are grouped into collapsible cards instead of one long scrolling list.
- **The AppImage build has been dropped.** See below for why.

Made for Windows switchers to Ubuntu to have an easier time. No Terminal to add things you probably never heard of, like PPAs. Just open it, it will ask to auto-add PPAs, install what you want, and remove what you want. Very easily done in the GUI, no code knowledge needed. For kernel testers, you have some nice features; see below.

# XKM Multi-Kernel Manager

A simple graphical tool for managing Linux kernels on Ubuntu-based systems. Install, remove, and pin kernel versions without touching the terminal.

Best used with these tools for Windows switchers: [Kernel Autotune](https://github.com/bobbycomet/kernel-autotune-V2) (auto configures the kernel parameters) and [Process Sentry](https://github.com/bobbycomet/Process-Sentry) (keeps everything responsive)

<p align="center">
  <img src="https://raw.githubusercontent.com/bobbycomet/Appify/main/Griffin-G.png" alt="Griffin Screenshot" width="25%"/>
</p>

<img width="1920" height="1080" alt="Screenshot from 2026-02-21 21-04-51" src="https://github.com/user-attachments/assets/ae78b8c6-85e8-453e-a3da-bacb14061746" />

---

## What it does

XKM gives you a clean interface to browse every kernel available through your system's package manager and take action on them, all in one place, no commands needed.

It supports three kernel families:

- **XanMod** — performance-focused kernels with options tuned for different CPU generations
- **Liquorix** — low-latency kernels aimed at desktop and gaming use
- **Mainline** — the standard Ubuntu/Debian kernels your system ships with
- **DKMS** — Auto handles DKMS for the new kernel for support.

---

## How it works

When you open XKM, it reads your system's package cache (the same database `apt` uses) and shows you what's installed, what's available, and which kernel you're currently running. No internet connection is needed just to browse; it only goes online when you actually install or update something. Linux-headers are pulled alongside the Linux-image.

Each tab shows kernels for that family. You check the boxes next to the ones you want to act on, then use the buttons at the top.

---

## The buttons

| Button | What it does |
|---|---|
| **Refresh** | Re-reads the package list (also runs `apt update` first) |
| **Install Selected** | Installs whatever you've checked |
| **Install + Hold** | Installs and immediately freezes those packages so they won't be upgraded automatically |
| **Remove Selected** | Removes checked kernels (asks whether to keep config files or delete everything) |
| **Hold** | Freezes an installed kernel so `apt upgrade` won't touch it |
| **Unhold** | Unfreezes a held kernel |
| **Auto-Remove Old** | Removes older installed kernels, keeping the active one and the most recent |

<img width="1920" height="1080" alt="Screenshot_20260709_235140" src="https://github.com/user-attachments/assets/80f21b28-504d-4256-b832-b6e08113a4f4" />
<img width="1920" height="1080" alt="Screenshot_20260709_235126" src="https://github.com/user-attachments/assets/116aac5d-eb2a-4a32-92ca-f347b8db615a" />
<img width="1920" height="1080" alt="Screenshot_20260709_235100" src="https://github.com/user-attachments/assets/035aef70-6156-48fa-9303-b6a4733feaf9" />

---

## Installing a kernel

1. Pick a tab (XanMod, Liquorix, or Mainline)
2. Check the box next to the kernel you want
3. Click **Install Selected**
4. Enter your password when prompted
5. Reboot when it's done

That's it. XKM handles the rest — including updating your bootloader so the new kernel shows up at startup.

---

## The XanMod flavor filter

XanMod packages come in different builds optimised for different CPUs. XKM will suggest one automatically based on your CPU, but you can use the **Flavor** dropdown on the XanMod tab to pick a different one:

- **v1** — works on any x86-64 CPU (safe default if you're unsure)
- **v2** — requires SSE4 (most CPUs from ~2008 onwards)
- **v3** — requires AVX2 (Intel Haswell / AMD Ryzen and newer)
- **v4** — requires AVX-512 (high-end modern CPUs only)
- **edge** — latest upstream version, may be less stable
- **lts** — long-term support release

---

## Mainline kernels meta-packages and variants

Mainline kernels are grouped into collapsible version cards, but a couple of things about them work a little differently from XanMod and Liquorix:

- **Meta/tracking packages still need to be picked manually.** Because these are grouped together under one version card, XKM won't auto-select them for you the way it does with a XanMod kernel's dependencies; you'll need to check the specific meta-package you want alongside the kernel itself.
- **Variant select button**: Use this to filter Mainline by build type: generic, low-latency, OEM, and so on. Handy for jumping straight to the variant you actually run instead of scrolling through every card.

### If a kernel version you expect isn't listed

XKM only shows what the kernel maintainers actually publish. Before listing a kernel, it checks the real package metadata (via `apt-cache`) straight from the source — it doesn't keep its own separate list. If a specific build, like a XanMod `6.12.74-x64v3`, has been phased out or pulled by the XanMod team upstream, it will disappear from XKM too, because there's genuinely nothing left to show. This isn't something XKM can control or override — it can only display what's currently published.

---

## Holding a kernel

"Holding" a package tells apt to never upgrade or remove it automatically. This is useful when you've found a kernel that works well and don't want it replaced by the next `apt upgrade`. A held kernel shows an orange **[Held]** badge. Use **Unhold** to release it whenever you're ready.

---

## The Details log

Click **Show Details** at the bottom to see a live log of everything happening — package downloads, installation output, bootloader updates, and so on. Logs are also saved automatically to `~/.config/xanmod-kernel-manager/logs/`.

---

## First launch repository setup

If XanMod or Liquorix isn't set up on your system yet, XKM will detect this when it first opens and offer to add them for you. Just click **Add Repositories** on the prompt, and it handles everything — adding the correct sources and importing the signing keys. If you'd rather do it manually or don't need those kernels, you can dismiss the prompt, and it won't ask again.

---

## Why the AppImage is gone in 3.0.0

Previous versions shipped both a `.deb` and a standalone AppImage. As of 3.0.0, only the `.deb` is provided.

The short version: XKM needs to install and remove kernels, which means it has to run privileged operations. It does this safely through a small helper program that Linux's permission system (Polkit) knows about and trusts. The `.deb` installer puts that helper in place at a fixed system location as a normal part of installing the package, clean, predictable, and easy to verify.

The AppImage version had to fake that same setup on its own, on first launch, by copying the helper into place itself with a permission prompt. That worked in principle, but it added a fragile extra step that depended on things not every system has set up the same way, like whether a permissions prompt (a "polkit authentication agent") is even running in the background, which isn't guaranteed on lighter desktop setups. If that step failed, the app could fail silently instead of clearly telling you what went wrong.

Given that XKM is aimed at people who just want kernel management to work without fuss, that risk wasn't worth it. The `.deb` remains the fully tested, fully supported way to install XKM going forward.

---

## Requirements

- Ubuntu 22.04 or newer (or a compatible derivative)
- `pkexec` (policykit-1) for privilege escalation, with a polkit authentication agent running; this is on by default in GNOME and KDE, but may need to be enabled manually on minimal window manager setups (e.g., i3, sway)
- `python3-pyqt6` and `python3-apt` installed (pulled in automatically by the `.deb`)

---

Download the `.deb` from the [releases page](https://github.com/bobbycomet/XKM-Multi-Kernel-Manager/releases/download/v3.0.0/XKM.deb) or run the Python file directly.

```
wget https://github.com/bobbycomet/XKM-Multi-Kernel-Manager/releases/download/v3.0.0/XKM.deb

sudo apt install ./XKM.deb
```

Or use your package installer.

[Dependencies](https://github.com/bobbycomet/XKM-Multi-Kernel-Manager/wiki#dependencies)

## Running it

```bash
python3 XKM
```

To run the built-in self-tests:

```bash
python3 XKM --test
```

## Acknowledgments

- XanMod project – [XanMod](https://xanmod.org)
- Liquorix kernel by Steven Barrett (damentz) – [Liquorix](https://liquorix.net)
- Ubuntu Mainline Kernel PPA team

---

**Enjoy a smoother, faster desktop with the kernel of your choice, all managed from one place!**

## Community and Support

- **Discord:** [Join Here](https://discord.gg/7fEt5W7DPh)
- **Patreon (Beta Builds):** [Patreon](https://www.patreon.com/c/BobbyComet/membership)
- **Support the Griffin Project:** [Ko-fi](https://ko-fi.com/bobby60908)

<div align="center">
  <img src="https://raw.githubusercontent.com/bobbycomet/Appify/main/Griffin-G.png" alt="Griffin Linux" width="15%"/>
  <p><strong>Griffin Linux. Where power meets simplicity.</strong><br/>
  Made with Windows switchers in mind. Built for everyone who wants a better PC.</p>
</div>

XKM is part of the Griffin Linux project. The name XKM, the Griffin Linux name, and associated icons are protected under the GPLv3 to preserve the integrity of the branding in all distributed versions.
