import test from 'node:test';
import assert from 'node:assert/strict';

import { featureUnsupported, itemKey, measureFacts, sqlLabel, targetDisplayName, verifiedText } from './targetFeatures.js';
import { plannedDetailsBySourcePath } from './databricksPlan.js';

test('unknown features keep today\'s behaviour; only a loaded "false" turns a feature off', () => {
  assert.equal(featureUnsupported(null, 'live_query'), false);
  assert.equal(featureUnsupported(undefined, 'parity_check'), false);
  assert.equal(featureUnsupported({ live_query: true }, 'live_query'), false);
  assert.equal(featureUnsupported({ live_query: false }, 'live_query'), true);
  assert.equal(featureUnsupported({}, 'live_query'), false);
});

test('labels', () => {
  assert.equal(sqlLabel(null), 'Snowflake SQL');
  assert.equal(sqlLabel({ sql_label: 'Databricks SQL' }), 'Databricks SQL');
  assert.equal(sqlLabel({ sql_label: 'SQL' }), 'Snowflake SQL');
  assert.equal(targetDisplayName('databricks'), 'Databricks');
  assert.equal(targetDisplayName(''), 'this target');
});

test('item keys match the backend (case and spacing insensitive)', () => {
  assert.equal(itemKey('measure', '', '  Total   Sales '), 'measure::total sales');
  assert.equal(itemKey('field', 'Store', 'Store Name'), 'field:store:store name');
  const facts = { items: { 'measure::total sales': { name: 'Total Sales' } } };
  assert.equal(measureFacts(facts, 'TOTAL SALES').name, 'Total Sales');
  assert.equal(measureFacts(null, 'x'), null);
});

test('verified state text never claims verification it does not have', () => {
  assert.equal(verifiedText(null), 'not deployed');
  assert.equal(verifiedText({ status: 'verified', revision: 10 }), 'verified (revision 10)');
  assert.match(verifiedText({ status: 'unavailable', note: 'the bundle of revision 7 is no longer kept' }), /unavailable: the bundle/);
  assert.equal(verifiedText({ status: 'applied_unverified' }), 'applied, not verified');
});

test('planned details: final expression per view, approximation and left-out reason', () => {
  const plan = {
    views: [
      { name: 'sales_metrics',
        fields: [{ dataset: 'Store', column: 'Name', name: 'name', expr: '`store`.`name`' }],
        measures: [{ source_name: 'Total Sales', name: 'total_sales', expr: 'SUM(source.`amount`)', warnings: [] },
          { source_name: 'Sales LY', name: 'sales_ly', expr: 'SUM(x)', warnings: ['approximate: CALCULATE filters'] }] },
      { name: 'store_metrics', fields: [],
        measures: [{ source_name: 'Total Sales', name: 'total_sales', expr: 'SUM(sales.`amount`)' }] },
    ],
    left_out: [{ measure: 'Share', reason: 'conversion_failed', detail: 'needs window metric' }],
  };
  const d = plannedDetailsBySourcePath(plan);
  assert.deepEqual(d.get('metrics.Total Sales').placements.map((p) => [p.view, p.expr]),
    [['sales_metrics', 'SUM(source.`amount`)'], ['store_metrics', 'SUM(sales.`amount`)']]);
  assert.equal(d.get('metrics.Sales LY').placements[0].approximate, true);
  assert.equal(d.get('datasets.Store.columns.Name').placements[0].expr, '`store`.`name`');
  assert.deepEqual(d.get('metrics.Share'), { placements: [], leftOut: { reason: 'conversion_failed', detail: 'needs window metric' } });
  assert.equal(plannedDetailsBySourcePath(null).size, 0);
});
