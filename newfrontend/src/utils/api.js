const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const TOKEN_KEY = 'semabridge-token';

function normalizeProject(project) {
    if (!project || typeof project !== 'object') return project;

    // Some backend compatibility modes wrap created project as { status, project: {...} }
    if (project.project && typeof project.project === 'object') {
        return normalizeProject(project.project);
    }

    const id = project.id ?? project.project_id ?? null;
    const source = project.source ?? project.adapter ?? project.source_type ?? null;
    const targetType = project.target_type ?? project.target?.type ?? null;

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
        const data = await handleResponse(res);
        return (data || []).map(normalizeWorkspace);
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
        return handleResponse(res);
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

    // ── Projects ───────────────────────────────────────────────────────────
    async listProjects() {
        const res = await authFetch(`${API_BASE_URL}/projects`);
        const data = await handleResponse(res);
        return dedupeProjects(data || []);
    },

    async getProject(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}`);
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

    // ── Job Runs ───────────────────────────────────────────────────────────
    async listJobRuns(filters = {}) {
        const params = new URLSearchParams();
        if (filters.status) params.set('status', filters.status);
        if (filters.project_id) params.set('project_id', filters.project_id);
        const query = params.toString();
        const res = await authFetch(`${API_BASE_URL}/jobs/runs${query ? `?${query}` : ''}`);
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

    async autoMap(projectId) {
        const res = await authFetch(`${API_BASE_URL}/mappings/auto`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(projectId ? { project_id: projectId } : {}),
        });
        return handleResponse(res);
    },

    async updateMapping(mappingId, data) {
        const res = await authFetch(`${API_BASE_URL}/mappings/${mappingId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
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

    async getProjectConfig(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/config`);
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

    async getProjectRuns(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/runs`);
        return handleResponse(res);
    },

    async runProjectNow(projectId) {
        const res = await authFetch(`${API_BASE_URL}/projects/${projectId}/run`, {
            method: 'POST',
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
        const res = await authFetch(`${API_BASE_URL}/discovery/fabric/workspaces`);
        const data = await handleResponse(res);
        return (data || []).map(normalizeWorkspace);
    },

    async discoverFabricModels(workspaceId) {
        if (!workspaceId || workspaceId === 'undefined' || workspaceId === 'null') {
            throw new Error('Workspace ID is required to discover Fabric models.');
        }
        const res = await authFetch(
            `${API_BASE_URL}/discovery/fabric/workspaces/${encodeURIComponent(workspaceId)}/models`
        );
        return handleResponse(res);
    },

    async discoverFabricReports(workspaceId) {
        if (!workspaceId || workspaceId === 'undefined' || workspaceId === 'null') {
            throw new Error('Workspace ID is required to discover Fabric reports.');
        }
        const res = await authFetch(
            `${API_BASE_URL}/discovery/fabric/workspaces/${encodeURIComponent(workspaceId)}/reports`
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

    // ── Global Config — connector sections ───────────────────────────────

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

    // ── Global Config — settings sections ────────────────────────────────

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

