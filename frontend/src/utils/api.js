// ...existing code...
// (Removed duplicate export of api. Only export once at the end of the file, with getDatabricksSources included as a method.)
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const TOKEN_KEY = 'semabridge-token';
const FABRIC_TOKEN_KEY = 'semabridge-fabric-token';
const FABRIC_TOKEN_EXPIRES_KEY = 'semabridge-fabric-token-expires';

function extractSnapshotList(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.snapshots)) return payload.snapshots;
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
}

export function formatDate(dateString) {
    if (!dateString) return '—';
    const d = new Date(dateString);
    // Handle invalid dates and epoch (1970-01-01)
    if (isNaN(d.getTime()) || d.getTime() <= 86400000) { // 86400000 = 1 day in ms
        return '—';
    }
    return d.toLocaleString(undefined, {
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
        hour: 'numeric',
        minute: 'numeric',
        second: 'numeric'
    });
}

function normalizeProject(project) {
    if (!project || typeof project !== 'object') return project;

    // Some backend compatibility modes wrap created project as { status, project: {...} }
    if (project.project && typeof project.project === 'object') {
        return normalizeProject(project.project);
    }

    const id = project.id ?? project.project_id ?? null;
    const source = project.source ?? project.adapter ?? project.source_type ?? null;
    const targetType = project.target_type ?? project.target?.type ?? project.targets?.[0]?.type ?? null;

    return {
        ...project,
        id,
        project_id: project.project_id ?? id,
        source,
        adapter: project.adapter ?? source,
        target_type: targetType,
    };
}

function dedupeProjects(projects) {
    const byId = new Map();
    const unnamed = [];

    for (const raw of (projects || [])) {
        const p = normalizeProject(raw);
        const id = p?.id != null ? String(p.id) : '';

        if (!id) {
            unnamed.push(p);
            continue;
        }

        const existing = byId.get(id);
        if (!existing) {
            byId.set(id, p);
            continue;
        }

        // Keep the freshest payload when duplicate IDs are returned.
        const prevTs = String(existing.updated_at || existing.created_at || '');
        const nextTs = String(p.updated_at || p.created_at || '');
        if (nextTs.localeCompare(prevTs) >= 0) {
            byId.set(id, p);
        }
    }

    return [...byId.values(), ...unnamed];
}

function normalizeFolder(folder) {
    if (!folder || typeof folder !== 'object') return folder;

    const id = folder.id ?? folder.folder_id ?? null;

    return {
        ...folder,
        id,
        folder_id: folder.folder_id ?? id,
    };
}

function normalizeWorkspace(workspace) {
    if (!workspace || typeof workspace !== 'object') return workspace;

    const id = workspace.id ?? workspace.workspace_id ?? null;
    const name = workspace.name ?? workspace.display_name ?? workspace.displayName ?? id;

    return {
        ...workspace,
        id,
        workspace_id: workspace.workspace_id ?? id,
        name,
    };
}

function normalizeLocalFolder(folder) {
    if (!folder || typeof folder !== 'object') return folder;

    const id = folder.id ?? folder.folder_id ?? null;

    return {
        ...folder,
        id,
        folder_id: folder.folder_id ?? id,
        tag_name: folder.tag_name ?? folder.tag ?? '',
        absolute_path: folder.absolute_path ?? folder.path ?? '',
        is_active: folder.is_active ?? true,
    };
}

function getAuthHeaders() {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) return { Authorization: `Bearer ${token}` };
    return {};
}

/**
 * Return the stored Fabric MSAL access token as a Bearer header if valid.
 * This is injected on Fabric-specific API calls so the backend resolves
 * the correct per-user token instead of falling back to the shared DB row.
 */
function getFabricAuthHeaders() {
    const token = localStorage.getItem(FABRIC_TOKEN_KEY);
    const expiresAt = parseInt(localStorage.getItem(FABRIC_TOKEN_EXPIRES_KEY) || '0', 10);
    if (token && Date.now() < expiresAt) {
        return { Authorization: `Bearer ${token}` };
    }
    // Token expired or missing - clean up stale values.
    localStorage.removeItem(FABRIC_TOKEN_KEY);
    localStorage.removeItem(FABRIC_TOKEN_EXPIRES_KEY);
    return {};
}

function hasValidFabricToken() {
    const token = localStorage.getItem(FABRIC_TOKEN_KEY);
    const expiresAt = parseInt(localStorage.getItem(FABRIC_TOKEN_EXPIRES_KEY) || '0', 10);
    return Boolean(token && Date.now() < expiresAt);
}

const AUTH_BASE = (import.meta.env.VITE_AUTH_BASE_URL || '/auth').replace(/\/$/, '');

