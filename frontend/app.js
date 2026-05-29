// Hermes Webhook Console Application Script
const API_BASE = '/api/v1';
const API_KEY_STORAGE_KEY = 'hermes_api_key';
const TOKEN_STORAGE_KEY = 'hermes_auth_token';
const USER_STORAGE_KEY = 'hermes_user';
const PROJECT_STORAGE_KEY = 'hermes_current_project';

// Application State
let state = {
    webhooks: [],
    stats: {
        total_webhooks: 0,
        pending_count: 0,
        processing_count: 0,
        completed_count: 0,
        failed_count: 0,
        success_rate: 100.0
    },
    activeFilter: 'all',
    searchQuery: '',
    selectedWebhookId: null,
    pollingInterval: null,
    activeView: 'console',
    projects: [],
    currentProject: null
};

let apiKey = localStorage.getItem(API_KEY_STORAGE_KEY) || '';
let authToken = localStorage.getItem(TOKEN_STORAGE_KEY) || '';

// DOM Cache
const dom = {
    webhooksTbody: document.getElementById('webhooks-tbody'),
    searchInput: document.getElementById('search-input'),
    refreshBtn: document.getElementById('refresh-btn'),
    filterBtns: document.querySelectorAll('.filter-btn'),
    
    // Stats Cards
    statTotal: document.getElementById('stat-total'),
    statActive: document.getElementById('stat-active'),
    statSuccess: document.getElementById('stat-success'),
    statFailed: document.getElementById('stat-failed'),
    
    // Sidebar Badges
    countAll: document.getElementById('count-all'),
    countPending: document.getElementById('count-pending'),
    countProcessing: document.getElementById('count-processing'),
    countCompleted: document.getElementById('count-completed'),
    countFailed: document.getElementById('count-failed'),
    
    // Inspector elements
    inspectorPanel: document.getElementById('inspector-panel'),
    inspectorCloseBtn: document.getElementById('inspector-close-btn'),
    inspectActions: document.getElementById('inspect-actions'),
    inspectId: document.getElementById('inspect-id'),
    inspectStatus: document.getElementById('inspect-status'),
    inspectUrl: document.getElementById('inspect-url'),
    inspectAttempts: document.getElementById('inspect-attempts'),
    inspectNextAttempt: document.getElementById('inspect-next-attempt'),
    inspectHeaders: document.getElementById('inspect-headers'),
    inspectPayload: document.getElementById('inspect-payload'),
    inspectAttemptsList: document.getElementById('inspect-attempts-list'),

    // Alerts Settings elements
    navAlertsBtn: document.getElementById('nav-alerts-btn'),
    alertsSettingsContent: document.getElementById('alerts-settings-content'),
    dashboardContent: document.querySelector('.dashboard-content'),
    backToConsoleBtn: document.getElementById('back-to-console-btn'),
    slackAlertForm: document.getElementById('slack-alert-form'),
    emailAlertForm: document.getElementById('email-alert-form'),
    configuredChannelsList: document.getElementById('configured-channels-list'),
    channelTabBtns: document.querySelectorAll('.channel-tab-btn'),
    
    // Projects Settings elements
    navProjectsBtn: document.getElementById('nav-projects-btn'),
    projectsSettingsContent: document.getElementById('projects-settings-content'),
    backToConsoleFromProjectsBtn: document.getElementById('back-to-console-from-projects-btn'),
    createProjectForm: document.getElementById('create-project-form'),
    projectName: document.getElementById('project-name'),
    projectsList: document.getElementById('projects-list'),
    
    // Team Members elements
    teamMembersCard: document.getElementById('team-members-card'),
    teamProjectName: document.getElementById('team-project-name'),
    addMemberForm: document.getElementById('add-member-form'),
    memberEmail: document.getElementById('member-email'),
    memberRole: document.getElementById('member-role'),
    teamMembersList: document.getElementById('team-members-list'),
    
    // Auth elements
    projectSelect: document.getElementById('project-select'),
    logoutBtn: document.getElementById('logout-btn')
};

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
    // Check authentication
    if (authToken) {
        state.currentProject = JSON.parse(localStorage.getItem(PROJECT_STORAGE_KEY) || 'null');
        loadProjects();
    } else {
        // Redirect to login if no auth token
        window.location.href = 'login.html';
        return;
    }
    
    setupEventListeners();
    refreshAll();
    
    // Real-time updates: Poll every 3 seconds for stats and list
    state.pollingInterval = setInterval(() => {
        if (state.activeView === 'console') {
            refreshAll(true); // pass true to suppress full UI reloading states during polling
        } else {
            fetchStats(); // keep stats updated in background
        }
    }, 3000);
});

