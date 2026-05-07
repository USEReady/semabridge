import { parseDocument as parseYamlDocument } from 'yaml';

export function parseConfigYamlToInitialData(yamlText, projectMeta) {
  const raw = String(yamlText || '').trim();
  const source = projectMeta?.source && typeof projectMeta.source === 'object' ? projectMeta.source : {};
  const fallback = {
    source_type: String(source.type || projectMeta?.source || 'fabric'),
    target_type: String(projectMeta?.target_type || 'snowflake'),
    output_format: 'osi',
    identity_id: '',
    workspace_id: '',
    database: '',
    schema: '',
    target_database: '',
    target_schema: '',
    target_account: '',
    target_warehouse: '',
    target_identity_id: '',
    target_workspace_id: '',
    pbix_path: '',
    pbix_folder: '',
    pbix_uploaded_path: '',
    models: [],
  };

  if (!raw) return fallback;

  try {
    const doc = parseYamlDocument(raw, { uniqueKeys: false, prettyErrors: true });
    const tree = doc.toJS ? doc.toJS() : {};

    const parsedSource = tree?.source && typeof tree.source === 'object' ? tree.source : {};
    const targets = Array.isArray(tree?.targets) ? tree.targets : (tree?.target ? [tree.target] : []);
    const firstTarget = targets[0] && typeof targets[0] === 'object' ? targets[0] : {};
    const ui = tree?.ui && typeof tree.ui === 'object' ? tree.ui : {};

    let models = [];
    if (Array.isArray(parsedSource.models)) {
      models = parsedSource.models.filter(model => model && model !== '*');
    } else if (typeof parsedSource.model === 'string' && parsedSource.model && parsedSource.model !== '*') {
      models = [parsedSource.model];
    }

    const selectionModelIds = Array.isArray(tree?.selection?.model_ids)
      ? tree.selection.model_ids
      : [];
    if (selectionModelIds.length > 0 && models.length === 0) {
      models = selectionModelIds;
    }

    return {
      source_type: String(parsedSource.type || fallback.source_type),
      target_type: String(firstTarget.type || fallback.target_type),
      output_format: String(ui.intermediate_format || fallback.output_format),
      identity_id: String(parsedSource.identity_id || ''),
      workspace_id: String(parsedSource.workspace_id || ''),
      database: String(parsedSource.database || ''),
      schema: String(parsedSource.schema || ''),
      target_database: String(firstTarget.database || ''),
      target_schema: String(firstTarget.schema || ''),
      target_account: String(firstTarget.account || ''),
      target_warehouse: String(firstTarget.warehouse || ''),
      target_identity_id: String(firstTarget.identity_id || ''),
      target_workspace_id: String(firstTarget.workspace_id || ''),
      pbix_path: String(parsedSource.pbix_path || parsedSource.pbix_file_path || parsedSource.source_path || parsedSource.file_path || ''),
      pbix_folder: String(parsedSource.pbix_folder || ''),
      pbix_uploaded_path: String(projectMeta?.pbix_file_path || parsedSource.pbix_file_path || parsedSource.pbix_path || ''),
      models,
    };
  } catch {
    return fallback;
  }
}