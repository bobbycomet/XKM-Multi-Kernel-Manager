## The future of XKM

XKM 3.1.0 is being bypassed to a 4.0 release with a brand name change to GKM. XKM was focused originally on Xanmod, and then was later introduced to having Liquorix, and mainline coverage. That is where the X came from, and due to confusion with the branding internally, the name GKM (Griffin Kernel Manager) is adopting the torch of XKM. 

### > **Added Note: 11/02/2026:** XKM is moving to `LTS`, while GKM will be the future for multi-distro support. This decision was made because of the architectural changes of GKM and its migration. Some people may not need GKM if they are already on Ubuntu/Debian, and like XKM. If any features do get added to XKM, they would likely come from improvements of the UX over anything additive, as XKM is already feature complete. This means XKM will stay a `.deb` file, as it will not need to be adjusted for multi-distro. Some features of GKM may trickle down to XKM, but again, this is only if it improves the UX, or if packages or dependencies have shifted from their respective developers. This means that XKM will stay in its current version, and not a 3.1.0 version, as all of those features that were added are in GKM's multi-distro architecture.
>
> XKM will still be part of the Griffin line up. This is because CUXL apps are specialized in a niche, while Griffin apps are specialized in UX. Both are strong in their UX philosophies. However, these philosophies serve a separate purpose from one another. Griffin is for a cohesive experience, CUXL is for a smoother experience within the niche an application was built, such as [Conky Studio](https://github.com/bobbycomet/Conky-Studio) for desktop HUDs and themes for this example.

## What to expect in 4.0.

- Bringing back the AppImage with a dedicated helper for Pkexec and installer for the Polkit
- Better distro detection as Arch, Fedora/RHEL, CachyOS, and OpenSUSE will be fully supported, and not just Debian/Ubuntu. This includes their derivatives, such as Garuda, Nobara, etc.
- Raspberry PI support for Debian/Ubuntu
- psABI level (v1–v4) detection beyond Xanmod, for CachyOS and COPR builds.
- Cloud VM detection (AWS/Azure/GCP/Oracle) will preselect the matching Ubuntu flavor
- Exotic kernel tab
- Intel GPU support, not just AMD and NVIDIA
- Better x86_64 detection as XanMod, CachyOS and Liquorix tabs are not offered on non-x86-64 machines
- And many more fixes and added support

> **Development Note, October 23, 2026: XKM 3.1.0 is currently in development alongside the other Griffin applications.** I am investigating what architecture I may need to shift in order to get CachyOS, Zen, TKG, and possibly Libre working in the application. The AppImage will be reinvestigated for packaging again. If architecture changes too much, it will become a 4.0 release, but the goal is to not change too much to destroy the current workflow.
> 
> Why this shift from just Ubuntu support and not Fedora? Kernel Autotune and SentryV3 are also getting architectural changes to work with Systemd (the default), RunIt, and OpenRC init systems, along with better Kernel detection, and specific distro detection and packaging. XKM works along side these tools, and because they work at the user and kernel spaces, the tools need to be evaluated for such a shift to not destroy that harmony. Kernel Autotune is already set up this way, however, I must have the other tools follow suit, and the state.json will be part of the core for all Griffin applications. 
>
> To support Fedora along with it, I will need to assess if the architecture for Kernels and other distros is sound before adding more support. Fedora is planned as part of the next update cycle, once the newer update stabilizes after release.