// Event Listeners
function setupEventListeners() {
    // Refresh button
    dom.refreshBtn.addEventListener('click', () => refreshAll());

    // Search bar (with debounce or keyup)
    dom.searchInput.addEventListener('input', (e) => {
        state.searchQuery = e.target.value.trim();
        fetchWebhooks();
    });

    // Project switcher
    dom.projectSelect.addEventListener('change', (e) => {
        const projectId = e.target.value;
        const project = state.projects.find(p => p.id === projectId);
        if (project) {
            state.currentProject = project;
            localStorage.setItem(PROJECT_STORAGE_KEY, JSON.stringify(project));
            refreshAll();
        }
    });

    // Logout button
    dom.logoutBtn.addEventListener('click', () => {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        localStorage.removeItem(USER_STORAGE_KEY);
        localStorage.removeItem(PROJECT_STORAGE_KEY);
        window.location.href = 'login.html';
    });

    // Sidebar filters
    dom.filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            dom.filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.activeFilter = btn.dataset.status;
            showView('console');
            fetchWebhooks();
        });
    });

    // Close Inspector
    dom.inspectorCloseBtn.addEventListener('click', closeInspector);

    // Alerts Settings Navigation Toggle
    dom.navAlertsBtn.addEventListener('click', () => showView('settings'));
    dom.backToConsoleBtn.addEventListener('click', () => showView('console'));

    // Projects Settings Navigation Toggle
    dom.navProjectsBtn.addEventListener('click', () => showView('projects'));
    dom.backToConsoleFromProjectsBtn.addEventListener('click', () => showView('console'));

    // Create project form
    dom.createProjectForm.addEventListener('submit', handleCreateProject);

    // Add member form
    dom.addMemberForm.addEventListener('submit', handleAddMember);

    // Alerts channel selector tabs
    dom.channelTabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            dom.channelTabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            const channel = btn.dataset.channel;
            document.querySelectorAll('.alert-form').forEach(f => f.classList.remove('active'));
            document.getElementById(`${channel}-alert-form`).classList.add('active');
        });
    });

    // Forms submission
    dom.slackAlertForm.addEventListener('submit', handleSlackFormSubmit);
    dom.emailAlertForm.addEventListener('submit', handleEmailFormSubmit);
}

// Fetch stats and lists
async function refreshAll(isPoll = false) {
    await fetchStats();
    await fetchWebhooks(isPoll);
    
    // If a webhook is currently open in the inspector, fetch its fresh details
    if (state.selectedWebhookId) {
        await fetchWebhookDetails(state.selectedWebhookId, true);
    }
}

// Fetch stats from backend API
async function fetchStats() {
    try {
        const res = await fetchApi(`${API_BASE}/stats`);
        if (!res.ok) throw new Error('Failed to fetch statistics');
        const data = await res.json();
        
        state.stats = data;
        updateStatsUI();
    } catch (err) {
        console.error('Error fetching statistics:', err);
    }
}

// Update stats in the UI
function updateStatsUI() {
    const s = state.stats;
    dom.statTotal.textContent = s.total_webhooks.toLocaleString();
    dom.statActive.textContent = (s.pending_count + s.processing_count).toLocaleString();
    dom.statSuccess.textContent = `${s.success_rate}%`;
    dom.statFailed.textContent = s.failed_count.toLocaleString();

    // Update sidebar numbers
    dom.countAll.textContent = s.total_webhooks;
    dom.countPending.textContent = s.pending_count;
    dom.countProcessing.textContent = s.processing_count;
    dom.countCompleted.textContent = s.completed_count;
    dom.countFailed.textContent = s.failed_count;
}

