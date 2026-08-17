// ...existing code...
// (Removed duplicate export of api. Only export once at the end of the file, with getDatabricksSources included as a method.)
import { buildRunReportUrl, resolveReportFilename } from './runReportDownload.js';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
// Fabric MSAL token is kept in memory only — not persisted to localStorage.
// This prevents XSS exfiltration of the Fabric OAuth token. On page reload the
// token is re-acquired via the MSAL device-code / refresh flow.
// (Legacy localStorage keys are kept only to remove stale values on cleanup.)
const FABRIC_TOKEN_KEY = 'semabridge-fabric-token';
const FABRIC_TOKEN_EXPIRES_KEY = 'semabridge-fabric-token-expires';

// Module-level memory storage for the Fabric MSAL token (not persisted across
// page refreshes — intentional: re-authentication is required after reload).
let _fabricTokenMemory = null;
let _fabricTokenExpiresAt = 0;
const API_CACHE_TTL_MS = Number(import.meta.env.VITE_CACHE_TTL_MS) || 2 * 60 * 1000;
const API_REQUEST_TIMEOUT_MS = 60000;
const UI_LIST_REQUEST_TIMEOUT_MS = 30000;
const PROJECT_LIST_REQUEST_TIMEOUT_MS = parseInt(import.meta.env.VITE_API_TIMEOUT_MS ?? '60000', 10);
// Safety margin, not a tuned value: dry-run/run now issue a handful of
// batched Tier-5 calls instead of one sequential call per hard-to-translate
// metric (see osi_to_sml.py's skip_tier5 fix), but this must still protect
// against the same wall as models grow or a slower/failing-over provider is
// selected -- raised from 180000 (3 min, the value that was already being
// hit by the backend's real ~192s completion time on a live key).
const PROJECT_RUN_REQUEST_TIMEOUT_MS = parseInt(import.meta.env.VITE_RUN_TIMEOUT_MS ?? '600000', 10);
const HEALTH_CHECK_TIMEOUT_MS = parseInt(import.meta.env.VITE_HEALTH_TIMEOUT_MS ?? '5000', 10);
const UI_LIST_CACHE_TTL_MS = 20000;
const apiCache = new Map();
const inFlightApiCalls = new Map();
// Prevent unbounded growth of in-memory caches. Keep a simple LRU-ish cap.
const API_CACHE_MAX_ITEMS = Number(import.meta.env.VITE_CACHE_MAX_ITEMS) || 200;
/**
 * Read the csrf_token cookie set by the backend CSRFMiddleware.
 * The cookie is httponly=false so JS can read it and echo it back
 * as X-CSRF-Token on state-changing requests (double-submit cookie pattern).
 */
function getCsrfToken() {
    try {
        const match = document.cookie.split(';').find(c => c.trim().startsWith('csrf_token='));
        return match ? decodeURIComponent(match.trim().slice('csrf_token='.length)) : '';
    } catch {
        return '';
    }
}
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) {
        return parts.pop().split(';').shift();
    }
    return null;
}

function ensureCacheSize() {
    try {
        if (apiCache.size > API_CACHE_MAX_ITEMS) {
            // remove oldest entries (Map preserves insertion order)
            const toRemove = apiCache.size - API_CACHE_MAX_ITEMS;
            const it = apiCache.keys();
            for (let i = 0; i < toRemove; i++) {
                const k = it.next().value;
                apiCache.delete(k);
            }
        }
    } catch {
        // best-effort only
    }
}

function getCachedApiValue(cacheKey) {
    const cached = apiCache.get(cacheKey);
    if (!cached) return null;
    if (Date.now() > cached.expiresAt) {
        apiCache.delete(cacheKey);
        return null;
    }
    return cached.value;
}

function setCachedApiValue(cacheKey, value, ttlMs = API_CACHE_TTL_MS) {
    apiCache.set(cacheKey, {
        value,
        expiresAt: Date.now() + ttlMs,
    });
    ensureCacheSize();
    return value;
}

function invalidateApiCache(pattern = null) {
    try {
        if (!pattern) {
            apiCache.clear();
            return;
        }
        for (const key of apiCache.keys()) {
            if (key.includes(pattern) || key.startsWith(pattern)) {
                apiCache.delete(key);
            }
        }
    } catch {
        // best-effort
    }
}

function coalesceApiCall(cacheKey, callFn) {
    if (inFlightApiCalls.has(cacheKey)) {
        return inFlightApiCalls.get(cacheKey);
    }
    const next = (async () => callFn())().finally(() => {
        inFlightApiCalls.delete(cacheKey);
    });
    inFlightApiCalls.set(cacheKey, next);
    return next;
}

function isTimeoutError(error) {
    return /timed out/i.test(String(error?.message || ''));
}

async function withSingleTimeoutRetry(callFn, retryDelayMs = 250) {
    try {
        return await callFn();
    } catch (error) {
        if (!isTimeoutError(error)) throw error;
        await new Promise((resolve) => window.setTimeout(resolve, retryDelayMs));
        return callFn();
    }
}

function isRetriableStatus(status) {
    return status === 429 || status >= 500;
}

function extractSnapshotList(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.snapshots)) return payload.snapshots;
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
}

