import { emitTelemetryEvent, TELEMETRY_EVENTS } from '../../utils/graphTelemetry.js';

export function looksLikeProjectId(value) {
    const v = String(value || '').trim().toLowerCase();
    return v.startsWith('proj-') || v.startsWith('preview-');
}

export function normalizeProjectKey(value) {
    return String(value || '').trim().toLowerCase().replace(/^proj-/, '');
}

export function resolveProjectCandidates(projectId, allProjectIds = [], aliasCandidates = []) {
    const pid = String(projectId || '').trim();
    if (!pid) return [];

    const candidates = new Set([pid]);
    if (!looksLikeProjectId(pid)) {
        candidates.add(`proj-${pid}`);
    }
    (aliasCandidates || []).forEach((alias) => {
        const value = String(alias || '').trim();
        if (!value) return;
        candidates.add(value);
    });

    const normalized = normalizeProjectKey(pid);
    (allProjectIds || []).forEach((id) => {
        const value = String(id || '').trim();
        if (!value) return;
        if (value === pid || normalizeProjectKey(value) === normalized) {
            candidates.add(value);
        }
    });

    return [...candidates];
}

export function filterSnapshotsByProjectCandidates(snapshotRows = [], projectCandidates = []) {
    const candidateKeys = new Set((projectCandidates || []).map((id) => normalizeProjectKey(id)));
    if (!candidateKeys.size) {
        emitTelemetryEvent(TELEMETRY_EVENTS.SNAPSHOT_NO_MATCH, { reason: 'no_candidates' });
        return [];
    }

    const strongMatches = [];
    const weakMatches = [];

    (snapshotRows || []).forEach((row) => {
        const strongValues = [row?.project_id, row?.projectId, row?.model_id, row?.modelId]
            .map((v) => String(v || '').trim())
            .filter(Boolean);

        const weakValues = [row?.model_name, row?.modelName]
            .map((v) => String(v || '').trim())
            .filter(Boolean);

        const hasStrongMatch = strongValues.some((value) => candidateKeys.has(normalizeProjectKey(value)));
        if (hasStrongMatch) {
            strongMatches.push(row);
            emitTelemetryEvent(TELEMETRY_EVENTS.SNAPSHOT_MATCH_STRONG, {
                model_id: row?.model_id,
                snapshot_id: row?.snapshot_id,
            });
            return;
        }

        const hasWeakMatch = weakValues.some((value) => candidateKeys.has(normalizeProjectKey(value)));
        if (hasWeakMatch) {
            weakMatches.push(row);
            emitTelemetryEvent(TELEMETRY_EVENTS.SNAPSHOT_MATCH_WEAK, {
                model_name: row?.model_name,
                snapshot_id: row?.snapshot_id,
            });
        }
    });

    const result = strongMatches.length ? strongMatches : weakMatches;
    if (!result.length) {
        emitTelemetryEvent(TELEMETRY_EVENTS.SNAPSHOT_NO_MATCH, {
            candidate_count: projectCandidates.length,
            row_count: (snapshotRows || []).length,
        });
    }
    return result;
}

export function graphMatchesProjectCandidates(payload, projectCandidates = [], options = {}) {
    const matchStrength = graphMatchStrength(payload, projectCandidates);
    if (!matchStrength) return false;
    if (options?.requireStrong) return matchStrength === 'strong';
    return matchStrength === 'strong' || matchStrength === 'weak';
}

export function graphMatchStrength(payload, projectCandidates = []) {
    const candidateKeys = new Set((projectCandidates || []).map((id) => normalizeProjectKey(id)));
    if (!candidateKeys.size) return false;

    const payloadStrongValues = [payload?.model_id, payload?.meta?.model_id, payload?.model, payload?.meta?.model]
        .map((v) => String(v || '').trim())
        .filter(Boolean);

    const nodes = Array.isArray(payload?.nodes) ? payload.nodes : [];
    const nodeStrongValues = nodes.flatMap((node) => {
        const data = node?.data && typeof node.data === 'object' ? node.data : {};
        const nodeType = String(data?.nodeType || data?.type || '').toLowerCase();
        const nodeId = String(node?.id || '').trim();
        const modelFromNodeId = nodeType === 'model' && nodeId.startsWith('model-')
            ? nodeId.slice('model-'.length)
            : '';
        return [data?.model_id, data?.modelId, modelFromNodeId];
    })
        .map((v) => String(v || '').trim())
        .filter(Boolean);

    const strongMatches = [...payloadStrongValues, ...nodeStrongValues].some((value) => candidateKeys.has(normalizeProjectKey(value)));
    if (strongMatches) return 'strong';

    const payloadWeakValues = [payload?.model, payload?.model_name, payload?.modelName]
        .map((v) => String(v || '').trim())
        .filter(Boolean);
    const nodeWeakValues = nodes.flatMap((node) => {
        const data = node?.data && typeof node.data === 'object' ? node.data : {};
        return [data?.model_name, data?.modelName];
    })
        .map((v) => String(v || '').trim())
        .filter(Boolean);

    const weakMatches = [...payloadWeakValues, ...nodeWeakValues].some((value) => candidateKeys.has(normalizeProjectKey(value)));
    if (weakMatches) return 'weak';

    emitTelemetryEvent(TELEMETRY_EVENTS.GRAPH_LOAD_MISMATCH, {
        reason: 'no_matching_values',
        payload_models: (payloadWeakValues || []).slice(0, 3),
        candidates: projectCandidates.slice(0, 3),
    });
    return false;
}

export function buildSnapshotScopes(snapshot = {}, preferredScope = '') {
    const scopes = [
        preferredScope,
        snapshot?.snapshot_scope,
        snapshot?.project_id,
        snapshot?.projectId,
        snapshot?.model_id,
        snapshot?.modelId,
        snapshot?.model_name,
        snapshot?.modelName,
    ]
        .map((v) => String(v || '').trim())
        .filter(Boolean)
        .filter((v) => v !== '__all__');
    return [...new Set(scopes)];
}
