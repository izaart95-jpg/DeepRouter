"""
reCAPTCHA Token Harvester
=========================
Config at the top — edit before running.

Usage:
    pip install playwright fastapi uvicorn
    playwright install chromium
    python arena_token.py

Then open http://localhost:5000

Tokens are saved directly to tokens.json (compatible with modula.py / main.py).
"""

import asyncio
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from playwright.async_api import async_playwright, BrowserContext, Page

# ============================================================
# CONFIGURATION — edit these
# ============================================================

CUSTOM = True
PATH = "/path/to/browser/must/be/chromium"  # Brave is recommended
N = 1  # number of windows (TABS=False) or tabs (TABS=True)
EXTENSIONS = False
EXTENSIONS_DIR = "/path/to/extensions/"  # Download RektCaptcha
CUS_PROFILE = False
PROFILE_PATH = ""
TABS = False   # False = N separate browser windows  |  True = N tabs in one window
SERVER_PORT = 5000
AUTO_LOGIN = True

# ============================================================
# 5_GAIN — Auto-navigate to a specific arena.ai conversation
# ============================================================
# Requires AUTO_LOGIN=True.
# When True, you will be prompted for an eval_id at startup.
# After login/ready flow, the window navigates to:
#   https://arena.ai/c/<eval_id>
# instead of staying on https://arena.ai

FIVE_GAIN = False   # set to True to enable (AUTO_LOGIN must also be True)

# ============================================================
# TUNING — Reload page after each v2/v3 token is harvested
# ============================================================
# When True (default): after every v2 or v3 token the page reloads,
# re-runs the blocker, re-marks ready, and re-injects the active harvester
# script so harvesting continues automatically.
# When False: tokens are stored normally with no page reload.

TUNING = True

# ============================================================
# HARD_TUNING — Clear cookies/localStorage/fingerprint on each reload
# ============================================================
# Requires TUNING=True. Incompatible with CUS_PROFILE=True (raises error).
#
# When True: instead of a simple page.reload(), HARD_TUNING clears
# browser fingerprint data on every token cycle while keeping the profile:
#   1. Save the 4 essential cookies to RAM:
#        arena-auth-prod-v1.0, arena-auth-prod-v1.1, __cf_bm, cf_clearance
#   2. Clear cookies and localStorage/sessionStorage via JavaScript
#   3. Clear Playwright context cookies (context stays open)
#   4. Re-inject the 4 saved cookies into the context
#   5. Navigate to arena.ai (or arena.ai/c/<eval_id> if FIVE_GAIN=True)
#   6. Run blocker → ready signal → re-inject active harvester script
#
# Effect: each harvest cycle starts with fresh browser fingerprint
# (cleared localStorage, fresh cookies beyond the 4 saved ones).
# This makes each token request look like a fresh browser session to reCAPTCHA.
# The browser context and profile remain open - no context rebuild needed.

HARD_TUNING = False   # set to True to enable (TUNING must also be True)

# ============================================================
# TOKENS FILE — output compatible with modula.py / main.py
# ============================================================

TOKENS_FILE = "tokens.json"
CONFIG_FILE  = "config.json"

# ============================================================

# ============================================================
# COOKIE INJECTION — edit these when COOKIES=True
# ============================================================

COOKIES = False
# When COOKIES=True the harvester will inject auth cookies into each context.
COOKIE_V1 = ""
# Paste the full value for arena-auth-prod-v1.0 here.

COOKIE_V2 = ""
# Paste the full value for arena-auth-prod-v1.1 here.



# If EXTENSIONS=True you MUST set EXTENSIONS_DIR to the Extensions folder of
# the browser profile you want to load extensions from.
# Leave as "" only if EXTENSIONS=False.
#
# How to find it:
#   Open your browser → go to:  chrome://version  (or brave://version)
#   Look for "Profile Path" — your Extensions folder is inside that path.
#   Example:  Profile Path = /home/user/.config/BraveSoftware/.../Default
#             Extensions dir = /home/user/.config/BraveSoftware/.../Default/Extensions
#
#   Linux Brave ........ "/root/.config/BraveSoftware/Brave-Browser/Default/Extensions"
#   Linux Chrome ....... "/home/USERNAME/.config/google-chrome/Default/Extensions"
#   Linux Chromium ..... "/home/USERNAME/.config/chromium/Default/Extensions"
#
#   Windows Brave ...... r"C:\Users\USERNAME\AppData\Local\BraveSoftware\Brave-Browser\User Data\Default\Extensions"
#   Windows Chrome ..... r"C:\Users\USERNAME\AppData\Local\Google\Chrome\User Data\Default\Extensions"
#   Windows Edge ....... r"C:\Users\USERNAME\AppData\Local\Microsoft\Edge\User Data\Default\Extensions"
#
#   macOS Brave ........ "/Users/USERNAME/Library/Application Support/BraveSoftware/Brave-Browser/Default/Extensions"
#   macOS Chrome ....... "/Users/USERNAME/Library/Application Support/Google/Chrome/Default/Extensions"
#   macOS Chromium ..... "/Users/USERNAME/Library/Application Support/Chromium/Default/Extensions"


# If CUS_PROFILE=True, ALL contexts/windows use PROFILE_PATH as their
# user_data_dir instead of the auto-generated harvester_profiles/ dirs.
#
#   Linux Brave ........ "/root/.config/BraveSoftware/Brave-Browser"
#   Linux Chrome ....... "/home/USERNAME/.config/google-chrome"
#   Windows Brave ...... r"C:\Users\USERNAME\AppData\Local\BraveSoftware\Brave-Browser\User Data"
#   Windows Chrome ..... r"C:\Users\USERNAME\AppData\Local\Google\Chrome\User Data"
#   macOS Brave ........ "/Users/USERNAME/Library/Application Support/BraveSoftware/Brave-Browser"
#   macOS Chrome ....... "/Users/USERNAME/Library/Application Support/Google/Chrome"


# When AUTO_LOGIN=True and COOKIES=False, the harvester will automatically
# sign in to arena.ai for each browser window/tab using credentials you enter
# in the terminal at startup.
#
# Login flow per window:
#   1. Navigate to arena.ai
#   2. Run initial v2 script
#   3. POST to /nextjs-api/sign-in/email with email + password
#   4. Extract arena-auth-prod-v1.0 and arena-auth-prod-v1.1 from response
#      headers and persist them to config.json (auth_prod / auth_prod_v2)
#   5. Reload page, run blocker script, continue as normal
#
# After ready: cf_clearance and __cf_bm cookies from the browser context are
# also written to config.json automatically.
#
# NOTE: AUTO_LOGIN=True is incompatible with COOKIES=True — an error is raised
#       at startup if both are enabled simultaneously.



PROFILES_DIR = Path("harvester_profiles")

# ── Startup validation ─────────────────────────────────────────
if CUS_PROFILE:
    if not PROFILE_PATH or not PROFILE_PATH.strip():
        raise RuntimeError(
            "CUS_PROFILE=True but PROFILE_PATH is empty.\n"
            "Set PROFILE_PATH to your browser's user data directory.\n"
            "See the config comments above for examples per OS."
        )
    _profile_path_obj = Path(PROFILE_PATH.strip())
    if not _profile_path_obj.exists():
        raise RuntimeError(
            f"CUS_PROFILE=True but PROFILE_PATH does not exist: {_profile_path_obj}\n"
            "Check the path. It should be the User Data dir, not the browser exe.\n"
            "Example (Brave Linux): /root/.config/BraveSoftware/Brave-Browser"
        )
    if not _profile_path_obj.is_dir():
        raise RuntimeError(f"PROFILE_PATH is not a directory: {_profile_path_obj}")
    print(f"[profile] Using custom profile: {_profile_path_obj}")

if COOKIES:
    if not COOKIE_V1 or not COOKIE_V1.strip():
        raise RuntimeError(
            "COOKIES=True but COOKIE_V1 is empty.\n"
            "Set COOKIE_V1 to the value for the arena-auth-prod-v1.0 cookie."
        )
    if not COOKIE_V2 or not COOKIE_V2.strip():
        raise RuntimeError(
            "COOKIES=True but COOKIE_V2 is empty.\n"
            "Set COOKIE_V2 to the value for the new arena-auth-prod-v1.1 cookie."
        )

if AUTO_LOGIN and COOKIES:
    raise RuntimeError(
        "AUTO_LOGIN=True and COOKIES=True cannot be used together.\n"
        "AUTO_LOGIN manages auth cookies itself via the sign-in API.\n"
        "Set either AUTO_LOGIN=False (to use manual cookies) or COOKIES=False (to use auto-login)."
    )

if FIVE_GAIN and not AUTO_LOGIN:
    raise RuntimeError(
        "FIVE_GAIN=True requires AUTO_LOGIN=True.\n"
        "Enable AUTO_LOGIN or set FIVE_GAIN=False."
    )

if HARD_TUNING and not TUNING:
    raise RuntimeError(
        "HARD_TUNING=True requires TUNING=True.\n"
        "HARD_TUNING is an enhancement of TUNING — enable TUNING first."
    )

