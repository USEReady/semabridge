import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isDesktopInstanceSelected,
  toggleDesktopSelection,
  reconcileDesktopSelections,
  getDesktopStepState,
  formatInspectSummary,
  buildPbiDesktopSourceConfig,
} from '../src/utils/pbiDesktopSelection.js';
import { parseConfigYamlToInitialData } from '../src/utils/parseConfigYamlToInitialData.js';
import { buildDryRunPayload } from '../src/utils/dryRunPayload.js';

const INSTANCE_A = {
  file_name: 'Sales.pbix',
  file_path: 'C:/Users/test/Documents/Sales.pbix',
  port: 54321,
  database_name: 'db-guid-1',
  has_unsaved_changes: false,
  is_live_connected: false,
};

const INSTANCE_B = {
  file_name: 'Marketing.pbix',
  file_path: 'C:/Users/test/Documents/Marketing.pbix',
  port: 54322,
  database_name: 'db-guid-2',
  has_unsaved_changes: true,
  is_live_connected: false,
};

const LIVE_CONNECTED_INSTANCE = {
  file_name: 'LiveReport.pbix',
  file_path: 'C:/Users/test/Documents/LiveReport.pbix',
  port: 54323,
  database_name: 'db-guid-3',
  has_unsaved_changes: false,
  is_live_connected: true,
};

test('Desktop step state: loading state', () => {
  assert.equal(getDesktopStepState({ loading: true }), 'loading');
  assert.equal(getDesktopStepState({ loading: true, instances: [INSTANCE_A] }), 'loading');
});

test('Desktop step state: error state', () => {
  assert.equal(getDesktopStepState({ error: 'Engine discovery failed' }), 'error');
});

test('Desktop step state: empty state when no running instances', () => {
  assert.equal(getDesktopStepState({ instances: [] }), 'empty');
  assert.equal(getDesktopStepState({ instances: null }), 'empty');
});

test('Desktop step state: list state when instances detected', () => {
  assert.equal(getDesktopStepState({ instances: [INSTANCE_A] }), 'list');
});

test('Selection: toggle selection adds model to list', () => {
  const selected = toggleDesktopSelection([], INSTANCE_A);
  assert.equal(selected.length, 1);
  assert.equal(selected[0].file_path, INSTANCE_A.file_path);
  assert.equal(selected[0].file_name, INSTANCE_A.file_name);
  assert.equal(selected[0].has_unsaved_changes, false);
  assert.equal(isDesktopInstanceSelected(INSTANCE_A, selected), true);
});

test('Selection: multi-select allows selecting multiple instances', () => {
  let selected = toggleDesktopSelection([], INSTANCE_A);
  selected = toggleDesktopSelection(selected, INSTANCE_B);
  assert.equal(selected.length, 2);
  assert.equal(isDesktopInstanceSelected(INSTANCE_A, selected), true);
  assert.equal(isDesktopInstanceSelected(INSTANCE_B, selected), true);
  assert.equal(selected.some((f) => f.has_unsaved_changes), true);
});

test('Selection: deselecting removes item from list', () => {
  let selected = toggleDesktopSelection([], INSTANCE_A);
  selected = toggleDesktopSelection(selected, INSTANCE_B);
  assert.equal(selected.length, 2);

  selected = toggleDesktopSelection(selected, INSTANCE_A);
  assert.equal(selected.length, 1);
  assert.equal(isDesktopInstanceSelected(INSTANCE_A, selected), false);
  assert.equal(isDesktopInstanceSelected(INSTANCE_B, selected), true);
});

test('Disabled: live-connected instance cannot be selected', () => {
  const initial = [INSTANCE_A];
  const next = toggleDesktopSelection(initial, LIVE_CONNECTED_INSTANCE);
  assert.deepEqual(next, initial);
  assert.equal(isDesktopInstanceSelected(LIVE_CONNECTED_INSTANCE, next), false);
});

test('Reconciliation: keeps open files and drops closed files with a note', () => {
  const previouslySelected = [
    { file_path: INSTANCE_A.file_path, file_name: INSTANCE_A.file_name },
    { file_path: 'C:/Users/test/Closed.pbix', file_name: 'Closed.pbix' },
  ];

  const currentRunning = [INSTANCE_A, INSTANCE_B];
  const { kept, closed, closedNotice } = reconcileDesktopSelections(previouslySelected, currentRunning);

  assert.equal(kept.length, 1);
  assert.equal(kept[0].file_name, 'Sales.pbix');
  assert.deepEqual(closed, ['Closed.pbix']);
  assert.match(closedNotice, /Closed\.pbix/);
  assert.match(closedNotice, /closed and were removed/);
});

test('Inspect summary: formats counts properly', () => {
  const summary = formatInspectSummary({
    tables_count: 14,
    measures_count: 42,
    relationships_count: 11,
  });

  assert.equal(summary.tablesCount, 14);
  assert.equal(summary.measuresCount, 42);
  assert.equal(summary.relationshipsCount, 11);
  assert.equal(summary.tablesText, '14 Tables');
  assert.equal(summary.measuresText, '42 Measures');
  assert.equal(summary.relationshipsText, '11 Relationships');

  assert.equal(formatInspectSummary(null), null);
  assert.equal(formatInspectSummary({ error: 'Inspection failed' }), null);
});

