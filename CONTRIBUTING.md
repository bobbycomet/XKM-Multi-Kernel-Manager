# Contributing to XKM

Thanks for considering a contribution to XKM. This is a solo-developed project (part of the Griffin Linux ecosystem), 
maintained full-time, so please bear with response times on issues and PRs. That said, contributions are genuinely 
welcome and appreciated.

---

## Before you start

For anything beyond a small fix (typo, obvious bug, small refactor), please open an issue on Discord first to discuss the change 
before writing code. This avoids spending time on a PR that doesn't fit the project's direction, especially for anything 
touching the privileged helper (`xkm-helper`) or the kernel classification logic, both of which have specific design 
constraints described below.
XKM intentionally focuses on supported, production-ready kernel families. Requests to add additional kernel ecosystems 
(such as Zen, CachyOS, or Ubuntu Mainline) are generally outside the project's scope.

For small fixes, feel free to just open a PR directly.

---

## Getting set up

XKM is a single Python 3 script using PyQt6, backed by a small privileged bash helper (`xkm-helper`) invoked via `pkexec`.

### Dependencies

```bash
sudo apt update && sudo apt install \
    python3 \
    python3-apt \
    python3-pyqt6 \
    policykit-1
```

### Running it

```bash
xkm
```

### Running the self-tests

```bash
xkm --test
```

Example:

```
test_extract_generic (__main__._run_tests.<locals>.TestClassification.test_extract_generic) ... ok
test_extract_headers (__main__._run_tests.<locals>.TestClassification.test_extract_headers) ... ok
test_extract_modules_extra (__main__._run_tests.<locals>.TestClassification.test_extract_modules_extra) ... ok
test_extract_none (__main__._run_tests.<locals>.TestClassification.test_extract_none) ... ok
test_extract_unsigned (__main__._run_tests.<locals>.TestClassification.test_extract_unsigned) ... ok
test_flavor_edge (__main__._run_tests.<locals>.TestClassification.test_flavor_edge) ... ok
test_flavor_generic (__main__._run_tests.<locals>.TestClassification.test_flavor_generic) ... ok
test_flavor_lts (__main__._run_tests.<locals>.TestClassification.test_flavor_lts) ... ok
test_flavor_meta_v3 (__main__._run_tests.<locals>.TestClassification.test_flavor_meta_v3) ... ok
test_flavor_rt (__main__._run_tests.<locals>.TestClassification.test_flavor_rt) ... ok
test_flavor_x64v2_lts (__main__._run_tests.<locals>.TestClassification.test_flavor_x64v2_lts) ... ok
test_flavor_x64v3 (__main__._run_tests.<locals>.TestClassification.test_flavor_x64v3) ... ok
test_generic_excludes_liquorix (__main__._run_tests.<locals>.TestClassification.test_generic_excludes_liquorix) ... ok
test_generic_excludes_xanmod (__main__._run_tests.<locals>.TestClassification.test_generic_excludes_xanmod) ... ok
test_generic_headers (__main__._run_tests.<locals>.TestClassification.test_generic_headers) ... ok
test_generic_image (__main__._run_tests.<locals>.TestClassification.test_generic_image) ... ok
test_liquorix_headers (__main__._run_tests.<locals>.TestClassification.test_liquorix_headers) ... ok
test_liquorix_image (__main__._run_tests.<locals>.TestClassification.test_liquorix_image) ... ok
test_not_liquorix (__main__._run_tests.<locals>.TestClassification.test_not_liquorix) ... ok
test_not_xanmod (__main__._run_tests.<locals>.TestClassification.test_not_xanmod) ... ok
test_xanmod_meta (__main__._run_tests.<locals>.TestClassification.test_xanmod_meta) ... ok
test_xanmod_meta_image (__main__._run_tests.<locals>.TestClassification.test_xanmod_meta_image) ... ok
test_xanmod_versioned_headers (__main__._run_tests.<locals>.TestClassification.test_xanmod_versioned_headers) ... ok
test_xanmod_versioned_image (__main__._run_tests.<locals>.TestClassification.test_xanmod_versioned_image) ... ok
test_malformed (__main__._run_tests.<locals>.TestVersionCompare.test_malformed) ... ok
test_newer_minor (__main__._run_tests.<locals>.TestVersionCompare.test_newer_minor) ... ok
test_newer_patch (__main__._run_tests.<locals>.TestVersionCompare.test_newer_patch) ... ok
test_older (__main__._run_tests.<locals>.TestVersionCompare.test_older) ... ok
test_same (__main__._run_tests.<locals>.TestVersionCompare.test_same) ... ok

```

These are pure logic tests (classification functions, flavor extraction, version comparison, the kernel-version regex). 
No GTK, no PyQt6 display, no apt cache, no filesystem access required, so they should pass in any environment with 
Python 3 available, including CI. Please run these before opening a PR, and add to them if you're changing any of the logic they cover.

---

## What's especially welcome

- Bug fixes, especially around kernel classification edge cases (unusual package naming from XanMod/Liquorix/Mainline),
  DKMS handling, or GRUB integration on non-standard setups
- Improvements to error handling and user-facing messages, particularly around `pkexec` failures and restricted environments
  (no sudo rights, locked-down enterprise setups, minimal window managers without a polkit agent running)
- Testing on distros/derivatives outside the ones I can personally verify (see the compatibility table in the wiki)
- Documentation improvements

---

## Things that need extra care

### The privileged helper (`xkm-helper`)

Every write operation, installs, removals, holds, repository additions, DKMS builds, grub updates, goes through 
this one script via `pkexec`. It's a single, deliberately small, reviewable trust boundary. If you're touching it:

- Any new subcommand must validate its own arguments independently; never trust that the GUI side already validated something.
  The helper is the actual security boundary, not the Python app.
- Keep the subcommand vocabulary as a fixed, hardcoded set. Don't add anything that accepts arbitrary shell fragments,
  arbitrary paths, or unbounded argument lists.
- Package names and kernel version strings must go through the existing strict regex patterns (or an equally strict one for
  new argument types). If you're not sure whether a change loosens validation, ask in the issue before submitting the PR.
- Changes here get the most scrutiny before merging, for obvious reasons. Please don't be discouraged if a PR to `xkm-helper`
  takes longer to review than one elsewhere.

### Kernel classification logic

The functions that decide whether a package is XanMod, Liquorix, or Mainline, and what version/flavor it represents, 
are pure string matching against real-world package names from three different upstreams. If you're changing these, 
please include a few real `apt-cache` package name examples (or a link to the relevant repo's package listing) 
in your PR description so the change can be verified against actual naming conventions rather than a guess.

---

## Code style

- Keep functions focused; the existing classification and validation functions are intentionally small and single-purpose.
- Match the existing PyQt6 patterns already in use in the file (background threads dispatch back to the main thread rather
  than touching widgets directly from a thread).
- No new runtime dependencies without discussion first, the goal is to keep the dependency list short (see above).

---

## Submitting a PR

- Describe what the change does and why, not just what it does.
- If it's a bug fix, describe how to reproduce the original bug.
- If it changes behavior, a user would notice, mention whether the README or wiki needs updating too
  (feel free to include those updates in the same PR).
- Keep PRs focused on one thing where possible; smaller PRs get reviewed faster.

---

## Questions

Feel free to open an issue for questions, or drop by the [Discord](https://discord.gg/7fEt5W7DPh) if you want to chat something through before writing code.
