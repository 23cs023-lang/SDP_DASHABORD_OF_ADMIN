// auth.js - Token-based authentication
class AuthManager {
    constructor() {
        this.token = null;
        this.username = null;
        this.init();
    }
    
    init() {
        // Get token from cookie
        this.token = this.getCookie('admin_token');
    }
    
    getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return null;
    }
    
    async checkAuth() {
        try {
            const response = await fetch('/api/auth/verify', {
                credentials: 'include'
            });
            
            if (response.ok) {
                const data = await response.json();
                if (data.authenticated) {
                    this.username = data.username;
                    return true;
                }
            }
            return false;
        } catch (error) {
            console.error('Auth check failed:', error);
            return false;
        }
    }
    
    async login(username, password) {
        try {
            const response = await fetch('/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ username, password })
            });
            
            const data = await response.json();
            
            if (data.success) {
                this.token = data.token;
                this.username = data.user.username;
                return { success: true, data };
            } else {
                return { success: false, error: data.error };
            }
        } catch (error) {
            return { success: false, error: 'Login failed' };
        }
    }
    
    logout() {
        fetch('/logout', { credentials: 'include' })
            .then(() => {
                window.location.href = '/login';
            });
    }
    
    getAuthHeaders() {
        const headers = {
            'Content-Type': 'application/json'
        };
        
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }
        
        return headers;
    }
    
    // Fetch wrapper with auth
    async authFetch(url, options = {}) {
        const defaultOptions = {
            credentials: 'include',
            headers: this.getAuthHeaders()
        };
        
        const response = await fetch(url, { ...defaultOptions, ...options });
        
        // Check for 401 Unauthorized
        if (response.status === 401) {
            const data = await response.json();
            if (data.redirect) {
                window.location.href = data.redirect;
            }
            return null;
        }
        
        return response;
    }
}

// Create global auth manager
window.authManager = new AuthManager();

// Protect pages on load
document.addEventListener('DOMContentLoaded', async function() {
    // Skip auth check for login page
    if (window.location.pathname === '/login') return;
    
    const isAuthenticated = await authManager.checkAuth();
    
    if (!isAuthenticated) {
        window.location.href = '/login';
    }
});