let _refreshPromise = null;

/**
 * Attempt to refresh the JWT access token using the HttpOnly refresh cookie.
 * Falls back to auto-login if the refresh cookie is missing or expired.
 * Uses a singleton promise to prevent concurrent refresh races.
 *
 * Industry pattern: coalesce all concurrent 401 recovery attempts into a
 * single promise so that parallel API calls don't each trigger separate
 * refresh/auto-login requests.
 */
async function tryRefreshToken() {
    if (_refreshPromise) return _refreshPromise;
    _refreshPromise = (async () => {
        try {
            // Step 1: Try refresh via HttpOnly cookie
            const res = await fetch(`${AUTH_BASE}/refresh`, {
                method: 'POST',
                credentials: 'include',
            });
            if (res.ok) {
                const data = await res.json();
                if (data.access_token) {
                    localStorage.setItem(TOKEN_KEY, data.access_token);
                    window.dispatchEvent(new CustomEvent('semabridge:token-refreshed', { detail: data.access_token }));
                    return data.access_token;
                }
            }

            // Step 2: Refresh cookie failed — try auto-login (dev mode)
            const autoRes = await fetch(`${AUTH_BASE}/auto-login`, {
                method: 'POST',
                credentials: 'include',
            });
            if (autoRes.ok) {
                const autoData = await autoRes.json();
                if (autoData.access_token) {
                    localStorage.setItem(TOKEN_KEY, autoData.access_token);
                    window.dispatchEvent(new CustomEvent('semabridge:token-refreshed', { detail: autoData.access_token }));
                    return autoData.access_token;
                }
            }

            return null;
        } catch {
            return null;
        } finally {
            _refreshPromise = null;
        }
    })();
    return _refreshPromise;
}

async function handleResponse(res) {
    if (res.status === 401) {
        try {
            const cloned = res.clone();
            const data = await cloned.json();
            if (data?.error === 'reauth_required' || data?.detail?.error === 'reauth_required') {
                return data.detail || data;
            }
        } catch (e) { }

        // Try refresh + auto-login before giving up
        const _retryFn = res._retryFn;
        if (_retryFn) {
            const newToken = await tryRefreshToken();
            if (newToken) {
                // Retry the original request with the new token
                const retryRes = await _retryFn(newToken);
                if (retryRes.ok) return retryRes.json();
            }
        }

        // All recovery failed — notify AuthContext to handle cleanup.
        // Important: Do NOT clear localStorage here. AuthContext's
        // silentRecover will decide whether to clear state.
        window.dispatchEvent(new Event('semabridge:auth-expired'));
        throw new Error('Session expired. Please log in again.');
    }
    if (!res.ok) {
        const text = await res.text();
        let detail = text;
        let parsed = null;
        try {
            parsed = JSON.parse(text);
            if (typeof parsed?.detail === 'string' && parsed.detail.trim()) {
                detail = parsed.detail;
            } else if (typeof parsed?.detail?.message === 'string' && parsed.detail.message.trim()) {
                detail = parsed.detail.message;
            } else if (typeof parsed?.message === 'string' && parsed.message.trim()) {
                detail = parsed.message;
            }
        } catch (e) { }
        const error = new Error(`API Error ${res.status}: ${detail}`);
        error.status = res.status;
        error.payload = parsed;
        throw error;
    }
    return res.json();
}

/**
 * Wrapper around fetch that automatically injects the JWT
 * Authorization header when a token is stored.
 */
async function authFetch(url, options = {}) {
    // Inject X-Fabric-Context header if workspace ID is available
    let workspaceId = null;
    try {
        workspaceId = localStorage.getItem('FABRIC_WORKSPACE_ID');
    } catch (e) { }
    const headers = { ...getAuthHeaders(), ...options.headers };
    if (workspaceId) {
        headers['X-Fabric-Context'] = workspaceId;
    }
    const res = await fetch(url, { ...options, headers, credentials: 'include' });

    // Attach retry info so handleResponse can retry on 401 with a fresh token
    res._retryFn = (newToken) => {
        const retryHeaders = { ...headers, Authorization: `Bearer ${newToken}` };
        return fetch(url, { ...options, headers: retryHeaders, credentials: 'include' });
    };
    return res;
}

