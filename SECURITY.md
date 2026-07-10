# Security Policy

## Reporting a vulnerability

If you find a security issue in XKM, please report it privately rather than opening a public issue. The best way to do this is through 
GitHub's private vulnerability reporting for this repository:

1. Go to the **Griffin apps issue reporting** forun tab on the [DISCORD](https://github.com/bobbycomet/XKM-Multi-Kernel-Manager)
2. Create a new post and **Use the tags**
3. Include as much detail as you can: what the issue is, how to reproduce it, and what you think the impact is

If that doesn't work, feel free to open a regular issue with minimal detail and ask me to reach out privately (e.g., via a Discord DM). 
Please don't post exploit details or proof-of-concept code publicly before a fix is out.

**I do not offer a bug bounty.** I'm a solo developer working on this full-time (no income coming in as of now), so there's no financial 
reward program for reports. I still want to hear about real issues. If you'd like, I'll credit you in the changelog/release notes. 
Please go in with that expectation.

### What to expect

I'll do my best to acknowledge reports within a few days and keep you updated as I work through a fix. XKM is maintained by a single developer, 
so response times may vary depending on severity and availability. Straightforward issues (a validation bypass, a clear privilege escalation path) 
will be prioritized over lower-severity findings.

---

## What's in scope

Given XKM's architecture, the areas that matter most from a security standpoint are:

- **`xkm-helper`**, the privileged bash script that all install/remove/hold/repository/DKMS/grub operations route through via `pkexec`.
  This is the actual trust boundary of the application; anything that lets an unprivileged input reach it in an unvalidated or unexpected way is a real concern.
- **Package name and kernel version validation**, both the client-side regex checks in the Python app and the authoritative re-validation
  inside `xkm-helper` itself. A way to smuggle unexpected characters, paths, or shell metacharacters through either layer is in scope.
- **PolicyKit integration** (`com.xanmod.kernel.manager.helper`), including anything that would let an unprivileged process trigger
  helper actions outside of the intended flow.
- **The `.deb` packaging and postinst/prerm scripts**, if a bug exists there, could result in incorrect permissions, an unintended setuid/setgid
  state, or a helper installed somewhere other than its fixed system path.
- Security reports involving privilege escalation, command injection, PolicyKit bypasses, arbitrary file writes as root, or repository trust issues
  will receive the highest priority.

## What's out of scope

- Issues that require the attacker to already have root or equivalent local privilege (at that point, they don't need XKM to do anything)
- Denial-of-service style bugs that just crash the GUI app itself with no privilege implication (still worth a normal bug report, just not a security report)
- Missing hardening that doesn't correspond to an actual exploitable path

If you're not sure whether something qualifies, report it privately anyway, and I'll let you know; false positives are fine, I'd rather hear about 
something that turns out to be a non-issue rather than missing a real issue.

---

## Supported versions

Only the latest released version of XKM (3.0.0 and onward) receives security fixes. Given the small scale of this project, 
older versions won't get backported patches; please update to the latest release if you're reporting or affected by an issue.

Version | Supported
-- | --
3.0.x | Yes
2.x and earlier | No, please upgrade

---

## Disclosure

I aim for coordinated disclosure: once a fix is out, I'm happy to work with you on when and how details get shared publicly 
(release notes, an advisory, or your own write-up), but I'd ask that details aren't published before a fix is released to users.
