/**
 * View utility functions for normalizing view identifiers and computing display labels.
 */

export function normalizeViewName(v) {
  if (!v) return '';
  return String(v).trim().replace(/"/g, '').toLowerCase();
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