// Fetch webhook list from backend API
async function fetchWebhooks(isPoll = false) {
    try {
        let url = `${API_BASE}/webhooks?limit=100`;
        if (state.activeFilter !== 'all') {
            url += `&status=${state.activeFilter}`;
        }
        
        const res = await fetchApi(url);
        if (!res.ok) throw new Error('Failed to fetch webhooks list');
        const data = await res.json();
        
        let filteredWebhooks = data.webhooks;
        
        // Filter locally by Search Query if set
        if (state.searchQuery) {
            const query = state.searchQuery.toLowerCase();
            filteredWebhooks = filteredWebhooks.filter(w => 
                w.destination_url.toLowerCase().includes(query) ||
                w.id.toLowerCase().includes(query)
            );
        }
        
        state.webhooks = filteredWebhooks;
        renderWebhooksTable(isPoll);
    } catch (err) {
        console.error('Error fetching webhooks:', err);
    }
}

// Render the list of webhooks in the main table
function renderWebhooksTable(isPoll = false) {
    if (state.webhooks.length === 0) {
        dom.webhooksTbody.innerHTML = `
            <tr>
                <td colspan="5" style="text-align: center; color: var(--text-muted); padding: 40px 0;">
                    No webhooks match the current filters.
                </td>
            </tr>
        `;
        return;
    }

    // Keep track of the current selected row index so it doesn't lose highlight on refresh
    const rowsHtml = state.webhooks.map(w => {
        const isSelected = w.id === state.selectedWebhookId ? 'selected' : '';
        return `
            <tr class="table-row ${isSelected}" data-id="${w.id}" onclick="handleRowClick('${w.id}')">
                <td>${getStatusBadge(w.status)}</td>
                <td><span class="url-text mono" title="${w.destination_url}">${w.destination_url}</span></td>
                <td style="text-align: right;" class="mono">${w.retry_count}/${w.max_retries}</td>
                <td class="mono">${formatDateTime(w.last_attempt_at) || '<span style="color: var(--text-muted);">Never attempted</span>'}</td>
                <td class="mono">${formatDateTime(w.created_at)}</td>
            </tr>
        `;
    }).join('');

    dom.webhooksTbody.innerHTML = rowsHtml;
}

// Handle clicking on a row to inspect a webhook
async function handleRowClick(id) {
    // Toggle highlight
    document.querySelectorAll('.table-row').forEach(row => {
        row.classList.remove('selected');
        if (row.dataset.id === id) {
            row.classList.add('selected');
        }
    });

    state.selectedWebhookId = id;
    await fetchWebhookDetails(id);
}

// Fetch full detail of a webhook (payload, headers, attempts)
async function fetchWebhookDetails(id, isPoll = false) {
    try {
        const res = await fetchApi(`${API_BASE}/webhooks/${id}`);
        if (!res.ok) throw new Error('Failed to fetch details');
        const webhook = await res.json();
        
        renderInspector(webhook);
        
        if (!isPoll) {
            dom.inspectorPanel.style.display = 'flex';
        }
    } catch (err) {
        console.error('Error fetching webhook details:', err);
    }
}

