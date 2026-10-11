# -*- coding: utf-8 -*-
"""Human-like browsing helpers for BR Shopee — reduce bot signals on account sessions."""
from __future__ import annotations

import random
import time

import captcha_mint as mint

VIEW_W, VIEW_H = mint.VIEW_W, mint.VIEW_H


def pause(page, lo_ms: int = 400, hi_ms: int = 1200) -> None:
    try:
        if page is None or getattr(page, "is_closed", lambda: False)():
            time.sleep(random.uniform(lo_ms, hi_ms) / 1000.0)
            return
        page.wait_for_timeout(int(random.uniform(lo_ms, hi_ms)))
    except Exception:
        # Browser/context closed mid-wait — fall back to wall-clock sleep.
        try:
            time.sleep(random.uniform(lo_ms, hi_ms) / 1000.0)
        except Exception:
            pass


def mouse_move(page, x: int | None = None, y: int | None = None, *, steps: int | None = None) -> None:
    """Move mouse to a viewport point with variable step count (not teleport)."""
    try:
        if page is None or getattr(page, "is_closed", lambda: False)():
            return
        tx = x if x is not None else random.randint(80, max(120, VIEW_W - 80))
        ty = y if y is not None else random.randint(90, max(140, VIEW_H - 120))
        n = steps if steps is not None else random.randint(4, 14)
        page.mouse.move(tx, ty, steps=n)
    except Exception:
        pass


def scroll(page, delta: int | None = None) -> None:
    try:
        if page is None or getattr(page, "is_closed", lambda: False)():
            return
        page.mouse.wheel(0, delta if delta is not None else random.randint(280, 1100))
    except Exception:
        pass


def warm_home(page, domain: str = "shopee.com.br") -> None:
    """Establish a natural session: land on home, dwell, scroll — before any PDP."""
    try:
        page.goto(f"https://{domain}/", wait_until="commit", timeout=60000)
    except Exception:
        pass
    pause(page, 2200, 4500)
    mouse_move(page)
    scroll(page, random.randint(900, 2600))
    pause(page, 900, 2200)
    if random.random() < 0.35:
        mouse_move(page)
        scroll(page, random.randint(400, 1200))
        pause(page, 600, 1400)


def read_pdp(page) -> None:
    """Simulate reading a product page while get_pc loads in the background."""
    mouse_move(page)
    if random.random() < 0.75:
        scroll(page, random.randint(350, 950))
    pause(page, 450, 1100)
    if random.random() < 0.45:
        mouse_move(page)
        scroll(page, random.randint(-400, 600))
        pause(page, 350, 900)


def traffic_challenge(page, secs: float = 18.0) -> bool:
    """Wait on /verify/traffic with occasional mouse/scroll (real users fidget)."""
    deadline = time.time() + secs
    while time.time() < deadline:
        url = page.url or ""
        if "/verify/traffic" not in url:
            return True
        if "/error" in url:
            return False
        mouse_move(page)
        if random.random() < 0.4:
            scroll(page, random.randint(120, 480))
        pause(page, 550, 1200)
    return "/verify/traffic" not in (page.url or "")


def maybe_visit_home(page, domain: str, urls_since_home: int, every_n: int) -> int:
    """Occasionally return to homepage between products — breaks PDP-only bot pattern."""
    if every_n <= 0 or urls_since_home < every_n:
        return urls_since_home
    try:
        page.goto(f"https://{domain}/", wait_until="commit", timeout=45000)
    except Exception:
        pass
    pause(page, 1500, 3200)
    mouse_move(page)
    if random.random() < 0.6:
        scroll(page, random.randint(500, 1800))
    pause(page, 800, 1800)
    return 0


def after_success(page) -> None:
    """Brief dwell after data captured — user glances at page before leaving."""
    read_pdp(page)
    pause(page, 250, 750)
    try:
        page.evaluate("window.stop()")
    except Exception:
        pass
