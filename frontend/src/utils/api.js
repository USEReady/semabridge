const API_BASE_URL = 'http://127.0.0.1:8001/api';
const AUTH_BASE_URL = 'http://127.0.0.1:8001/auth';
const TOKEN_KEY = 'semabridge-token';

function getAuthHeaders() {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) return { Authorization: `Bearer ${token}` };
    return {};
}

async function handleResponse(res) {
    if (res.status === 401) {
        // Token expired or invalid â€” clear it so the UI shows login
        localStorage.removeItem(TOKEN_KEY);
        window.dispatchEvent(new Event('semabridge:auth-expired'));
    }
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`API Error ${res.status}: ${text}`);
    }
    return res.json();
}

/**
 * Wrapper around fetch that automatically injects the JWT
 * Authorization header when a token is stored.
 */
async function authFetch(url, options = {}) {
    const headers = { ...getAuthHeaders(), ...options.headers };
    return fetch(url, { ...options, headers });
}

export const api = {
    async getHealth() {
        const res = await authFetch(`${API_BASE_URL}/health`);
        return handleResponse(res);
    },

    async getDiscovery(sourceType, workspaceId) {
        const url = workspaceId
            ? `${API_BASE_URL}/discovery/${sourceType}?workspace_id=${workspaceId}`
            : `${API_BASE_URL}/discovery/${sourceType}`;
        const res = await authFetch(url);
        return handleResponse(res);
    },

    async getConfig() {
        const res = await authFetch(`${API_BASE_URL}/config`);
        return handleResponse(res);
    },

    async generateConfig(payload) {
        const res = await authFetch(`${API_BASE_URL}/config/generate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        if (!res.ok) {
            const errText = await res.text();
            throw new Error(errText);
        }

        return res.json();
    },

    async validateConfig(content) {
        const res = await authFetch(`${API_BASE_URL}/config/validate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        return handleResponse(res);
    },

    async getHistory() {
        const res = await authFetch(`${API_BASE_URL}/history`);
        return handleResponse(res);
    },

    async sync(payload) {
        const res = await authFetch(`${API_BASE_URL}/sync`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        return handleResponse(res);
    },

    // Model CRUD (local repository + DuckDB versioning)
    async getModel(modelId) {
        const res = await authFetch(`${API_BASE_URL}/models/${encodeURIComponent(modelId)}`);
        return handleResponse(res);
    },

    async saveModel(modelId, content, message = 'Saved from UI', author = 'ui') {
        const res = await authFetch(`${API_BASE_URL}/models/${encodeURIComponent(modelId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content, message, author }),
        });
        return handleResponse(res);
    },

    // Version Control (new model-versions API)
    async getModelVersions(modelId = '', workspaceId = '', limit = 50) {
        const params = new URLSearchParams();
        if (modelId) params.set('model_id', modelId);
        if (workspaceId) params.set('workspace_id', workspaceId);
        if (limit) params.set('limit', limit);
        const res = await authFetch(`${API_BASE_URL}/model-versions?${params}`);
        return handleResponse(res);
    },

    async compareVersions(versionFrom, versionTo) {
        const res = await authFetch(`${API_BASE_URL}/model-versions/compare?v1=${versionFrom}&v2=${versionTo}`);
        return handleResponse(res);
    },

    async rollbackVersion(versionId, modelId = 'default', workspaceId = 'default') {
        const res = await authFetch(`${API_BASE_URL}/model-versions/rollback`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ version_id: versionId, model_id: modelId, workspace_id: workspaceId }),
        });
        return handleResponse(res);
    },

    async deleteAllVersions(modelId, workspaceId = '') {
        const params = new URLSearchParams({ model_id: modelId });
        if (workspaceId) params.set('workspace_id', workspaceId);
        const res = await authFetch(`${API_BASE_URL}/model-versions?${params}`, {
            method: 'DELETE',
        });
        return handleResponse(res);
    },

    // Workspaces
    async getWorkspaces() {
        const res = await authFetch(`${API_BASE_URL}/workspaces`);
        return handleResponse(res);
    },

    // â”€â”€ Repository Map â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    async getRepoTree() {
        const res = await authFetch(`${API_BASE_URL}/repo/tree`);
        return handleResponse(res);
    },

    async getRepoModels(workspaceId = '') {
        const params = workspaceId ? `?workspace_id=${workspaceId}` : '';
        const res = await authFetch(`${API_BASE_URL}/repo/models${params}`);
        return handleResponse(res);
    },

    async getModelGraph(modelId = '__all__') {
        const res = await authFetch(`${API_BASE_URL}/repo/models/${encodeURIComponent(modelId)}/graph`);
        return handleResponse(res);
    },

    async getRepoFile(path) {
        const res = await authFetch(`${API_BASE_URL}/repo/file?path=${encodeURIComponent(path)}`);
        return handleResponse(res);
    },

    async syncRepo() {
        const res = await authFetch(`${API_BASE_URL}/repo/sync`, { method: 'POST' });
        return handleResponse(res);
    },

    // Live Validation
    async validateLive() {
        const res = await authFetch(`${API_BASE_URL}/config/validate-live`, { method: 'POST' });
        return handleResponse(res);
    },

    // Version snapshot
    async getVersionSnapshot(versionId) {
        const res = await authFetch(`${API_BASE_URL}/model-versions/snapshot?version_id=${encodeURIComponent(versionId)}`);
        return handleResponse(res);
    },

    // â”€â”€ PBIX Import â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    async importPbix(pbixPath) {
        const res = await authFetch(`${API_BASE_URL}/pbix/import`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pbix_path: pbixPath }),
        });
        return handleResponse(res);
    },

    async browsePbixFiles(directory = '') {
        const params = directory ? `?directory=${encodeURIComponent(directory)}` : '';
        const res = await authFetch(`${API_BASE_URL}/pbix/browse${params}`);
        return handleResponse(res);
    },

    // â”€â”€ Composite Models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    async registerCompositeReport(reportName, sourcePath, workspaceId, connections) {
        const res = await authFetch(`${API_BASE_URL}/composite/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_name: reportName,
                source_path: sourcePath,
                workspace_id: workspaceId,
                connections,
            }),
        });
        return handleResponse(res);
    },

    async getImpactAnalysis(modelGuid) {
        const res = await authFetch(`${API_BASE_URL}/composite/impact/${encodeURIComponent(modelGuid)}`);
        return handleResponse(res);
    },

    async getCompositeLinks() {
        const res = await authFetch(`${API_BASE_URL}/composite/links`);
        return handleResponse(res);
    },

    // â”€â”€ Multi-Workspace Discovery â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    async discoverMultiWorkspace(workspaceIds) {
        const res = await authFetch(`${API_BASE_URL}/multi-workspace/discover`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace_ids: workspaceIds }),
        });
        return handleResponse(res);
    },

    // â”€â”€ Connections (UI-Driven Auth) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    async getConnectionsStatus() {
        const res = await authFetch(`${API_BASE_URL}/connections/status`);
        return handleResponse(res);
    },

    async saveConnection(service, credentials) {
        const res = await authFetch(`${API_BASE_URL}/connections/${service}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(credentials),
        });
        return handleResponse(res);
    },

    async testConnection(service) {
        const res = await authFetch(`${API_BASE_URL}/connections/${service}/test`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    async deleteConnection(service) {
        const res = await authFetch(`${API_BASE_URL}/connections/${service}`, {
            method: 'DELETE',
        });
        return handleResponse(res);
    },

    // â”€â”€ Fabric Interactive Login (Device Code) â”€â”€â”€â”€â”€â”€
    async fabricLogin(tenantId = null) {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(tenantId ? { tenant_id: tenantId } : {}),
        });
        return handleResponse(res);
    },

    async fabricPoll() {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/poll`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    async fabricAuthStatus() {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/auth-status`);
        return handleResponse(res);
    },

    async fabricLogout() {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/logout`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    // â”€â”€ Fabric Workspace Discovery â”€â”€â”€â”€â”€â”€
    async fabricListWorkspaces() {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/workspaces`);
        return handleResponse(res);
    },

    async fabricSelectWorkspace(workspaceId, workspaceName) {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/select-workspace`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace_id: workspaceId, workspace_name: workspaceName }),
        });
        return handleResponse(res);
    },

    // â”€â”€ Snowflake SSO â”€â”€â”€â”€â”€â”€
    async snowflakeSsoLogin() {
        const res = await authFetch(`${API_BASE_URL}/connections/snowflake/sso-login`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    // â”€â”€ DuckDB Snapshot Explorer â”€â”€â”€â”€â”€â”€
    async getSnapshotTree() {
        const res = await authFetch(`${API_BASE_URL}/repo/snapshots/tree`);
        return handleResponse(res);
    },

    async getSnapshotFile(modelId) {
        const res = await authFetch(`${API_BASE_URL}/repo/snapshots/file?model_id=${encodeURIComponent(modelId)}`);
        return handleResponse(res);
    },

    // ── Semantic Model Sync (Fabric ↔ Snowflake) ─────────────────────────
    async getSemanticDiscovery() {
        const res = await authFetch(`${API_BASE_URL}/discovery/semantic`);
        return handleResponse(res);
    },

    async triggerSemanticSync(payload) {
        const res = await authFetch(`${API_BASE_URL}/semantic/sync`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        return handleResponse(res);
    },

    async refreshSemanticMetadata(payload = {}) {
        const res = await authFetch(`${API_BASE_URL}/semantic/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        return handleResponse(res);
    },

    // â”€â”€ Global Config (~/.semabridge/config.yaml) â”€â”€â”€â”€â”€â”€
    async getGlobalConfig() {
        const res = await authFetch(`${API_BASE_URL}/global-config`);
        return handleResponse(res);
    },

    async saveGlobalConfig(content) {
        const res = await authFetch(`${API_BASE_URL}/global-config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        return handleResponse(res);
    },
};