if HARD_TUNING and CUS_PROFILE:
    raise RuntimeError(
        "HARD_TUNING=True is incompatible with CUS_PROFILE=True.\n"
        "HARD_TUNING wipes and rebuilds the profile directory on each cycle.\n"
        "Using your real browser profile would destroy it. Set CUS_PROFILE=False."
    )

# ── Collect AUTO_LOGIN credentials once at startup (before browser launch) ────
_AUTO_LOGIN_EMAIL    = ""
_AUTO_LOGIN_PASSWORD = ""
_EVAL_ID             = ""   # populated when FIVE_GAIN=True

if AUTO_LOGIN:
    print("\n" + "=" * 55)
    print("  AUTO_LOGIN enabled — enter arena.ai credentials")
    print("  These will be used to sign in each browser window.")
    print("=" * 55)
    _AUTO_LOGIN_EMAIL    = input("  Email    : ").strip()
    _AUTO_LOGIN_PASSWORD = input("  Password : ").strip()
    if not _AUTO_LOGIN_EMAIL or not _AUTO_LOGIN_PASSWORD:
        raise RuntimeError("AUTO_LOGIN=True but email or password was left blank.")

if FIVE_GAIN:
    print("\n" + "=" * 55)
    print("  5_GAIN enabled — enter the arena.ai eval/conversation ID")
    print("  Windows will navigate to https://arena.ai/c/<eval_id>")
    print("=" * 55)
    _EVAL_ID = input("  Eval ID  : ").strip()
    if not _EVAL_ID:
        raise RuntimeError("FIVE_GAIN=True but eval_id was left blank.")
    print(f"  Target URL: https://arena.ai/c/{_EVAL_ID}")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ──────────────────────────────────────────────
_windows: dict[int, dict] = {}
_tokens_lock = asyncio.Lock()  # guards tokens.json file I/O

# ── Token file helpers ────────────────────────────────────────

def _load_tokens_file() -> dict:
    """Load tokens.json from disk, or return a fresh structure."""
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"tokens": [], "total_count": 0, "last_updated": ""}


def _save_tokens_file(tokens_data: dict) -> None:
    """Write tokens_data to tokens.json atomically via a temp file."""
    tmp = TOKENS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tokens_data, f, indent=2)
    os.replace(tmp, TOKENS_FILE)


def _append_token(data: dict) -> int:
    """
    Append a new token entry to tokens.json in the format expected by modula.py.
    Returns the new total count.
    Must be called inside _tokens_lock.
    """
    tokens_data = _load_tokens_file()

    now_utc    = datetime.utcnow()
    raw_token  = data.get("token", "")

    entry = {
        "token":           raw_token,
        "version":         data.get("version", "v3"),
        "action":          data.get("action", ""),
        "source_url":      data.get("source_url", ""),
        "window_id":       data.get("window_id", -1),
        "harvest_number":  data.get("harvest_number", 0),
        "timestamp_utc":   now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timestamp_local": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "token_preview":   (raw_token[:40] + "...") if len(raw_token) > 40 else raw_token,
    }

    tokens_data["tokens"].append(entry)
    tokens_data["total_count"]  = len(tokens_data["tokens"])
    tokens_data["last_updated"] = entry["timestamp_utc"]

    _save_tokens_file(tokens_data)
    return tokens_data["total_count"]


# ── Injected scripts ──────────────────────────────────────────

INITIAL_V2_SCRIPT = """
(function() {
  'use strict';

  const CONFIG = {
    SITE_KEY: '6Led_uYrAAAAAKjxDIF58fgFtX3t8loNAK85bW9I',
    TIMEOUT: 60000
  };

  console.log('🎯 INITIAL v2 reCAPTCHA Token Generator');

  async function getV2Token() {
    const w = window.wrappedJSObject || window;
    await waitForGrecaptcha(w);
    const g = w.grecaptcha?.enterprise;
    if (!g || typeof g.render !== 'function') throw new Error('NO_GRECAPTCHA_V2');

    let settled = false;
    const done = (fn, arg) => { if (settled) return; settled = true; fn(arg); };

    return new Promise((resolve, reject) => {
      try {
        const el = w.document.createElement('div');
        el.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;';
        w.document.body.appendChild(el);

        const timer = w.setTimeout(() => done(reject, 'V2_TIMEOUT'), CONFIG.TIMEOUT);

        const wid = g.render(el, {
          sitekey: CONFIG.SITE_KEY,
          size: 'invisible',
          callback: (tok) => {
            w.clearTimeout(timer);
            done(resolve, tok);
          },
          'error-callback': () => {
            w.clearTimeout(timer);
            done(reject, 'V2_ERROR');
          }
        });

        if (typeof g.execute === 'function') g.execute(wid);
      } catch (e) {
        done(reject, String(e));
      }
    });
  }

  async function waitForGrecaptcha(w) {
    const start = Date.now();
    while (Date.now() - start < 60000) {
      if (w.grecaptcha?.enterprise?.render) return true;
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error('Timeout waiting for grecaptcha');
  }

  (async function() {
    try {
      const token = await getV2Token();
      console.log('✅ INITIAL token received, length:', token.length);
      fetch('http://localhost:5000/api', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token,
          version: 'v2_initial',
          action: 'initial_page_load',
          source_url: window.location.href
        })
      }).catch(err => console.log('Store failed:', err));
    } catch (error) {
      console.error('❌ Initial token failed:', error);
    }
  })();
})();
"""

BLOCKER_SCRIPT = """
(function() {
    console.log('🔧 Installing forceLowRecaptchaScore blocker...');

    const originalFetch = window.fetch;
    window.fetch = function(...args) {
        let [url, options = {}] = args;

        if (options.body && typeof options.body === 'string') {
            try {
                const body = JSON.parse(options.body);

                const deepClean = (obj) => {
                    if (!obj || typeof obj !== 'object') return obj;
                    if (Array.isArray(obj)) return obj.map(deepClean);
                    const cleaned = {};
                    for (const [key, value] of Object.entries(obj)) {
                        if (key === 'forceLowRecaptchaScore') {
                            console.log('🚫 REMOVED forceLowRecaptchaScore');
                            continue;
                        }
                        cleaned[key] = deepClean(value);
                    }
                    return cleaned;
                };

                options = { ...options, body: JSON.stringify(deepClean(body)) };
                args[1] = options;
            } catch (e) {}
        }

        return originalFetch.apply(this, args);
    };

    console.log('✅ Blocker installed!');
})();
"""

ON_DEMAND_V2_SCRIPT = """
(function() {
  'use strict';

  const CONFIG = {
    SITE_KEY: '6Led_uYrAAAAAKjxDIF58fgFtX3t8loNAK85bW9I',
    TIMEOUT: 60000
  };

  async function getV2Token() {
    const w = window.wrappedJSObject || window;

    if (!w.grecaptcha?.enterprise) {
      await loadRecaptchaScript(w);
    }
    await waitForGrecaptcha(w);

    const g = w.grecaptcha.enterprise;
    let settled = false;
    const done = (fn, arg) => { if (settled) return; settled = true; fn(arg); };

    return new Promise((resolve, reject) => {
      try {
        const el = w.document.createElement('div');
        el.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;';
        w.document.body.appendChild(el);

        const timer = w.setTimeout(() => done(reject, 'V2_TIMEOUT'), CONFIG.TIMEOUT);

        const wid = g.render(el, {
          sitekey: CONFIG.SITE_KEY,
          size: 'invisible',
          callback: (tok) => { w.clearTimeout(timer); done(resolve, tok); },
          'error-callback': () => { w.clearTimeout(timer); done(reject, 'V2_ERROR'); }
        });

        if (typeof g.execute === 'function') g.execute(wid);
      } catch (e) {
        done(reject, String(e));
      }
    });
  }

  async function loadRecaptchaScript(w) {
    return new Promise((resolve, reject) => {
      if (w.document.querySelector('script[src*="recaptcha/enterprise.js"]')) { resolve(); return; }
      const script = w.document.createElement('script');
      script.src = 'https://www.google.com/recaptcha/enterprise.js?render=' + CONFIG.SITE_KEY;
      script.async = true;
      script.defer = true;
      script.onload = resolve;
      script.onerror = reject;
      w.document.head.appendChild(script);
    });
  }

  async function waitForGrecaptcha(w) {
    const start = Date.now();
    while (Date.now() - start < 30000) {
      if (w.grecaptcha?.enterprise?.render) return true;
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error('Timeout');
  }

  (async function() {
    try {
      const token = await getV2Token();
      console.log('✅ ON-DEMAND token received');
      fetch('http://localhost:5000/api', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token,
          version: 'v2_ondemand',
          action: 'manual_trigger',
          source_url: window.location.href
        })
      }).then(r => r.json()).then(data => {
        console.log('📤 Stored. Total:', data.total_count);
      }).catch(err => console.log('Store failed:', err));

      return token;
    } catch (error) {
      console.error('❌ On-demand token failed:', error);
    }
  })();
})();
"""

