import test from 'node:test';
import assert from 'node:assert/strict';

import { parseConfigYamlToInitialData } from '../src/utils/parseConfigYamlToInitialData.js';

test('preserves PBIX attachment metadata when hydrating edit data', () => {
  const yamlText = `
source:
  type: pbix
  pbix_path: C:/data/reports/sales.pbix
  pbix_folder: C:/data/reports
target:
  type: snowflake
`;

  const result = parseConfigYamlToInitialData(yamlText, {
    id: 42,
    source: { type: 'pbix' },
    pbix_file_path: 'C:/uploads/sales.pbix',
    target_type: 'snowflake',
  });

  assert.equal(result.source_type, 'pbix');
  assert.equal(result.pbix_path, 'C:/data/reports/sales.pbix');
  assert.equal(result.pbix_folder, 'C:/data/reports');
  assert.equal(result.pbix_uploaded_path, 'C:/uploads/sales.pbix');
});

test('preserves sync mode from project metadata when hydrating edit data', () => {
  const yamlText = `
source:
  type: fabric
target:
  type: snowflake
options:
  write_strategy: copy
`;

  const result = parseConfigYamlToInitialData(yamlText, {
    id: 42,
    source: { type: 'fabric' },
    target_type: 'snowflake',
    sync_mode: 'upsert',
  });

  assert.equal(result.sync_mode, 'upsert');
  assert.equal(result.write_strategy, 'upsert');
});

test('restores a multi-PBIX project file list (source.models paths) for edit mode', () => {
  const yamlText = `
source:
  type: pbix
  models:
    - "C:/up/aa_Sales.pbix"
    - "C:/up/bb_Cost.pbix"
targets:
  - type: snowflake
`;
  const result = parseConfigYamlToInitialData(yamlText, { id: 7, source: { type: 'pbix' }, target_type: 'snowflake' });
  assert.deepEqual(result.pbix_file_paths, ['C:/up/aa_Sales.pbix', 'C:/up/bb_Cost.pbix']);
  assert.equal(result.pbix_path, '');
});

test('single-PBIX project restores pbix_path and folder-tag ids, with an empty file list', () => {
  const yamlText = `
source:
  type: pbix
  pbix_path: "D:/tag/x.pbix"
  local_folder_id: "3"
  local_folder_tag: "finance"
targets:
  - type: snowflake
`;
  const result = parseConfigYamlToInitialData(yamlText, { id: 8, source: { type: 'pbix' }, target_type: 'snowflake' });
  assert.deepEqual(result.pbix_file_paths, []);
  assert.equal(result.pbix_path, 'D:/tag/x.pbix');
  assert.equal(result.local_folder_id, '3');
  assert.equal(result.local_folder_tag, 'finance');
});

test('Fabric model names are never treated as PBIX paths', () => {
  const yamlText = `
source:
  type: fabric
  models:
    - "Sales"
targets:
  - type: snowflake
`;
  const result = parseConfigYamlToInitialData(yamlText, { id: 9, source: { type: 'fabric' } });
  assert.deepEqual(result.pbix_file_paths, []);
  assert.deepEqual(result.models, ['Sales']);
});