export const api = {
    // ── Configuration ─────────────────────────────────────────────────────
    async getConfig() {
        const res = await authFetch(`${API_BASE_URL}/config`);
        return handleResponse(res);
    },

    async saveConfig(newConfig) {
        const res = await authFetch(`${API_BASE_URL}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newConfig),
        });
        return handleResponse(res);
    },

    async validateLive(payload) {
        const res = await authFetch(`${API_BASE_URL}/config/validate-live`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        return handleResponse(res);
    },

    // ── Auth & Identity Vault ──────────────────────────────────────────────
    async getAccounts(connectorType = '') {
        const query = connectorType ? `?connector_type=${encodeURIComponent(connectorType)}` : '';
        const res = await authFetch(`${API_BASE_URL}/accounts${query}`);
        return handleResponse(res);
    },

    async createAccount(payload) {
        const res = await authFetch(`${API_BASE_URL}/accounts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        return handleResponse(res);
    },

    async deleteAccount(accountId) {
        const res = await authFetch(`${API_BASE_URL}/accounts/${accountId}`, {
            method: 'DELETE'
        });
        if (res.status === 204) return null;
        return handleResponse(res);
    },

    async updateAccountTag(accountId, tag) {
        const res = await authFetch(`${API_BASE_URL}/accounts/${accountId}/tag`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag })
        });
        return handleResponse(res);
    },

    async linkProjectAccount(projectId, payload) {
        const res = await authFetch(`${API_BASE_URL}/accounts/project/${projectId}/link-account`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        return handleResponse(res);
    },

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

    async getConflicts(runId) {
        const res = await authFetch(`${API_BASE_URL}/runs/${encodeURIComponent(runId)}/conflicts`);
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
        const data = await handleResponse(res);
        return (data || []).map(normalizeWorkspace);
    },

    // --- Repository Map ---
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

    async getGraphSnapshot(modelId = '__all__', snapshotId, includeSystemTables = false) {
        if (!snapshotId) {
            throw new Error('snapshotId is required');
        }
        const params = new URLSearchParams({
            include_system_tables: includeSystemTables ? 'true' : 'false',
        });
        const res = await authFetch(`${API_BASE_URL}/graph/${encodeURIComponent(modelId)}/snapshot/${encodeURIComponent(snapshotId)}?${params}`);
        return handleResponse(res);
    },

    async getGraphSnapshots(modelId = '__all__') {
        const res = await authFetch(`${API_BASE_URL}/graph/${encodeURIComponent(modelId)}/snapshots`);
        const directData = await handleResponse(res);
        const directList = extractSnapshotList(directData);

        // Compatibility: some backends store snapshots per project_id and return
        // no rows for the synthetic __all__ identifier.
        if (String(modelId) !== '__all__' || directList.length > 0) {
            return directList;
        }

        try {
            const projects = await this.listProjects();
            const ids = [...new Set((projects || []).map((p) => p?.id || p?.project_id).filter(Boolean).map(String))];
            if (!ids.length) return [];

            const nestedResults = await Promise.all(
                ids.map(async (id) => {
                    try {
                        const projectRes = await authFetch(`${API_BASE_URL}/graph/${encodeURIComponent(id)}/snapshots`);
                        const projectData = await handleResponse(projectRes);
                        return extractSnapshotList(projectData).map((row) => ({
                            ...row,
                            model_name: row?.model_name || id,
                        }));
                    } catch {
                        return [];
                    }
                })
            );

            const deduped = new Map();
            for (const list of nestedResults) {
                for (const item of list) {
                    const sid = String(item?.snapshot_id || '');
                    if (!sid) continue;
                    deduped.set(sid, item);
                }
            }

            return [...deduped.values()].sort(
                (a, b) => new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime()
            );
        } catch {
            return [];
        }
    },

    async compareGraphSnapshots(modelId = '__all__', fromSnapshotId, toSnapshotId, includeSystemTables = false) {
        if (!fromSnapshotId || !toSnapshotId) {
            throw new Error('fromSnapshotId and toSnapshotId are required');
        }
        const params = new URLSearchParams({
            from_snapshot_id: String(fromSnapshotId),
            to_snapshot_id: String(toSnapshotId),
            include_system_tables: includeSystemTables ? 'true' : 'false',
        });
        const res = await authFetch(`${API_BASE_URL}/graph/${encodeURIComponent(modelId)}/compare?${params}`);
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


    // Version snapshot
    async getVersionSnapshot(versionId) {
        const res = await authFetch(`${API_BASE_URL}/model-versions/snapshot?version_id=${encodeURIComponent(versionId)}`);
        return handleResponse(res);
    },

    // --- PBIX Import ---
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

    // --- Composite Models ---
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

    // --- Multi-Workspace Discovery ---
    async discoverMultiWorkspace(workspaceIds) {
        const res = await authFetch(`${API_BASE_URL}/multi-workspace/discover`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace_ids: workspaceIds }),
        });
        return handleResponse(res);
    },

    // --- Connections (UI-Driven Auth) ---
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

    // --- Fabric Interactive Login (Device Code) ---
    async fabricLogin(tenantId = null) {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(tenantId ? { tenant_id: tenantId } : {}),
        });
        const data = await handleResponse(res);
        // data now contains { flow_id, user_code, verification_uri, ... }
        return data;
    },

    async fabricPoll(flowId) {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/poll`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ flow_id: flowId }),
        });
        const data = await handleResponse(res);

        // On success, store the MSAL token in localStorage for Bearer passthrough.
        if (data.status === 'success' && data.access_token) {
            localStorage.setItem(FABRIC_TOKEN_KEY, data.access_token);
            const expiresAt = Date.now() + (data.expires_in || 3600) * 1000;
            localStorage.setItem(FABRIC_TOKEN_EXPIRES_KEY, String(expiresAt));
        }
        return data;
    },

    async fabricAuthStatus() {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/auth-status`);
        return handleResponse(res);
    },

    async refreshFabricCredentials() {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/refresh-token`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    async fabricLogout() {
        localStorage.removeItem(FABRIC_TOKEN_KEY);
        localStorage.removeItem(FABRIC_TOKEN_EXPIRES_KEY);
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/logout`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    // --- Fabric Workspace Discovery ---
    async fabricListWorkspaces(accountId = '') {
        const resolvedConnectionId = String(accountId || '').trim();
        if (!resolvedConnectionId && !hasValidFabricToken()) {
            return { workspaces: [] };
        }
        const query = resolvedConnectionId
            ? `?identity_id=${encodeURIComponent(resolvedConnectionId)}&connectionId=${encodeURIComponent(resolvedConnectionId)}`
            : '';
        const res = await fetch(`${API_BASE_URL}/connections/fabric/workspaces${query}`, {
            headers: { ...getAuthHeaders(), ...getFabricAuthHeaders() },
        });
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

    // ── Snowflake OAuth S2S ──────
    async snowflakeOAuthTest(body) {
        const res = await authFetch(`${API_BASE_URL}/connections/snowflake/oauth-test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        return handleResponse(res);
    },

    // ── Databricks Native OAuth (U2M / PKCE) ──────
    async databricksLogin(host, clientId, redirectUri) {
        const res = await authFetch(`${API_BASE_URL}/connections/databricks/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ host, client_id: clientId, redirect_uri: redirectUri }),
        });
        return handleResponse(res);
    },

    async databricksPoll(flowId, connectionConfig = {}) {
        const res = await authFetch(`${API_BASE_URL}/connections/databricks/poll`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ flow_id: flowId, ...connectionConfig }),
        });
        return handleResponse(res);
    },

    async databricksAuthStatus() {
        const res = await authFetch(`${API_BASE_URL}/connections/databricks/auth-status`);
        return handleResponse(res);
    },

    async databricksLogout() {
        const res = await authFetch(`${API_BASE_URL}/connections/databricks/logout`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    // --- DuckDB Snapshot Explorer ---
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

    async getSemanticSyncStatus(jobId) {
        const res = await authFetch(`${API_BASE_URL}/semantic/sync/${encodeURIComponent(jobId)}`);
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

    // --- Global Config (~/.semabridge/config.yaml) ---
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

    // ── Projects ───────────────────────────────────────────────────────────
    async listProjects() {
        const res = await authFetch(`${API_BASE_URL}/projects`);
        const data = await handleResponse(res);
        return dedupeProjects(data || []);
    },

    async getProjectRuns(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/runs`);
        return handleResponse(res);
    },

    async getProjectLineage(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/lineage`);
        return handleResponse(res);
    },

    async tagSnapshot(projectId, snapshotId, tag, comment = '') {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/${snapshotId}/tag?tag=${encodeURIComponent(tag)}&comment=${encodeURIComponent(comment)}`, {
            method: 'PUT'
        });
        return handleResponse(res);
    },

    async toggleSnapshotPin(projectId, snapshotId, isPinned) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/${snapshotId}/pin?is_pinned=${isPinned}`, {
            method: 'PUT'
        });
        return handleResponse(res);
    },

    async previewRestore(projectId, snapshotId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/${snapshotId}/preview-restore`);
        return handleResponse(res);
    },

    async compareProjectSnapshots(projectId, s1, s2) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/compare?s1=${s1}&s2=${s2}`);
        return handleResponse(res);
    },

    async getSnapshotContent(projectId, snapshot_id) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/${snapshot_id}/content`);
        return handleResponse(res);
    },

    async getSnapshotReport(projectId, snapshot_id) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/${snapshot_id}/report`);
        return handleResponse(res);
    },

    async manualDeploy(projectId, snapshotId, comment = '') {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/${snapshotId}/deploy?comment=${encodeURIComponent(comment)}`, {
            method: 'POST'
        });
        return handleResponse(res);
    },

    async restoreProjectVersion(projectId, payload) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...payload, run_type: 'restore' })
        });
        return handleResponse(res);
    },

    // Generic fetch for specialized calls
    async apiFetch(url, options = {}) {
        const fullUrl = url.startsWith('http') ? url : `${API_BASE_URL}${url}`;
        const res = await authFetch(fullUrl, options);
        return res; // Return raw response so caller can handle json()
    },

    async listProjectDiscovery() {
        const res = await authFetch(`${API_BASE_URL}/projects/discovery`);
        const data = await handleResponse(res);
        return Array.isArray(data) ? data : [];
    },

    async getProject(projectId, options = {}) {
        const noCache = Boolean(options?.noCache);
        const query = noCache ? `?refresh=${Date.now()}` : '';
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}${query}`);
        const data = await handleResponse(res);
        return normalizeProject(data);
    },

    async createProject(data) {
        const res = await authFetch(`${API_BASE_URL}/projects`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const created = await handleResponse(res);
        return normalizeProject(created?.project ?? created);
    },

    async uploadPbix(file) {
        const form = new FormData();
        form.append('file', file);
        const res = await authFetch(`${API_BASE_URL}/upload`, {
            method: 'POST',
            body: form,
        });
        return handleResponse(res);
    },

    async uploadProjectPbix(projectId, file) {
        const form = new FormData();
        form.append('file', file);
        const res = await authFetch(`${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/upload`, {
            method: 'POST',
            body: form,
        });
        return handleResponse(res);
    },

    async updateProject(projectId, data) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return handleResponse(res);
    },

    async deleteProject(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}`, {
            method: 'DELETE',
        });
        if (res.status === 204) return null;
        return handleResponse(res);
    },

    async compareProjectSnapshots(projectId, fromSnapshotId, toSnapshotId) {
        const params = new URLSearchParams();
        params.set('from_snapshot_id', fromSnapshotId);
        params.set('to_snapshot_id', toSnapshotId);
        params.set('include_states', 'false');

        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/compare?${params.toString()}`);
        return handleResponse(res);
    },

    // ── Job Runs ───────────────────────────────────────────────────────────
    async listJobRuns(filters = {}) {
        const params = new URLSearchParams();
        if (filters.status) params.set('status', filters.status);
        if (filters.project_id) params.set('project_id', filters.project_id);
        const query = params.toString();
        const res = await authFetch(`${API_BASE_URL}/jobs/runs${query ? `?${query}` : ''}`);
        const runs = await handleResponse(res);
        return (Array.isArray(runs) ? runs : []).map((run) => ({
            ...run,
            id: run?.id ?? run?.run_id,
            run_id: run?.run_id ?? run?.id,
            source_type: run?.source_type ?? run?.source ?? run?.adapter,
            message: run?.message ?? run?.error ?? run?.error_message ?? '',
            before_target_snapshot_ids: Array.isArray(run?.before_target_snapshot_ids) ? run.before_target_snapshot_ids : (run?.before_target_snapshot_ids ? [run.before_target_snapshot_ids] : []),
            after_target_snapshot_ids: Array.isArray(run?.after_target_snapshot_ids) ? run.after_target_snapshot_ids : (run?.after_target_snapshot_ids ? [run.after_target_snapshot_ids] : []),
            after_tgt_snapshots: Array.isArray(run?.after_tgt_snapshots) ? run.after_tgt_snapshots : (run?.after_tgt_snapshots ? [run.after_tgt_snapshots] : []),
            error: run?.error ?? run?.error_message ?? '',
        }));
    },

    async listJobSchedules() {
        const res = await authFetch(`${API_BASE_URL}/jobs/schedules`);
        if (res.status === 404) {
            return [];
        }
        return handleResponse(res);
    },


    async getJobConfig(projectId) {
        const url = projectId
            ? `${API_BASE_URL}/jobs/config?project_id=${projectId}`
            : `${API_BASE_URL}/jobs/config`;
        const res = await authFetch(url);
        return handleResponse(res);
    },

    async updateJobConfig(projectId, configData) {
        const res = await authFetch(`${API_BASE_URL}/jobs/config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(typeof projectId === 'object' ? projectId : { project_id: projectId, ...configData }),
        });
        return handleResponse(res);
    },

    async triggerJob(projectId) {
        const res = await authFetch(`${API_BASE_URL}/jobs/trigger`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(projectId ? { project_id: projectId } : {}),
        });
        return handleResponse(res);
    },

    // ── Model Mapping ──────────────────────────────────────────────────────
    async getMappings(projectId) {
        const url = projectId
            ? `${API_BASE_URL}/mappings?project_id=${projectId}`
            : `${API_BASE_URL}/mappings`;
        const res = await authFetch(url);
        return handleResponse(res);
    },

    async listMappings(projectId) {
        return this.getMappings(projectId);
    },

    async autoMap(projectIdOrPayload) {
        const res = await authFetch(`${API_BASE_URL}/mappings/auto`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(
                typeof projectIdOrPayload === 'object' && projectIdOrPayload !== null
                    ? projectIdOrPayload
                    : (projectIdOrPayload ? { project_id: projectIdOrPayload } : {})
            ),
        });
        return handleResponse(res);
    },

    async runProjectDryRun(projectId, payload) {
        try {
            const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/dry-run`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            
            // Handle non-200 responses
            if (!res.ok) {
                const errorText = await res.text();
                console.error('[API] Dry run failed:', res.status, errorText);
                throw new Error(`Dry run failed: ${res.status} - ${errorText}`);
            }
            
            const data = await res.json();
            
            // Validate response has required structure
            if (!data || typeof data !== 'object') {
                throw new Error('Invalid response format: not an object');
            }
            
            // Ensure entity_mappings exists (even if empty)
            if (!data.entity_mappings) {
                data.entity_mappings = [];
            }
            
            if (!Array.isArray(data.entity_mappings)) {
                console.warn('[API] entity_mappings is not an array, fixing...');
                data.entity_mappings = [];
            }
            
            return data;
            
        } catch (error) {
            console.error('[API] runProjectDryRun error:', error);
            throw error;
        }
    },

    async updateMapping(projectId, mappingId, targetNameOrPayload) {
        // Accept either a plain string (legacy) or a full payload object
        const body = typeof targetNameOrPayload === 'object' && targetNameOrPayload !== null
            ? targetNameOrPayload
            : { target_name: targetNameOrPayload };
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/mappings/${mappingId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        return handleResponse(res);
    },

    async rerunAutoMap(projectId, payload) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/auto-map`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        return handleResponse(res);
    },

    async deployMappings(projectId, fieldMappings) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/deploy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ field_mappings: fieldMappings }),
        });
        return handleResponse(res);
    },

    async deleteMappings(projectId) {
        const url = projectId
            ? `${API_BASE_URL}/mappings?project_id=${projectId}`
            : `${API_BASE_URL}/mappings`;
        const res = await authFetch(url, { method: 'DELETE' });
        if (res.status === 204) return null;
        return handleResponse(res);
    },

    async bulkResolve(collisions) {
        const res = await authFetch(`${API_BASE_URL}/mapping/bulk-resolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ collisions }),
        });
        return handleResponse(res);
    },

    // ── Projects: enhanced CRUD ───────────────────────────────────────────

    async patchProject(projectId, data) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return handleResponse(res);
    },

    // ── Project Config (semabridge.yaml per-project blob) ─────────────────

    async getProjectConfig(projectId, options = {}) {
        const preferRepo = Boolean(options?.preferRepo);
        const query = preferRepo ? '?prefer_repo=true' : '';
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/config${query}`);
        return handleResponse(res);
    },

    async saveProjectConfig(projectId, configYaml) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config_yaml: configYaml }),
        });
        return handleResponse(res);
    },

    // ── Project Runs ──────────────────────────────────────────────────────

    async getProjectSchedule(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/schedule`);
        if (res.status === 404) {
            return { project_id: projectId, schedule_type: 'manual', enabled: false };
        }
        return handleResponse(res);
    },

    async saveProjectSchedule(projectId, data) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/schedule`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (res.status === 404) {
            return {
                project_id: projectId,
                schedule_type: data?.schedule_type || 'manual',
                cron: data?.cron || '',
                date: data?.date || '',
                time: data?.time || '',
                scheduled_time: data?.scheduled_time || '',
                timezone: data?.timezone || 'UTC',
                enabled: data?.schedule_type && data.schedule_type !== 'manual',
                message: 'Scheduler API is not available yet on the backend.',
            };
        }
        return handleResponse(res);
    },

    async deleteProjectSchedule(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/schedule`, {
            method: 'DELETE',
        });
        if (res.status === 404) {
            return { status: 'deleted', project_id: projectId, schedule_type: 'manual', enabled: false };
        }
        return handleResponse(res);
    },

    async getProjectRuns(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/runs`);
        const runs = await handleResponse(res);
        return (Array.isArray(runs) ? runs : []).map(run => ({
            ...run,
            before_tgt_snapshots: run.before_target_snapshot_ids || [],
            after_tgt_snapshots: run.after_target_snapshot_ids || run.after_tgt_snapshots || [],
        }));
    },

    async runProjectNow(projectId, payload = null) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/run`, {
            method: 'POST',
            headers: payload ? { 'Content-Type': 'application/json' } : undefined,
            body: payload ? JSON.stringify(payload) : undefined,
        });
        return handleResponse(res);
    },

    async syncProject(projectId) {
        // Backend compatibility API exposes /run as the sync trigger route.
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/run`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    async listProjectSnapshots(projectId, options = {}) {
        const params = new URLSearchParams();
        if (options.role) params.set('role', String(options.role));
        if (options.stage) params.set('stage', String(options.stage));
        if (options.origin) params.set('origin', String(options.origin));
        if (options.run_id) params.set('run_id', String(options.run_id));
        if (options.group_id) params.set('group_id', String(options.group_id));
        if (options.include_state) params.set('include_state', 'true');
        if (options.limit) params.set('limit', String(options.limit));
        const query = params.toString();
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots${query ? `?${query}` : ''}`);
        return handleResponse(res);
    },

    async listProjectSnapshotGroups(projectId, options = {}) {
        const params = new URLSearchParams();
        if (options.limit) params.set('limit', String(options.limit));
        const query = params.toString();
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshot-groups${query ? `?${query}` : ''}`);
        return handleResponse(res);
    },

    async captureProjectSnapshot(projectId, payload) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/capture`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        });
        return handleResponse(res);
    },


    async compareProjectSnapshots(projectId, fromSnapshotId, toSnapshotId, options = {}) {
        const params = new URLSearchParams();
        params.set('from_snapshot_id', String(fromSnapshotId || ''));
        params.set('to_snapshot_id', String(toSnapshotId || ''));
        if (options.max_changes) params.set('max_changes', String(options.max_changes));
        if (options.include_states) params.set('include_states', 'true');
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots/compare?${params.toString()}`);
        return handleResponse(res);
    },

    async deleteModelVersions(projectId, snapshotIds) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/snapshots`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(snapshotIds),
        });
        return handleResponse(res);
    },

    // ── Project Export ────────────────────────────────────────────────────

    async exportProject(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/export`);
        if (!res.ok) {
            const txt = await res.text();
            throw new Error(`Export failed: ${txt}`);
        }
        const blob = await res.blob();
        const cd = res.headers.get('Content-Disposition') || '';
        const match = cd.match(/filename="?([^";]+)"?/);
        const filename = match ? match[1] : `project_${projectId}.yaml`;
        _triggerDownload(blob, filename);
    },

    async exportProjectsBulk(projectIds) {
        const res = await authFetch(`${API_BASE_URL}/projects/export-bulk`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_ids: projectIds }),
        });
        if (!res.ok) {
            const txt = await res.text();
            throw new Error(`Bulk export failed: ${txt}`);
        }
        const blob = await res.blob();
        _triggerDownload(blob, 'semabridge_projects.zip');
    },

    // ── Project Import ────────────────────────────────────────────────────

    async importProjects(files) {
        const form = new FormData();
        for (const file of files) {
            form.append('files', file);
        }
        const res = await authFetch(`${API_BASE_URL}/projects/import`, {
            method: 'POST',
            body: form,
        });
        return handleResponse(res);
    },

    // ── Folders ───────────────────────────────────────────────────────────

    async listFolders() {
        const res = await authFetch(`${API_BASE_URL}/folders`);
        const data = await handleResponse(res);
        return (data || []).map(normalizeFolder);
    },

    async createFolder(data) {
        const res = await authFetch(`${API_BASE_URL}/folders`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const folder = await handleResponse(res);
        return normalizeFolder(folder);
    },

    async renameFolder(folderId, nameOrData, color) {
        const payload = typeof nameOrData === 'object'
            ? nameOrData
            : { name: nameOrData, color };
        const res = await authFetch(`${API_BASE_URL}/folders/${folderId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const folder = await handleResponse(res);
        return normalizeFolder(folder);
    },

    async deleteFolder(folderId) {
        const res = await authFetch(`${API_BASE_URL}/folders/${folderId}`, {
            method: 'DELETE',
        });
        if (res.status === 204) return null;
        return handleResponse(res);
    },

    async moveProjectToFolder(projectId, folderId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/folder`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_id: folderId }),
        });
        const data = await handleResponse(res);
        return normalizeProject(data);
    },

    // ── Discovery ─────────────────────────────────────────────────────────

    async discoverFabricWorkspaces() {
        const res = await fetch(`${API_BASE_URL}/connections/fabric/workspaces`, {
            headers: { ...getAuthHeaders(), ...getFabricAuthHeaders() },
        });
        const data = await handleResponse(res);
        return (data.workspaces || data || []).map(normalizeWorkspace);
    },

    async discoverFabricModels(workspaceId, connectionId = '') {
        if (!workspaceId || workspaceId === 'undefined' || workspaceId === 'null') {
            throw new Error('Workspace ID is required to discover Fabric models.');
        }
        const resolvedConnectionId = String(connectionId || '').trim();
        const query = resolvedConnectionId
            ? `?identity_id=${encodeURIComponent(resolvedConnectionId)}&connectionId=${encodeURIComponent(resolvedConnectionId)}`
            : '';
        const res = await authFetch(
            `${API_BASE_URL}/discovery/fabric/workspaces/${encodeURIComponent(workspaceId)}/models${query}`
        );
        return handleResponse(res);
    },

    async discoverFabricReports(workspaceId, connectionId = '') {
        if (!workspaceId || workspaceId === 'undefined' || workspaceId === 'null') {
            throw new Error('Workspace ID is required to discover Fabric reports.');
        }
        const resolvedConnectionId = String(connectionId || '').trim();
        const query = resolvedConnectionId
            ? `?identity_id=${encodeURIComponent(resolvedConnectionId)}&connectionId=${encodeURIComponent(resolvedConnectionId)}`
            : '';
        const res = await authFetch(
            `${API_BASE_URL}/discovery/fabric/workspaces/${encodeURIComponent(workspaceId)}/reports${query}`
        );
        return handleResponse(res);
    },

    async discoverSnowflakeWarehouses() {
        const res = await authFetch(`${API_BASE_URL}/discovery/snowflake/warehouses`);
        return handleResponse(res);
    },

    async discoverSnowflakeDatabases() {
        const res = await authFetch(`${API_BASE_URL}/discovery/snowflake/databases`);
        return handleResponse(res);
    },

    async discoverSnowflakeSchemas(database) {
        const res = await authFetch(
            `${API_BASE_URL}/discovery/snowflake/databases/${encodeURIComponent(database)}/schemas`
        );
        return handleResponse(res);
    },

    async discoverSnowflakeModels() {
        const res = await authFetch(`${API_BASE_URL}/discovery/snowflake`);
        return handleResponse(res);
    },

    // -- Global Config - connector sections --

    async getGlobalConnectorConfig() {
        const res = await authFetch(`${API_BASE_URL}/config/global/connectors`);
        return handleResponse(res);
    },

    async saveGlobalConnectorConfig(data) {
        const res = await authFetch(`${API_BASE_URL}/config/global/connectors`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return handleResponse(res);
    },

    // -- Global Config - settings sections --

    async getGlobalSettingsConfig() {
        const res = await authFetch(`${API_BASE_URL}/config/global/settings`);
        return handleResponse(res);
    },

    async saveGlobalSettingsConfig(data) {
        const res = await authFetch(`${API_BASE_URL}/config/global/settings`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return handleResponse(res);
    },

    // ── Fabric Workspace helpers ──────────────────────────────────────────

    /**
     * Returns the workspace_id and workspace_name that were previously saved
     * via the Settings → Connections page. Used to pre-populate the project
     * wizard without requiring the user to re-select the same workspace.
     */
    async getFabricDefaultWorkspace(accountId = '') {
        const query = accountId ? `?identity_id=${encodeURIComponent(accountId)}` : '';
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/default-workspace${query}`);
        return handleResponse(res);
    },

    /**
     * Persist a workspace selection globally (mirrors the Settings flow).
     */
    async selectFabricWorkspace({ workspace_id, workspace_name = '' }) {
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/select-workspace`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace_id, workspace_name }),
        });
        return handleResponse(res);
    },

    // ── API Secrets (user-scoped key-value store) ─────────────────────────

    /**
     * List all stored secret keys for the current user.
     * Values are NEVER returned — only the key name, masked hint, and updated_at.
     */
    async listSecrets() {
        const res = await authFetch(`${API_BASE_URL}/settings/secrets`);
        return handleResponse(res);
    },

    /**
     * Create or update a secret for the current user.
     * Key is normalized to UPPER_SNAKE_CASE by the backend.
     */
    async saveSecret({ key, value }) {
        const res = await authFetch(`${API_BASE_URL}/settings/secrets`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, value }),
        });
        return handleResponse(res);
    },

    /**
     * Delete a secret by key name for the current user.
     * Also removes the key from the backend os.environ.
     */
    async deleteSecret(key) {
        const res = await authFetch(`${API_BASE_URL}/settings/secrets/${encodeURIComponent(key)}`, {
            method: 'DELETE',
        });
        if (res.status === 204) return null;
        return handleResponse(res);
    },
};

// ---------------------------------------------------------------------------
// Internal helper: trigger a file download from a Blob
// ---------------------------------------------------------------------------
function _triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
}

