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