V2_SCRIPT = r"""
(() => {
    const SERVER_URL   = "http://localhost:5000/api";
    const V2_SITEKEY   = "6Ld7ePYrAAAAAB34ovoFoDau1fqCJ6IyOjFEQaMn";
    const FORCE_MODE   = "checkbox";
    const INV_MIN_INTERVAL = 80;
    const INV_MAX_INTERVAL = 100;
    const INV_RETRY    = 15;

    let v2Count = 0;
    let invisibleErrors = 0;
    let currentMode = FORCE_MODE === "auto" ? "invisible" : FORCE_MODE;
    let currentTimeoutId = null;
    let widgetCounter = 0;
    let panelCreated = false;

    function getRandomInterval(min, max) {
        const arr = new Uint32Array(1);
        crypto.getRandomValues(arr);
        return min + (arr[0] / (0xFFFFFFFF + 1)) * (max - min);
    }

    function sendToken(token, mode) {
        v2Count++;
        invisibleErrors = 0;
        console.log(`[v2-${mode} #${v2Count}] Token generated (${token.length} chars)`);
        updateCount();
        if (panelCreated) updateStatus(`Token #${v2Count} captured! Sending...`);
        return fetch(SERVER_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                token,
                version: "v2",
                action: mode === "invisible" ? "invisible_auto" : "checkbox_challenge",
                harvest_number: v2Count,
                source_url: window.location.href,
                _reload_after: true
            })
        }).then(r => r.json()).then(data => {
            console.log(`[v2-${mode} #${v2Count}] Stored. Total: ${data.total_count}`);
            if (panelCreated) updateStatus(`Token #${v2Count} stored! Reloading...`);
        }).catch(err => console.error(`[v2-${mode} #${v2Count}] Store failed:`, err));
    }

    function harvestInvisible() {
        const g = window.grecaptcha?.enterprise;
        if (!g || typeof g.render !== 'function') {
            currentTimeoutId = setTimeout(harvestInvisible, 2000);
            return;
        }
        widgetCounter++;
        const el = document.createElement('div');
        el.id = `__v2_inv_widget_${widgetCounter}`;
        el.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
        document.body.appendChild(el);
        let settled = false;
        const timer = setTimeout(() => {
            if (!settled) { settled = true; el.remove(); handleInvisibleFailure(); }
        }, 60000);
        try {
            const wid = g.render(el, {
                sitekey: V2_SITEKEY,
                size: 'invisible',
                callback: (token) => {
                    if (settled) return;
                    settled = true; clearTimeout(timer); el.remove();
                    sendToken(token, "invisible").then(() => {
                        const next = getRandomInterval(INV_MIN_INTERVAL, INV_MAX_INTERVAL);
                        currentTimeoutId = setTimeout(harvestInvisible, next * 1000);
                    });
                },
                'error-callback': () => {
                    if (settled) return;
                    settled = true; clearTimeout(timer); el.remove(); handleInvisibleFailure();
                }
            });
            if (typeof g.execute === 'function') g.execute(wid);
        } catch (e) {
            if (!settled) { settled = true; clearTimeout(timer); el.remove(); handleInvisibleFailure(); }
        }
    }

    function handleInvisibleFailure() {
        invisibleErrors++;
        if (FORCE_MODE === "invisible") {
            const backoff = Math.min(INV_RETRY * Math.pow(1.5, invisibleErrors - 1), 300);
            currentTimeoutId = setTimeout(harvestInvisible, backoff * 1000);
        } else if (FORCE_MODE === "auto" && invisibleErrors >= 2) {
            currentMode = "checkbox"; startCheckboxMode();
        } else {
            const backoff = Math.min(INV_RETRY * Math.pow(1.5, invisibleErrors - 1), 60);
            currentTimeoutId = setTimeout(harvestInvisible, backoff * 1000);
        }
    }

    function createPanel() {
        if (panelCreated) return;
        panelCreated = true;
        let panel = document.getElementById('__v2_harvest_panel');
        if (panel) return;
        panel = document.createElement('div');
        panel.id = '__v2_harvest_panel';
        panel.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:999999;background:#1a1a2e;border:2px solid #16213e;border-radius:12px;padding:12px 16px;box-shadow:0 4px 20px rgba(0,0,0,0.4);font-family:system-ui,sans-serif;min-width:320px;';
        const header = document.createElement('div');
        header.style.cssText = 'color:#e0e0e0;font-size:13px;margin-bottom:8px;font-weight:600;';
        header.innerHTML = 'v2 Harvester (checkbox) <span id="__v2_count" style="color:#4ade80;float:right;">0 tokens</span>';
        panel.appendChild(header);
        const status = document.createElement('div');
        status.id = '__v2_status';
        status.style.cssText = 'color:#9ca3af;font-size:11px;margin-bottom:10px;';
        status.textContent = 'Click the checkbox below to harvest a v2 token';
        panel.appendChild(status);
        const container = document.createElement('div');
        container.id = '__v2_checkbox_container';
        container.style.cssText = 'display:flex;justify-content:center;';
        panel.appendChild(container);
        const closeBtn = document.createElement('div');
        closeBtn.style.cssText = 'color:#6b7280;font-size:11px;margin-top:8px;cursor:pointer;text-align:center;';
        closeBtn.textContent = 'stop: window.__STOP_V2_HARVEST__()';
        closeBtn.onclick = () => window.__STOP_V2_HARVEST__();
        panel.appendChild(closeBtn);
        document.body.appendChild(panel);
    }

    function updateStatus(msg) { const el = document.getElementById('__v2_status'); if (el) el.textContent = msg; }
    function updateCount()  { const el = document.getElementById('__v2_count');  if (el) el.textContent = `${v2Count} token${v2Count !== 1 ? 's' : ''}`; }

    function startCheckboxMode() { createPanel(); renderCheckbox(); }

    function renderCheckbox() {
        const g = window.grecaptcha?.enterprise;
        if (!g || typeof g.render !== 'function') {
            updateStatus('Waiting for grecaptcha.enterprise...');
            setTimeout(renderCheckbox, 1000);
            return;
        }
        const panel = document.getElementById('__v2_harvest_panel');
        if (!panel) return;
        const old = document.getElementById('__v2_checkbox_container');
        if (old) old.remove();
        const container = document.createElement('div');
        container.id = '__v2_checkbox_container';
        container.style.cssText = 'display:flex;justify-content:center;';
        panel.insertBefore(container, panel.lastElementChild);
        updateStatus('Click the checkbox below to harvest a v2 token');
        const timeout = setTimeout(() => { updateStatus('Widget expired. Rendering fresh...'); renderCheckbox(); }, 60000);
        try {
            g.render(container, {
                sitekey: V2_SITEKEY,
                callback: (token) => {
                    clearTimeout(timeout);
                    sendToken(token, "checkbox").then(() => {
                        // page will reload via server-side trigger; no need to re-render
                    });
                },
                'error-callback':   () => { clearTimeout(timeout); updateStatus('Challenge failed. New widget in 5s...');  setTimeout(renderCheckbox, 5000); },
                'expired-callback': () => { clearTimeout(timeout); updateStatus('Token expired. New widget in 3s...');     setTimeout(renderCheckbox, 3000); },
                theme: document.documentElement.classList.contains('dark') ? 'dark' : 'light',
            });
        } catch (e) {
            clearTimeout(timeout);
            updateStatus(`Error: ${e.message}. Retry in 10s...`);
            setTimeout(renderCheckbox, 10000);
        }
    }

    window.__STOP_V2_HARVEST__ = () => {
        if (currentTimeoutId) { clearTimeout(currentTimeoutId); currentTimeoutId = null; }
        const panel = document.getElementById('__v2_harvest_panel');
        if (panel) panel.remove();
        panelCreated = false;
        console.log(`[v2] Stopped. Tokens: ${v2Count}`);
    };
    window.__V2_SWITCH_INVISIBLE__ = () => { window.__STOP_V2_HARVEST__(); currentMode = "invisible"; invisibleErrors = 0; harvestInvisible(); };
    window.__V2_SWITCH_CHECKBOX__  = () => { window.__STOP_V2_HARVEST__(); currentMode = "checkbox";  startCheckboxMode(); };

    console.log(`v2 Harvester started (mode: ${FORCE_MODE})`);
    if (FORCE_MODE === "checkbox") {
        currentMode = "checkbox";
        if (window.grecaptcha?.enterprise?.ready) { window.grecaptcha.enterprise.ready(() => startCheckboxMode()); }
        else { startCheckboxMode(); }
    } else {
        currentMode = "invisible";
        if (window.grecaptcha?.enterprise?.ready) { window.grecaptcha.enterprise.ready(() => harvestInvisible()); }
        else { harvestInvisible(); }
    }
})();
"""

