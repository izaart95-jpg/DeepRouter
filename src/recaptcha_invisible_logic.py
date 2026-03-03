// ============================================
// v2 RECAPTCHA ENTERPRISE TOKEN GENERATOR
// Browser Console Version
// ============================================
// Based on Playwright/Python code that works
// ============================================

(function() {
  'use strict';
  
  const CONFIG = {
    SITE_KEY: '6Led_uYrAAAAAKjxDIF58fgFtX3t8loNAK85bW9I',   // 6Ld7ePYrAAAAAB34ovoFoDau1fqCJ6IyOjFEQaMn
    TIMEOUT: 60000 // 60 seconds timeout
  };
  
  console.log('🎯 v2 reCAPTCHA Token Generator (Browser Console)');
  console.log('================================================\n');
  
  // This is exactly what your Python code does, converted to browser JS
  async function getV2Token() {
    // Use wrappedJSObject if available (Firefox), otherwise window
    const w = window.wrappedJSObject || window;
    
    console.log('🔍 Checking for grecaptcha.enterprise...');
    
    // Wait for grecaptcha to be ready (same as your Python wait_for_function)
    await waitForGrecaptcha(w);
    
    const g = w.grecaptcha?.enterprise;
    if (!g || typeof g.render !== 'function') {
      throw new Error('NO_GRECAPTCHA_V2');
    }
    
    console.log('✅ grecaptcha.enterprise found with render function');
    
    let settled = false;
    const done = (fn, arg) => {
      if (settled) return;
      settled = true;
      fn(arg);
    };
    
    return new Promise((resolve, reject) => {
      try {
        // Create hidden div (exactly like your Python code)
        const el = w.document.createElement('div');
        el.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;';
        w.document.body.appendChild(el);
        
        console.log('📦 Created hidden div, rendering invisible widget...');
        
        // Set timeout (same as your Python timer)
        const timer = w.setTimeout(() => {
          console.log('⏱️ Timeout reached after', CONFIG.TIMEOUT, 'ms');
          done(reject, 'V2_TIMEOUT');
        }, CONFIG.TIMEOUT);
        
        // Render the widget (exactly like your Python code)
        const wid = g.render(el, {
          sitekey: CONFIG.SITE_KEY,
          size: 'invisible',
          callback: (tok) => {
            console.log('✅ Token received via callback');
            w.clearTimeout(timer);
            done(resolve, tok);
          },
          'error-callback': () => {
            console.log('❌ Widget error callback triggered');
            w.clearTimeout(timer);
            done(reject, 'V2_ERROR');
          }
        });
        
        console.log('Widget rendered with ID:', wid);
        
        // Execute the widget (same as your Python try/catch)
        try {
          if (typeof g.execute === 'function') {
            console.log('🚀 Executing widget...');
            g.execute(wid);
          } else {
            console.log('⚠️ execute function not found');
          }
        } catch (e) {
          console.log('Execute error (ignored):', e.message);
        }
        
      } catch (e) {
        console.log('❌ Error in setup:', e);
        done(reject, String(e));
      }
    });
  }
  
  // Wait for grecaptcha to be ready (matches your Python wait_for_function)
  async function waitForGrecaptcha(w) {
    const startTime = Date.now();
    const maxWait = 60000; // 60 seconds
    
    while (Date.now() - startTime < maxWait) {
      const g = w.grecaptcha?.enterprise;
      if (g && typeof g.render === 'function') {
        console.log(`✅ grecaptcha ready after ${Date.now() - startTime}ms`);
        return true;
      }
      
      // Wait 100ms before checking again
      await new Promise(r => setTimeout(r, 100));
      
      // Log progress every 5 seconds
      if (Math.floor((Date.now() - startTime) / 1000) % 5 === 0) {
        console.log(`⏳ Waiting for grecaptcha... (${Math.floor((Date.now() - startTime)/1000)}s)`);
      }
    }
    
    throw new Error('Timeout waiting for grecaptcha');
  }
  
  // Helper to copy token to clipboard
  function copyToClipboard(text) {
    try {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      console.log('📋 Token copied to clipboard!');
      return true;
    } catch (e) {
      console.log('⚠️ Could not copy to clipboard:', e.message);
      return false;
    }
  }
  
  // Helper to display token nicely
  function displayToken(token) {
    console.log('\n' + '='.repeat(60));
    console.log('✅ SUCCESS! v2 Token Generated:');
    console.log('='.repeat(60));
    console.log('\n📋 Token:', token);
    console.log('\n📏 Length:', token.length, 'characters');
    console.log('🔍 Preview:', token.substring(0, 50) + '...');
    console.log('🔑 Type:', token.startsWith('03') ? 'v2 Token' : token.startsWith('0') ? 'v3 Token' : 'Unknown');
    

  }
  
  // Main execution
  (async function() {
    console.log('\n🚀 Starting v2 token generation...');
    console.log('Site Key:', CONFIG.SITE_KEY);
    
    try {
      const token = await getV2Token();
      displayToken(token);
      
      // Return token for further use if needed
      return token;
      
    } catch (error) {
      console.error('\n❌ Failed to generate token:', error);
      
      if (error === 'NO_GRECAPTCHA_V2') {
        console.log('\n💡 Trying to load reCAPTCHA Enterprise...');
        try {
          await loadRecaptchaScript();
          console.log('✅ Script loaded, retrying...');
          // Wait a bit and retry
          await new Promise(r => setTimeout(r, 2000));
          const token = await getV2Token();
          displayToken(token);
          return token;
        } catch (loadError) {
          console.error('❌ Could not load reCAPTCHA:', loadError);
        }
      } else if (error === 'V2_TIMEOUT') {
        console.log('\n💡 Timeout reached - this can happen if:');
        console.log('   - reCAPTCHA is taking too long to load');
        console.log('   - The site is rate-limiting requests');
        console.log('   - There might be a network issue');
      } else if (error === 'V2_ERROR') {
        console.log('\n💡 reCAPTCHA error callback triggered');
      }
      
      console.log('\n❌ Token generation failed');
    }
  })();
  
  // Function to load reCAPTCHA if not present
  async function loadRecaptchaScript() {
    return new Promise((resolve, reject) => {
      if (document.querySelector('script[src*="recaptcha/enterprise.js"]')) {
        resolve();
        return;
      }
      
      const script = document.createElement('script');
      script.src = 'https://www.google.com/recaptcha/enterprise.js?render=' + CONFIG.SITE_KEY;
      script.async = true;
      script.defer = true;
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }
  
})();
