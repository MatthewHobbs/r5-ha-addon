#!/usr/bin/env python3
"""Render the A290 dashboards across the mobile device matrix and fail on text truncation.

For every device viewport x dashboard it: injects auth, navigates, waits for the custom
cards + the Zen Dots font, then walks the (shadow-DOM-pierced) tree for any text element
that is clipped (text-overflow:ellipsis / nowrap+overflow:hidden with scrollWidth >
clientWidth) or any broken card (hui-error-card). A screenshot is saved per device. Exits
non-zero with a report if any truncation or card error is found.
"""
import argparse
import json
import os
import sys
import time
import traceback

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))

# The bubble dashboard auto-opens its main pop-up ~60ms after load by setting location.hash. If
# that fires while one of our evaluate/screenshot calls is in flight it tears down the execution
# context ("Execution context was destroyed") or leaves a webfont fetch pending (screenshot's
# font-wait then times out). Both are transient and viewport-independent. We settle the auto-open
# before touching the page and retry the whole main capture on such a render error — a genuine
# truncation is a returned finding (never an exception), so it is never retried away.
MAX_RENDER_ATTEMPTS = 3

UA_IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1")
UA_ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like "
              "Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")

# Recurses shadow roots; returns truncated text elements + broken cards.
JS_DETECT = r"""
() => {
  const out = [];
  const walk = (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of nodes) {
      if (el.shadowRoot) walk(el.shadowRoot);
      const tag = (el.tagName || '').toLowerCase();
      if (tag === 'hui-error-card' || tag === 'hui-warning' || tag === 'hui-warning-card') {
        out.push({ type: 'card-error', tag, text: (el.textContent || '').trim().slice(0, 160) });
        continue;
      }
      let own = '';
      for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
      own = own.trim();
      if (!own) continue;
      const cs = getComputedStyle(el);
      const clipsX = cs.textOverflow === 'ellipsis'
                  || (cs.overflowX === 'hidden' && cs.whiteSpace.indexOf('nowrap') >= 0);
      if (clipsX && el.scrollWidth > el.clientWidth + 1) {
        out.push({ type: 'truncated', tag, text: own.slice(0, 160),
                   scrollWidth: el.scrollWidth, clientWidth: el.clientWidth });
      }
    }
  };
  walk(document);
  return out;
}
"""

# Opt-in capture diagnostics (UI_TESTS_DIAG=1): a JSON sidecar beside each screenshot recording
# what the DOM contained at the moment of capture. Ported from the a290 twin (#122), where it
# refuted the leading guess (the main dashboard was fully rendered at capture) and found the real
# bug: the pop-up capture writing an EMPTY page. Measure with this before adding another wait —
# a "stable layout" wait already made things worse there, because a blank page is stable.
JS_DIAG = r"""
() => {
  const tags = {};
  const walk = (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of nodes) {
      const t = (el.tagName || '').toLowerCase();
      if (t.indexOf('mushroom') >= 0 || t === 'bubble-card' || t === 'button-card'
          || t === 'hui-error-card' || t === 'ha-card') tags[t] = (tags[t] || 0) + 1;
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  // Playwright awaits document.fonts.ready before every screenshot; a face stuck 'loading' stalls it.
  let fonts = [];
  try { fonts = [...document.fonts].map(f => [f.family, f.weight, f.status]).slice(0, 60); } catch (e) {}
  return {
    cards: Object.values(tags).reduce((a, b) => a + b, 0), byTag: tags,
    fontsStatus: document.fonts ? document.fonts.status : null, fonts, frames: window.frames.length,
    navType: ((performance.getEntriesByType('navigation') || [])[0] || {}).type || null,
    swController: !!(navigator.serviceWorker && navigator.serviceWorker.controller),
    scrollW: document.documentElement.scrollWidth,
    scrollH: document.documentElement.scrollHeight,
    imagesPending: Array.from(document.images).filter(i => !i.complete).length,
    readyState: document.readyState,
  };
}
"""


class _Stages:
    """Which step of a capture is running, and how long each took: the skip below used to print
    only the error's type, so an intermittent TimeoutError could not be tied to a step."""

    def __init__(self, first):
        self.name, self.t0, self.done = first, time.monotonic(), []

    def next(self, name):
        self.done.append((self.name, round(time.monotonic() - self.t0, 2)))
        self.name, self.t0 = name, time.monotonic()

    def elapsed(self):
        return round(time.monotonic() - self.t0, 2)


