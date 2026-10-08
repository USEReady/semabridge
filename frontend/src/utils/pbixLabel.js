// Display-only cleanup for a raw PBIX path/model label -- both
// result.model (sync_execution_service.py's job_label, currently the full
// on-disk upload path) and model_reports[].model carry the RAW upload path,
// including pbix_service.py's `{uuid4().hex}_` collision-prevention prefix
// (e.g. "C:/.../7ab1bcb7c7d144a6a42c8d58a73c3d46_Sales & Returns Sample
// v201912.pbix"). Mirrors the backend's clean_pbix_model_name
// (identifiers.py) for display purposes: strip the directory, then the
// hex prefix, leaving just "Sales & Returns Sample v201912.pbix".
//
// Falls back to the raw input for anything that doesn't look like a path
// (e.g. a Fabric dataset ID or Snowflake view name used as model_label for
// non-PBIX sources) -- never throws, never returns an empty string for a
// non-empty input.
export function cleanPbixLabel(raw) {
  if (!raw) return '';
  const str = String(raw);
  const basename = str.split(/[/\\]/).pop() || str;
  return basename.replace(/^[0-9a-fA-F]{32}_/, '');
}
