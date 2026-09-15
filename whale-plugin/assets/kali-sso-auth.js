/**
 * Kali noVNC SSO Authentication
 * Handles automatic login to noVNC/KasmWeb using CTFd user context
 * The user account is created on container startup from KALI_USER env variable
 */

(function() {
    'use strict';
    
    // Extract SSO token from URL parameters (for verification)
    function getSSOToken() {
        var params = new URLSearchParams(window.location.search);
        return params.get('sso_token');
    }
    
    // Extract password from URL parameters (fallback)
    function getVNCPassword() {
        var params = new URLSearchParams(window.location.search);
        return params.get('password');
    }
    
    // Main SSO handler
    function handleSSO() {
        var token = getSSOToken();
        var password = getVNCPassword();
        
        if (token) {
            console.log('[Kali SSO] SSO token detected - User authenticated via CTFd');
            injectSSOIndicator();
            
            // Set auto-login password if not already provided
            if (!password) {
                scheduleAutoLogin();
            }
        } else if (password) {
            console.log('[Kali SSO] Using noVNC password for login');
        } else {
            console.log('[Kali SSO] No authentication context provided');
        }
    }
    
    // Inject a visual indicator showing SSO authentication status
    function injectSSOIndicator() {
        var indicator = document.createElement('div');
        indicator.id = 'sso-indicator';
        indicator.style.cssText = `
            position: fixed;
            top: 10px;
            right: 10px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
            z-index: 999999;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            display: flex;
            align-items: center;
            gap: 8px;
            text-align: center;
        `;
        indicator.innerHTML = '✓ CTFd SSO Authenticated';
        
        // Wait for body to be available
        function addIndicator() {
            if (document.body) {
                document.body.appendChild(indicator);
                
                // Auto-fade out after 8 seconds
                setTimeout(function() {
                    indicator.style.opacity = '0';
                    indicator.style.transition = 'opacity 0.5s ease-out';
                    setTimeout(function() {
                        if (document.body.contains(indicator)) {
                            document.body.removeChild(indicator);
                        }
                    }, 500);
                }, 8000);
            } else {
                setTimeout(addIndicator, 100);
            }
        }
        addIndicator();
    }
    
    // Auto-fill VNC password for seamless login
    function scheduleAutoLogin() {
        // Look for noVNC password input and auto-fill it
        var attempts = 0;
        var maxAttempts = 30; // Try for 3 seconds
        
        function tryAutoLogin() {
            attempts++;
            
            // Try to find the password field in noVNC UI
            var passwordInput = null;
            
            // Method 1: Look for input with type password
            var inputs = document.querySelectorAll('input[type="password"]');
            if (inputs.length > 0) {
                passwordInput = inputs[0];
            }
            
            // Method 2: Look for connect button and try to fill context
            var connectBtn = document.querySelector('button[type="submit"], button.noVNC_button');
            
            if (passwordInput && passwordInput.value === '') {
                // Auto-fill with the default VNC password
                passwordInput.value = 'kalipass';
                passwordInput.dispatchEvent(new Event('input', { bubbles: true }));
                passwordInput.dispatchEvent(new Event('change', { bubbles: true }));
                console.log('[Kali SSO] Auto-filled VNC password');
                
                // Try to auto-submit if connect button is available
                if (connectBtn) {
                    setTimeout(function() {
                        connectBtn.click();
                        console.log('[Kali SSO] Triggered automatic connection');
                    }, 500);
                }
            } else if (attempts < maxAttempts) {
                setTimeout(tryAutoLogin, 100);
            }
        }
        
        // Start auto-login attempts after a short delay
        setTimeout(tryAutoLogin, 500);
    }
    
    // Run handler when page is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', handleSSO);
    } else {
        handleSSO();
    }
    
    // Also check on page load
    window.addEventListener('load', handleSSO);
})();