V3_SCRIPT = r"""
(() => {
    const SERVER_URL   = "http://localhost:5000/api";
    const SITE_KEY     = "6Led_uYrAAAAAKjxDIF58fgFtX3t8loNAK85bW9I";
    const ACTION       = "chat_submit";
    const MIN_INTERVAL = 12;
    const MAX_INTERVAL = 18;

    let tokenCount = 0;
    let currentTimeoutId = null;

    function getRandomInterval() {
        const arr = new Uint32Array(1);
        crypto.getRandomValues(arr);
        return MIN_INTERVAL + (arr[0] / (0xFFFFFFFF + 1)) * (MAX_INTERVAL - MIN_INTERVAL);
    }

    function harvest() {
        grecaptcha.enterprise.ready(() => {
            grecaptcha.enterprise.execute(SITE_KEY, { action: ACTION })
                .then(token => {
                    tokenCount++;
                    console.log(`[v3 #${tokenCount}] Token generated (${token.length} chars)`);
                    return fetch(SERVER_URL, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            token,
                            version: "v3",
                            action: ACTION,
                            harvest_number: tokenCount,
                            source_url: window.location.href,
                            _reload_after: true
                        })
                    }).then(res => res.json()).then(data => {
                        console.log(`[v3 #${tokenCount}] Stored. Total: ${data.total_count}`);
                        window.__RECAPTCHA_TOKEN__ = token;
                        // page reloads server-side; scheduleNext only needed if reload doesn't happen
                        scheduleNext();
                    });
                }).catch(err => { console.error("[v3] Error:", err); scheduleNext(); });
        });
    }

    function scheduleNext() {
        const next = getRandomInterval();
        console.log(`[v3] Next harvest in ${next.toFixed(2)}s`);
        currentTimeoutId = setTimeout(harvest, next * 1000);
    }

    window.__STOP_HARVEST__ = () => {
        if (currentTimeoutId) { clearTimeout(currentTimeoutId); currentTimeoutId = null; }
        console.log("[v3] Stopped. Total captured:", tokenCount);
    };

    console.log(`v3 Auto-harvester started (${MIN_INTERVAL}-${MAX_INTERVAL}s interval)`);
    harvest();
})();
"""

