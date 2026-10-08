/**
 * modelSelection — pure helpers behind the Step 3 model picker (Fabric /
 * Snowflake semantic models).
 *
 * A selected model can be keyed three ways in the wizard store's
 * `selectedModels` list:
 *   - its GUID/id                (fresh picks in the flat list)
 *   - `${workspaceId}::${id}`    (legacy tree-view picks)
 *   - its display name           (edit-mode hydration from a saved config,
 *                                 which stores model NAMES under source.models)
 * Edit mode used to add BOTH the saved name and the saved GUID, so one model
 * showed up twice and a single click only removed one of the two keys (the row
 * stayed checked). Every helper here treats all keys that refer to the same
 * model as one selection.
 */
import { MAX_MULTI_SOURCES } from './dryRunPayload.js';

export const MAX_SELECTED_MODELS = MAX_MULTI_SOURCES;

export function modelKeyId(key) {
  const text = String(key ?? '');
  return text.includes('::') ? text.split('::').pop() : text;
}

function norm(value) {
  return String(value ?? '').trim();
}

/** Every key in `selectedKeys` that refers to `model` ({ id, name }). */
export function findSelectedKeysForModel(model, selectedKeys = [], nameByKey = {}) {
  const id = norm(model?.id);
  const name = norm(model?.name);
  return Array.from(selectedKeys || []).filter((key) => {
    const keyId = norm(modelKeyId(key));
    if (id && keyId === id) return true;
    if (name && norm(key) === name) return true;
    if (name && norm(nameByKey?.[key]) === name) return true;
    return false;
  });
}

export function isModelSelected(model, selectedKeys = [], nameByKey = {}) {
  return findSelectedKeysForModel(model, selectedKeys, nameByKey).length > 0;
}

/**
 * Unique model identities in the selection (a name key and a GUID key for the
 * same model count once). Identity = resolved display name when known, else
 * the key's id part.
 */
export function selectionIdentities(selectedKeys = [], nameByKey = {}) {
  const seen = new Set();
  const out = [];
  Array.from(selectedKeys || []).forEach((key) => {
    const identity = norm(nameByKey?.[key]) || norm(modelKeyId(key));
    if (identity && !seen.has(identity)) {
      seen.add(identity);
      out.push(identity);
    }
  });
  return out;
}

/** Deduped model names sent to the backend (keys without a known name are skipped, as before). */
export function resolveSelectedModelNames(selectedKeys = [], nameByKey = {}) {
  const seen = new Set();
  const out = [];
  Array.from(selectedKeys || []).forEach((key) => {
    const name = norm(nameByKey?.[key]);
    if (name && !seen.has(name)) {
      seen.add(name);
      out.push(name);
    }
  });
  return out;
}

/**
 * toggleModelSelection — one click on a model row.
 *  - If ANY key for this model is selected, ALL of them are removed (so a
 *    single click always unchecks, even with legacy name+GUID duplicates).
 *  - Otherwise the model is added under its id (with its name), unless that
 *    would exceed `max`, in which case nothing changes and limitReached=true.
 */
export function toggleModelSelection({ selectedKeys = [], nameByKey = {}, model, max = MAX_SELECTED_MODELS } = {}) {
  const keys = Array.from(selectedKeys || []);
  const names = { ...(nameByKey || {}) };
  const matches = findSelectedKeysForModel(model, keys, names);

  if (matches.length > 0) {
    const drop = new Set(matches);
    matches.forEach((key) => { delete names[key]; });
    return {
      selectedKeys: keys.filter((key) => !drop.has(key)),
      nameByKey: names,
      selected: false,
      limitReached: false,
    };
  }

  const key = norm(model?.id) || norm(model?.name);
  if (!key) {
    return { selectedKeys: keys, nameByKey: names, selected: false, limitReached: false };
  }
  if (Number.isFinite(max) && selectionIdentities(keys, names).length >= max) {
    return { selectedKeys: keys, nameByKey: names, selected: false, limitReached: true };
  }
  names[key] = norm(model?.name) || key;
  return { selectedKeys: [...keys, key], nameByKey: names, selected: true, limitReached: false };
}

/**
 * dedupeModelSelection — collapse keys that refer to the same model into one
 * GUID key once the workspace's model list is known (edit-mode hydration
 * seeds names; this converts them to ids and fills in names for bare GUIDs).
 * Keys that don't match any available model are kept as-is (they render as
 * "previously synced but not found").
 */
export function dedupeModelSelection(selectedKeys = [], nameByKey = {}, availableModels = []) {
  const models = Array.isArray(availableModels) ? availableModels : [];
  const outKeys = [];
  const outNames = {};
  const seenIdentity = new Set();

  Array.from(selectedKeys || []).forEach((key) => {
    const keyId = norm(modelKeyId(key));
    const knownName = norm(nameByKey?.[key]);
    const match = models.find((m) => norm(m?.id) === keyId)
      || models.find((m) => norm(m?.name) && (norm(m.name) === norm(key) || norm(m.name) === knownName));

    const canonicalKey = match ? norm(match.id) || norm(match.name) : String(key);
    const canonicalName = match ? (norm(match.name) || knownName || canonicalKey) : knownName;
    const identity = match ? `id:${canonicalKey}` : `name:${knownName || keyId}`;
    if (seenIdentity.has(identity)) return;
    seenIdentity.add(identity);
    outKeys.push(canonicalKey);
    if (canonicalName) outNames[canonicalKey] = canonicalName;
  });

  return { selectedKeys: outKeys, nameByKey: outNames };
}

/**
 * Edit-mode hydration seed: prefer saved model NAMES (that's what the backend
 * consumes); only fall back to saved GUIDs when no names were stored. Never
 * seed both, which is what used to double every saved model.
 */
export function seedSelectionFromSavedConfig({ models = [], selectionModelIds = [] } = {}) {
  const names = (Array.isArray(models) ? models : [models]).map(norm).filter(Boolean);
  const ids = (Array.isArray(selectionModelIds) ? selectionModelIds : []).map(norm).filter(Boolean);
  const keys = names.length > 0 ? [...new Set(names)] : [...new Set(ids)];
  const nameByKey = {};
  if (names.length > 0) keys.forEach((key) => { nameByKey[key] = key; });
  return { selectedKeys: keys, nameByKey };
}