function normalizeProjectSnapshotCompareRequest(projectId, arg1, arg2, arg3 = {}) {
    const safeProjectId = String(projectId ?? '').trim();
    const input =
        arg1 && typeof arg1 === 'object' && !Array.isArray(arg1)
            ? arg1
            : {
                from_snapshot_id: arg1,
                to_snapshot_id: arg2,
                ...(arg3 && typeof arg3 === 'object' ? arg3 : {}),
            };

    const resolvedFromSnapshotId = String(
        input?.from_snapshot_id
        ?? input?.base_id
        ?? input?.s1
        ?? ''
    ).trim();
    const resolvedToSnapshotId = String(
        input?.to_snapshot_id
        ?? input?.target_id
        ?? input?.s2
        ?? ''
    ).trim();

    if (!safeProjectId) {
        throw new Error('Snapshot compare requires a valid projectId.');
    }
    if (!resolvedFromSnapshotId || !resolvedToSnapshotId) {
        throw new Error(
            'Snapshot compare requires both from/to snapshot IDs. Accepted keys: from_snapshot_id, to_snapshot_id, base_id, target_id, s1, s2.'
        );
    }

    const params = new URLSearchParams();
    params.set('from_snapshot_id', resolvedFromSnapshotId);
    params.set('to_snapshot_id', resolvedToSnapshotId);
    if (input?.max_changes != null && String(input.max_changes).trim()) {
        params.set('max_changes', String(input.max_changes).trim());
    }
    if (input?.include_states === true) {
        params.set('include_states', 'true');
    }

    return {
        projectId: safeProjectId,
        fromSnapshotId: resolvedFromSnapshotId,
        toSnapshotId: resolvedToSnapshotId,
        query: params.toString(),
    };
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
    const displayName = project.display_name ?? project.name ?? project.project_name ?? project.semantic_name ?? id;
    const semanticName = project.semantic_name ?? displayName;
    const source = project.source ?? project.adapter ?? project.source_type ?? null;
    const targetType = project.target_type ?? project.target?.type ?? project.targets?.[0]?.type ?? null;

    return {
        ...project,
        id,
        project_id: project.project_id ?? id,
        semantic_name: semanticName,
        name: displayName,
        source,
        adapter: project.adapter ?? source,
        target_type: targetType,
    };
}

