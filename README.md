<img width="1024" height="1024" alt="com xanmod kernel manager" src="https://github.com/user-attachments/assets/0ab6fac5-b4ef-4be4-8450-049b7d278098" />


# XKM — Multi-Kernel Manager

A simple graphical tool for managing Linux kernels on Ubuntu-based systems. Install, remove, and pin kernel versions without touching the terminal.

<p align="center">
  <img src="https://raw.githubusercontent.com/bobbycomet/Appify/main/Griffin-G.png" alt="Griffin Screenshot" width="25%"/>
</p>



<img width="1920" height="1080" alt="Screenshot from 2026-02-21 21-04-51" src="https://github.com/user-attachments/assets/ae78b8c6-85e8-453e-a3da-bacb14061746" />

---

## What it does

XKM gives you a clean interface to browse every kernel available through your system's package manager and take action on them — all in one place, no commands needed.

It supports three kernel families:

- **XanMod** — performance-focused kernels with options tuned for different CPU generations
- **Liquorix** — low-latency kernels aimed at desktop and gaming use
- **Mainline** — the standard Ubuntu/Debian kernels your system ships with

---

## How it works

When you open XKM it reads your system's package cache (the same database `apt` uses) and shows you what's installed, what's available, and which kernel you're currently running. No internet connection is needed just to browse — it only goes online when you actually install or update something.

Each tab shows kernels for that family. You check the boxes next to the ones you want to act on, then use the buttons at the top.

---

## The buttons

| Button | What it does |
|---|---|
| **↺ Refresh** | Re-reads the package list (also runs `apt update` first) |
| **⬇ Install Selected** | Installs whatever you've checked |
| **⬇🔒 Install + Hold** | Installs and immediately freezes those packages so they won't be upgraded automatically |
| **✕ Remove Selected** | Removes checked kernels (asks whether to keep config files or delete everything) |
| **🔒 Hold** | Freezes an installed kernel so `apt upgrade` won't touch it |
| **🔓 Unhold** | Unfreezes a held kernel |
| **Auto-Remove Old** | Removes older installed kernels, keeping the active one and the most recent |

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

XanMod packages come in different builds optimised for different CPUs. Use the **Flavor** dropdown on the XanMod tab to narrow things down:

- **v1** — works on any x86-64 CPU (safe default if you're unsure)
- **v2** — requires SSE4 (most CPUs from ~2008 onwards)
- **v3** — requires AVX2 (Intel Haswell / AMD Ryzen and newer)
- **v4** — requires AVX-512 (high-end modern CPUs only)
- **edge** — latest upstream version, may be less stable
- **lts** — long-term support release

---

## Holding a kernel

"Holding" a package tells apt to never upgrade or remove it automatically. This is useful when you've found a kernel that works well and don't want it replaced by the next `apt upgrade`. A held kernel shows an orange **[Held]** badge. Use **Unhold** to release it whenever you're ready.

---

## The Details log

Click **Show Details** at the bottom to see a live log of everything happening — package downloads, installation output, bootloader updates, and so on. Logs are also saved automatically to `~/.config/xanmod-kernel-manager/logs/`.

---

## First launch — repository setup

If XanMod or Liquorix aren't set up on your system yet, XKM will detect this when it first opens and offer to add them for you. Just click **Add Repositories** on the prompt and it handles everything — adding the correct sources and importing the signing keys. If you'd rather do it manually or don't need those kernels, you can dismiss the prompt and it won't ask again.

---

## Requirements

- Ubuntu 22.04 or newer (or a compatible derivative)
- `pkexec` for privilege escalation (standard on most desktop systems)

---

Download the Deb file or run the Python file.

## Running it

```bash
python3 XKM
```

To run the built-in self-tests:

```bash
python3 XKM --test
```
## Acknowledgments

- XanMod project – https://xanmod.org
- Liquorix kernel by Steven Barrett (damentz) – https://liquorix.net
- Ubuntu Mainline Kernel PPA team

---

**Enjoy a smoother, faster desktop with the kernel of your choice – all managed from one place!**

## Community & Support

Discord: https://discord.gg/7fEt5W7DPh

Patreon (Beta Builds): https://www.patreon.com/c/BobbyComet/membership

Support the Griffin Project: https://ko-fi.com/bobby60908
