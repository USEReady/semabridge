/**
 * pbixFileEntries — pure helpers for the PBIX "Multiple files" list that lives
 * in the persisted wizard store (projectWizardStore.pbixFileEntries).
 *
 * Entry shape: { id, name, size, path, status: 'uploading' | 'done' | 'error', error }
 */
import { cleanPbixLabel } from './pbixLabel.js';
import { MAX_MULTI_SOURCES } from './dryRunPayload.js';

let entryCounter = 0;
function nextEntryId(name) {
  entryCounter += 1;
  return `pbix-${Date.now().toString(36)}-${entryCounter}-${String(name || '').length}`;
}

/** Paths of successfully uploaded entries, in list order. */
export function donePbixPaths(entries = []) {
  return (Array.isArray(entries) ? entries : [])
    .filter((e) => e?.status === 'done' && String(e?.path || '').trim())
    .map((e) => String(e.path).trim());
}

/**
 * planPbixFileAdditions — decides which incoming files get added, given the
 * current list. Duplicate names and non-.pbix files are NOT silently dropped:
 * they're reported back so the UI can show a visible message.
 *
 * incoming: Array<{ name, size }> (File objects work as-is)
 * returns { accepted: File[], newEntries: Entry[], duplicates: string[], invalid: string[], overCap: string[], message }
 */
export function planPbixFileAdditions(existing = [], incoming = [], maxFiles = MAX_MULTI_SOURCES) {
  const current = Array.isArray(existing) ? existing : [];
  const names = new Set(current.map((e) => String(e?.name || '').toLowerCase()));
  const accepted = [];
  const newEntries = [];
  const duplicates = [];
  const invalid = [];
  const overCap = [];

  Array.from(incoming || []).forEach((file) => {
    const name = String(file?.name || '').trim();
    if (!name.toLowerCase().endsWith('.pbix')) {
      invalid.push(name || '(unnamed)');
      return;
    }
    const key = name.toLowerCase();
    if (names.has(key)) {
      duplicates.push(name);
      return;
    }
    if (current.length + accepted.length >= maxFiles) {
      overCap.push(name);
      return;
    }
    names.add(key);
    accepted.push(file);
    newEntries.push({ id: nextEntryId(name), name, size: Number(file?.size) || 0, path: '', status: 'uploading', error: '' });
  });

  const parts = [];
  if (duplicates.length) {
    parts.push(`${duplicates.join(', ')} ${duplicates.length === 1 ? 'is' : 'are'} already in the list (same file name) and ${duplicates.length === 1 ? 'was' : 'were'} not added again. Remove the existing entry first to replace it.`);
  }
  if (invalid.length) parts.push(`Only .pbix files are supported (skipped: ${invalid.join(', ')}).`);
  if (overCap.length) parts.push(`Up to ${maxFiles} PBIX files are supported per project (not added: ${overCap.join(', ')}).`);

  return { accepted, newEntries, duplicates, invalid, overCap, message: parts.join(' ') };
}

/** Entries for an already-saved project (edit mode): every path is an already-uploaded file. */
export function entriesFromSavedPaths(paths = []) {
  return (Array.isArray(paths) ? paths : [])
    .map((p) => String(p || '').trim())
    .filter(Boolean)
    .map((path, index) => ({
      id: `saved-${index}-${cleanPbixLabel(path)}`,
      name: cleanPbixLabel(path) || path,
      size: 0,
      path,
      status: 'done',
      error: '',
    }));
}

/** After a reload, an 'uploading' entry's request is gone -- surface it instead of spinning forever. */
export function markInterruptedUploads(entries = []) {
  return (Array.isArray(entries) ? entries : []).map((e) => (
    e?.status === 'uploading'
      ? { ...e, status: 'error', error: 'Upload was interrupted (page reloaded). Remove and add the file again.' }
      : e
  ));
}