READY_SIGNAL_SCRIPT = """
async (windowId) => {
    try {
        await fetch('http://localhost:5000/windows/' + windowId + '/ready', { method: 'POST' });
        console.log('[harvester] Marked ready, window ' + windowId);
    } catch(e) {
        console.warn('[harvester] Ready signal failed:', e);
    }
}
"""

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver',  { get: () => undefined });
Object.defineProperty(navigator, 'plugins',    { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages',  { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {} };
"""

# ── Dashboard HTML ─────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>reCAPTCHA Harvester</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f1a; color: #e0e0e0; font-family: system-ui, -apple-system, sans-serif; padding: 24px; min-height: 100vh; }
  h1 { font-size: 20px; font-weight: 700; color: #fff; margin-bottom: 4px; }
  .subtitle { color: #6b7280; font-size: 13px; margin-bottom: 24px; }
  .stats { display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
  .stat { background: #1a1a2e; border: 1px solid #2d2d4e; border-radius: 10px; padding: 14px 20px; flex: 1; min-width: 110px; }
  .stat-label { font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; }
  .stat-value { font-size: 28px; font-weight: 700; color: #4ade80; margin-top: 4px; }
  .stat-value.blue   { color: #60a5fa; }
  .stat-value.purple { color: #c084fc; }
  .stat-value.orange { color: #fb923c; }
  .file-note { background: #111827; border: 1px solid #1f2937; border-radius: 8px; padding: 10px 16px; margin-bottom: 20px; font-size: 12px; color: #6b7280; }
  .file-note span { color: #4ade80; font-family: monospace; font-weight: 600; }
  .windows { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; }
  .window-card { background: #1a1a2e; border: 1px solid #2d2d4e; border-radius: 12px; padding: 18px; }
  .window-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
  .window-title { font-size: 15px; font-weight: 600; }
  .badge { font-size: 11px; padding: 3px 10px; border-radius: 20px; font-weight: 600; }
  .badge.loading       { background: #1c2a1c; color: #6b7280;  border: 1px solid #374151; }
  .badge.ready         { background: #1c2a1c; color: #4ade80;  border: 1px solid #166534; }
  .badge.idle          { background: #1c1c2a; color: #9ca3af;  border: 1px solid #374151; }
  .badge.harvesting_v2 { background: #2a1c1c; color: #f87171;  border: 1px solid #991b1b; }
  .badge.harvesting_v3 { background: #1c1c2a; color: #60a5fa;  border: 1px solid #1d4ed8; }
  .badge.reloading     { background: #2a2a1c; color: #facc15;  border: 1px solid #854d0e; }
  .btn-row { display: flex; gap: 8px; flex-wrap: wrap; }
  .btn { padding: 7px 14px; border: none; border-radius: 7px; cursor: pointer; font-size: 12px; font-weight: 600; transition: opacity 0.15s, transform 0.1s; }
  .btn:hover   { opacity: 0.85; transform: translateY(-1px); }
  .btn:active  { transform: translateY(0); }
  .btn:disabled { opacity: 0.35; cursor: not-allowed; transform: none; }
  .btn.v2-start  { background: #dc2626; color: #fff; }
  .btn.v2-stop   { background: #374151; color: #f87171; }
  .btn.v3-start  { background: #1d4ed8; color: #fff; }
  .btn.v3-stop   { background: #374151; color: #60a5fa; }
  .btn.inv-run   { background: #8b5cf6; color: #fff; width: 100%; margin-top: 4px; }
  .window-info { font-size: 11px; color: #4b5563; margin-top: 10px; }
  .danger-row { margin-top: 24px; display: flex; justify-content: flex-end; gap: 12px; }
  .btn-warn   { padding: 8px 18px; border: 1px solid #78350f; background: #1a100a; color: #fb923c; border-radius: 7px; cursor: pointer; font-size: 12px; font-weight: 600; transition: background 0.15s; }
  .btn-warn:hover   { background: #78350f; color: #fff; }
  .btn-danger { padding: 8px 18px; border: 1px solid #7f1d1d; background: #1a0a0a; color: #f87171; border-radius: 7px; cursor: pointer; font-size: 12px; font-weight: 600; transition: background 0.15s; }
  .btn-danger:hover { background: #7f1d1d; color: #fff; }
  .refresh-info { text-align: right; color: #374151; font-size: 11px; margin-top: 20px; }
  .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #1a1a2e; border: 1px solid #4ade80; color: #4ade80; padding: 10px 20px; border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 9999; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<h1>reCAPTCHA Harvester</h1>
<p class="subtitle">Token harvesting dashboard — auto-refreshes every 3s</p>

<div class="file-note">
  💾 Tokens persist to <span>tokens.json</span> — directly compatible with modula.py / main.py / arena_client.py
</div>

<div class="stats">
  <div class="stat"><div class="stat-label">Total Tokens</div><div class="stat-value"         id="stat-total">0</div></div>
  <div class="stat"><div class="stat-label">v2 Tokens</div>   <div class="stat-value purple"  id="stat-v2">0</div></div>
  <div class="stat"><div class="stat-label">v3 Tokens</div>   <div class="stat-value blue"    id="stat-v3">0</div></div>
  <div class="stat"><div class="stat-label">Fresh (&lt;2min)</div><div class="stat-value orange" id="stat-fresh">0</div></div>
  <div class="stat"><div class="stat-label">Windows Ready</div><div class="stat-value"        id="stat-ready">0</div></div>
</div>

<div class="windows" id="windows-container">
  <p style="color:#6b7280;font-size:13px;">Loading windows...</p>
</div>

<div class="danger-row">
  <button class="btn-warn"   onclick="clearTokens()">🗑 Clear tokens.json</button>
  <button class="btn-danger" onclick="deleteProfiles()">🗑 Delete All Profiles</button>
</div>
<div class="refresh-info" id="refresh-info">Last refresh: —</div>
<div class="toast" id="toast"></div>

<script>
function showToast(msg, color) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.borderColor = color || '#4ade80';
  t.style.color = color || '#4ade80';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

async function apiCall(path, method = 'POST') {
  try {
    const r = await fetch(path, { method });
    return await r.json();
  } catch (e) {
    showToast('Error: ' + e.message, '#f87171');
    return null;
  }
}

let _tabsMode = false;

function renderWindows(windows) {
  const container = document.getElementById('windows-container');
  if (!windows.length) {
    container.innerHTML = '<p style="color:#6b7280;font-size:13px;">No windows yet...</p>';
    return;
  }
  const label = _tabsMode ? 'Tab' : 'Window';
  container.innerHTML = windows.map(w => {
    const bc      = w.status || 'loading';
    const bt      = bc.replace(/_/g, ' ').toUpperCase();
    const isReady = w.status !== 'loading';
    const dis     = !isReady ? 'disabled' : '';
    return `
    <div class="window-card">
      <div class="window-header">
        <span class="window-title">${label} ${w.id}</span>
        <span class="badge ${bc}">${bt}</span>
      </div>
      <div class="btn-row">
        <button class="btn v2-start" onclick="v2Start(${w.id})" ${dis}>V2 Start</button>
        <button class="btn v2-stop"  onclick="v2Stop(${w.id})"  ${dis}>V2 Stop</button>
        <button class="btn v3-start" onclick="v3Start(${w.id})" ${dis}>V3 Start</button>
        <button class="btn v3-stop"  onclick="v3Stop(${w.id})"  ${dis}>V3 Stop</button>
      </div>
      <div class="btn-row">
        <button class="btn inv-run" onclick="runInvisible(${w.id})" ${dis}>🎯 Run Invisible Script</button>
      </div>
      <div class="window-info">
        Profile: harvester_profiles/${label.toLowerCase()}_${w.id}
        &nbsp;|&nbsp; Session tokens: ${w.token_count || 0}
      </div>
    </div>`;
  }).join('');
}

async function refresh() {
  try {
    const [status, tokData] = await Promise.all([
      fetch('/status').then(r => r.json()),
      fetch('/api/tokens').then(r => r.json()),
    ]);
    const windows = status.windows || [];
    _tabsMode = !!status.tabs_mode;
    const all   = tokData.tokens || [];
    const now   = Date.now();
    const v2    = all.filter(t => (t.version || '').includes('v2')).length;
    const v3    = all.filter(t => t.version === 'v3').length;
    const fresh = all.filter(t => {
      try { return (now - new Date(t.timestamp_utc).getTime()) / 1000 < 120; } catch { return false; }
    }).length;
    const ready = windows.filter(w => w.status !== 'loading' && w.status !== 'reloading').length;

    document.getElementById('stat-total').textContent = all.length;
    document.getElementById('stat-v2').textContent    = v2;
    document.getElementById('stat-v3').textContent    = v3;
    document.getElementById('stat-fresh').textContent = fresh;
    document.getElementById('stat-ready').textContent = `${ready}/${windows.length}`;

    const byWin = {};
    for (const t of all) byWin[t.window_id] = (byWin[t.window_id] || 0) + 1;
    for (const w of windows) w.token_count = byWin[w.id] || 0;

    renderWindows(windows);
    document.getElementById('refresh-info').textContent = 'Last refresh: ' + new Date().toLocaleTimeString();
  } catch (e) {}
}

async function v2Start(id)    { const d = await apiCall(`/windows/${id}/v2/start`);       if (d) showToast(`Window ${id}: V2 started`); await refresh(); }
async function v2Stop(id)     { const d = await apiCall(`/windows/${id}/v2/stop`);        if (d) showToast(`Window ${id}: V2 stopped`, '#f87171'); await refresh(); }
async function v3Start(id)    { const d = await apiCall(`/windows/${id}/v3/start`);       if (d) showToast(`Window ${id}: V3 started`, '#60a5fa'); await refresh(); }
async function v3Stop(id)     { const d = await apiCall(`/windows/${id}/v3/stop`);        if (d) showToast(`Window ${id}: V3 stopped`, '#6b7280'); await refresh(); }
async function runInvisible(id){ const d = await apiCall(`/windows/${id}/invisible/run`); if (d) showToast(`Window ${id}: Invisible triggered`, '#8b5cf6'); await refresh(); }

async function clearTokens() {
  if (!confirm('Clear all tokens from tokens.json?\\nThis cannot be undone.')) return;
  const d = await apiCall('/tokens/clear', 'DELETE');
  if (d && d.ok) showToast(`Cleared ${d.removed} token(s)`, '#fb923c');
  else if (d) showToast('Error: ' + (d.detail || 'unknown'), '#f87171');
  await refresh();
}

async function deleteProfiles() {
  if (!confirm('Delete ALL harvester_profiles? Browsers must be restarted after.')) return;
  const d = await apiCall('/profiles/delete', 'DELETE');
  if (d && d.ok) showToast(`Deleted ${d.deleted} profile(s)`, '#f87171');
  else if (d) showToast('Error: ' + (d.detail || 'unknown'), '#f87171');
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""

# ── FastAPI routes ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/status")
async def get_status():
    windows = [{"id": wid, "status": w.get("status", "loading")} for wid, w in _windows.items()]
    return {"windows": windows, "tabs_mode": TABS}


@app.post("/api")
async def store_token(request: Request):
    """Receive a harvested token and persist it directly to tokens.json."""
    data = await request.json()
    async with _tokens_lock:
        total = _append_token(data)
    version = data.get("version", "v3")
    action  = data.get("action", "")
    token   = data.get("token", "")
    preview = (token[:40] + "...") if len(token) > 40 else token
    print(f"[token] {version:<14} {action:<22} {preview}  (total on disk: {total})")

    # ── Auto-reload: triggered for v2 and v3 harvester tokens only ──────────
    # Excluded: v2_initial (initial page load) and v2_ondemand (invisible/manual).
    # Included: version == "v2" (checkbox/invisible harvester) or "v3".
    # Gated by TUNING=True — set False to disable all post-token reloads.
    should_reload = TUNING and data.get("_reload_after", False) and version in ("v2", "v3")
    if should_reload:
        window_id = data.get("window_id", -1)
        # Find the window by id or fall back to scanning all windows
        target_wid = None
        for wid, w in _windows.items():
            if w.get("id") == window_id or window_id == -1:
                target_wid = wid
                if window_id != -1:
                    break
        if target_wid is not None and _windows[target_wid].get("status") not in ("loading", "reloading"):
            asyncio.create_task(_reload_window_after_token(target_wid, version))

    return {"total_count": total, "ok": True}


async def _reload_window_after_token(window_id: int, version: str):
    """
    Reload a window/tab after a v2 or v3 token is harvested, then
    re-run the full ready flow (blocker, ready signal, re-inject harvester).

    TUNING=True (standard):  page.reload() — fast, keeps existing profile.
    HARD_TUNING=True:        full profile wipe + fresh context rebuild each cycle.
      Steps:
        1. Save 4 cookies from the live context into RAM:
               arena-auth-prod-v1.0, arena-auth-prod-v1.1, __cf_bm, cf_clearance
        2. Close the browser context (releases file locks on profile dir)
        3. Delete the profile directory from disk entirely
        4. Launch a brand-new persistent context at the same profile path
        5. Re-inject the 4 saved cookies into the fresh context
        6. Navigate to arena.ai  (or arena.ai/c/<eval_id> if FIVE_GAIN=True)
        7. Blocker → ready signal → re-inject active harvester script
    """
    label = "tab" if TABS else "window"
    w = _windows.get(window_id)
    if not w:
        return

    page: Page    = w.get("page")
    context       = w.get("context")
    prev_status   = w.get("status", "ready")

    # If stop was clicked while token POST was in-flight — bail out.
    active_script  = w.get("active_script")
    active_version = w.get("active_version")
    if not active_script or not active_version:
        print(f"[{label} {window_id}] Harvester was stopped — skipping reload.")
        return

    if not page or not context:
        return

    # ── HARD_TUNING path ──────────────────────────────────────────────────────
    if HARD_TUNING:
        print(f"[{label} {window_id}] 💥 HARD_TUNING: clearing cookies/localStorage/fingerprint, keeping profile...")
        _windows[window_id]["status"] = "reloading"

        # ── 1. Save the 4 essential cookies to RAM ────────────────────────────
        HARD_COOKIE_NAMES = {
            "arena-auth-prod-v1.0",
            "arena-auth-prod-v1.1",
            "__cf_bm",
            "cf_clearance",
        }
        saved_cookies: list[dict] = []
        try:
            all_cookies = await context.cookies(["https://arena.ai"])
            saved_cookies = [c for c in all_cookies if c.get("name") in HARD_COOKIE_NAMES]
            for c in saved_cookies:
                print(f"[{label} {window_id}]   💾 saved cookie: {c['name']} ({len(c.get('value',''))} chars)")
        except Exception as e:
            print(f"[{label} {window_id}]   ⚠ Cookie save error: {e}")

        # ── 2. Clear cookies and localStorage via JavaScript (no context close) ─
        try:
            await page.evaluate("""
                () => {
                    // Clear all cookies
                    const cookies = document.cookie.split(';');
                    for (let i = 0; i < cookies.length; i++) {
                        const cookie = cookies[i];
                        const eqPos = cookie.indexOf('=');
                        const name = eqPos > -1 ? cookie.substr(0, eqPos) : cookie;
                        document.cookie = name + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;domain=.arena.ai';
                        document.cookie = name + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;domain=arena.ai';
                    }
                    // Clear localStorage and sessionStorage
                    try {
                        localStorage.clear();
                        sessionStorage.clear();
                    } catch(e) {}
                    console.log('🧹 Cookies, localStorage, and sessionStorage cleared');
                }
            """)
            print(f"[{label} {window_id}]   ✓ Cleared cookies and storage in page")
        except Exception as e:
            print(f"[{label} {window_id}]   ⚠ Storage clear error: {e}")

        # ── 3. Clear Playwright context cookies (keep context open) ──────────
        try:
            await context.clear_cookies()
            print(f"[{label} {window_id}]   ✓ Context cookies cleared (context stays open)")
        except Exception as e:
            print(f"[{label} {window_id}]   ⚠ Context cookie clear error: {e}")

        # ── 4. Re-inject the 4 saved cookies into the fresh context ───────
        if saved_cookies:
            try:
                # Sanitize: remove fields Playwright doesn't accept on add_cookies
                clean = []
                for c in saved_cookies:
                    entry = {
                        "name":     c["name"],
                        "value":    c["value"],
                        "domain":   c.get("domain", ".arena.ai"),
                        "path":     c.get("path", "/"),
                        "secure":   c.get("secure", True),
                        "httpOnly": c.get("httpOnly", False),
                        "sameSite": c.get("sameSite", "Lax"),
                    }
                    if c.get("expires", -1) > 0:
                        entry["expires"] = c["expires"]
                    clean.append(entry)
                await context.add_cookies(clean)
                print(f"[{label} {window_id}]   ✓ {len(clean)} cookies re-injected")
            except Exception as e:
                print(f"[{label} {window_id}]   ⚠ Cookie re-inject error: {e}")

        # ── 4. Reload the page ────────────────────────────────────────────────
        if FIVE_GAIN and _EVAL_ID:
            target_url = f"https://arena.ai/c/{_EVAL_ID}"
        else:
            target_url = "https://arena.ai"

        print(f"[{label} {window_id}]   → Reloading to {target_url}")
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[{label} {window_id}]   ⚠ Navigation error: {e}")

    # ── Standard TUNING path (simple reload) ─────────────────────────────────
    else:
        print(f"[{label} {window_id}] 🔄 Token received ({version}) — reloading page...")
        _windows[window_id]["status"] = "reloading"

        try:
            await page.reload(wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[{label} {window_id}] Reload error: {e}")
            _windows[window_id]["status"] = prev_status
            return

        # If FIVE_GAIN: navigate to eval URL after reload
        if FIVE_GAIN and _EVAL_ID:
            target_url = f"https://arena.ai/c/{_EVAL_ID}"
            print(f"[{label} {window_id}] 5_GAIN: navigating to {target_url}")
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2)
            except Exception as e:
                print(f"[{label} {window_id}] 5_GAIN navigation error: {e}")

    # ── Shared post-reload steps (both TUNING and HARD_TUNING) ───────────────

    # Re-run blocker
    try:
        await page.evaluate(BLOCKER_SCRIPT)
    except Exception as e:
        print(f"[{label} {window_id}] Post-reload blocker error: {e}")

    # Re-mark ready
    try:
        await page.evaluate(READY_SIGNAL_SCRIPT, window_id)
    except Exception as e:
        print(f"[{label} {window_id}] Post-reload ready signal error: {e}")
        _windows[window_id]["status"] = "harvesting_v2" if active_version == "v2" else "harvesting_v3"

    # Re-check: if stop was clicked during the reload, don't re-inject
    if _windows[window_id].get("active_script") is None:
        print(f"[{label} {window_id}] Harvester stopped during reload — not re-injecting.")
        return

    # Add delay before re-injecting so user can click Stop button
    await asyncio.sleep(3)

    # Re-check again after delay in case user clicked Stop
    if _windows[window_id].get("active_script") is None:
        print(f"[{label} {window_id}] Harvester stopped after delay — not re-injecting.")
        return

    # Re-inject the harvester script so it keeps running
    print(f"[{label} {window_id}] 💉 Re-injecting {active_version} harvester script...")
    try:
        await page.evaluate(active_script)
        status = "harvesting_v2" if active_version == "v2" else "harvesting_v3"
        _windows[window_id]["status"] = status
        mode_label = "HARD_TUNING" if HARD_TUNING else "TUNING"
        print(f"[{label} {window_id}] ✅ {mode_label} cycle complete — {active_version} harvester running.")
    except Exception as e:
        print(f"[{label} {window_id}] Re-inject error: {e}")
        _windows[window_id]["status"] = "ready"


@app.get("/api/tokens")
async def get_tokens():
    """Return all tokens currently stored in tokens.json."""
    async with _tokens_lock:
        tokens_data = _load_tokens_file()
    tokens = tokens_data.get("tokens", [])
    return {"tokens": tokens, "total": len(tokens)}


@app.get("/api/tokens/latest")
async def get_latest_tokens():
    """Return the most recent token per version."""
    async with _tokens_lock:
        tokens_data = _load_tokens_file()
    tokens = tokens_data.get("tokens", [])
    latest: dict[str, dict] = {}
    for t in tokens:
        v = str(t.get("version", "unknown"))
        latest[v] = t
    return {"latest": latest}


@app.delete("/tokens/clear")
async def clear_tokens():
    """Wipe all tokens from tokens.json, keeping the file intact."""
    async with _tokens_lock:
        tokens_data = _load_tokens_file()
        removed = len(tokens_data.get("tokens", []))
        empty = {
            "tokens": [],
            "total_count": 0,
            "last_updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _save_tokens_file(empty)
    print(f"[tokens] Cleared {removed} token(s) from {TOKENS_FILE}")
    return {"ok": True, "removed": removed}


@app.delete("/profiles/delete")
async def delete_profiles():
    if not PROFILES_DIR.exists():
        return {"ok": True, "deleted": 0, "detail": "No profiles directory found"}
    deleted = 0
    errors  = []
    for item in sorted(PROFILES_DIR.iterdir()):
        if item.is_dir():
            try:
                import shutil
                shutil.rmtree(item)
                deleted += 1
                print(f"[profiles] Deleted: {item}")
            except Exception as e:
                errors.append(str(e))
    if errors:
        return {"ok": False, "deleted": deleted, "detail": "; ".join(errors)}
    return {"ok": True, "deleted": deleted}


@app.post("/windows/{window_id}/ready")
async def window_ready(window_id: int):
    if window_id not in _windows:
        raise HTTPException(status_code=404, detail="Window not found")
    _windows[window_id]["status"] = "ready"
    return {"ok": True, "window_id": window_id, "status": "ready"}


@app.post("/windows/{window_id}/v2/start")
async def v2_start(window_id: int):
    w = _windows.get(window_id)
    if not w:
        raise HTTPException(status_code=404, detail="Window not found")
    try:
        # Inject window_id into V2_SCRIPT so tokens carry the correct window_id
        script = V2_SCRIPT.replace(
            'source_url: window.location.href,',
            f'source_url: window.location.href, window_id: {window_id},',
            1  # first occurrence only (v2 sendToken)
        )
        await w["page"].evaluate(script)
        w["status"] = "harvesting_v2"
        w["active_script"] = script          # remember for post-reload re-injection
        w["active_version"] = "v2"
        return {"ok": True, "status": "harvesting_v2"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/windows/{window_id}/v2/stop")
async def v2_stop(window_id: int):
    w = _windows.get(window_id)
    if not w:
        raise HTTPException(status_code=404, detail="Window not found")
    try:
        await w["page"].evaluate("if (typeof window.__STOP_V2_HARVEST__ === 'function') window.__STOP_V2_HARVEST__();")
        w["status"] = "idle"
        w["active_script"]  = None
        w["active_version"] = None
        return {"ok": True, "status": "idle"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/windows/{window_id}/v3/start")
async def v3_start(window_id: int):
    w = _windows.get(window_id)
    if not w:
        raise HTTPException(status_code=404, detail="Window not found")
    try:
        # Inject window_id into V3_SCRIPT so tokens carry the correct window_id
        script = V3_SCRIPT.replace(
            'source_url: window.location.href,',
            f'source_url: window.location.href, window_id: {window_id},',
            1  # first occurrence only (v3 harvest)
        )
        await w["page"].evaluate(script)
        w["status"] = "harvesting_v3"
        w["active_script"] = script          # remember for post-reload re-injection
        w["active_version"] = "v3"
        return {"ok": True, "status": "harvesting_v3"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/windows/{window_id}/v3/stop")
async def v3_stop(window_id: int):
    w = _windows.get(window_id)
    if not w:
        raise HTTPException(status_code=404, detail="Window not found")
    try:
        await w["page"].evaluate("if (typeof window.__STOP_HARVEST__ === 'function') window.__STOP_HARVEST__();")
        w["status"] = "idle"
        w["active_script"]  = None
        w["active_version"] = None
        return {"ok": True, "status": "idle"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/windows/{window_id}/invisible/run")
async def invisible_run(window_id: int):
    w = _windows.get(window_id)
    if not w:
        raise HTTPException(status_code=404, detail="Window not found")
    try:
        await w["page"].evaluate(ON_DEMAND_V2_SCRIPT)
        return {"ok": True, "message": "Invisible script triggered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Mouse movement coroutine ──────────────────────────────────

async def mouse_mover(page: Page, window_id: int):
    """Continuously moves the mouse in natural bezier curves. Never clicks."""
    try:
        vp = page.viewport_size or {"width": 1280, "height": 800}
    except Exception:
        vp = {"width": 1280, "height": 800}

    W, H = vp["width"], vp["height"]
    cx, cy = W // 2, H // 2

    def rand_point():
        return random.randint(80, W - 80), random.randint(80, H - 80)

    def bezier_points(x0, y0, x1, y1, steps=12):
        cpx = (x0 + x1) // 2 + random.randint(-80, 80)
        cpy = (y0 + y1) // 2 + random.randint(-80, 80)
        pts = []
        for i in range(1, steps + 1):
            t  = i / steps
            bx = (1-t)**2 * x0 + 2*(1-t)*t * cpx + t**2 * x1
            by = (1-t)**2 * y0 + 2*(1-t)*t * cpy + t**2 * y1
            pts.append((int(bx), int(by)))
        return pts

    while True:
        try:
            tx, ty = rand_point()
            for px, py in bezier_points(cx, cy, tx, ty, steps=random.randint(8, 16)):
                await page.mouse.move(px, py)
                await asyncio.sleep(random.uniform(0.03, 0.12))
            cx, cy = tx, ty
            await asyncio.sleep(random.uniform(0.8, 3.5))
        except Exception:
            break  # page/context closed — exit gracefully


# ── config.json patch helpers ─────────────────────────────────

def _load_config_file() -> dict:
    """Load config.json from disk. Returns empty dict if missing/corrupt."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config_file(cfg: dict) -> None:
    """Write config.json atomically."""
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def _patch_config(updates: dict) -> None:
    """Merge *updates* into config.json, preserving all existing keys."""
    cfg = _load_config_file()
    cfg.update(updates)
    _save_config_file(cfg)
    for k, v in updates.items():
        preview = (v[:30] + "...") if isinstance(v, str) and len(v) > 30 else v
        print(f"[config] {k} = {preview}")


# ── AUTO_LOGIN sign-in helper ──────────────────────────────────

async def auto_login_window(page: Page, context: BrowserContext, window_id: int) -> bool:
    """
    Perform the arena.ai email/password sign-in inside the browser page via
    fetch(), then extract the two auth cookies from the response Set-Cookie
    headers and persist them (plus cf_clearance / __cf_bm) to config.json.

    Returns True on success, False on failure (window continues regardless).
    """
    label = "tab" if TABS else "window"
    print(f"[{label} {window_id}] 🔐 AUTO_LOGIN: signing in as {_AUTO_LOGIN_EMAIL}...")

    # Build the JS payload — credentials are interpolated server-side (Python),
    # so they never appear in the browser console log.
    email_escaped    = _AUTO_LOGIN_EMAIL.replace('"', '\\"')
    password_escaped = _AUTO_LOGIN_PASSWORD.replace('"', '\\"')

    login_script = f"""
async () => {{
    const resp = await fetch("https://arena.ai/nextjs-api/sign-in/email", {{
        method: "POST",
        headers: {{
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.6",
            "content-type": "application/json",
            "priority": "u=1, i",
            "sec-ch-ua": "\\"Brave\\";v=\\"143\\", \\"Chromium\\";v=\\"143\\", \\"Not A(Brand\\";v=\\"24\\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\\"Linux\\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "sec-gpc": "1"
        }},
        referrer: "https://arena.ai/",
        body: JSON.stringify({{
            email: "{email_escaped}",
            password: "{password_escaped}",
            shouldLinkHistory: false
        }}),
        credentials: "include"
    }});

    // Return status + all readable response headers as a plain object
    const headers = {{}};
    resp.headers.forEach((v, k) => {{ headers[k] = v; }});
    let body = "";
    try {{ body = await resp.text(); }} catch(_) {{}}
    return {{ status: resp.status, headers, body }};
}}
"""

    try:
        result = await page.evaluate(login_script)
    except Exception as e:
        print(f"[{label} {window_id}] ⚠ AUTO_LOGIN fetch error: {e}")
        return False

    status = result.get("status", 0)
    print(f"[{label} {window_id}]   Sign-in response status: {status}")

    if status not in (200, 201, 204):
        body_preview = result.get("body", "")[:200]
        print(f"[{label} {window_id}] ⚠ AUTO_LOGIN failed (status {status}): {body_preview}")
        return False

    # ── Extract auth cookies from browser context (set via credentials:include) ─
    # The browser automatically stores Set-Cookie headers when credentials:"include"
    # is used, so we read them from the context after the fetch completes.
    await asyncio.sleep(1)  # brief pause to let cookies settle

    try:
        all_cookies = await context.cookies(["https://arena.ai"])
    except Exception as e:
        print(f"[{label} {window_id}] ⚠ Could not read context cookies: {e}")
        return False

    cookie_map = {c["name"]: c["value"] for c in all_cookies}

    updates: dict = {}

    v10 = cookie_map.get("arena-auth-prod-v1.0", "")
    v11 = cookie_map.get("arena-auth-prod-v1.1", "")
    # Also accept plain v1 in case the site hasn't migrated this window yet
    v1  = cookie_map.get("arena-auth-prod-v1",   "")

    if v10:
        updates["auth_prod"]    = v10
        updates["auth_prod_v2"] = v11
        updates["v2_auth"]      = True
        print(f"[{label} {window_id}]   ✓ arena-auth-prod-v1.0 captured ({len(v10)} chars)")
        if v11:
            print(f"[{label} {window_id}]   ✓ arena-auth-prod-v1.1 captured ({len(v11)} chars)")
    elif v1:
        updates["auth_prod"] = v1
        print(f"[{label} {window_id}]   ✓ arena-auth-prod-v1 captured ({len(v1)} chars)")
    else:
        print(f"[{label} {window_id}]   ⚠ No auth cookie found in context after login")

    if updates:
        _patch_config(updates)
        print(f"[{label} {window_id}] ✅ AUTO_LOGIN: auth cookies saved to {CONFIG_FILE}")

    return bool(updates)


async def sync_cf_cookies_to_config(context: BrowserContext, window_id: int) -> None:
    """
    After a window is marked ready, find cf_clearance and __cf_bm cookies in
    the browser context and write their values to config.json.
    Called once per window when it transitions to 'ready'.
    """
    label = "tab" if TABS else "window"
    try:
        all_cookies = await context.cookies(["https://arena.ai"])
    except Exception as e:
        print(f"[{label} {window_id}] ⚠ cf-cookie sync failed (read): {e}")
        return

    cookie_map = {c["name"]: c["value"] for c in all_cookies}
    updates: dict = {}

    cf_clearance = cookie_map.get("cf_clearance", "")
    cf_bm        = cookie_map.get("__cf_bm", "")

    if cf_clearance:
        updates["cf_clearance"] = cf_clearance
        print(f"[{label} {window_id}]   ✓ cf_clearance synced ({len(cf_clearance)} chars)")
    if cf_bm:
        updates["cf_bm"] = cf_bm
        print(f"[{label} {window_id}]   ✓ __cf_bm synced ({len(cf_bm)} chars)")

    if updates:
        _patch_config(updates)


# ── Cookie injection helper ────────────────────────────────────

async def inject_cookies(context: BrowserContext, window_id: int) -> None:
    label = "tab" if TABS else "window"
    print(f"[{label} {window_id}] 🍪 Injecting cookies (COOKIES=True)...")
    try:
        all_cookies = await context.cookies()
        old_cookie  = next((c for c in all_cookies if c.get("name") == "arena-auth-prod-v1"), None)

        if old_cookie:
            await context.clear_cookies(name="arena-auth-prod-v1")
            print(f"[{label} {window_id}]   ✓ Removed arena-auth-prod-v1")
        else:
            print(f"[{label} {window_id}]   ℹ arena-auth-prod-v1 not found — using defaults")

        base: dict = {
            "domain":   old_cookie.get("domain",   ".arena.ai") if old_cookie else ".arena.ai",
            "path":     old_cookie.get("path",      "/")         if old_cookie else "/",
            "secure":   old_cookie.get("secure",    True)        if old_cookie else True,
            "httpOnly": old_cookie.get("httpOnly",  True)        if old_cookie else True,
            "sameSite": old_cookie.get("sameSite",  "Lax")       if old_cookie else "Lax",
        }
        if old_cookie and old_cookie.get("expires", -1) > 0:
            base["expires"] = old_cookie["expires"]

        await context.add_cookies([{**base, "name": "arena-auth-prod-v1.0", "value": COOKIE_V1}])
        print(f"[{label} {window_id}]   ✓ Set arena-auth-prod-v1.0")

        await context.add_cookies([{**base, "name": "arena-auth-prod-v1.1", "value": COOKIE_V2}])
        print(f"[{label} {window_id}]   ✓ Added arena-auth-prod-v1.1")

    except Exception as e:
        print(f"[{label} {window_id}] ⚠ Cookie injection error: {e}")


# ── Extension args builder ────────────────────────────────────

def _get_extension_args() -> list[str]:
    if not CUSTOM or not EXTENSIONS:
        return []

    if not EXTENSIONS_DIR or not EXTENSIONS_DIR.strip():
        raise RuntimeError(
            "EXTENSIONS=True but EXTENSIONS_DIR is empty.\n"
            "Set EXTENSIONS_DIR to your browser's Extensions folder path."
        )

    base = Path(EXTENSIONS_DIR.strip())
    if not base.exists():
        raise RuntimeError(f"EXTENSIONS_DIR does not exist: {base}")
    if not base.is_dir():
        raise RuntimeError(f"EXTENSIONS_DIR is not a directory: {base}")

    ext_dirs: list[str] = []
    print(f"[extensions] Scanning: {base}")

    for ext_id_dir in sorted(base.iterdir()):
        if not ext_id_dir.is_dir():
            continue
        for version_dir in sorted(ext_id_dir.iterdir(), reverse=True):
            if version_dir.is_dir() and (version_dir / "manifest.json").exists():
                ext_dirs.append(str(version_dir))
                print(f"[extensions]   + {ext_id_dir.name}/{version_dir.name}")
                break

    if not ext_dirs:
        raise RuntimeError(f"No extensions found in: {base}")

    joined = ",".join(ext_dirs)
    return [
        "--enable-extensions",
        f"--load-extension={joined}",
        f"--disable-extensions-except={joined}",
    ]


_BASE_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-session-crashed-bubble",
    "--disable-features=TranslateUI",
    "--window-size=1280,800",
] + ([] if os.name == "nt" else [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
])

# Shared state for TABS mode
_shared_browser = None
_shared_context = None
_tab_cookie_store: dict[int, list] = {}
_playwright_instance = None   # set during run_browsers; used by HARD_TUNING


def _resolve_profile_dir(slot: str) -> Path:
    if CUS_PROFILE:
        return Path(PROFILE_PATH.strip())
    d = PROFILES_DIR / slot
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _launch_persistent(playwright, window_id: int) -> tuple[BrowserContext, Page]:
    """WINDOWS mode — each window is its own isolated persistent context."""
    profile_dir = _resolve_profile_dir(f"window_{window_id}")
    profile_dir.mkdir(parents=True, exist_ok=True)

    args = _BASE_ARGS + [
        f"--window-position={100 + window_id * 40},{50 + window_id * 40}",
    ] + _get_extension_args()

    launch_kwargs = dict(user_data_dir=str(profile_dir), headless=False, args=args)
    if CUSTOM and PATH:
        launch_kwargs["executable_path"] = PATH

    context: BrowserContext = await playwright.chromium.launch_persistent_context(**launch_kwargs)
    await context.add_init_script(STEALTH_SCRIPT)
    page: Page = await context.new_page()
    await page.set_viewport_size({"width": 1280, "height": 800})
    return context, page


async def _launch_tab(playwright, tab_id: int) -> tuple[BrowserContext, Page]:
    """TABS mode — all tabs share one persistent context / one OS window."""
    global _shared_browser, _shared_context

    if _shared_browser is None:
        profile_dir = _resolve_profile_dir("tab_0")
        profile_dir.mkdir(parents=True, exist_ok=True)

        args = _BASE_ARGS + _get_extension_args()
        launch_kwargs = dict(user_data_dir=str(profile_dir), headless=False, args=args)
        if CUSTOM and PATH:
            launch_kwargs["executable_path"] = PATH

        _shared_context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
        await _shared_context.add_init_script(STEALTH_SCRIPT)
        _shared_browser = True  # sentinel

    page: Page = await _shared_context.new_page()
    await page.set_viewport_size({"width": 1280, "height": 800})

    try:
        await _shared_context.clear_cookies()
    except Exception:
        pass

    state_file = PROFILES_DIR / f"tab_{tab_id}" / "cookies.json"
    if state_file.exists():
        try:
            saved = json.loads(state_file.read_text())
            if saved:
                await _shared_context.add_cookies(saved)
        except Exception:
            pass

    return _shared_context, page


async def _save_tab_cookies(tab_id: int) -> None:
    if _shared_context is None:
        return
    try:
        cookies   = await _shared_context.cookies()
        _tab_cookie_store[tab_id] = cookies
        state_dir = PROFILES_DIR / f"tab_{tab_id}"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "cookies.json").write_text(json.dumps(cookies))
    except Exception as e:
        print(f"[tab {tab_id}] Cookie save failed: {e}")


