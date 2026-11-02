"""Manual sign-in for websites that need an account.

Opens a VISIBLE Chromium window with a persistent, private browser profile
(local/browser_profiles/<name>, permissions 700, git-ignored). You sign in yourself — FrameFoundry never
sees, prints or exports passwords, cookies or tokens, and never bypasses CAPTCHAs or 2FA. Close the window
(or press Enter in the terminal) when you are signed in; later recordings use --browser-profile <name>.
Delete the profile when you no longer need it: ./framefoundry.sh login --forget <name>
"""
import os
import re
import shutil
import sys
import threading
import time

from framefoundry.util import ROOT, FrameFoundryError, info


def profile_dir(name):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""):
        raise FrameFoundryError("profile names may only contain letters, digits, '-' and '_'")
    return ROOT / "local" / "browser_profiles" / name


def login(url, name, timeout_min=20):
    from playwright.sync_api import sync_playwright
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise FrameFoundryError("manual login needs a desktop session (no DISPLAY/WAYLAND_DISPLAY found)")
    d = profile_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    done = threading.Event()

    def wait_enter():
        try:
            sys.stdin.readline()
        except Exception:  # noqa: BLE001
            pass
        done.set()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(d), headless=False, viewport={"width": 1440, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        if sys.stdin and sys.stdin.isatty():
            info("A browser window is open. Sign in there, then close the window or press Enter here.")
            threading.Thread(target=wait_enter, daemon=True).start()
        else:
            info(f"A browser window is open. Sign in there, then close the window (timeout {timeout_min:g} min).")
        t0 = time.time()
        reason = "timeout"
        while time.time() - t0 < timeout_min * 60:
            time.sleep(0.5)
            if done.is_set():
                reason = "confirmed"
                break
            try:
                if not ctx.pages:
                    reason = "window closed"
                    break
            except Exception:  # noqa: BLE001
                reason = "window closed"
                break
        try:
            time.sleep(1.0)           # let the site persist its session
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    info(f"login session saved in {d} ({reason}). Use: --browser-profile {name}")
    return str(d)


def forget(name):
    d = profile_dir(name)
    if d.exists():
        shutil.rmtree(d)
        info(f"deleted browser profile {d}")
    else:
        info(f"no browser profile named {name}")