test('Saved config: never saves port, PID or database name', () => {
  const selectedWithSession = [
    {
      file_path: 'C:/reports/Sales.pbix',
      file_name: 'Sales.pbix',
      port: 50123,
      pid: 9999,
      database_name: 'Guid-XYZ',
      has_unsaved_changes: true,
    },
  ];

  const config = buildPbiDesktopSourceConfig(selectedWithSession);
  assert.deepEqual(config, {
    type: 'pbi_desktop',
    models: [
      {
        file_path: 'C:/reports/Sales.pbix',
        display_name: 'Sales.pbix',
      },
    ],
  });

  const modelEntry = config.models[0];
  assert.equal('port' in modelEntry, false);
  assert.equal('pid' in modelEntry, false);
  assert.equal('database_name' in modelEntry, false);
  assert.equal('database' in modelEntry, false);
  assert.equal('server' in modelEntry, false);
});

test('parseConfigYamlToInitialData: restores pbi_desktop_models for edit mode', () => {
  const yamlText = `
source:
  type: pbi_desktop
  models:
    - file_path: "C:/Reports/Sales.pbix"
      display_name: "Sales"
    - file_path: "C:/Reports/Costs.pbix"
      display_name: "Costs"
targets:
  - type: snowflake
`;
  const result = parseConfigYamlToInitialData(yamlText, {
    id: 10,
    source: { type: 'pbi_desktop' },
    target_type: 'snowflake',
  });

  assert.equal(result.source_type, 'pbi_desktop');
  assert.deepEqual(result.pbi_desktop_models, [
    { file_path: 'C:/Reports/Sales.pbix', display_name: 'Sales' },
    { file_path: 'C:/Reports/Costs.pbix', display_name: 'Costs' },
  ]);
});

test('buildDryRunPayload: includes pbi_desktop source configuration', () => {
  const payload = buildDryRunPayload({
    sourceConnector: 'pbi_desktop',
    pbiDesktopModels: [
      { file_path: 'C:/Reports/Model.pbix', display_name: 'Model' },
    ],
    targetConnectors: new Set(['snowflake']),
    snowflakeDatabase: 'ANALYTICS',
    targetDatabase: 'REPORTING',
  });

  assert.deepEqual(payload.sourceConfig, {
    type: 'pbi_desktop',
    models: [
      { file_path: 'C:/Reports/Model.pbix', display_name: 'Model' },
    ],
  });
});

// ════════════════════════════════════════════════════════════════════════════
// Tests with REAL live-recorded fixtures from the running backend
// ════════════════════════════════════════════════════════════════════════════
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FIXTURES_DIR = path.resolve(__dirname, 'fixtures', 'pbi_desktop');

function loadFixture(filename) {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES_DIR, filename), 'utf8'));
}

test('Real fixture: instances_success.json parsing, state, and selection', () => {
  const data = loadFixture('instances_success.json');
  assert.equal(data.enabled, true);
  assert.equal(data.total, 1);
  assert.equal(data.instances.length, 1);

  const inst = data.instances[0];
  assert.equal(inst.port, 56888);
  assert.equal(inst.database_name, '3f81a039-98ba-4ff6-bfe3-82c5c5bb69db');
  assert.equal(inst.file_name, 'Untitled.pbix');
  assert.equal(inst.is_live_connected, false);

  const state = getDesktopStepState({ loading: false, error: '', instances: data.instances });
  assert.equal(state, 'list');

  const selected = toggleDesktopSelection([], inst);
  assert.equal(selected.length, 1);
  assert.equal(isDesktopInstanceSelected(inst, selected), true);
});

test('Real fixture: inspect_success.json formatting and metadata counts', () => {
  const data = loadFixture('inspect_success.json');
  assert.equal(data.file_name, 'Untitled.pbix');
  assert.equal(data.tables_count, 11);
  assert.equal(data.measures_count, 15);
  assert.equal(data.relationships_count, 8);

  const formatted = formatInspectSummary(data);
  assert.equal(formatted.tablesCount, 11);
  assert.equal(formatted.measuresCount, 15);
  assert.equal(formatted.relationshipsCount, 8);
  assert.equal(formatted.tablesText, '11 Tables');
  assert.equal(formatted.measuresText, '15 Measures');
  assert.equal(formatted.relationshipsText, '8 Relationships');
});

test('Real fixture: instances_empty.json handles 0 instances correctly', () => {
  const data = loadFixture('instances_empty.json');
  assert.equal(data.total, 0);
  assert.equal(data.instances.length, 0);

  const state = getDesktopStepState({ loading: false, error: '', instances: data.instances });
  assert.equal(state, 'empty');
});

test('Real fixture: error_500.json handles backend failure cleanly without boundary crash', () => {
  const data = loadFixture('error_500.json');
  assert.match(data.detail, /Analysis Services engine discovery/);

  const state = getDesktopStepState({ loading: false, error: data.detail, instances: [] });
  assert.equal(state, 'error');
});

test('Real fixture: validate_preselection_valid.json matches active engine', () => {
  const data = loadFixture('validate_preselection_valid.json');
  assert.equal(data.valid, true);
  assert.equal(data.instance.port, 56888);
  assert.equal(data.instance.database_name, '3f81a039-98ba-4ff6-bfe3-82c5c5bb69db');
});

test('Real fixture: validate_preselection_invalid.json reports closed notice', () => {
  const data = loadFixture('validate_preselection_invalid.json');
  assert.equal(data.valid, false);
  assert.equal(data.reason, "That file isn't open anymore");
});

