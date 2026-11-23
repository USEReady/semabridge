import assert from 'node:assert/strict';
import { getViewBadgeLabel, normalizeViewName } from '../../utils/viewUtils.js';

// 1. normalizeViewName tests
assert.equal(normalizeViewName('"DB"."SCHEMA"."VIEW"'), 'db.schema.view');
assert.equal(normalizeViewName('  MY_VIEW  '), 'my_view');
assert.equal(normalizeViewName(''), '');

// 2. getViewBadgeLabel tests
const latestView = {
  view: 'VIEW_A',
  schema: 'HR',
  is_latest: true,
  managed: true,
};
assert.equal(getViewBadgeLabel(latestView, 'HR'), 'Latest');

const olderView = {
  view: 'VIEW_B',
  schema: 'HR',
  is_latest: false,
  managed: true,
};
assert.equal(getViewBadgeLabel(olderView, 'HR'), 'Older deploy');

const otherSchemaView = {
  view: 'VIEW_C',
  schema: 'FINANCE',
  is_latest: false,
  managed: true,
};
assert.equal(getViewBadgeLabel(otherSchemaView, 'HR'), 'Other schema');

const unmanagedView = {
  view: 'VIEW_UNMANAGED',
  schema: 'HR',
  is_latest: false,
  managed: false,
};
assert.equal(getViewBadgeLabel(unmanagedView, 'HR'), 'Not deployed by this project');

console.log('DeployedViewSelector unit tests PASSED!');
