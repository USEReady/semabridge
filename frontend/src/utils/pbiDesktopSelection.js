/**
 * Helper utilities for Power BI Desktop source selection, state management,
 * reconciliation, and configuration building.
 */

export function isDesktopInstanceSelected(instance, selectedFiles = []) {
  if (!instance || !selectedFiles) return false;
  const targetPath = (instance.file_path || '').toLowerCase();
  const targetName = (instance.file_name || '').toLowerCase();
  const targetPort = instance.port;
  const targetDb = instance.database_name;

  return selectedFiles.some((f) => {
    const fPath = (f.file_path || '').toLowerCase();
    const fName = (f.file_name || f.display_name || '').toLowerCase();
    if (fPath && targetPath && fPath === targetPath) return true;
    if (fName && targetName && fName === targetName) return true;
    if (targetPort && targetDb && f.port === targetPort && f.database_name === targetDb) return true;
    return false;
  });
}

export function toggleDesktopSelection(currentSelected = [], instance) {
  if (!instance || instance.is_live_connected) {
    return currentSelected; // Live-connected models cannot be selected
  }

  const isSelected = isDesktopInstanceSelected(instance, currentSelected);
  const targetPath = (instance.file_path || '').toLowerCase();
  const targetName = (instance.file_name || '').toLowerCase();
  const targetPort = instance.port;
  const targetDb = instance.database_name;

  if (isSelected) {
    return currentSelected.filter((f) => {
      const fPath = (f.file_path || '').toLowerCase();
      const fName = (f.file_name || f.display_name || '').toLowerCase();
      if (fPath && targetPath && fPath === targetPath) return false;
      if (fName && targetName && fName === targetName) return false;
      if (targetPort && targetDb && f.port === targetPort && f.database_name === targetDb) return false;
      return true;
    });
  }

  const newEntry = {
    file_path: instance.file_path || instance.file_name,
    file_name: instance.file_name,
    port: instance.port,
    database_name: instance.database_name,
    has_unsaved_changes: Boolean(instance.has_unsaved_changes),
  };

  return [...currentSelected, newEntry];
}

export function reconcileDesktopSelections(selectedFiles = [], currentInstances = []) {
  const kept = [];
  const closed = [];

  for (const sel of selectedFiles) {
    const match = currentInstances.find((inst) => {
      if (sel.file_path && inst.file_path && sel.file_path.toLowerCase() === inst.file_path.toLowerCase()) {
        return true;
      }
      if (sel.file_name && inst.file_name && sel.file_name.toLowerCase() === inst.file_name.toLowerCase()) {
        return true;
      }
      if (sel.port && sel.database_name && sel.port === inst.port && sel.database_name === inst.database_name) {
        return true;
      }
      return false;
    });

    if (match) {
      kept.push({
        ...sel,
        file_path: match.file_path || sel.file_path,
        file_name: match.file_name || sel.file_name,
        port: match.port,
        database_name: match.database_name,
        has_unsaved_changes: match.has_unsaved_changes,
        is_live_connected: match.is_live_connected,
      });
    } else {
      closed.push(sel.file_name || sel.file_path || 'Unknown file');
    }
  }

  const closedNotice = closed.length > 0
    ? `The following file(s) closed and were removed from selection: ${closed.join(', ')}`
    : '';

  return { kept, closed, closedNotice };
}

export function getDesktopStepState({ loading = false, error = '', instances = [] } = {}) {
  if (loading) return 'loading';
  if (error) return 'error';
  if (!instances || instances.length === 0) return 'empty';
  return 'list';
}

export function formatInspectSummary(summary) {
  if (!summary || summary.error) return null;
  return {
    tablesCount: summary.tables_count ?? 0,
    measuresCount: summary.measures_count ?? 0,
    relationshipsCount: summary.relationships_count ?? 0,
    tablesText: `${summary.tables_count ?? 0} Tables`,
    measuresText: `${summary.measures_count ?? 0} Measures`,
    relationshipsText: `${summary.relationships_count ?? 0} Relationships`,
  };
}

export function buildPbiDesktopSourceConfig(selectedFiles = []) {
  return {
    type: 'pbi_desktop',
    models: selectedFiles.map((f) => ({
      file_path: f.file_path,
      display_name: f.file_name || f.display_name || f.file_path.split(/[\\/]/).pop(),
    })),
  };
}
