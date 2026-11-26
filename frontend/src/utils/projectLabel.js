// A project's name as the user typed it. `name` holds the source model's name
// (several projects can sync the same model), so it is only the fallback.
export function projectName(p) {
  if (!p) return '';
  return String(p.display_name || p.semantic_name || p.name || p.id || p.project_id || '').trim();
}

// Option label for project pickers: the project's name, plus its id when two
// projects share that name.
export function projectLabel(p, all = []) {
  const name = projectName(p);
  const id = p?.id || p?.project_id || '';
  const dup = all.filter((o) => projectName(o).toLowerCase() === name.toLowerCase()).length > 1;
  return dup && id && id !== name ? `${name} (${id})` : name;
}
