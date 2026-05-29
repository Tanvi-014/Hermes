// Authentication Handler
const API_BASE = '/api/v1';
const TOKEN_STORAGE_KEY = 'hermes_auth_token';
const USER_STORAGE_KEY = 'hermes_user';
const PROJECT_STORAGE_KEY = 'hermes_current_project';

// Check if user is authenticated
function isAuthenticated() {
    return localStorage.getItem(TOKEN_STORAGE_KEY) !== null;
}

// Get auth token
function getAuthToken() {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
}

// Get current user
function getCurrentUser() {
    const userJson = localStorage.getItem(USER_STORAGE_KEY);
    return userJson ? JSON.parse(userJson) : null;
}

// Get current project
function getCurrentProject() {
    const projectJson = localStorage.getItem(PROJECT_STORAGE_KEY);
    return projectJson ? JSON.parse(projectJson) : null;
}

// Set current project
function setCurrentProject(project) {
    localStorage.setItem(PROJECT_STORAGE_KEY, JSON.stringify(project));
}

// Logout
function logout() {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem(USER_STORAGE_KEY);
    localStorage.removeItem(PROJECT_STORAGE_KEY);
    window.location.href = 'login.html';
}

// Redirect to dashboard if authenticated
function checkAuthAndRedirect() {
    if (isAuthenticated()) {
        window.location.href = 'index.html';
    }
}

// Redirect to login if not authenticated
function requireAuth() {
    if (!isAuthenticated()) {
        window.location.href = 'login.html';
    }
}

// API helper with auth token
async function authenticatedFetch(url, options = {}) {
    const token = getAuthToken();
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers,
    };
    
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    
    const project = getCurrentProject();
    if (project && project.id) {
        // Add project_id as query param or header
        const urlObj = new URL(url, window.location.origin);
        urlObj.searchParams.set('project_id', project.id);
        url = urlObj.toString();
    }
    
    return fetch(url, {
        ...options,
        headers,
    });
}

// Login form handler
if (document.getElementById('login-form')) {
    document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;
        const errorDiv = document.getElementById('login-error');
        const loginBtn = document.getElementById('login-btn');
        const btnText = loginBtn.querySelector('.btn-text');
        const btnSpinner = loginBtn.querySelector('.btn-spinner');
        
        // Show loading state
        loginBtn.disabled = true;
        btnText.style.display = 'none';
        btnSpinner.style.display = 'inline';
        errorDiv.style.display = 'none';
        
        try {
            const response = await fetch(`${API_BASE}/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password }),
            });
            
            // Check if response is JSON
            const contentType = response.headers.get('content-type');
            let data;
            if (contentType && contentType.includes('application/json')) {
                data = await response.json();
            } else {
                const text = await response.text();
                throw new Error(`Server error: ${text.substring(0, 100)}`);
            }
            
            if (!response.ok) {
                throw new Error(data.detail || 'Login failed');
            }
            
            // Store token and user
            localStorage.setItem(TOKEN_STORAGE_KEY, data.access_token);
            localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(data.user));
            
            // Redirect to dashboard
            window.location.href = 'index.html';
        } catch (error) {
            errorDiv.textContent = error.message;
            errorDiv.style.display = 'block';
            loginBtn.disabled = false;
            btnText.style.display = 'inline';
            btnSpinner.style.display = 'none';
        }
    });
}

// Register form handler
if (document.getElementById('register-form')) {
    document.getElementById('register-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;
        const confirmPassword = document.getElementById('confirm-password').value;
        const errorDiv = document.getElementById('register-error');
        const registerBtn = document.getElementById('register-btn');
        const btnText = registerBtn.querySelector('.btn-text');
        const btnSpinner = registerBtn.querySelector('.btn-spinner');
        
        // Validate passwords match
        if (password !== confirmPassword) {
            errorDiv.textContent = 'Passwords do not match';
            errorDiv.style.display = 'block';
            return;
        }
        
        // Show loading state
        registerBtn.disabled = true;
        btnText.style.display = 'none';
        btnSpinner.style.display = 'inline';
        errorDiv.style.display = 'none';
        
        try {
            const response = await fetch(`${API_BASE}/auth/register`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password }),
            });
            
            // Check if response is JSON
            const contentType = response.headers.get('content-type');
            let data;
            if (contentType && contentType.includes('application/json')) {
                data = await response.json();
            } else {
                const text = await response.text();
                throw new Error(`Server error: ${text.substring(0, 100)}`);
            }
            
            if (!response.ok) {
                throw new Error(data.detail || 'Registration failed');
            }
            
            // Redirect to login page
            window.location.href = 'login.html';
        } catch (error) {
            errorDiv.textContent = error.message;
            errorDiv.style.display = 'block';
            registerBtn.disabled = false;
            btnText.style.display = 'inline';
            btnSpinner.style.display = 'none';
        }
    });
}
