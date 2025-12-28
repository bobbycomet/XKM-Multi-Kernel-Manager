<div align="center">
  <img src="https://github.com/bobbycomet/XKM-Multi-Kernel-Manager/blob/main/com.xanmod.kernel.manager.png" alt="XKM Screenshot" width="25%"/>
</div>

# XKM – Multi-Kernel Manager  
**XanMod • Liquorix • Mainline • DKMS support**
**Xanmod and Liquorix kernels are widely used in gaming and low-latency systems. They are unofficial but well-maintained.**

<p align="center">
  <img src="https://raw.githubusercontent.com/bobbycomet/Appify/main/Griffin-G.png" alt="Griffin Screenshot" width="25%"/>
</p>

**Built for Griffin Linux (coming soon) and other Ubuntu variants.**

**XKM** is a modern, user-friendly graphical tool for managing multiple Linux kernel sources on Debian-based distributions (Ubuntu, Linux Mint, Pop!_OS, etc.). It lets you easily browse, install, remove, and keep track of kernels from three popular sources:

- **XanMod** – high-performance custom kernels  
- **Liquorix** – Zen-tuned kernels optimized for desktop responsiveness  
- **Mainline / Generic** – official Ubuntu/mainline kernels  

All in one clean GTK4/Libadwaita interface.

## Features

- Native GNOME-style UI built with GTK4 and Libadwaita
- Separate tabs for XanMod, Liquorix, and Mainline kernels
- Live search/filter across all kernels
- Check-box selection for bulk install/remove
- One-click repository addition (XanMod and official Liquorix PPA) with automatic `apt update`
- Automatic DKMS module rebuild for newly installed kernels
- Optional auto-remove of old kernels after successful installation
- Smart “Auto-Remove Old Kernels” button (keeps current + 2 newest) or manual removal
- Detailed operation log panel (with persistent log files)
- Dark/Light mode switch
- Periodic background refresh
- Reboot prompt after kernel installation
- Silent APT operations (terminal does not open)
- Safety checks – prevents removal of the currently running kernel


![XKM Screenshot](https://github.com/bobbycomet/XKM-Multi-Kernel-Manager/blob/main/XKM.png)

## Forks & Derivatives

If you build a project using XKM as a base
Or in your distribution
Please credit the original project:

> Forks “Based on XKM – Multi-Kernel Manager by Bobby Comet”
> Distros "Uses XKM - Multi-Kernel-Manager by Bobby Comet"
This is not legally required beyond GPL,
but is requested out of respect for the work involved.


## Installation

### Requirements if built from source
- Ubuntu 22.04+ or any recent Debian-based distro
- Python 3.10 or newer
- `python3-gi`, `gir1.2-adw-1`, `gir1.2-gtk-4.0`
- `apt` and `pkexec` (policykit) for privileged operations
- Internet connection (for adding repositories and downloading kernels)

### Install dependencies
```
sudo apt update
sudo apt install python3-gi python3-apt gir1.2-gtk-4.0 gir1.2-adw-1 policykit-1 curl gnupg
```
### IMPORTANT!!! Add this if you have not already
```
sudo apt install dkms linux-headers-$(uname -r)
```
### Install XKM
Gdebi is preferred to make sure dependencies are installed (your distro might have another tool or Gdebi by default), or use the above method to set up the environment. 

```
sudo apt install gdebi

```

```
wget https://github.com/bobbycomet/XKM-Multi-Kernel-Manager/releases/download/v1.0.0/XKM-1.0.0.deb
sudo gdebi XKM-1.0.0.deb
```

## Usage

1. Launch XKM – it will automatically check for missing repositories and offer to add them. Skips if already in the system.
2. Choose the kernel(s) you want by checking the boxes (or double-click).
3. Click **Install Selected** → the tool will:
   - Install the kernel packages
   - Rebuild any DKMS modules (e.g., NVIDIA, gamepads, etc.)
   - Prompt for reboot
4. Use **Remove Selected** to purge unwanted kernels (active kernel is protected).
5. **Auto-Remove Old** keeps only the running kernel + the two newest versions.

All operations require administrator privileges via `pkexec`.

## Configuration for the nerds like me

Configuration is stored in `~/.config/xanmod-kernel-manager/config.json`:

- Default for window size
- Dark/Light mode preference
- Auto-remove after install toggle
- Auto-check interval (hours)

Logs are saved in `~/.config/xanmod-kernel-manager/logs/` with timestamps.

## Contributing

Contributions are very welcome!

- Report bugs or request features via GitHub Issues
- Submit pull requests (please follow PEP 8 and keep the code readable)
- Help improve the UI/UX, add translations, or write documentation

## License

This project is licensed under the **GNU General Public License v3.0** – see the [LICENSE](LICENSE) file for details.

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
