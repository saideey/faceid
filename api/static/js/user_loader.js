// User Loader - Load current user info from localStorage
(function() {
    function loadCurrentUser() {
        const userStr = localStorage.getItem('user');
        if (userStr) {
            try {
                const user = JSON.parse(userStr);

                // Update sidebar user info
                const nameEl = document.getElementById('sidebarUserName');
                const emailEl = document.getElementById('sidebarUserEmail');

                if (nameEl && user.full_name) {
                    nameEl.textContent = user.full_name;
                }
                if (emailEl && user.email) {
                    emailEl.textContent = user.email;
                }

                // Update header user info if exists
                const headerNameEl = document.getElementById('headerUserName');
                if (headerNameEl && user.full_name) {
                    headerNameEl.textContent = user.full_name;
                }

                console.log('✅ User info loaded:', user.full_name, user.email);
            } catch (e) {
                console.error('❌ Error loading user info:', e);
            }
        }
    }

    async function loadCompanyInfo() {
        try {
            const token = localStorage.getItem('token');
            if (!token) return;

            const response = await fetch('/api/settings', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (!response.ok) return;

            const result = await response.json();
            const data = result.data || result;

            // Update sidebar logo
            const logoImg = document.getElementById('sidebarLogo');
            const logoPlaceholder = document.getElementById('sidebarLogoPlaceholder');
            const logoContainer = document.getElementById('sidebarLogoContainer');

            if (data.logo_url && logoImg && logoPlaceholder) {
                logoImg.src = data.logo_url;
                logoImg.classList.remove('hidden');
                logoPlaceholder.classList.add('hidden');
                // Remove blue background when logo is shown
                if (logoContainer) {
                    logoContainer.classList.remove('bg-blue-500');
                }
            }

            // Update company name
            const companyNameEl = document.getElementById('sidebarCompanyName');
            if (companyNameEl && data.company_name) {
                companyNameEl.textContent = data.company_name;
            }

            console.log('✅ Company info loaded:', data.company_name, data.logo_url);
        } catch (e) {
            console.error('❌ Error loading company info:', e);
        }
    }

    function checkAuth() {
        const token = localStorage.getItem('token');
        const currentPath = window.location.pathname;

        // Public pages that don't need auth
        const publicPages = ['/login', '/test'];

        // Check if current page is public
        const isPublicPage = publicPages.some(page => currentPath.startsWith(page));

        if (!token && !isPublicPage) {
            console.warn('⚠️ No token found, redirecting to login...');
            // Use replace to avoid adding to history
            window.location.replace('/login');
            return false;
        }

        return true;
    }

    // Initialize - only run once when DOM is ready
    let initialized = false;

    function init() {
        if (initialized) return;
        initialized = true;

        // Only check auth on protected pages
        const currentPath = window.location.pathname;
        const publicPages = ['/login', '/test'];
        const isPublicPage = publicPages.some(page => currentPath.startsWith(page));

        if (!isPublicPage) {
            if (checkAuth()) {
                loadCurrentUser();
                loadCompanyInfo();  // Load company logo and name
            }
        } else {
            // On login page, just load user info if available (for redirect logic)
            const token = localStorage.getItem('token');
            if (token && currentPath === '/login') {
                console.log('✅ Already logged in, redirecting to dashboard...');
                window.location.replace('/dashboard');
            }
        }
    }

    // Run init when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose globally
    window.loadCurrentUser = loadCurrentUser;
    window.loadCompanyInfo = loadCompanyInfo;
    window.checkAuth = checkAuth;
})();