async def _restore_tab_cookies(tab_id: int) -> None:
    if _shared_context is None:
        return
    try:
        await _shared_context.clear_cookies()
        cookies = _tab_cookie_store.get(tab_id)
        if cookies:
            await _shared_context.add_cookies(cookies)
        else:
            state_file = PROFILES_DIR / f"tab_{tab_id}" / "cookies.json"
            if state_file.exists():
                saved = json.loads(state_file.read_text())
                if saved:
                    await _shared_context.add_cookies(saved)
    except Exception as e:
        print(f"[tab {tab_id}] Cookie restore failed: {e}")


# ── Window / tab setup ────────────────────────────────────────

async def setup_window(playwright, window_id: int):
    label = "tab" if TABS else "window"

    if TABS:
        context, page = await _launch_tab(playwright, window_id)
    else:
        context, page = await _launch_persistent(playwright, window_id)

    _windows[window_id] = {"id": window_id, "status": "loading", "page": page, "context": context}

    # ── Determine initial landing URL ──────────────────────────
    # If FIVE_GAIN is active we start at arena.ai for the auth flow, then
    # navigate to the eval URL as the final step after ready.
    initial_url = "https://arena.ai"

    print(f"[{label} {window_id}] Navigating to {initial_url}...")
    try:
        await page.goto(initial_url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"[{label} {window_id}] Navigation warning: {e}")

    await asyncio.sleep(2)

    print(f"[{label} {window_id}] Running initial v2 script...")
    try:
        await page.evaluate(INITIAL_V2_SCRIPT)
        await asyncio.sleep(1)
    except Exception as e:
        print(f"[{label} {window_id}] Initial script error: {e}")

    # ── AUTO_LOGIN: sign in before reload ─────────────────────────
    if AUTO_LOGIN:
        await auto_login_window(page, context, window_id)
        await asyncio.sleep(1)

    print(f"[{label} {window_id}] Reloading page...")
    try:
        await page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(2)
    except Exception as e:
        print(f"[{label} {window_id}] Reload error: {e}")

    if COOKIES:
        await inject_cookies(context, window_id)

    if TABS:
        await _save_tab_cookies(window_id)

    # ── FIVE_GAIN: navigate to eval URL after auth + reload ───────
    if FIVE_GAIN and _EVAL_ID:
        target_url = f"https://arena.ai/c/{_EVAL_ID}"
        print(f"[{label} {window_id}] 5_GAIN: navigating to {target_url}...")
        await asyncio.sleep(3)  # brief settle before final navigation
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[{label} {window_id}] 5_GAIN navigation error: {e}")

    print(f"[{label} {window_id}] Marking as ready...")
    try:
        await page.evaluate(READY_SIGNAL_SCRIPT, window_id)
    except Exception as e:
        print(f"[{label} {window_id}] Ready signal JS failed ({e}), marking directly")
        _windows[window_id]["status"] = "ready"

    # ── Sync cf_clearance + __cf_bm to config.json ───────────────
    if AUTO_LOGIN:
        await sync_cf_cookies_to_config(context, window_id)

    await asyncio.sleep(1)
    print(f"[{label} {window_id}] Running blocker script...")
    try:
        await page.evaluate(BLOCKER_SCRIPT)
    except Exception as e:
        print(f"[{label} {window_id}] Blocker script error: {e}")

    print(f"[{label} {window_id}] ✅ Ready. Starting mouse mover.")
    asyncio.create_task(mouse_mover(page, window_id))


