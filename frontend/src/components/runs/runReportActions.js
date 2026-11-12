/** Non-component helpers shared by RunReportModal and the Runs page. */
import { api } from '../../utils/api';

/** Default model for a run: null for single-file runs, the first file for multi-file runs. */
export function defaultReportModel(run, preselectModel = null) {
  const entries = Array.isArray(run?.model_reports) ? run.model_reports : [];
  if (preselectModel) return entries.some(e => e.model === preselectModel) ? preselectModel : null;
  return entries.length > 1 ? entries[0].model : null;
}

export async function downloadRunMarkdown(run, model = null) {
  const rid = run.id || run.run_id;
  if (model) await api.downloadRunModelReport(run.project_id, rid, model);
  else await api.downloadRunReport(run.project_id, rid);
}