// Render the details in the split inspector panel
function renderInspector(w) {
    dom.inspectId.textContent = w.id;
    dom.inspectStatus.innerHTML = getStatusBadge(w.status);
    dom.inspectUrl.textContent = w.destination_url;
    dom.inspectAttempts.textContent = `${w.retry_count} / ${w.max_retries}`;
    dom.inspectNextAttempt.textContent = w.status === 'pending' ? formatDateTime(w.next_attempt_at) : 'N/A';
    
    // Format JSON blocks
    dom.inspectHeaders.textContent = JSON.stringify(w.headers, null, 2);
    dom.inspectPayload.textContent = JSON.stringify(w.payload, null, 2);
    
    // Add actions (e.g. Replay button for failed webhooks)
    dom.inspectActions.innerHTML = '';
    if (w.status === 'failed' || w.status === 'completed') {
        const replayBtn = document.createElement('button');
        replayBtn.className = 'btn btn-secondary';
        replayBtn.style.flex = '1';
        replayBtn.innerHTML = `
            <svg style="width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2" viewBox="0 0 24 24">
                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l.73-.73" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            Force Replay
        `;
        replayBtn.addEventListener('click', () => handleReplay(w.id));
        dom.inspectActions.appendChild(replayBtn);
    }

    // Render historical attempts list
    if (!w.attempts || w.attempts.length === 0) {
        dom.inspectAttemptsList.innerHTML = `<span style="color: var(--text-muted); font-size:12px;">No delivery attempts logged yet.</span>`;
    } else {
        dom.inspectAttemptsList.innerHTML = w.attempts.map((att, idx) => {
            const isSuccess = att.status_code && att.status_code >= 200 && att.status_code < 300;
            const statusClass = isSuccess ? 'attempt-success' : 'attempt-failed';
            const statusText = isSuccess ? `Success (${att.status_code})` : (att.status_code ? `Error (${att.status_code})` : 'Failed');
            
            return `
                <div class="attempt-card">
                    <div class="attempt-header">
                        <span class="font-weight-bold">Attempt #${att.attempt_number}</span>
                        <span class="${statusClass} font-weight-bold">${statusText}</span>
                    </div>
                    <div class="metadata-grid" style="grid-template-columns: 80px 1fr; margin-top: 4px;">
                        <span class="metadata-label">Time</span>
                        <span class="metadata-value mono">${formatDateTime(att.attempted_at)}</span>
                        
                        <span class="metadata-label">Duration</span>
                        <span class="metadata-value mono">${att.duration_ms ? `${att.duration_ms}ms` : 'N/A'}</span>
                        
                        ${att.error_message ? `
                            <span class="metadata-label">Error</span>
                            <span class="metadata-value" style="color: var(--color-rose);">${att.error_message}</span>
                        ` : ''}
                    </div>
                    ${att.response_body ? `
                        <div style="margin-top: 8px;">
                            <span class="metadata-label" style="display:block; margin-bottom: 2px;">Response Body Snippet:</span>
                            <pre style="max-height: 80px; padding: 6px; font-size: 11px;"><code>${escapeHtml(att.response_body)}</code></pre>
                        </div>
                    ` : ''}
                </div>
            `;
        }).join('');
    }
}

// Handle trigger manual replay
async function handleReplay(id) {
    try {
        const res = await fetchApi(`${API_BASE}/webhooks/${id}/replay`, { method: 'POST' });
        if (!res.ok) throw new Error('Replay trigger failed');
        
        // Show immediate loading/pending state
        refreshAll();
    } catch (err) {
        alert(`Failed to trigger replay: ${err.message}`);
    }
}

async function fetchApi(url, options = {}) {
    const headers = new Headers(options.headers || {});
    
    // Use JWT auth if available, otherwise fall back to API key
    if (authToken) {
        headers.set('Authorization', `Bearer ${authToken}`);
        // Add project_id as query param if using JWT auth
        if (state.currentProject && !url.includes('project_id=')) {
            const urlObj = new URL(url, window.location.origin);
            urlObj.searchParams.set('project_id', state.currentProject.id);
            url = urlObj.toString();
        }
    } else if (apiKey) {
        headers.set('X-Hermes-API-Key', apiKey);
    }

    let response = await fetch(url, { ...options, headers });
    if (response.status !== 401) {
        return response;
    }

    // If JWT auth failed, redirect to login
    if (authToken) {
        window.location.href = 'login.html';
        return response;
    }

    // Otherwise, prompt for API key (legacy mode)
    const nextKey = prompt('Enter Hermes API key');
    if (!nextKey) {
        return response;
    }

    apiKey = nextKey.trim();
    localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);
    headers.set('X-Hermes-API-Key', apiKey);
    response = await fetch(url, { ...options, headers });

    if (response.status === 401) {
        localStorage.removeItem(API_KEY_STORAGE_KEY);
        apiKey = '';
    }

    return response;
}

async function loadProjects() {
    if (!authToken) return;
    
    try {
        const response = await fetchApi(`${API_BASE}/projects`);
        if (!response.ok) {
            if (response.status === 401) {
                window.location.href = 'login.html';
                return;
            }
            throw new Error('Failed to fetch projects');
        }
        
        const projects = await response.json();
        state.projects = projects;
        
        // Update project switcher
        dom.projectSelect.innerHTML = '';
        projects.forEach(project => {
            const option = document.createElement('option');
            option.value = project.id;
            option.textContent = project.name;
            if (state.currentProject && state.currentProject.id === project.id) {
                option.selected = true;
            }
            dom.projectSelect.appendChild(option);
        });
        
        // If no current project selected, select the first one
        if (!state.currentProject && projects.length > 0) {
            state.currentProject = projects[0];
            localStorage.setItem(PROJECT_STORAGE_KEY, JSON.stringify(projects[0]));
            dom.projectSelect.value = projects[0].id;
        }
    } catch (error) {
        console.error('Error loading projects:', error);
    }
}

