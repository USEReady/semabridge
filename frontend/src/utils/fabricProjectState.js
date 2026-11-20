/**
 * Fabric project state helpers (create + edit), kept pure so they can be unit
 * tested without React:
 *
 *  - hydrateFabricEditState: a saved project -> SOURCE and TARGET fields kept
 *    apart (a Fabric target identity/workspace must never overwrite the
 *    source's, or the reverse), with the saved connection restored.
 *  - classifyModelDiscovery: a discovery result or error -> ok / empty /
 *    reauth / error. A Fabric re-authentication answer (401, or the server's
 *    `reauth_required` payload) becomes `reauth`, so the UI offers
 *    "Sign in again" instead of "Failed to load semantic models".
 *  - modelPickerOptions: the workspace listing -> picker rows, with the saved
 *    selection matched exactly (GUID, or display name case-insensitively).
 *    A saved entry that is not in the workspace is shown as missing; nothing
 *    is guessed.
 *
 * Nothing here stores tokens or secrets: only account / workspace / model ids.
 */

const str = (v) => (v == null ? '' : String(v)).trim();

export function hydrateFabricEditState(initialData = {}) {
  const d = initialData || {};
  const sourceIdentity = str(d.identity_id);
  return {
    source: {
      // The saved source connection drives model discovery (selectedConnectionId)
      // and is written back as source.identity_id (fabricAccountId).
      fabricAccountId: sourceIdentity,
      selectedConnectionId: sourceIdentity,
      fabricWorkspaceId: str(d.workspace_id),
    },
    target: {
      targetFabricAccountId: str(d.target_identity_id),
      targetFabricWorkspaceId: str(d.target_workspace_id),
    },
  };
}

export function isReauthPayload(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const detail = value.detail && typeof value.detail === 'object' ? value.detail : {};
  return value.error === 'reauth_required'
    || detail.error === 'reauth_required'
    || value.error_type === 'AuthenticationError'
    || (typeof value.detail === 'string' && value.detail.includes('reauth_required'));
}

export function classifyModelDiscovery({ result, error } = {}) {
  if (error) {
    const status = Number(error.status || error?.response?.status);
    if (status === 401 || isReauthPayload(error.payload)) {
      return { state: 'reauth', models: [], message: 'Your Fabric sign-in has expired. Sign in again to load the models.' };
    }
    return { state: 'error', models: [], message: `Failed to load semantic models: ${error.message || 'Unknown error'}` };
  }
  if (isReauthPayload(result)) {
    return { state: 'reauth', models: [], message: 'Your Fabric sign-in has expired. Sign in again to load the models.' };
  }
  if (!Array.isArray(result)) {
    return { state: 'error', models: [], message: 'Failed to load semantic models: unexpected response from the server.' };
  }
  if (result.length === 0) {
    return { state: 'empty', models: [], message: 'No semantic models found for this workspace. Check Fabric permissions or workspace contents.' };
  }
  return { state: 'ok', models: result, message: '' };
}

export function modelDisplayName(model) {
  return str(model?.displayName || model?.name || model?.id);
}

export function parseModelList(text) {
  return str(text).split(',').map((s) => s.trim()).filter(Boolean);
}

export function modelPickerOptions(models = [], selected = []) {
  const wanted = (Array.isArray(selected) ? selected : parseModelList(selected)).map(str).filter(Boolean);
  const used = new Set();
  const rows = (models || []).map((m) => {
    const id = str(m?.id);
    const name = modelDisplayName(m);
    const hit = wanted.find((w) => w === id || w.toLowerCase() === name.toLowerCase());
    if (hit) used.add(hit);
    return { id, name, checked: Boolean(hit), missing: false };
  });
  const missing = wanted
    .filter((w) => !used.has(w))
    .map((w) => ({ id: w, name: w, checked: true, missing: true }));
  return [...rows, ...missing];
}

/** Picker rows -> the value saved in source.models (display names, or the saved text for missing rows). */
export function selectedModelNames(rows = []) {
  return rows.filter((r) => r.checked).map((r) => r.name);
}