# ── Tab switcher (TABS mode only) ─────────────────────────────

async def tab_switcher():
    current_tab_id: Optional[int] = None

    while True:
        await asyncio.sleep(15)
        ids = sorted(_windows.keys())
        if not ids:
            continue

        for wid in ids:
            w = _windows.get(wid)
            if not w:
                continue
            page: Page = w.get("page")
            if page is None:
                continue
            try:
                if current_tab_id is not None and current_tab_id != wid:
                    await _save_tab_cookies(current_tab_id)
                    await _restore_tab_cookies(wid)
                await page.bring_to_front()
                current_tab_id = wid
                await asyncio.sleep(0.15)
            except Exception:
                pass


# ── Browser runner ────────────────────────────────────────────

async def run_browsers(server_ready_event: asyncio.Event):
    await server_ready_event.wait()
    await asyncio.sleep(0.5)

    PROFILES_DIR.mkdir(exist_ok=True)
    async with async_playwright() as pw:
        global _playwright_instance
        _playwright_instance = pw
        for i in range(N):
            await setup_window(pw, i)
            await asyncio.sleep(0.8)

        label = "tab(s) in one window" if TABS else "window(s)"
        print(f"\n✅ {N} {label} launched.")
        print(f"   Dashboard  : http://localhost:{SERVER_PORT}")
        print(f"   Token file : {TOKENS_FILE}  ← read by modula.py / main.py")
        if FIVE_GAIN:
            print(f"   5_GAIN     : arena.ai/c/{_EVAL_ID}")

        if TABS:
            asyncio.create_task(tab_switcher())
            print("   Tab switcher active (cycles every 15s)")

        while True:
            await asyncio.sleep(10)


