// ============================================================
// reCAPTCHA Enterprise v3 Token Harvester (Auto-loop)
// ============================================================
// Paste into browser console on lmarena.ai
// Harvests v3 tokens on a random interval and POSTs to server.py
// ============================================================
(() => {
    const SERVER_URL    = "http://localhost:5000/api";
    const SITE_KEY      = "6Led_uYrAAAAAKjxDIF58fgFtX3t8loNAK85bW9I";
    const ACTION        = "chat_submit";
    const MIN_INTERVAL  = 8;   // minimum seconds between requests
    const MAX_INTERVAL  = 12;  // maximum seconds between requests

    let tokenCount = 0;
    let currentTimeoutId = null;

    /**
     * Generate cryptographically random interval between MIN and MAX seconds
     */
    function getRandomInterval() {
        const randomArray = new Uint32Array(1);
        crypto.getRandomValues(randomArray);
        const randomFloat = randomArray[0] / (0xFFFFFFFF + 1); // [0, 1)
        const intervalSec = MIN_INTERVAL + (randomFloat * (MAX_INTERVAL - MIN_INTERVAL));
        return intervalSec;
    }

    function harvest() {
        grecaptcha.enterprise.ready(() => {
            grecaptcha.enterprise.execute(SITE_KEY, { action: ACTION })
                .then(token => {
                    tokenCount++;
                    console.log(`\n🔄 [v3 #${tokenCount}] Token generated (${token.length} chars)`);
                    return fetch(SERVER_URL, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            token: token,
                            version: "v3",
                            action: ACTION,
                            harvest_number: tokenCount,
                            source_url: window.location.href
                        })
                    })
                    .then(res => res.json())
                    .then(data => {
                        console.log(`✅ [v3 #${tokenCount}] Stored. Server total: ${data.total_count}`);
                        window.__RECAPTCHA_TOKEN__ = token;
                        // Schedule next harvest after success
                        scheduleNext();
                    });
                })
                .catch(err => {
                    console.error("❌ [v3] Error:", err);
                    // Schedule next harvest even after error
                    scheduleNext();
                });
        });
    }

    function scheduleNext() {
        const nextInterval = getRandomInterval();
        console.log(`⏱️  [v3] Next harvest in ${nextInterval.toFixed(2)}s`);
        currentTimeoutId = setTimeout(harvest, nextInterval * 1000);
    }

    // Expose stop function
    window.__STOP_HARVEST__ = () => {
        if (currentTimeoutId) {
            clearTimeout(currentTimeoutId);
            currentTimeoutId = null;
        }
        console.log("🛑 [v3] Harvesting stopped. Total captured:", tokenCount);
    };

    // Start immediately
    console.log(`🚜 v3 Auto-harvester started (random interval: ${MIN_INTERVAL}-${MAX_INTERVAL}s)`);
    console.log("   Stop with: window.__STOP_HARVEST__()");
    harvest();
})();