// Close Details Inspector panel
function closeInspector() {
    dom.inspectorPanel.style.display = 'none';
    state.selectedWebhookId = null;
    document.querySelectorAll('.table-row').forEach(row => row.classList.remove('selected'));
}

// Helpers
function getStatusBadge(status) {
    const s = status.toLowerCase();
    return `<span class="badge badge-${s}">${s}</span>`;
}

function formatDateTime(isoString) {
    if (!isoString) return null;
    const date = new Date(isoString);
    return date.toLocaleString();
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, function(m) { return map[m]; });
}

// ==========================================
// Projects Settings View & API handlers
// ==========================================

async function handleCreateProject(e) {
    e.preventDefault();
    
    const name = dom.projectName.value.trim();
    if (!name) return;
    
    try {
        const response = await fetchApi(`${API_BASE}/projects`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        
        if (!response.ok) {
            throw new Error('Failed to create project');
        }
        
        const project = await response.json();
        
        // Clear form
        dom.projectName.value = '';
        
        // Reload projects
        await loadProjects();
        renderProjectsList();
        
        // If this is the first project, select it
        if (state.projects.length === 1) {
            state.currentProject = project;
            localStorage.setItem(PROJECT_STORAGE_KEY, JSON.stringify(project));
            dom.projectSelect.value = project.id;
        }
    } catch (error) {
        console.error('Error creating project:', error);
        alert('Failed to create project. Please try again.');
    }
}

function renderProjectsList() {
    if (state.projects.length === 0) {
        dom.projectsList.innerHTML = `
            <div class="empty-state">No projects yet. Create your first project to get started.</div>
        `;
        return;
    }
    
    dom.projectsList.innerHTML = state.projects.map(project => {
        const isCurrent = state.currentProject && state.currentProject.id === project.id;
        const roleBadge = project.role ? `<span class="badge" style="background: var(--border-subtle);">${project.role}</span>` : '';
        
        return `
            <div class="configured-card" style="border-color: ${isCurrent ? 'var(--color-cyan)' : 'var(--border-subtle)'}">
                <div class="configured-card-header">
                    <div class="configured-card-title-group">
                        <span class="configured-card-title">${project.name}</span>
                        ${roleBadge}
                    </div>
                    ${isCurrent ? '<span class="badge" style="background: var(--color-cyan); color: var(--bg-main);">Active</span>' : ''}
                </div>
                <div class="configured-card-body">
                    <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">
                        <span style="color: var(--text-muted);">API Key:</span>
                        <span class="mono">${project.api_key}</span>
                    </div>
                    <div style="font-size: 12px; color: var(--text-secondary);">
                        <span style="color: var(--text-muted);">Created:</span>
                        ${formatDateTime(project.created_at)}
                    </div>
                </div>
                <div style="display: flex; gap: 8px; margin-top: 12px;">
                    ${!isCurrent ? `
                        <button class="btn btn-secondary btn-sm" onclick="switchToProject('${project.id}')">Switch</button>
                    ` : ''}
                    <button class="btn btn-secondary btn-sm" onclick="showTeamMembers('${project.id}', '${project.name}')">Team</button>
                </div>
            </div>
        `;
    }).join('');
}

async function switchToProject(projectId) {
    const project = state.projects.find(p => p.id === projectId);
    if (project) {
        state.currentProject = project;
        localStorage.setItem(PROJECT_STORAGE_KEY, JSON.stringify(project));
        dom.projectSelect.value = project.id;
        renderProjectsList();
        refreshAll();
    }
}

async function showTeamMembers(projectId, projectName) {
    state.selectedProjectForTeam = projectId;
    dom.teamProjectName.textContent = projectName;
    dom.teamMembersCard.style.display = 'block';
    await loadTeamMembers(projectId);
}

async function loadTeamMembers(projectId) {
    try {
        const response = await fetchApi(`${API_BASE}/projects/${projectId}/members`);
        if (!response.ok) {
            throw new Error('Failed to fetch team members');
        }
        const members = await response.json();
        renderTeamMembers(members);
    } catch (error) {
        console.error('Error loading team members:', error);
        dom.teamMembersList.innerHTML = `
            <div class="empty-state">Failed to load team members. Please try again.</div>
        `;
    }
}

function renderTeamMembers(members) {
    if (members.length === 0) {
        dom.teamMembersList.innerHTML = `
            <div class="empty-state">No team members yet. Add members to collaborate.</div>
        `;
        return;
    }
    
    dom.teamMembersList.innerHTML = members.map(member => {
        const roleBadge = `<span class="badge" style="background: var(--border-subtle);">${member.role}</span>`;
        return `
            <div class="configured-card">
                <div class="configured-card-header">
                    <div class="configured-card-title-group">
                        <span class="configured-card-title">${member.email}</span>
                        ${roleBadge}
                    </div>
                </div>
                <div class="configured-card-body">
                    <div style="font-size: 12px; color: var(--text-secondary);">
                        <span style="color: var(--text-muted);">Joined:</span>
                        ${formatDateTime(member.created_at)}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

async function handleAddMember(e) {
    e.preventDefault();
    
    if (!state.selectedProjectForTeam) {
        alert('Please select a project first');
        return;
    }
    
    const email = dom.memberEmail.value.trim();
    const role = dom.memberRole.value;
    
    if (!email) return;
    
    try {
        const response = await fetchApi(`${API_BASE}/projects/${state.selectedProjectForTeam}/members`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, role })
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to add member');
        }
        
        // Clear form
        dom.memberEmail.value = '';
        
        // Reload team members
        await loadTeamMembers(state.selectedProjectForTeam);
    } catch (error) {
        console.error('Error adding member:', error);
        alert(error.message || 'Failed to add team member. Please try again.');
    }
}

// ==========================================
// Alerts Settings View & API handlers
// ==========================================

function showView(view) {
    state.activeView = view;
    if (view === 'settings') {
        dom.dashboardContent.style.display = 'none';
        dom.projectsSettingsContent.style.display = 'none';
        dom.alertsSettingsContent.style.display = 'flex';
        dom.navAlertsBtn.classList.add('active');
        dom.navProjectsBtn.classList.remove('active');
        dom.filterBtns.forEach(btn => btn.classList.remove('active'));
        closeInspector();
        fetchAlertConfigs();
    } else if (view === 'projects') {
        dom.dashboardContent.style.display = 'none';
        dom.alertsSettingsContent.style.display = 'none';
        dom.projectsSettingsContent.style.display = 'flex';
        dom.navProjectsBtn.classList.add('active');
        dom.navAlertsBtn.classList.remove('active');
        dom.filterBtns.forEach(btn => btn.classList.remove('active'));
        closeInspector();
        renderProjectsList();
    } else {
        dom.dashboardContent.style.display = 'flex';
        dom.alertsSettingsContent.style.display = 'none';
        dom.projectsSettingsContent.style.display = 'none';
        dom.navAlertsBtn.classList.remove('active');
        dom.navProjectsBtn.classList.remove('active');
        
        // Restore active sidebar filter button highlight
        dom.filterBtns.forEach(btn => {
            if (btn.dataset.status === state.activeFilter) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
        refreshAll();
    }
}

async function fetchAlertConfigs() {
    try {
        const res = await fetchApi(`${API_BASE}/alerts`);
        if (!res.ok) throw new Error('Failed to fetch alert configurations');
        const configs = await res.json();
        renderAlertConfigs(configs);
    } catch (err) {
        console.error('Error fetching alert configurations:', err);
    }
}

function renderAlertConfigs(configs) {
    if (configs.length === 0) {
        dom.configuredChannelsList.innerHTML = `
            <div class="empty-state">No alert destinations configured yet. Configure one on the left to start receiving alerts.</div>
        `;
        return;
    }

    dom.configuredChannelsList.innerHTML = configs.map(c => {
        const isSlack = c.channel_type === 'slack';
        const typeBadge = `<span class="channel-icon-badge ${c.channel_type}">${c.channel_type}</span>`;
        const bodyContent = isSlack 
            ? `Webhook URL: <span class="mono">${c.config.webhook_url}</span>`
            : `SMTP: <span class="mono">${c.config.smtp_host}:${c.config.smtp_port}</span> &bull; To: <span class="mono">${c.config.to}</span>`;

        return `
            <div class="configured-card" data-id="${c.id}">
                <div class="configured-card-header">
                    <div class="configured-card-title-group">
                        ${typeBadge}
                        <span class="channel-name">${escapeHtml(c.name)}</span>
                    </div>
                    <div class="configured-card-actions">
                        <button class="btn btn-secondary btn-xs test-alert-btn" onclick="handleTestAlert('${c.id}')" style="padding: 4px 8px; font-size: 11px;">Test</button>
                        <button class="btn btn-secondary btn-xs delete-alert-btn" onclick="handleDeleteAlert('${c.id}')" style="padding: 4px 8px; font-size: 11px; color: var(--color-rose); border-color: rgba(239,68,68,0.2)">Delete</button>
                        <label class="switch">
                            <input type="checkbox" ${c.enabled ? 'checked' : ''} onchange="handleToggleAlert('${c.id}', this.checked)">
                            <span class="slider"></span>
                        </label>
                    </div>
                </div>
                <div class="configured-card-body" style="margin-top: 8px;">
                    ${bodyContent}
                </div>
            </div>
        `;
    }).join('');
}

window.handleTestAlert = async function(id) {
    try {
        const res = await fetchApi(`${API_BASE}/alerts/${id}/test`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Test alert failed');
        alert(data.message || 'Test alert sent successfully!');
    } catch (err) {
        alert(`Failed to send test alert: ${err.message}`);
    }
};

window.handleDeleteAlert = async function(id) {
    if (!confirm('Are you sure you want to delete this alert destination?')) return;
    try {
        const res = await fetchApi(`${API_BASE}/alerts/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Failed to delete alert configuration');
        fetchAlertConfigs();
    } catch (err) {
        alert(`Failed to delete alert: ${err.message}`);
    }
};

window.handleToggleAlert = async function(id, enabled) {
    try {
        const res = await fetchApi(`${API_BASE}/alerts/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        if (!res.ok) throw new Error('Failed to toggle alert configuration');
    } catch (err) {
        alert(`Failed to toggle alert: ${err.message}`);
        fetchAlertConfigs(); // reload to reset toggle state
    }
};

async function handleSlackFormSubmit(e) {
    e.preventDefault();
    const name = document.getElementById('slack-name').value.trim();
    const webhookUrl = document.getElementById('slack-webhook-url').value.trim();

    try {
        const res = await fetchApi(`${API_BASE}/alerts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                channel_type: 'slack',
                config: { webhook_url: webhookUrl }
            })
        });
        if (!res.ok) {
            const data = await res.json();
            throw new Error(data.detail || 'Failed to create Slack alert config');
        }
        
        // Reset form and reload
        document.getElementById('slack-alert-form').reset();
        fetchAlertConfigs();
    } catch (err) {
        alert(`Failed to save Slack alert: ${err.message}`);
    }
}

async function handleEmailFormSubmit(e) {
    e.preventDefault();
    const name = document.getElementById('email-name').value.trim();
    const smtpHost = document.getElementById('email-smtp-host').value.trim();
    const smtpPort = parseInt(document.getElementById('email-smtp-port').value);
    const fromEmail = document.getElementById('email-from').value.trim();
    const toEmail = document.getElementById('email-to').value.trim();
    const username = document.getElementById('email-username').value.trim() || null;
    const password = document.getElementById('email-password').value.trim() || null;
    const useTls = document.getElementById('email-use-tls').checked;

    const config = {
        smtp_host: smtpHost,
        smtp_port: smtpPort,
        from: fromEmail,
        to: toEmail,
        use_tls: useTls
    };
    if (username) config.username = username;
    if (password) config.password = password;

    try {
        const res = await fetchApi(`${API_BASE}/alerts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                channel_type: 'email',
                config
            })
        });
        if (!res.ok) {
            const data = await res.json();
            throw new Error(data.detail || 'Failed to create Email alert config');
        }
        
        // Reset form and reload
        document.getElementById('email-alert-form').reset();
        fetchAlertConfigs();
    } catch (err) {
        alert(`Failed to save Email alert: ${err.message}`);
    }
}

