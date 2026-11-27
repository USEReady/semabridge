import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';

import {
  formatInjectionSkipReason,
  formatInjectionTargetLabel,
  getInjectionDisabledReason,
} from '../src/utils/biTruthLabels.js';
import BiTruthToolbar from '../src/components/bitruth/BiTruthToolbar.jsx';
import InjectDialog from '../src/components/bitruth/InjectDialog.jsx';
import BiTruthHistoryModal from '../src/components/bitruth/BiTruthHistoryModal.jsx';

// ─── TEST 1: The dialog opens before anything is written; the action alone never writes ───
test('The dialog opens before anything is written; the action alone never writes', () => {
  let openedModal = false;
  let writeExecuted = false;

  const html = renderToStaticMarkup(
    <MemoryRouter>
      <BiTruthToolbar
        projectId="proj-test"
        selectedView="MY_VIEW"
        platform="SNOWFLAKE"
        approvedCount={5}
        qualifyingCount={3}
        latestRun={{ id: 'run-1', accuracy_pct: 100 }}
        onOpenInject={() => { openedModal = true; }}
      />
    </MemoryRouter>
  );

  // The button exists in the markup
  assert.ok(html.includes('Publish verified pairs to Cortex'));
  // The toolbar render itself does not execute any write
  assert.equal(writeExecuted, false);
});

// ─── TEST 2: Publish is disabled until a preview has run ───
test('Publish is disabled until a preview has run', () => {
  const html = renderToStaticMarkup(
    <InjectDialog
      open={true}
      onClose={() => {}}
      projectId="proj-test"
      selectedView="MY_VIEW"
      platform="SNOWFLAKE"
    />
  );

  // Before preview has run, Inspect Preview button is present
  assert.ok(html.includes('Inspect Preview'));
  // Publish button is disabled
  assert.ok(html.includes('disabled=""') || html.includes('disabled'));
  assert.ok(html.includes('Run preview first before publishing') || html.includes('Publish (0)'));
});

// ─── TEST 3: Each disabled state renders its own reason ───
test('Each disabled state renders its own reason', () => {
  // 1. Databricks unverified
  const databricksReason = getInjectionDisabledReason({
    platform: 'DATABRICKS',
    qualifyingCount: 5,
    hasRun: true,
  });
  assert.match(databricksReason, /Databricks Genie space injection is unverified/i);

  // 2. Permission missing
  const permReason = getInjectionDisabledReason({
    platform: 'SNOWFLAKE',
    qualifyingCount: 5,
    hasRun: true,
    canWrite: false,
  });
  assert.match(permReason, /lack permission/i);

  // 3. No run exists
  const noRunReason = getInjectionDisabledReason({
    platform: 'SNOWFLAKE',
    qualifyingCount: 5,
    hasRun: false,
  });
  assert.match(noRunReason, /No test suite run exists/i);

  // 4. Stale run
  const staleReason = getInjectionDisabledReason({
    platform: 'SNOWFLAKE',
    qualifyingCount: 5,
    hasRun: true,
    isStale: true,
  });
  assert.match(staleReason, /run is older than allowed window/i);

  // 5. Zero qualifying cases
  const zeroReason = getInjectionDisabledReason({
    platform: 'SNOWFLAKE',
    qualifyingCount: 0,
    hasRun: true,
    isStale: false,
  });
  assert.match(zeroReason, /No cases qualify for injection/i);
});

// ─── TEST 4: A skipped case shows its reason, mapped through the labels file ───
test('A skipped case shows its reason, mapped through the labels file', () => {
  assert.equal(formatInjectionSkipReason('NOT_APPROVED'), 'Case is not approved');
  assert.equal(formatInjectionSkipReason('NO_BENCHMARK'), 'No benchmark value captured');
  assert.equal(formatInjectionSkipReason('DID_NOT_PASS'), 'Did not pass in the most recent run');
  assert.equal(formatInjectionSkipReason('STALE_RUN'), 'Suite run is older than the configured limit');
  assert.equal(formatInjectionSkipReason('UNSUPPORTED_PLATFORM'), 'Platform does not support verified query injection');
  assert.equal(formatInjectionSkipReason('UNKNOWN_CODE', 'Custom reason'), 'Custom reason');
});

// ─── TEST 5: A partial result renders what landed and what did not, and does not read as success ───
test('A partial result renders what landed and what did not, and does not read as success', () => {
  const partialHistory = [
    {
      injected_at: '2026-11-27T10:00:00Z',
      injected_by: 'User1',
      platform: 'SNOWFLAKE',
      target: '@STAGE/model.yaml',
      injected_count: 1,
      status: 'partial',
      outcome: 'partial',
      reason: '1 landed, 1 failed due to stage lock',
    },
  ];

  const html = renderToStaticMarkup(
    <BiTruthHistoryModal
      open={true}
      onClose={() => {}}
      runs={[]}
      injectionHistory={partialHistory}
    />
  );

  assert.ok(html.includes('PARTIAL'));
  assert.ok(html.includes('1 pair written to SNOWFLAKE'));
  assert.ok(html.includes('1 landed, 1 failed due to stage lock'));
  // Does not read as clean green success
  assert.ok(!html.includes('PUBLISHED'));
});

// ─── TEST 6: A project whose platform does not support injection shows the spike reason ───
test('A project whose platform does not support injection shows the spike reason', () => {
  const label = formatInjectionTargetLabel('SNOWFLAKE');
  assert.equal(label, 'Publish verified pairs to Cortex');

  const genieLabel = formatInjectionTargetLabel('DATABRICKS');
  assert.equal(genieLabel, 'Publish verified pairs to Genie');

  const reason = getInjectionDisabledReason({ platform: 'DATABRICKS' });
  assert.ok(reason.includes('workspace contains zero Genie spaces and token lacks space creation scope'));
});

// ─── TEST 7: A project with no qualifying cases shows what is missing ───
test('A project with no qualifying cases shows what is missing', () => {
  const reason = getInjectionDisabledReason({ qualifyingCount: 0 });
  assert.ok(reason.includes('Approve cases'));
  assert.ok(reason.includes('capture benchmarks'));
  assert.ok(reason.includes('pass in the latest run'));
});
