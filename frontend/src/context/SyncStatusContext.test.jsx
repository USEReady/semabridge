import { describe, expect, it } from 'vitest';
import { buildProjectRunSnapshot } from './SyncStatusContext';

describe('buildProjectRunSnapshot', () => {
  it('drops stale running projects when a newer poll snapshot no longer includes them', () => {
    const initial = buildProjectRunSnapshot([
      {
        id: 'run-a',
        project_id: 'project-a',
        status: 'running',
        started_at: '2026-05-21T10:00:00.000Z',
        total_models: 10,
        models_synced: 2,
      },
    ], Date.parse('2026-05-21T10:05:00.000Z'));

    expect(initial.projectStatusById['project-a']).toBe('running');
    expect(initial.projectProgressById['project-a']).toBe(20);
    expect(initial.activeRun?.project_id).toBe('project-a');

    const next = buildProjectRunSnapshot([
      {
        id: 'run-b',
        project_id: 'project-b',
        status: 'success',
        started_at: '2026-05-21T10:06:00.000Z',
        total_models: 4,
        models_synced: 4,
      },
    ], Date.parse('2026-05-21T10:07:00.000Z'));

    expect(next.projectStatusById['project-a']).toBeUndefined();
    expect(next.projectProgressById['project-a']).toBeUndefined();
    expect(next.projectStatusById['project-b']).toBe('success');
    expect(next.projectProgressById['project-b']).toBe(100);
    expect(next.activeRun).toBeNull();
    expect(next.completedRun?.project_id).toBe('project-b');
  });
});