class _NavLog:
    """Frame navigations since a page opened. A pop-up capture failed with "Execution context was
    destroyed ... navigation" on one HA leg only, so a skip prints what navigated, and when."""

    def __init__(self, page):
        self.page, self.t0, self.items = page, time.monotonic(), []
        page.on("framenavigated", self.record)

    def record(self, frame):
        self.items.append((round(time.monotonic() - self.t0, 2),
                           "main" if frame == self.page.main_frame else "sub", frame.url[-90:]))


def _write_diag(page, shot_path):
    """Record the DOM state at capture time, when UI_TESTS_DIAG=1."""
    if os.environ.get("UI_TESTS_DIAG") != "1":
        return
    try:
        data = page.evaluate(JS_DIAG)
    except Exception as err:      # never let diagnostics break the gate they are diagnosing
        data = {"error": f"{type(err).__name__}: {err}"}
    try:
        with open(os.path.splitext(shot_path)[0] + ".diag.json", "w") as fh:
            json.dump(data, fh, sort_keys=True)
    except OSError:
        pass


# True once a custom card (or an error card) is present in the (pierced) tree.
JS_RENDERED = r"""
() => {
  const find = (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return false; }
    for (const el of nodes) {
      const t = (el.tagName || '').toLowerCase();
      if (t.indexOf('mushroom') >= 0 || t === 'bubble-card' || t === 'button-card'
          || t === 'hui-error-card') return true;
      if (el.shadowRoot && find(el.shadowRoot)) return true;
    }
    return false;
  };
  return find(document);
}
"""


# Remove Home Assistant's transient startup toasts (e.g. "Starting radio_browser. Not
# everything will be available until it is finished" from default_config) so they don't leak
# into the captured documentation screenshots. The toast host (notification-manager) lives in
# home-assistant's shadow root; clear it (and any stray ha-toast/snackbar) before each capture.
JS_DISMISS_TOASTS = r"""
() => {
  const roots = [document];
  const ha = document.querySelector('home-assistant');
  if (ha && ha.shadowRoot) roots.push(ha.shadowRoot);
  for (const r of roots) {
    try { r.querySelectorAll('notification-manager, ha-toast, mwc-snackbar').forEach(e => e.remove()); }
    catch (e) {}
  }
}
"""


def _stable_issues(page, settle_ms=500, max_passes=12):
    """Poll the truncation scan across a settle window and report only issues that SURVIVE it.
    card-mod styles (e.g. `white-space:normal` on the card labels) and webfonts apply
    asynchronously after a card first paints, so an early measurement can catch a label still in
    its default nowrap/ellipsis state and false-flag it as truncated.

    Crucially, a phantom truncation is *stable* for the whole pre-card-mod plateau (the flagged
    set doesn't change from scan to scan) and then clears all at once when card-mod lands — so a
    fixed two-pass, or any "stop when two scans agree" rule, can return the phantom set outright.
    On the narrowest, most-card-dense viewport (Galaxy S24 @ 360px) that plateau outlasts a couple
    of samples and the gate flakes (observed pass->fail->fail->pass on identical dashboards).

    The reliable signal is *survival*, not stability: a phantom clears once card-mod applies (a
    terminal state — card-mod does not un-apply), whereas a genuine truncation stays flagged for
    the entire window. So keep scanning up to `max_passes`; exit early only on a clean state
    (confirmed by two consecutive empty scans, to rule out a transient empty), and otherwise report
    whatever is still flagged when the window closes. Fast path: a clean pair returns quickly; a
    genuine failure uses the full window (~6s), which is fine since failures are rare."""
    issues, clean_streak = [], 0
    for i in range(max_passes):
        issues = page.evaluate(JS_DETECT)
        clean_streak = clean_streak + 1 if not issues else 0
        if clean_streak >= 2:                  # two consecutive clean scans → genuinely settled
            return []
        if i < max_passes - 1:                 # no point sleeping after the final scan
            page.wait_for_timeout(settle_ms)
    return issues                              # still flagged when the window closed → genuine