function dedupeProjects(projects) {
    const byId = new Map();

    for (const raw of (projects || [])) {
        const p = normalizeProject(raw);
        const id = String(p?.id || p?.project_id || '').trim().toLowerCase();

        if (!id) {
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

    return [...byId.values()];
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
    // Access token is now stored in an HttpOnly cookie sent automatically by the browser.
    // No Authorization header is injected here; credentials: 'include' in authFetch handles it.
    return {};
}

// Safe wrapper for callers expecting a Databricks sources helper. Keeps
// API surface stable; implementation delegates to discovery endpoint.
async function _getDatabricksSources(connectionId = '') {
    const query = connectionId ? `?identity_id=${encodeURIComponent(connectionId)}` : '';
    const res = await authFetch(`${API_BASE_URL}/discovery/databricks/sources${query}`);
    return handleResponse(res);
}

/**
 * Return the stored Fabric MSAL access token as a Bearer header if valid.
 * This is injected on Fabric-specific API calls so the backend resolves
 * the correct per-user token instead of falling back to the shared DB row.
 * The token is stored in memory only (not localStorage) to prevent XSS exfiltration.
 */
function getFabricAuthHeaders() {
    if (_fabricTokenMemory && Date.now() < _fabricTokenExpiresAt) {
        return { Authorization: `Bearer ${_fabricTokenMemory}` };
    }
    // Token expired or missing — clear in-memory state and any stale localStorage remnants.
    _fabricTokenMemory = null;
    _fabricTokenExpiresAt = 0;
    localStorage.removeItem(FABRIC_TOKEN_KEY);
    localStorage.removeItem(FABRIC_TOKEN_EXPIRES_KEY);
    return {};
}

export async function fetchWithTimeout(url, options = {}, timeoutMs = API_REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

    try {
        return await fetch(url, {
            ...options,
            signal: controller.signal,
        });
    } catch (error) {
        if (error?.name === 'AbortError') {
            throw new Error(`Request timed out after ${timeoutMs}ms`);
        }
        throw error;
    } finally {
        window.clearTimeout(timeoutId);
    }
}

function hasValidFabricToken() {
    return Boolean(_fabricTokenMemory && Date.now() < _fabricTokenExpiresAt);
}

const AUTH_BASE = (import.meta.env.VITE_AUTH_BASE_URL || '/auth').replace(/\/$/, '');

let _refreshPromise = null;
let _lastRefreshFailureAt = 0;
let _lastAuthExpiredEventAt = 0;
const REFRESH_FAILURE_COOLDOWN_MS = 5000;
const AUTH_EXPIRED_EVENT_COOLDOWN_MS = 5000;

function hasJwtToken() {
    // Token is in an HttpOnly cookie — we can't read it from JS.
    // Assume a token exists if the user has been authenticated (checked via /auth/me on bootstrap).
    // This function is used only for logging/diagnostics; always return true as a safe default.
    return true;
}

function readPersistentListCache(key) {
    try {
        const raw = localStorage.getItem(key);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

function emitAuthExpiredOnce() {
    const now = Date.now();
    if (now - _lastAuthExpiredEventAt < AUTH_EXPIRED_EVENT_COOLDOWN_MS) return;
    _lastAuthExpiredEventAt = now;
    window.dispatchEvent(new Event('semabridge:auth-expired'));
}

function isAuthError(error) {
    return Number(error?.status) === 401 || Number(error?.status) === 403;
}

/**
 * Attempt to refresh the JWT access token using the HttpOnly refresh cookie.
 * Falls back to auto-login if the refresh cookie is missing or expired.
 * Uses a singleton promise to prevent concurrent refresh races.
 *
 * Industry pattern: coalesce all concurrent 401 recovery attempts into a
 * single promise so that parallel API calls don't each trigger separate
 * refresh/auto-login requests.
 */
export async function tryRefreshToken() {
    if (_refreshPromise) return _refreshPromise;
    if (Date.now() - _lastRefreshFailureAt < REFRESH_FAILURE_COOLDOWN_MS) {
        return null;
    }
    _refreshPromise = (async () => {
        try {
            // Step 1: Try refresh via HttpOnly cookie
            const res = await fetchWithTimeout(`${AUTH_BASE}/refresh`, {
                method: 'POST',
                credentials: 'include',
            });
            if (res.ok) {
                const data = await res.json();
                if (data.access_token) {
                    // Backend sets the new access_token cookie; just notify listeners
                    window.dispatchEvent(new CustomEvent('semabridge:token-refreshed', { detail: data.access_token }));
                    _lastRefreshFailureAt = 0;
                    return data.access_token;
                }
            }

            // Refresh cookie missing or expired — notify AuthContext to handle
            // cleanup. Auto-login is intentionally NOT called here; it may only
            // be triggered during the explicit dev bootstrap (app first load).
            emitAuthExpiredOnce();
            _lastRefreshFailureAt = Date.now();
            return null;
        } catch {
            _lastRefreshFailureAt = Date.now();
            return null;
        } finally {
            _refreshPromise = null;
        }
    })();
    return _refreshPromise;
}

/**
 * Reset the refresh-failure cooldown timer.
 * Call this when an external recovery path (e.g. AuthContext silentRecover
 * or the proactive timer) succeeds, so that api.js' 401 handler can
 * immediately attempt recovery on the next failure instead of waiting.
 */
export function resetRefreshCooldown() {
    _lastRefreshFailureAt = 0;
}

async function handleResponse(res) {
    if (res.status === 401) {
        try {
            const cloned = res.clone();
            const data = await cloned.json();
            const detailStr = typeof data?.detail === 'string' ? data.detail : '';
            const isReauth = data?.error === 'reauth_required'
                || data?.detail?.error === 'reauth_required'
                || detailStr.includes('reauth_required')
                || data?.error_type === 'AuthenticationError';
            if (isReauth) {
                return data.detail || data;
            }
        } catch {
            // Response may not be JSON; continue recovery flow.
        }

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
        emitAuthExpiredOnce();
        const error = new Error('Session expired. Please log in again.');
        error.status = 401;
        throw error;
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
        } catch {
            // Keep raw response text detail when JSON parsing fails.
        }
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
    const { timeoutMs = API_REQUEST_TIMEOUT_MS, ...restOptions } = options;
    const method = String(restOptions.method || 'GET').toUpperCase();
    const maxAttempts = method === 'GET' || method === 'HEAD' ? 2 : 1;
    const retryDelayMs = 250;

    // Automatically inject CSRF token for unsafe methods
    const unsafeMethods = ['POST', 'PUT', 'PATCH', 'DELETE'];
    if (unsafeMethods.includes(method)) {
        const csrfToken = getCookie('csrf_token');
        if (csrfToken) {
            restOptions.headers = {
                ...restOptions.headers,
                'x-csrf-token': csrfToken,
            };
        } else if (import.meta.env.DEV) {
            console.warn('CSRF token cookie not found. Request may be rejected by the server.');
        }
    }

    // Inject X-Fabric-Context header if workspace ID is available
    let workspaceId = null;
    // tokenPresent is always true when using HttpOnly cookie auth (can't be read from JS)
    const tokenPresent = true;
    try {
        workspaceId = localStorage.getItem('FABRIC_WORKSPACE_ID');
    } catch {
        workspaceId = null;
    }
    const headers = { ...getAuthHeaders(), ...restOptions.headers };
    if (workspaceId) {
        headers['X-Fabric-Context'] = workspaceId;
    }
    // Double-submit cookie CSRF protection: echo the csrf_token cookie back as
    // X-CSRF-Token on all state-changing requests. The backend CSRFMiddleware
    // (active when AUTH_ENABLED=true) validates header == cookie before processing.
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
        const csrfToken = getCsrfToken();
        if (csrfToken) {
            headers['X-CSRF-Token'] = csrfToken;
        }
    }
    let res;
    let lastError = null;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        try {
            res = await fetchWithTimeout(url, { ...restOptions, headers, credentials: 'include' }, timeoutMs);
            if (attempt < maxAttempts && isRetriableStatus(Number(res.status))) {
                await new Promise((resolve) => window.setTimeout(resolve, retryDelayMs));
                continue;
            }
            break;
        } catch (error) {
            lastError = error;
            if (attempt < maxAttempts && isTimeoutError(error)) {
                await new Promise((resolve) => window.setTimeout(resolve, retryDelayMs));
                continue;
            }
            throw error;
        }
    }
    if (!res && lastError) throw lastError;
    if (res.status === 403 && /\/projects\/[^/]+(?:\/config)?(?:\?|$)/.test(String(url))) {
        console.warn('[authFetch] Project request forbidden', {
            url,
            method: restOptions.method || 'GET',
            tokenPresent,
            hasFabricContext: Boolean(workspaceId),
        });
    }

    // Attach retry info so handleResponse can retry on 401 with a fresh token
    res._retryFn = (newToken) => {
        const retryHeaders = { ...headers, Authorization: `Bearer ${newToken}` };
        return fetchWithTimeout(url, { ...restOptions, headers: retryHeaders, credentials: 'include' }, timeoutMs);
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
        try {
            const cacheKey = `accounts:${String(connectorType || '').trim().toLowerCase()}`;
            const cached = getCachedApiValue(cacheKey);
            if (cached) return cached;
            const query = connectorType ? `?connector_type=${encodeURIComponent(connectorType)}` : '';
            const res = await authFetch(`${API_BASE_URL}/accounts${query}`);
            const data = await handleResponse(res);
            return setCachedApiValue(cacheKey, data);
        } catch (error) {
            if (isAuthError(error)) return [];
            throw error;
        }
    },

    async createAccount(payload) {
        invalidateApiCache('accounts:');
        // Bust discovery cache so the new account's warehouses/databases load fresh
        invalidateApiCache('discovery:snowflake:');
        invalidateApiCache('discovery:fabric:');
        const res = await authFetch(`${API_BASE_URL}/accounts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        return handleResponse(res);
    },

    async deleteAccount(accountId) {
        invalidateApiCache('accounts:');
        const res = await authFetch(`${API_BASE_URL}/accounts/${accountId}`, {
            method: 'DELETE'
        });
        if (res.status === 204) return null;
        return handleResponse(res);
    },

    async updateAccountTag(accountId, tag) {
        invalidateApiCache('accounts:');
        const res = await authFetch(`${API_BASE_URL}/accounts/${accountId}/tag`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag })
        });
        return handleResponse(res);
    },

    async linkProjectAccount(projectId, payload) {
        invalidateApiCache('accounts:');
        const res = await authFetch(`${API_BASE_URL}/accounts/project/${projectId}/link-account`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        return handleResponse(res);
    },

    async getHealth() {
        // Health endpoint is public — bypass authFetch/handleResponse to
        // avoid triggering the 401 recovery pipeline on network errors.
        // A failed health check should NOT clear auth state.
        try {
            const res = await fetchWithTimeout(`${API_BASE_URL}/health`, {
                credentials: 'include',
            }, HEALTH_CHECK_TIMEOUT_MS);
            if (!res.ok) return { status: 'error' };
            return await res.json();
        } catch {
            return { status: 'error' };
        }
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

    async getSynonymOverride({ projectId, modelName, tableName, columnName }) {
        const path = [projectId, modelName, tableName, columnName]
            .map((part) => encodeURIComponent(String(part || '')))
            .join('/');
        const res = await authFetch(`${API_BASE_URL}/synonyms/${path}`);
        return handleResponse(res);
    },

    async saveSynonymOverride(payload) {
        const res = await authFetch(`${API_BASE_URL}/synonyms/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        return handleResponse(res);
    },

    async deleteSynonymOverride({ projectId, modelName, tableName, columnName }) {
        const path = [projectId, modelName, tableName, columnName]
            .map((part) => encodeURIComponent(String(part || '')))
            .join('/');
        const res = await authFetch(`${API_BASE_URL}/synonyms/${path}`, {
            method: 'DELETE',
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
        try {
            const res = await authFetch(`${API_BASE_URL}/workspaces`);
            const data = await handleResponse(res);
            return (data || []).map(normalizeWorkspace);
        } catch (error) {
            if (isAuthError(error)) return [];
            throw error;
        }
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

        // On success, store the MSAL token in memory only (not localStorage) to
        // prevent XSS exfiltration. The token will need to be re-acquired on reload.
        if (data.status === 'success' && data.access_token) {
            const expiresAt = Date.now() + (data.expires_in || 3600) * 1000;
            _fabricTokenMemory = data.access_token;
            _fabricTokenExpiresAt = expiresAt;
            // Clean up any stale localStorage remnants from previous sessions.
            localStorage.removeItem(FABRIC_TOKEN_KEY);
            localStorage.removeItem(FABRIC_TOKEN_EXPIRES_KEY);
            // Notify listeners (UI) that a new Fabric token is available.
            try {
                window.dispatchEvent(new CustomEvent('semabridge:fabric-token-refreshed', {
                    detail: { access_token: data.access_token, expires_at: expiresAt }
                }));
            } catch (e) {
                // ignore; best-effort notification
            }
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
        // Clear in-memory token and any stale localStorage remnants.
        _fabricTokenMemory = null;
        _fabricTokenExpiresAt = 0;
        localStorage.removeItem(FABRIC_TOKEN_KEY);
        localStorage.removeItem(FABRIC_TOKEN_EXPIRES_KEY);
        try {
            window.dispatchEvent(new Event('semabridge:fabric-token-removed'));
        } catch (e) {
            // ignore
        }
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

        // Avoid clobbering app JWT with Fabric token when account-scoped lookup is requested.
        // Account-scoped discovery should resolve Fabric token server-side via identity_id.
        const headers = resolvedConnectionId
            ? { ...getAuthHeaders() }
            : { ...getAuthHeaders(), ...getFabricAuthHeaders() };

        console.info('[SemaBridge][FabricDiscovery] api:request', {
            accountId: resolvedConnectionId || '(none)',
            hasAppAuth: Boolean(headers.Authorization),
            hasFabricToken: Boolean(getFabricAuthHeaders().Authorization),
        });

        let res;
        try {
            res = await fetchWithTimeout(
                `${API_BASE_URL}/connections/fabric/workspaces${query}`,
                { headers, credentials: 'include' },
                4000,
            );
        } catch (error) {
            console.warn('[SemaBridge][FabricDiscovery] api:timeout_or_network_error', {
                accountId: resolvedConnectionId || '(none)',
                message: error?.message || String(error),
            });
            return { workspaces: [] };
        }

        console.info('[SemaBridge][FabricDiscovery] api:response', {
            accountId: resolvedConnectionId || '(none)',
            status: res.status,
            ok: res.ok,
        });

        if (res.status === 401 || res.status === 403) {
            return { workspaces: [] };
        }

        try {
            return await handleResponse(res);
        } catch (err) {
            throw err;
        }
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
        invalidateApiCache('jobs:runs:');
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
    async listProjects({ limit = 50, offset = 0 } = {}) {
        const cacheKey = `projects:list:${limit}:${offset}`;
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;

        return coalesceApiCall(cacheKey, async () => {
            try {
                const res = await withSingleTimeoutRetry(() =>
                    authFetch(`${API_BASE_URL}/projects?limit=${limit}&offset=${offset}`, { timeoutMs: PROJECT_LIST_REQUEST_TIMEOUT_MS })
                );
                const data = await handleResponse(res);
                const normalized = dedupeProjects(data || []);
                return setCachedApiValue(cacheKey, normalized, UI_LIST_CACHE_TTL_MS);
            } catch (error) {
                if (isAuthError(error) || isTimeoutError(error)) {
                    const fallback = getCachedApiValue(cacheKey);
                    if (fallback != null) {
                        return setCachedApiValue(cacheKey, fallback, 4000);
                    }
                    const persistent = dedupeProjects(readPersistentListCache('semabridge:cache:projects'));
                    if (persistent.length > 0) {
                        return setCachedApiValue(cacheKey, persistent, 4000);
                    }
                    throw error;
                }
                throw error;
            }
        });
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
        invalidateApiCache('jobs:runs:');
        const body = {
            ...payload,
            restore_snapshot_id: payload?.restore_snapshot_id || payload?.snapshot_id,
            run_type: 'restore',
        };
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
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
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}${query}`, {
            timeoutMs: PROJECT_LIST_REQUEST_TIMEOUT_MS,
        });
        const data = await handleResponse(res);
        return normalizeProject(data);
    },

    async createProject(data) {
        invalidateApiCache('projects:list');
        const res = await authFetch(`${API_BASE_URL}/projects`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
            timeoutMs: PROJECT_RUN_REQUEST_TIMEOUT_MS,
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
        invalidateApiCache('projects:list');
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return handleResponse(res);
    },

    async deleteProject(projectId) {
        invalidateApiCache('projects:list');
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}`, {
            method: 'DELETE',
        });
        if (res.status === 204) return null;
        return handleResponse(res);
    },


    // ── Job Runs ───────────────────────────────────────────────────────────
    async listJobRuns(filters = {}) {
        const params = new URLSearchParams();
        if (filters.status) params.set('status', filters.status);
        if (filters.project_id) params.set('project_id', filters.project_id);
        const query = params.toString();
        const querySuffix = query ? `?${query}` : '';
        const cacheKey = `jobs:runs:${querySuffix || 'all'}`;
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;

        return coalesceApiCall(cacheKey, async () => {
            try {
                const res = await authFetch(`${API_BASE_URL}/jobs/runs${querySuffix}`, { timeoutMs: UI_LIST_REQUEST_TIMEOUT_MS });
                const runs = await handleResponse(res);

                let projects = [];
                try {
                    projects = await this.listProjects();
                } catch (e) {
                    projects = readPersistentListCache('semabridge:cache:projects') || [];
                }
                const projectMap = new Map((projects || []).map(p => [String(p.id), p]));

                const normalized = (Array.isArray(runs) ? runs : []).map((run) => {
                    const project = projectMap.get(String(run?.project_id));
                    const currentProjectName = project?.name || project?.display_name || run?.project_name;
                    return {
                        ...run,
                        id: run?.id ?? run?.run_id,
                        run_id: run?.run_id ?? run?.id,
                        project_name: currentProjectName || 'Project run',
                        source_type: run?.source_type ?? run?.source ?? run?.adapter,
                        message: run?.message ?? run?.error ?? run?.error_message ?? '',
                        before_target_snapshot_ids: Array.isArray(run?.before_target_snapshot_ids) ? run.before_target_snapshot_ids : (run?.before_target_snapshot_ids ? [run.before_target_snapshot_ids] : []),
                        after_target_snapshot_ids: Array.isArray(run?.after_target_snapshot_ids) ? run.after_target_snapshot_ids : (run?.after_target_snapshot_ids ? [run.after_target_snapshot_ids] : []),
                        after_tgt_snapshots: Array.isArray(run?.after_tgt_snapshots) ? run.after_tgt_snapshots : (run?.after_tgt_snapshots ? [run.after_tgt_snapshots] : []),
                        error: run?.error ?? run?.error_message ?? '',
                    };
                });
                return setCachedApiValue(cacheKey, normalized, 8000);
            } catch (error) {
                if (isAuthError(error) || isTimeoutError(error)) {
                    return setCachedApiValue(cacheKey, getCachedApiValue(cacheKey) || [], 3000);
                }
                throw error;
            }
        });
    },

    async listJobSchedules() {
        const cacheKey = 'jobs:schedules';
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;

        return coalesceApiCall(cacheKey, async () => {
            try {
                const res = await authFetch(`${API_BASE_URL}/jobs/schedules`, { timeoutMs: UI_LIST_REQUEST_TIMEOUT_MS });
                if (res.status === 404) {
                    return setCachedApiValue(cacheKey, [], 8000);
                }
                const data = await handleResponse(res);

                let projects = [];
                try {
                    projects = await this.listProjects();
                } catch (e) {
                    projects = readPersistentListCache('semabridge:cache:projects') || [];
                }
                const projectMap = new Map((projects || []).map(p => [String(p.id), p]));

                const normalized = (Array.isArray(data) ? data : []).map((schedule) => {
                    const project = projectMap.get(String(schedule?.project_id));
                    const currentProjectName = project?.name || project?.display_name || schedule?.project_name;
                    return {
                        ...schedule,
                        project_name: currentProjectName || schedule?.project_id || '',
                    };
                });
                return setCachedApiValue(cacheKey, normalized, 8000);
            } catch (error) {
                if (isAuthError(error) || isTimeoutError(error)) {
                    return setCachedApiValue(cacheKey, getCachedApiValue(cacheKey) || [], 3000);
                }
                throw error;
            }
        });
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
        invalidateApiCache('jobs:runs:');
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
                timeoutMs: PROJECT_RUN_REQUEST_TIMEOUT_MS,
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

            // Normalise schema_conflicts
            if (!Array.isArray(data.schema_conflicts)) {
                data.schema_conflicts = [];
            }
            // compatibility_score comes from the backend only when a real dry-run
            // executed. If the backend returns null/undefined we leave it as null so
            // the UI does NOT show a score bar at all — a fake 100% is misleading.
            if (typeof data.compatibility_score !== 'number') {
                data.compatibility_score = null;
            }

            return data;
            
        } catch (error) {
            console.error('[API] runProjectDryRun error:', error);
            throw error;
        }
    },

    /**
     * Multi-PBIX background dry-run jobs — background-job-plus-polling
     * pattern so an N-file batch never risks the HTTP timeout a single
     * long, synchronous runProjectDryRun() request would. Each of these
     * calls is fast (job creation / a status read / a rerun trigger); the
     * actual per-file pipeline work happens server-side in the background
     * and is observed via polling createDryRunJob's returned job_id with
     * getDryRunJobStatus.
     */
    async createDryRunJob(projectId, payload) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/dry-run-jobs`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        return handleResponse(res);
    },

    async getDryRunJobStatus(projectId, jobId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/dry-run-jobs/${jobId}`);
        return handleResponse(res);
    },

    async getDryRunJobFile(projectId, jobId, fileId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/dry-run-jobs/${jobId}/files/${fileId}`);
        return handleResponse(res);
    },

    async rerunDryRunJobFile(projectId, jobId, fileId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/dry-run-jobs/${jobId}/files/${fileId}/rerun`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    /**
     * Auto-add a missing dimension column to the Snowflake physical table.
     * Called from the dry-run conflict UI when the user clicks "Auto-add to Snowflake".
     * After this succeeds the caller should trigger a re-sync.
     */
    async addMissingDimensionColumn(projectId, datasetName, columnName, columnType = 'VARCHAR') {
        const res = await authFetch(`${API_BASE_URL.replace('/api', '')}/sync/schema/add-missing-column`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: String(projectId),
                dataset_name: datasetName,
                column_name: columnName,
                column_type: columnType,
            }),
        });
        return handleResponse(res);
    },

    async updateMapping(projectId, mappingId, targetNameOrPayload) {
        const safeProjectId = String(projectId ?? '').trim();
        const safeMappingId = String(mappingId ?? '').trim();

        if (!safeProjectId || !safeMappingId || safeProjectId === '[object Object]' || safeMappingId === '[object Object]') {
            throw new Error('Invalid mapping update request: projectId and mappingId must be scalar values.');
        }

        // Accept either a plain string (legacy) or a full payload object
        const body = typeof targetNameOrPayload === 'object' && targetNameOrPayload !== null
            ? targetNameOrPayload
            : { target_name: targetNameOrPayload };
        const res = await authFetch(`${API_BASE_URL}/projects/${encodeURIComponent(safeProjectId)}/mappings/${encodeURIComponent(safeMappingId)}`, {
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
        const noCache = Boolean(options?.noCache);
        const params = new URLSearchParams();
        if (preferRepo) params.set('prefer_repo', 'true');
        if (noCache) params.set('refresh', Date.now().toString());
        const query = params.toString() ? `?${params.toString()}` : '';
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

    // Downloads the Markdown run-summary report written by the backend's
    // write_run_report() (see run_report_service.py) for one run. Mirrors
    // exportProject()'s fetch -> Content-Disposition -> _triggerDownload
    // pattern below. Works identically for a successful, warning, partial,
    // or failed run -- write_run_report runs unconditionally from
    // _perform_project_run's `finally` block, so every run has a report to
    // download, not just successful ones. URL/filename resolution is
    // extracted to runReportDownload.js so it's unit-testable without a
    // fetch/DOM environment.
    async downloadRunReport(projectId, runId) {
        const res = await authFetch(buildRunReportUrl(API_BASE_URL, projectId, runId));
        if (!res.ok) {
            const txt = await res.text();
            throw new Error(`Report download failed: ${txt}`);
        }
        const blob = await res.blob();
        const filename = resolveReportFilename(res.headers.get('Content-Disposition'), projectId, runId);
        _triggerDownload(blob, filename);
    },

    async runProjectNow(projectId, payload = null) {
        invalidateApiCache('jobs:runs:');
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/run`, {
            method: 'POST',
            headers: payload ? { 'Content-Type': 'application/json' } : undefined,
            body: payload ? JSON.stringify(payload) : undefined,
            timeoutMs: PROJECT_RUN_REQUEST_TIMEOUT_MS,
        });
        return handleResponse(res);
    },

    async getRunPreview(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/run-preview`, {
            timeoutMs: 10000,
        });
        return handleResponse(res);
    },

    async syncProject(projectId) {
        invalidateApiCache('jobs:runs:');
        // Backend compatibility API exposes /run as the sync trigger route.
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/run`, {
            method: 'POST',
            timeoutMs: PROJECT_RUN_REQUEST_TIMEOUT_MS,
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


    async compareProjectSnapshots(projectId, compareArg1, compareArg2, compareArg3 = {}) {
        const request = normalizeProjectSnapshotCompareRequest(
            projectId,
            compareArg1,
            compareArg2,
            compareArg3,
        );
        const res = await authFetch(`${API_BASE_URL}/projects/${request.projectId}/snapshots/compare?${request.query}`);
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
        const cacheKey = 'folders:list';
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;

        return coalesceApiCall(cacheKey, async () => {
            try {
                const res = await withSingleTimeoutRetry(() =>
                    authFetch(`${API_BASE_URL}/folders`, { timeoutMs: UI_LIST_REQUEST_TIMEOUT_MS })
                );
                const data = await handleResponse(res);
                const normalized = (data || []).map(normalizeFolder);
                return setCachedApiValue(cacheKey, normalized, UI_LIST_CACHE_TTL_MS);
            } catch (error) {
                if (isAuthError(error) || isTimeoutError(error)) {
                    const fallback = getCachedApiValue(cacheKey);
                    if (fallback != null) {
                        return setCachedApiValue(cacheKey, fallback, 4000);
                    }
                    throw error;
                }
                throw error;
            }
        });
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
        // Skip entirely when no Fabric token is available — avoids a guaranteed 400.
        if (!hasValidFabricToken()) return [];
        const cacheKey = 'discovery:fabric:workspaces';
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;
        // Use authFetch so the app JWT and retry/timeout logic is applied consistently.
        // Fabric auth headers are merged in via the options headers field.
        const res = await authFetch(`${API_BASE_URL}/connections/fabric/workspaces`, {
            method: 'GET',
            headers: { ...getFabricAuthHeaders() },
        });
        try {
            const data = await handleResponse(res);
            const normalized = (data.workspaces || data || []).map(normalizeWorkspace);
            return setCachedApiValue(cacheKey, normalized);
        } catch (err) {
            throw err;
        }
    },

    async discoverFabricModels(workspaceId, connectionId = '') {
        if (!workspaceId || workspaceId === 'undefined' || workspaceId === 'null') {
            throw new Error('Workspace ID is required to discover Fabric models.');
        }
        const resolvedConnectionId = String(connectionId || '').trim();
        const query = resolvedConnectionId
            ? `?identity_id=${encodeURIComponent(resolvedConnectionId)}&connectionId=${encodeURIComponent(resolvedConnectionId)}`
            : '';
        const cacheKey = `discovery:fabric:models:${workspaceId}:${resolvedConnectionId || '-'}`;
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;
        const res = await authFetch(
            `${API_BASE_URL}/discovery/fabric/workspaces/${encodeURIComponent(workspaceId)}/models${query}`
        );
        const data = await handleResponse(res);
        return setCachedApiValue(cacheKey, data);
    },

    async discoverFabricReports(workspaceId, connectionId = '') {
        if (!workspaceId || workspaceId === 'undefined' || workspaceId === 'null') {
            throw new Error('Workspace ID is required to discover Fabric reports.');
        }
        const resolvedConnectionId = String(connectionId || '').trim();
        const query = resolvedConnectionId
            ? `?identity_id=${encodeURIComponent(resolvedConnectionId)}&connectionId=${encodeURIComponent(resolvedConnectionId)}`
            : '';
        const cacheKey = `discovery:fabric:reports:${workspaceId}:${resolvedConnectionId || '-'}`;
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;
        const res = await authFetch(
            `${API_BASE_URL}/discovery/fabric/workspaces/${encodeURIComponent(workspaceId)}/reports${query}`
        );
        const data = await handleResponse(res);
        return setCachedApiValue(cacheKey, data);
    },

    async discoverSnowflakeWarehouses(connectionId = '') {
        const cacheKey = `discovery:snowflake:warehouses:${connectionId}`;
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;
        const query = connectionId ? `?identity_id=${encodeURIComponent(connectionId)}` : '';
        const res = await authFetch(`${API_BASE_URL}/discovery/snowflake/warehouses${query}`);
        const data = await handleResponse(res);
        return setCachedApiValue(cacheKey, data);
    },

    async discoverSnowflakeDatabases(connectionId = '') {
        const cacheKey = `discovery:snowflake:databases:${connectionId}`;
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;
        const query = connectionId ? `?identity_id=${encodeURIComponent(connectionId)}` : '';
        const res = await authFetch(`${API_BASE_URL}/discovery/snowflake/databases${query}`);
        const data = await handleResponse(res);
        return setCachedApiValue(cacheKey, data);
    },

    async discoverSnowflakeSchemas(database, connectionId = '') {
        const cacheKey = `discovery:snowflake:schemas:${String(database || '').trim().toUpperCase()}:${connectionId}`;
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;
        const query = connectionId ? `?identity_id=${encodeURIComponent(connectionId)}` : '';
        const res = await authFetch(
            `${API_BASE_URL}/discovery/snowflake/databases/${encodeURIComponent(database)}/schemas${query}`
        );
        const data = await handleResponse(res);
        return setCachedApiValue(cacheKey, data);
    },

    async discoverSnowflakeModels(connectionId = '') {
        const cacheKey = `discovery:snowflake:models:${connectionId}`;
        const cached = getCachedApiValue(cacheKey);
        if (cached) return cached;
        const query = connectionId ? `?identity_id=${encodeURIComponent(connectionId)}` : '';
        const res = await authFetch(`${API_BASE_URL}/discovery/snowflake${query}`);
        const data = await handleResponse(res);
        return setCachedApiValue(cacheKey, data);
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

    // ── LLM Provider Configuration (Tier 5 DAX translation) ────────────────

    /**
     * Status for all 5 LLM providers (OpenAI/Gemini/Groq/Featherless/Anthropic):
     * which source (Settings vs .env vs none) is supplying the key, whether a
     * model has been selected, and a masked hint when Settings-configured.
     */
    async getLlmProviders() {
        const res = await authFetch(`${API_BASE_URL}/settings/llm-providers`);
        return handleResponse(res);
    },

    /**
     * Save (or overwrite) the Settings-configured API key for a provider.
     * The raw key is never returned again after saving.
     */
    async saveLlmProviderApiKey(provider, apiKey) {
        const res = await authFetch(`${API_BASE_URL}/settings/llm-providers/${encodeURIComponent(provider)}/api-key`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: apiKey }),
        });
        if (res.status === 204) return null;
        return handleResponse(res);
    },

    /**
     * Remove the Settings-configured key for a provider, reverting it to
     * .env-only (if a .env value exists).
     */
    async deleteLlmProviderApiKey(provider) {
        const res = await authFetch(`${API_BASE_URL}/settings/llm-providers/${encodeURIComponent(provider)}/api-key`, {
            method: 'DELETE',
        });
        if (res.status === 204) return null;
        return handleResponse(res);
    },

    /**
     * Live model discovery against the provider's currently effective key
     * (Settings, else .env). Returns { provider, models, truncated, total_available }.
     * Throws on failure — callers should surface err.message (400 no key,
     * 401 bad key, 502 network/provider error, each with a distinct detail).
     */
    async discoverLlmProviderModels(provider) {
        const res = await authFetch(`${API_BASE_URL}/settings/llm-providers/${encodeURIComponent(provider)}/discover-models`, {
            method: 'POST',
        });
        return handleResponse(res);
    },

    /** Save the selected model for a provider (not validated server-side — trust the discovered-models dropdown). */
    async saveLlmProviderModel(provider, model) {
        const res = await authFetch(`${API_BASE_URL}/settings/llm-providers/${encodeURIComponent(provider)}/model`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model }),
        });
        if (res.status === 204) return null;
        return handleResponse(res);
    },

    async browseDirectory(pathValue = '') {
        const params = new URLSearchParams();
        const normalizedPath = String(pathValue || '').trim();
        if (normalizedPath) params.set('path', normalizedPath);
        const query = params.toString();
        const res = await authFetch(`${API_BASE_URL}/browse-directory${query ? `?${query}` : ''}`);
        return handleResponse(res);
    },

    // Backward-compatible local-folder APIs used by settings and wizard flows.
    async listLocalFolders(includeInactive = false) {
        try {
            const params = new URLSearchParams();
            if (includeInactive) params.set('include_inactive', 'true');
            const query = params.toString();
            const res = await authFetch(`${API_BASE_URL}/settings/local-folders${query ? `?${query}` : ''}`);
            const data = await handleResponse(res);
            return (data || []).map(normalizeLocalFolder);
        } catch (error) {
            if (isAuthError(error)) return [];
            throw error;
        }
    },

    async saveLocalFolder(payload) {
        const res = await authFetch(`${API_BASE_URL}/settings/local-folders`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        });
        const data = await handleResponse(res);
        return normalizeLocalFolder(data);
    },

    async getLocalFolderFiles(tagName) {
        const tag = String(tagName || '').trim();
        if (!tag) throw new Error('Folder tag is required.');
        const res = await authFetch(`${API_BASE_URL}/folders/${encodeURIComponent(tag)}/files`);
        return handleResponse(res);
    },
    async getDatabricksSources(connectionId = '') {
        return _getDatabricksSources(connectionId);
    },

    // ── Password Reset ────────────────────────────────────────────────────

    /**
     * Request a password reset email for the given address.
     * Uses plain fetch — called before the user is authenticated.
     */
    async requestPasswordReset(email) {
        const res = await fetch(`${AUTH_BASE}/forgot-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Failed to request password reset');
        }
        return res.json();
    },

    /**
     * Complete a password reset using the token from the email link.
     * Uses plain fetch — called before the user is authenticated.
     */
    async resetPassword(token, newPassword) {
        const res = await fetch(`${AUTH_BASE}/reset-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, new_password: newPassword }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Failed to reset password');
        }
        return res.json();
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
