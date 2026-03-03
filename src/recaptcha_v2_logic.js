// ============================================================
// reCAPTCHA Enterprise v2 Token Harvester — TWO MODES
// ============================================================
// MODE 1: "invisible" (main.py style) — fully automatic, no click needed
//         Works when Google's risk score is low (trusted session).
//         Calls execute() programmatically. If Google rejects,
//         falls back to Mode 2 automatically.
//
// MODE 2: "checkbox" (LMArena style) — visible checkbox at bottom-right
//         You click the checkbox (may need to solve image challenge).
//         Always works but requires manual interaction.
//
// The harvester tries invisible first. If it fails, it switches
// to checkbox mode. Set FORCE_MODE below to skip auto-detection.
// ============================================================
(() => {
    const SERVER_URL   = "http://localhost:5000/api";
    const V2_SITEKEY   = "6Ld7ePYrAAAAAB34ovoFoDau1fqCJ6IyOjFEQaMn";

    // "auto" = try invisible first, fallback to checkbox
    // "invisible" = force invisible only (main.py style)
    // "checkbox" = force checkbox only (LMArena style)
    const FORCE_MODE   = "checkbox";

    // Invisible mode timing
    const INV_MIN_INTERVAL = 80;
    const INV_MAX_INTERVAL = 100;
    const INV_RETRY        = 15;

    let v2Count = 0;
    let invisibleErrors = 0;
    let currentMode = FORCE_MODE === "auto" ? "invisible" : FORCE_MODE;
    let currentTimeoutId = null;
    let widgetCounter = 0;
    let panelCreated = false;

    // ───────────────────────────────────────────
    // Shared helpers
    // ───────────────────────────────────────────
    function getRandomInterval(min, max) {
        const arr = new Uint32Array(1);
        crypto.getRandomValues(arr);
        return min + (arr[0] / (0xFFFFFFFF + 1)) * (max - min);
    }

    function sendToken(token, mode) {
        v2Count++;
        invisibleErrors = 0;
        console.log(`\n🔒 [v2-${mode} #${v2Count}] Token generated (${token.length} chars)`);
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
                source_url: window.location.href
            })
        })
        .then(r => r.json())
        .then(data => {
            console.log(`✅ [v2-${mode} #${v2Count}] Stored. Server total: ${data.total_count}`);
            if (panelCreated) updateStatus(`Token #${v2Count} stored! Total: ${data.total_count}`);
        })
        .catch(err => {
            console.error(`❌ [v2-${mode} #${v2Count}] Store failed:`, err);
        });
    }

    // ───────────────────────────────────────────
    // MODE 1: Invisible (main.py style)
    // Fully automatic — no user interaction needed
    // ───────────────────────────────────────────
    function harvestInvisible() {
        const g = window.grecaptcha?.enterprise;
        if (!g || typeof g.render !== 'function') {
            console.warn("[v2-invisible] grecaptcha.enterprise.render not ready, waiting...");
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
            if (!settled) {
                settled = true;
                console.warn("[v2-invisible] Timed out (60s)");
                el.remove();
                handleInvisibleFailure();
            }
        }, 60000);

        try {
            const wid = g.render(el, {
                sitekey: V2_SITEKEY,
                size: 'invisible',
                callback: (token) => {
                    if (settled) return;
                    settled = true;
                    clearTimeout(timer);
                    el.remove();
                    sendToken(token, "invisible").then(() => {
                        // Schedule next invisible harvest
                        const next = getRandomInterval(INV_MIN_INTERVAL, INV_MAX_INTERVAL);
                        console.log(`⏱️  [v2-invisible] Next in ${next.toFixed(1)}s`);
                        currentTimeoutId = setTimeout(harvestInvisible, next * 1000);
                    });
                },
                'error-callback': () => {
                    if (settled) return;
                    settled = true;
                    clearTimeout(timer);
                    el.remove();
                    handleInvisibleFailure();
                }
            });

            // Trigger the invisible challenge programmatically
            if (typeof g.execute === 'function') {
                g.execute(wid);
            }
        } catch (e) {
            if (!settled) {
                settled = true;
                clearTimeout(timer);
                el.remove();
                console.error("[v2-invisible] Render error:", e);
                handleInvisibleFailure();
            }
        }
    }

    function handleInvisibleFailure() {
        invisibleErrors++;
        if (FORCE_MODE === "invisible") {
            // Forced invisible — keep retrying with backoff
            const backoff = Math.min(INV_RETRY * Math.pow(1.5, invisibleErrors - 1), 300);
            console.warn(`[v2-invisible] Failed (${invisibleErrors}x). Retry in ${backoff.toFixed(0)}s`);
            currentTimeoutId = setTimeout(harvestInvisible, backoff * 1000);
        } else if (FORCE_MODE === "auto" && invisibleErrors >= 2) {
            // Auto mode — invisible failed twice, switch to checkbox
            console.warn(`[v2] Invisible failed ${invisibleErrors}x. Switching to CHECKBOX mode.`);
            console.warn("[v2] A widget will appear at the bottom-right. Click it to harvest tokens.");
            currentMode = "checkbox";
            startCheckboxMode();
        } else {
            // Auto mode — retry invisible once more
            const backoff = Math.min(INV_RETRY * Math.pow(1.5, invisibleErrors - 1), 60);
            console.warn(`[v2-invisible] Failed (${invisibleErrors}x). Retry in ${backoff.toFixed(0)}s`);
            currentTimeoutId = setTimeout(harvestInvisible, backoff * 1000);
        }
    }

    // ───────────────────────────────────────────
    // MODE 2: Checkbox (LMArena style)
    // Visible widget — user clicks to solve
    // ───────────────────────────────────────────
    function createPanel() {
        if (panelCreated) return;
        panelCreated = true;

        let panel = document.getElementById('__v2_harvest_panel');
        if (panel) return;

        panel = document.createElement('div');
        panel.id = '__v2_harvest_panel';
        panel.style.cssText = [
            'position: fixed',
            'bottom: 20px',
            'right: 20px',
            'z-index: 999999',
            'background: #1a1a2e',
            'border: 2px solid #16213e',
            'border-radius: 12px',
            'padding: 12px 16px',
            'box-shadow: 0 4px 20px rgba(0,0,0,0.4)',
            'font-family: system-ui, -apple-system, sans-serif',
            'min-width: 320px',
        ].join(';');

        const header = document.createElement('div');
        header.style.cssText = 'color: #e0e0e0; font-size: 13px; margin-bottom: 8px; font-weight: 600;';
        header.innerHTML = '🔒 v2 Harvester (checkbox) <span id="__v2_count" style="color:#4ade80;float:right;">0 tokens</span>';
        panel.appendChild(header);

        const status = document.createElement('div');
        status.id = '__v2_status';
        status.style.cssText = 'color: #9ca3af; font-size: 11px; margin-bottom: 10px;';
        status.textContent = 'Click the checkbox below to harvest a v2 token';
        panel.appendChild(status);

        const container = document.createElement('div');
        container.id = '__v2_checkbox_container';
        container.style.cssText = 'display: flex; justify-content: center;';
        panel.appendChild(container);

        const closeBtn = document.createElement('div');
        closeBtn.style.cssText = 'color: #6b7280; font-size: 11px; margin-top: 8px; cursor: pointer; text-align: center;';
        closeBtn.textContent = '[close] or window.__STOP_V2_HARVEST__()';
        closeBtn.onclick = () => window.__STOP_V2_HARVEST__();
        panel.appendChild(closeBtn);

        document.body.appendChild(panel);
    }

    function updateStatus(msg) {
        const el = document.getElementById('__v2_status');
        if (el) el.textContent = msg;
    }
    function updateCount() {
        const el = document.getElementById('__v2_count');
        if (el) el.textContent = `${v2Count} token${v2Count !== 1 ? 's' : ''}`;
    }

    function startCheckboxMode() {
        createPanel();
        renderCheckbox();
    }

    function renderCheckbox() {
        const g = window.grecaptcha?.enterprise;
        if (!g || typeof g.render !== 'function') {
            updateStatus('Waiting for grecaptcha.enterprise...');
            setTimeout(renderCheckbox, 1000);
            return;
        }

        // Destroy and recreate the container div — Google tracks rendered
        // widgets internally, so clearing innerHTML isn't enough.
        const panel = document.getElementById('__v2_harvest_panel');
        if (!panel) return;
        const oldContainer = document.getElementById('__v2_checkbox_container');
        if (oldContainer) oldContainer.remove();

        const container = document.createElement('div');
        container.id = '__v2_checkbox_container';
        container.style.cssText = 'display: flex; justify-content: center;';
        // Insert before the close button (last child)
        const closeBtn = panel.lastElementChild;
        panel.insertBefore(container, closeBtn);

        updateStatus('Click the checkbox below to harvest a v2 token');

        const timeout = setTimeout(() => {
            updateStatus('Widget expired (60s). Rendering fresh...');
            renderCheckbox();
        }, 60000);

        try {
            const wid = g.render(container, {
                sitekey: V2_SITEKEY,
                callback: (token) => {
                    clearTimeout(timeout);
                    sendToken(token, "checkbox").then(() => {
                        updateStatus(`Token #${v2Count} stored! New widget in 3s...`);
                        setTimeout(renderCheckbox, 3000);
                    });
                },
                'error-callback': () => {
                    clearTimeout(timeout);
                    console.warn('[v2-checkbox] error-callback — re-rendering in 5s');
                    updateStatus('Challenge failed. New widget in 5s...');
                    setTimeout(renderCheckbox, 5000);
                },
                'expired-callback': () => {
                    clearTimeout(timeout);
                    updateStatus('Token expired. New widget in 3s...');
                    setTimeout(renderCheckbox, 3000);
                },
                theme: document.documentElement.classList.contains('dark') ? 'dark' : 'light',
            });
        } catch (e) {
            clearTimeout(timeout);
            console.error('[v2-checkbox] Render error:', e);
            updateStatus(`Error: ${e.message}. Retry in 10s...`);
            setTimeout(renderCheckbox, 10000);
        }
    }

    // ───────────────────────────────────────────
    // Controls
    // ───────────────────────────────────────────
    window.__STOP_V2_HARVEST__ = () => {
        if (currentTimeoutId) {
            clearTimeout(currentTimeoutId);
            currentTimeoutId = null;
        }
        const panel = document.getElementById('__v2_harvest_panel');
        if (panel) panel.remove();
        panelCreated = false;
        console.log(`🛑 [v2] Stopped. Mode: ${currentMode}. Tokens: ${v2Count}. Invisible errors: ${invisibleErrors}`);
    };

    // Force switch modes at runtime
    window.__V2_SWITCH_INVISIBLE__ = () => {
        window.__STOP_V2_HARVEST__();
        currentMode = "invisible";
        invisibleErrors = 0;
        console.log("[v2] Switched to INVISIBLE mode");
        harvestInvisible();
    };
    window.__V2_SWITCH_CHECKBOX__ = () => {
        window.__STOP_V2_HARVEST__();
        currentMode = "checkbox";
        console.log("[v2] Switched to CHECKBOX mode");
        startCheckboxMode();
    };

    // ───────────────────────────────────────────
    // Start
    // ───────────────────────────────────────────
    console.log(`🔒 v2 Harvester started (mode: ${FORCE_MODE})`);
    console.log("   Controls:");
    console.log("     window.__STOP_V2_HARVEST__()      — stop");
    console.log("     window.__V2_SWITCH_INVISIBLE__()   — force invisible mode");
    console.log("     window.__V2_SWITCH_CHECKBOX__()    — force checkbox mode");

    if (FORCE_MODE === "checkbox") {
        currentMode = "checkbox";
        if (window.grecaptcha?.enterprise?.ready) {
            window.grecaptcha.enterprise.ready(() => startCheckboxMode());
        } else {
            startCheckboxMode();
        }
    } else {
        // "auto" or "invisible" — start with invisible
        currentMode = "invisible";
        if (window.grecaptcha?.enterprise?.ready) {
            window.grecaptcha.enterprise.ready(() => harvestInvisible());
        } else {
            harvestInvisible();
        }
    }
})();
