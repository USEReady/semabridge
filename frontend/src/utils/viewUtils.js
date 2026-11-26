/**
 * View utility functions for normalizing view identifiers and computing display labels.
 */

export function normalizeViewName(v) {
  if (!v) return '';
  return String(v).trim().replace(/"/g, '').toLowerCase();
}

export function canonicalViewName(v) {
  if (!v) return '';
  const parts = String(v).trim().replace(/"/g, '').split('.');
  return parts[parts.length - 1].toLowerCase();
}

export function isViewMatch(a, b) {
  if (!a || !b) return false;
  if (normalizeViewName(a) === normalizeViewName(b)) return true;
  const canA = canonicalViewName(a);
  const canB = canonicalViewName(b);
  return Boolean(canA && canB && canA === canB);
}

export function getViewBadgeLabel(v, projectSchema = '') {
  if (!v) return '';
  if (v.managed === false) {
    return 'Not deployed by this project';
  }
  if (projectSchema && v.schema && v.schema.toUpperCase() !== projectSchema.toUpperCase()) {
    return 'Other schema';
  }
  if (v.is_latest) {
    return 'Latest';
  }
  return 'Older deploy';
}