class _ServerWithReadyEvent(uvicorn.Server):
    def __init__(self, config, ready_event: asyncio.Event):
        super().__init__(config)
        self._ready_event = ready_event

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        self._ready_event.set()


async def main():
    print("=" * 55)
    print("  reCAPTCHA Token Harvester")
    print(f"  Windows / Tabs : {N}")
    print(f"  Custom browser : {CUSTOM}{(' → ' + PATH) if CUSTOM else ''}")
    print(f"  Extensions     : {EXTENSIONS}")
    print(f"  Cookies mode   : {COOKIES}")
    print(f"  Auto Login     : {AUTO_LOGIN}{(' (' + _AUTO_LOGIN_EMAIL + ')') if AUTO_LOGIN else ''}")
    print(f"  5_GAIN         : {FIVE_GAIN}{(' → arena.ai/c/' + _EVAL_ID) if FIVE_GAIN and _EVAL_ID else ''}")
    print(f"  Tuning (reload): {TUNING}")
    print(f"  Hard Tuning    : {HARD_TUNING}")
    print(f"  Output file    : {TOKENS_FILE}  (modula.py compatible)")
    print(f"  Dashboard      : http://localhost:{SERVER_PORT}")
    print("=" * 55)

    server_ready = asyncio.Event()
    config = uvicorn.Config(app, host="0.0.0.0", port=SERVER_PORT, log_level="warning")
    server = _ServerWithReadyEvent(config, server_ready)

    await asyncio.gather(
        server.serve(),
        run_browsers(server_ready),
    )


if __name__ == "__main__":
    asyncio.run(main())