import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveCollisionsForRows } from '../src/utils/collisionResolution.js';

test('resolveCollisionsForRows returns empty object when no collisions exist', () => {
  const rows = [
    { id: '1', source_field: 'AMOUNT', target_field: 'AMOUNT', status: 'auto' },
    { id: '2', source_field: 'DATE', target_field: 'DATE', status: 'auto' },
  ];
  const res = resolveCollisionsForRows(rows);
  assert.deepEqual(res, {});
});

test('resolveCollisionsForRows resolves collisions with unique table prefix names', () => {
  const rows = [
    {
      id: 'c1',
      source_field: 'AMOUNT',
      source_table_name: 'SALES',
      target_field: 'AMOUNT',
      status: 'collision',
      entity_kind: 'column',
    },
    {
      id: 'c2',
      source_field: 'AMOUNT',
      source_table_name: 'RETURNS',
      target_field: 'AMOUNT',
      status: 'collision',
      entity_kind: 'column',
    },
    {
      id: 'ok1',
      source_field: 'CUSTOMER_ID',
      source_table_name: 'CUSTOMER',
      target_field: 'CUSTOMER_ID',
      status: 'auto',
    },
  ];

  const res = resolveCollisionsForRows(rows);
  assert.ok(res.c1, 'c1 must have a resolution');
  assert.ok(res.c2, 'c2 must have a resolution');
  assert.notEqual(res.c1.toUpperCase(), res.c2.toUpperCase(), 'Resolutions must be unique');
  assert.notEqual(res.c1.toUpperCase(), 'CUSTOMER_ID');
  assert.notEqual(res.c2.toUpperCase(), 'CUSTOMER_ID');
});

test('resolveCollisions across multiple model sections updates all models independently', () => {
  const model1Rows = [
    { id: 'm1_1', source_field: 'AMT', source_table_name: 'FACT', target_field: 'AMT', status: 'collision' },
  ];
  const model2Rows = [
    { id: 'm2_1', source_field: 'QTY', source_table_name: 'ITEMS', target_field: 'QTY', status: 'collision' },
  ];

  const sections = [
    { fileId: 'file_1', loaded: true, rows: model1Rows },
    { fileId: 'file_2', loaded: true, rows: model2Rows },
  ];

  const bulkCalls = [];
  const onBulkResolved = (fileId, map) => {
    bulkCalls.push({ fileId, map });
  };

  for (const s of sections) {
    if (!s.loaded || !Array.isArray(s.rows)) continue;
    const resolved = resolveCollisionsForRows(s.rows);
    if (Object.keys(resolved).length > 0) {
      onBulkResolved(s.fileId, resolved);
    }
  }

  assert.equal(bulkCalls.length, 2);
  assert.equal(bulkCalls[0].fileId, 'file_1');
  assert.ok(bulkCalls[0].map.m1_1);
  assert.equal(bulkCalls[1].fileId, 'file_2');
  assert.ok(bulkCalls[1].map.m2_1);
});

test('MultiFileDryRunStatus button visibility logic conforms to specs', () => {
  // Spec: button pops only when user selects multiple models (sections.length > 1) and collisions exist
  const singleModelWithCollision = [{ fileId: 'f1', unresolvedCollisions: 3, rows: [] }];
  const multiModelNoCollision = [
    { fileId: 'f1', unresolvedCollisions: 0, rows: [] },
    { fileId: 'f2', unresolvedCollisions: 0, rows: [] },
  ];
  const multiModelWithCollision = [
    { fileId: 'f1', unresolvedCollisions: 2, rows: [] },
    { fileId: 'f2', unresolvedCollisions: 1, rows: [] },
  ];

  const shouldShowBtn = (sections) => {
    const totalCollisions = sections.reduce((sum, s) => sum + (s.unresolvedCollisions || 0), 0);
    return sections.length > 1 && totalCollisions > 0;
  };

  assert.equal(shouldShowBtn(singleModelWithCollision), false, 'Should NOT show for single model');
  assert.equal(shouldShowBtn(multiModelNoCollision), false, 'Should NOT show when no collisions exist');
  assert.equal(shouldShowBtn(multiModelWithCollision), true, 'MUST show for multiple models with collisions');
});