def auth_script(base, tokens):
    payload = {
        "access_token": tokens["access_token"],
        "token_type": "Bearer",
        "expires_in": tokens.get("expires_in", 1800),
        "hassUrl": base,
        "clientId": base + "/",
        "expires": 4102444800000,  # year 2100 — don't trigger a refresh during the run
        "refresh_token": tokens["refresh_token"],
    }
    return f"window.localStorage.setItem('hassTokens', {json.dumps(json.dumps(payload))});"


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8123")
    ap.add_argument("--tokens", default="/tmp/ha_tokens.json")
    ap.add_argument("--devices", default=os.path.join(HERE, "devices.json"))
    ap.add_argument("--dashboards", nargs="+", default=["renault-5", "renault-5-bubble"])
    ap.add_argument("--out", default=os.path.join(HERE, "screenshots"))
    args = ap.parse_args()

    tokens = json.load(open(args.tokens))
    devices = json.load(open(args.devices))["devices"]
    os.makedirs(args.out, exist_ok=True)
    init = auth_script(args.base, tokens)

    failures = []
    # The truncation gate runs in light mode (stable); set UI_TESTS_DARK=1 to render in dark
    # mode instead (used to produce the documentation screenshots).
    colour_scheme = "dark" if os.environ.get("UI_TESTS_DARK") else "light"
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        for dev in devices:
            ua = UA_IOS if "iphone" in dev["name"].lower() or "ipad" in dev["name"].lower() else UA_ANDROID
            ctx = browser.new_context(
                service_workers="block",  # CONTROL RUN: does blocking the SW reload remove the skips?
                viewport={"width": dev["width"], "height": dev["height"]},
                device_scale_factor=dev.get("deviceScaleFactor", 2),
                is_mobile=dev.get("isMobile", True), has_touch=dev.get("hasTouch", True),
                color_scheme=colour_scheme, user_agent=ua,
                # The dashboards run infinite CSS animations (pulse, spin, flap-wiggle,
                # socFillToTarget). A page that never stops moving cannot be captured
                # reproducibly and no settling time fixes it — every shot samples a different
                # frame. On the A290 twin the committed screenshots oscillated between two byte
                # sizes all day because of this. reduced_motion asks the page not to start them;
                # screenshot(animations="disabled") freezes anything that ignores the preference.
                reduced_motion="reduce")
            ctx.add_init_script(init)
            page = ctx.new_page()
            nav = _NavLog(page)
            for dash in args.dashboards:
                slug = dev["name"].lower().replace(" ", "_").replace("(", "").replace(")", "")
                shot = os.path.join(args.out, f"{dash}__{slug}.png")
                issues = None
                for attempt in range(MAX_RENDER_ATTEMPTS):
                    try:
                        page.goto(f"{args.base}/{dash}", wait_until="domcontentloaded", timeout=30000)
                        # Let the bubble dashboard's auto-open hash navigation fire (~60ms) and
                        # settle BEFORE any evaluate/screenshot, so it can't tear down an in-flight
                        # execution context or leave a font fetch pending mid-capture.
                        page.wait_for_timeout(300)
                        page.wait_for_function(JS_RENDERED, timeout=30000)
                        try:  # actually load Zen Dots before measuring — fonts.ready alone resolves
                            page.evaluate(                                 # before a not-yet-applied font fetches
                                "async () => { try {"
                                " await document.fonts.load('400 12px \"Zen Dots\"');"
                                " await document.fonts.load('700 13px \"Zen Dots\"');"
                                " await document.fonts.ready;"
                                " } catch (e) {} }")
                        except Exception:
                            pass
                        page.wait_for_timeout(1200)  # settle layout + late cards
                        issues = _stable_issues(page)   # confirm truncations across two passes (see helper)
                        # Drop HA's startup toasts only AFTER the truncation scan, so removing the
                        # toast node can never perturb the gate's measurement — it only cleans the shot.
                        page.evaluate(JS_DISMISS_TOASTS)
                        _write_diag(page, shot)
                        page.screenshot(path=shot, full_page=True, animations="disabled")
                        break
                    except Exception as err:
                        # A render error here is transient (auto-open context teardown / font-wait
                        # timeout). Retry the whole capture; only record it as a failure once the
                        # attempts are exhausted. Genuine truncations are returned by _stable_issues,
                        # not raised, so they never reach this branch and are never retried away.
                        if attempt < MAX_RENDER_ATTEMPTS - 1:
                            print(f"    [retry {attempt + 1}/{MAX_RENDER_ATTEMPTS - 1}] {dash} @ "
                                  f"{dev['name']}: transient {type(err).__name__} — re-rendering")
                            page.wait_for_timeout(600)
                            continue
                        issues = [{"type": "render-error", "tag": "-", "text": f"{type(err).__name__}: {err}"}]
                        try:
                            page.evaluate(JS_DISMISS_TOASTS)
                            _write_diag(page, shot)
                            page.screenshot(path=shot, full_page=True, animations="disabled")
                        except Exception:
                            pass
                # The Smart Charging pop-up ("tab") capture is best-effort and ISOLATED from the
                # gate: opening it via hash navigation can tear down the JS context on slower
                # viewports ("Execution context was destroyed"), and that must never fail the run.
                # When the scan DOES complete, its issues (truncation + broken cards) go through the
                # same two-pass stability filter, so the pop-up keeps its coverage without the flake.
                if dash == "renault-5-bubble":
                    stages = None
                    try:
                        # COMPLETENESS, not just settling. The selector wait used to be swallowed
                        # by a bare `except: pass` and the capture ran anyway, so a pop-up that
                        # failed to open wrote an EMPTY page as the documentation screenshot (on
                        # the a290 twin: 24 cards one run, 0 the next, 99.89% of pixels different).
                        # Reopen once; if still nothing rendered, skip and keep the committed file.
                        # Judge by the pop-up's own content becoming VISIBLE, never by a document
                        # card count: the main view behind a failed pop-up has cards too. "Charge
                        # Target" exists only in the deploy-injected #r5-charging pop-up.
                        for popup_attempt in range(2):
                            page.evaluate("() => { location.hash = '#r5-charging'; }")
                            try:  # the pop-up's inner cards lazy-render (Bubble Card)
                                page.wait_for_selector("text=Charge Target", state="visible", timeout=8000)
                                opened = True
                            except Exception:
                                opened = False
                            page.wait_for_timeout(800)
                            if opened:
                                break
                            print(f"    [popup empty {popup_attempt + 1}/2] {dash} @ "
                                  f"{dev['name']}: pop-up content never became visible — reopening")
                            page.evaluate("() => { location.hash = ''; }")
                            page.wait_for_timeout(400)
                        else:
                            print(f"    [popup skipped] {dash} @ {dev['name']}: pop-up never "
                                  f"rendered; not overwriting the committed screenshot")
                            raise RuntimeError("smart-charging pop-up never became visible")
                        stages = _Stages("dismiss-toasts")
                        page.evaluate(JS_DISMISS_TOASTS)
                        stages.next("write-diag")
                        pshot = os.path.join(args.out, f"{dash}__smart_charging__{slug}.png")
                        _write_diag(page, pshot)
                        stages.next("screenshot")
                        page.screenshot(path=pshot, full_page=True, animations="disabled")
                        stages.next("stable-issues")
                        issues += _stable_issues(page)
                        stages.next("done")
                    except Exception as err:
                        print(f"    pop-up capture skipped ({type(err).__name__}) — not failing the gate")
                        where = (f"stage {stages.name} after {stages.elapsed()}s; completed {stages.done}"
                                 if stages else "before the capture started (pop-up never opened)")
                        print(f"    [popup diag] {dash} @ {dev['name']}: {where}")
                        print(f"    [popup diag] url now: {page.url[-90:]}; navigations: {nav.items[-6:]}")
                        print("    [popup diag] " + traceback.format_exc().replace("\n", "\n    [popup diag] "))
                # De-dupe: the pop-up scan re-walks the whole document, so a main-dashboard finding
                # can otherwise appear twice when both the main view and the pop-up are flagged.
                _seen, _uniq = set(), []
                for _it in issues:
                    _k = (_it["type"], _it.get("tag"), _it.get("text"))
                    if _k not in _seen:
                        _seen.add(_k)
                        _uniq.append(_it)
                issues = _uniq
                if issues:
                    failures.append((dash, dev["name"], dev["width"], issues))
                status = "FAIL" if issues else "ok"
                print(f"  [{status}] {dash} @ {dev['name']} ({dev['width']}px): "
                      f"{len(issues)} issue(s)  -> {os.path.relpath(shot, HERE)}")
            ctx.close()
        browser.close()

    print()
    if failures:
        print(f"=== {len(failures)} device/dashboard combos with issues ===")
        for dash, name, width, issues in failures:
            print(f"\n{dash} @ {name} ({width}px):")
            for i in issues[:12]:
                if i["type"] == "truncated":
                    print(f"  - TRUNCATED <{i['tag']}> {i['scrollWidth']}>{i['clientWidth']}px: "
                          f"{i['text']!r}")
                else:
                    print(f"  - {i['type'].upper()} <{i['tag']}>: {i['text']!r}")
            if len(issues) > 12:
                print(f"  …and {len(issues) - 12} more")
        sys.exit(1)
    print("All dashboards render with no text truncation across the device matrix. ✅")


if __name__ == "__main__":
    run()
