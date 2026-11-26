/**
 * Reading a Databricks deploy preview and run on the screen (Phase 3.8b).
 * Pure helpers; the server decides everything -- these only summarise.
 */

export const ACTION_LABEL = {
  create: 'will be created',
  update: 'will be updated (ALTER, grants kept)',
  unchanged: 'unchanged',
  blocked: 'blocked',
  exists_owned: 'exists (ours, empty)',
  ok: 'ok',
  pending: 'pending',
};

export const OUTCOME_LABEL = {
  created: 'created ✓',
  updated: 'updated ✓',
  unchanged: 'unchanged ✓',
  verified: 'verified ✓',
  applied_unverified: 'applied — NOT verified',
  failed: 'failed',
  skipped: 'skipped',
};

export const RUN_STATUS_LABEL = {
  running: 'Deploying…',
  succeeded: 'Deployed',
  partial: 'Partly deployed',
  failed: 'Failed',
  no_op: 'Nothing to change',
  uncertain: 'Stopped: outcome unknown — run Reconcile',
  superseded: 'Not run: something changed since the preview — preview again',
};

/** Counts of views by action, for the preview headline. */
export function previewCounts(preview) {
  const out = { create: 0, update: 0, unchanged: 0, blocked: 0 };
  for (const v of preview?.views || []) {
    if (v.action in out) out[v.action] += 1;
  }
  out.stubs = (preview?.stub_tables || []).filter((t) => t.action === 'create').length;
  out.incomplete = (preview?.views || []).filter(
    (v) => v.readiness === 'incomplete_unverified' && (v.action === 'create' || v.action === 'update'),
  ).length;
  return out;
}

/** Whether Confirm may be pressed (the server checks again). */
export function canConfirm(result, acknowledged) {
  const intent = result?.intent;
  if (!intent) return false;
  if (intent.requires_access_acknowledgement && !acknowledged) return false;
  return true;
}

/** Problems the user must fix before a deploy (model-wide), plus per-object blocks. */
export function blockingReasons(preview) {
  const model = (preview?.model_blockers || []).map((b) => ({ scope: 'deploy', code: b.code, message: b.message }));
  const items = [];
  for (const group of ['views', 'stub_tables', 'sources']) {
    for (const it of preview?.[group] || []) {
      if (it.action !== 'blocked') continue;
      for (const r of it.reasons || []) items.push({ scope: it.name, code: r.code, message: r.message });
    }
  }
  return { model, items };
}

export function runFinished(run) {
  return Boolean(run && run.status && run.status !== 'running');
}

/** Results needing attention after a run. */
export function attention(run) {
  const results = run?.summary?.results || [];
  return {
    unverified: results.filter((r) => r.outcome === 'applied_unverified'),
    failed: results.filter((r) => r.outcome === 'failed' || r.outcome === 'skipped'),
    needsReconcile: run?.status === 'uncertain',
  };
}
