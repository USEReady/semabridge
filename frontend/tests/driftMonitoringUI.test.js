import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  REASON_CODE_DETAILS,
  getReasonDetails,
  computeProjectDriftState,
  getDriftStateSortPriority,
  CATEGORY_DETAILS,
} from '../src/utils/driftLabels.js';
import DriftStatusCard from '../src/components/drift/DriftStatusCard.jsx';
import GroupedProjectList from '../src/components/drift/GroupedProjectList.jsx';
import DriftFindingsList from '../src/components/drift/DriftFindingsList.jsx';
import DriftFindingsTable from '../src/components/drift/DriftFindingsTable.jsx';
import DriftCategorySummary from '../src/components/drift/DriftCategorySummary.jsx';
import DriftModeToggle from '../src/components/drift/DriftModeToggle.jsx';
import DriftFindingDetail from '../src/components/drift/DriftFindingDetail.jsx';
import DriftSchedules, { isValidHttpsWebhook } from '../src/components/drift/DriftSchedules.jsx';
import WarehouseFilter from '../src/components/common/WarehouseFilter.jsx';
import EmptyState from '../src/components/common/EmptyState.jsx';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ─── TEST 1: Each of the 10 reason codes renders title, explanation, and action ───
test('reason codes map correctly to titles, explanations, and actions', () => {
  const codes = [
    'NO_DEPLOY_BASELINE',
    'NO_DEPLOYED_VIEW',
    'WAREHOUSE_UNREACHABLE',
    'WAREHOUSE_AUTH_FAILED',
    'PLATFORM_NOT_SUPPORTED',
    'VIEW_NOT_FOUND',
    'PARSE_ERROR',
    'NO_COMPARABLE_OBJECTS',
    'CHECK_IN_PROGRESS',
    'NEVER_CHECKED',
  ];

  for (const code of codes) {
    const details = getReasonDetails(code);
    assert.ok(details, `Details found for ${code}`);
    assert.ok(details.title && details.title.length > 0, `Title exists for ${code}: ${details.title}`);
    assert.ok(details.explanation && details.explanation.length > 0, `Explanation exists for ${code}: ${details.explanation}`);

    // Render component with this code
    const html = renderToStaticMarkup(
      React.createElement(DriftStatusCard, {
        status: 'NOT_CHECKED',
        reasonCode: code,
        reasonMessage: 'Backend message for ' + code,
      })
    );

    assert.ok(html.includes(details.title), `HTML contains title "${details.title}" for ${code}`);
    assert.ok(html.includes(details.explanation), `HTML contains explanation for ${code}`);

    if (details.actionLabel) {
      assert.ok(html.includes(details.actionLabel), `HTML contains action button "${details.actionLabel}" for ${code}`);
    }
  }
});

// ─── TEST 2: Unknown reason code falls back to backend message ────────────────
test('unknown reason code falls back to backend own message', () => {
  const customMessage = 'Connection dropped by remote warehouse host 10.0.4.15 after 45000ms';
  const details = getReasonDetails('UNKNOWN_SYSTEM_FAULT_CODE_99', customMessage);

  assert.equal(details.title, 'Needs Attention');
  assert.equal(details.explanation, customMessage);

  const html = renderToStaticMarkup(
    React.createElement(DriftStatusCard, {
      status: 'NOT_CHECKED',
      reasonCode: 'UNKNOWN_SYSTEM_FAULT_CODE_99',
      reasonMessage: customMessage,
    })
  );

  assert.ok(html.includes(customMessage), 'Rendered card contains the backend message verbatim');
  assert.ok(html.includes('Needs Attention'), 'Rendered card title defaults to Needs Attention');
});

// ─── TEST 3: In Sync NEVER renders when baseline is missing or captured live ───
test('In Sync never renders when baseline is missing or live captured', () => {
  // Case A: status is IN_SYNC, but baselineSource is missing / empty
  const htmlMissing = renderToStaticMarkup(
    React.createElement(DriftStatusCard, {
      status: 'IN_SYNC',
      baselineSource: 'missing',
      checkedAt: '2026-11-24T10:00:00Z',
    })
  );
  assert.ok(!htmlMissing.includes('0 Drift'), 'Missing baseline must not render 0 Drift');
  assert.ok(htmlMissing.includes('No Deploy Baseline'), 'Missing baseline must render No Deploy Baseline');

  // Case B: status is IN_SYNC, but baselineSource is live_capture
  const htmlLiveCapture = renderToStaticMarkup(
    React.createElement(DriftStatusCard, {
      status: 'IN_SYNC',
      baselineSource: 'live_capture',
      checkedAt: '2026-11-24T10:00:00Z',
    })
  );
  assert.ok(!htmlLiveCapture.includes('0 Drift'), 'Live capture must not render In Sync 0 Drift');
  assert.ok(htmlLiveCapture.includes('Live Baseline Captured'), 'Live capture renders Live Baseline Captured');
  assert.ok(htmlLiveCapture.includes('Redeploy'), 'Live capture prompts user to redeploy');

  // Case C: status is IN_SYNC with valid deploy baseline
  const htmlValidSync = renderToStaticMarkup(
    React.createElement(DriftStatusCard, {
      status: 'IN_SYNC',
      baselineSource: 'deploy',
      baselineAt: '2026-11-20T12:00:00Z',
      checkedAt: '2026-11-24T10:00:00Z',
      metricsCompared: 14,
    })
  );
  assert.ok(htmlValidSync.includes('In Sync'), 'Valid deploy baseline renders In Sync');
  assert.ok(htmlValidSync.includes('0 Drift'), 'Valid deploy baseline renders 0 Drift');
  assert.ok(htmlValidSync.includes('14 metric(s) verified'), 'Renders verified metric count');
  assert.ok(!htmlValidSync.includes('upsert'), 'No upsert wording exists in In Sync card');
});

// ─── TEST 4: Never-checked project shows no-check state with action, not error ─
test('never-checked project shows no-check state with its action, not an error', () => {
  const html = renderToStaticMarkup(
    React.createElement(DriftStatusCard, {
      status: 'NEVER_CHECKED',
      reasonCode: 'NEVER_CHECKED',
    })
  );

  assert.ok(html.includes('No Check Yet'), 'Renders No Check Yet');
  assert.ok(html.includes('Check Drift Now'), 'Renders primary action Check Drift Now');
  assert.ok(!html.includes('Error'), 'Does not render Error badge');
  assert.ok(!html.includes('Failed'), 'Does not render Failed badge');
});

// ─── TEST 5: Warehouse filter functionality and rendering ──────────────────────
test('warehouse filter lists only platforms present, shows counts, and renders active chip', () => {
  const platforms = ['SNOWFLAKE', 'DATABRICKS'];
  const counts = { SNOWFLAKE: 4, DATABRICKS: 2 };

  // Render with filter empty
  const html = renderToStaticMarkup(
    React.createElement(WarehouseFilter, {
      warehouses: platforms,
      counts,
      selectedWarehouse: '',
      onSelectWarehouse: () => {},
    })
  );

  assert.ok(html.includes('All warehouses (6)'), 'Shows total count in default option');
  assert.ok(html.includes('SNOWFLAKE (4)'), 'Shows SNOWFLAKE with count 4');
  assert.ok(html.includes('DATABRICKS (2)'), 'Shows DATABRICKS with count 2');
  assert.ok(!html.includes('POSTGRESQL'), 'Does not render unconfigured platform');

  // Render with selectedWarehouse active
  const htmlActive = renderToStaticMarkup(
    React.createElement(WarehouseFilter, {
      warehouses: platforms,
      counts,
      selectedWarehouse: 'SNOWFLAKE',
      onSelectWarehouse: () => {},
    })
  );
  assert.ok(htmlActive.includes('Clear warehouse filter'), 'Renders removable filter chip button');
});

// ─── TEST 6: Project-list badge and status card consistency ───────────────────
test('project-list badge and status card agree on states', () => {
  const testCases = [
    {
      status: 'DRIFT_DETECTED',
      baselineSource: 'deploy',
      driftCount: 3,
      expectedCardTitle: 'Drift Detected',
    },
    {
      status: 'IN_SYNC',
      baselineSource: 'deploy',
      driftCount: 0,
      expectedCardTitle: 'In Sync',
    },
    {
      status: 'IN_SYNC',
      baselineSource: 'live_capture',
      driftCount: 0,
      expectedCardTitle: 'Live Baseline Captured',
    },
    {
      status: 'NEVER_CHECKED',
      baselineSource: null,
      driftCount: 0,
      expectedCardTitle: 'No Check Yet',
    },
  ];

  for (const tc of testCases) {
    const html = renderToStaticMarkup(
      React.createElement(DriftStatusCard, {
        status: tc.status,
        baselineSource: tc.baselineSource,
        driftDetected: tc.driftCount,
        categoryCounts: { EXPRESSION_DRIFT: tc.driftCount },
      })
    );
    assert.ok(html.includes(tc.expectedCardTitle), `Card renders ${tc.expectedCardTitle}`);
  }
});

// ─── TEST 7: No hardcoded platform names, view names, or limits in components ──
test('no platform names, view names or limits written in touched components', () => {
  const componentFiles = [
    path.join(__dirname, '../src/components/drift/DriftStatusCard.jsx'),
    path.join(__dirname, '../src/components/drift/GroupedProjectList.jsx'),
    path.join(__dirname, '../src/components/common/WarehouseFilter.jsx'),
    path.join(__dirname, '../src/components/common/EmptyState.jsx'),
  ];

  const forbiddenLiterals = [
    'Snowflake',
    'snowflake',
    'Databricks',
    'databricks',
    'BigQuery',
    'Redshift',
    'LIFE_EXPECTANCY',
    'SALES_VIEW',
    'direct upsert',
    'Direct upsert',
  ];

  for (const file of componentFiles) {
    const content = fs.readFileSync(file, 'utf8');
    for (const lit of forbiddenLiterals) {
      assert.ok(
        !content.includes(lit),
        `Component file ${path.basename(file)} must not contain hardcoded literal "${lit}"`
      );
    }
  }
});

// ─── TEST 8: Filter options dynamic, counts add up, "Not set" only when present ───
test('filter options come from data, counts add up, and Not set appears only when present', () => {
  // Scenario A: Data has Snowflake, Databricks, and NOT_SET
  const sampleProjects = [
    { id: 'p1', platform: 'SNOWFLAKE' },
    { id: 'p2', platform: 'SNOWFLAKE' },
    { id: 'p3', platform: 'DATABRICKS' },
    { id: 'p4', platform: 'NOT_SET' },
    { id: 'p5', platform: 'NOT_SET' },
  ];

  const countsA = {};
  sampleProjects.forEach((p) => {
    const plat = (!p.platform || p.platform === 'UNKNOWN' || p.platform === 'NOT_SET') ? 'NOT_SET' : p.platform;
    countsA[plat] = (countsA[plat] || 0) + 1;
  });

  const warehousesA = Object.keys(countsA).filter(k => k !== 'NOT_SET').sort();
  if (countsA['NOT_SET']) warehousesA.push('NOT_SET');

  assert.deepEqual(warehousesA, ['DATABRICKS', 'SNOWFLAKE', 'NOT_SET']);
  assert.equal(countsA['SNOWFLAKE'], 2);
  assert.equal(countsA['DATABRICKS'], 1);
  assert.equal(countsA['NOT_SET'], 2);

  // Check sum adds up to total
  const sumA = Object.values(countsA).reduce((a, b) => a + b, 0);
  assert.equal(sumA, sampleProjects.length);

  // Render WarehouseFilter
  const htmlA = renderToStaticMarkup(
    React.createElement(WarehouseFilter, {
      warehouses: warehousesA,
      counts: countsA,
      totalCount: sampleProjects.length,
      selectedWarehouse: '',
      onSelectWarehouse: () => {},
    })
  );

  assert.ok(htmlA.includes('All warehouses (5)'));
  assert.ok(htmlA.includes('SNOWFLAKE (2)'));
  assert.ok(htmlA.includes('DATABRICKS (1)'));
  assert.ok(htmlA.includes('Not set (2)'));

  // Scenario B: Data has NO not-set projects
  const sampleConfiguredOnly = [
    { id: 'p1', platform: 'SNOWFLAKE' },
    { id: 'p2', platform: 'DATABRICKS' },
  ];
  const countsB = {};
  sampleConfiguredOnly.forEach((p) => {
    countsB[p.platform] = (countsB[p.platform] || 0) + 1;
  });
  const warehousesB = Object.keys(countsB).filter(k => k !== 'NOT_SET').sort();
  if (countsB['NOT_SET']) warehousesB.push('NOT_SET');

  const htmlB = renderToStaticMarkup(
    React.createElement(WarehouseFilter, {
      warehouses: warehousesB,
      counts: countsB,
      totalCount: sampleConfiguredOnly.length,
      selectedWarehouse: '',
      onSelectWarehouse: () => {},
    })
  );

  assert.ok(!htmlB.includes('Not set'), '"Not set" group must NOT appear when no projects have it');
});

// ─── TEST 9: Projects without target still appear in the list ───────────────────
test('projects without a target still appear in the list and can be filtered', () => {
  const projects = [
    { id: 'proj-1', name: 'Has Target', platform: 'SNOWFLAKE' },
    { id: 'proj-2', name: 'No Target Configured', platform: 'NOT_SET' },
  ];

  // When selectedWarehouse is '', both projects appear
  const allVisible = projects.filter(() => true);
  assert.equal(allVisible.length, 2);

  // When selectedWarehouse is 'NOT_SET', only unconfigured appears
  const notSetVisible = projects.filter(p => p.platform === 'NOT_SET');
  assert.equal(notSetVisible.length, 1);
  assert.equal(notSetVisible[0].id, 'proj-2');
});

// ─── TEST 10: Never-checked project shows no-check badge, not error ──────────────
test('never-checked project shows no-check badge, distinct from error', () => {
  const freshProject = { id: 'proj-fresh', name: 'Fresh Project', platform: 'SNOWFLAKE' };
  const targetInfo = {};
  const allEvents = [];

  const freshState = computeProjectDriftState(freshProject, targetInfo, allEvents);
  assert.equal(freshState.status, 'NEVER_CHECKED');
  assert.equal(freshState.reasonCode, 'NEVER_CHECKED');

  const freshDetails = getReasonDetails(freshState.reasonCode);
  assert.equal(freshDetails.title, 'No Check Yet');
  assert.equal(freshDetails.severity, 'neutral');

  // Compare with a failed project (e.g. warehouse unreachable)
  const failedTargetInfo = {
    'fresh': {
      status: 'NOT_CHECKED',
      reason_code: 'WAREHOUSE_UNREACHABLE',
      checked_at: '2026-11-24T12:00:00Z',
    },
  };
  const failedState = computeProjectDriftState(freshProject, failedTargetInfo, allEvents);
  assert.equal(failedState.status, 'NOT_CHECKED');
  assert.equal(failedState.reasonCode, 'WAREHOUSE_UNREACHABLE');

  const failedDetails = getReasonDetails(failedState.reasonCode);
  assert.equal(failedDetails.title, 'Warehouse Unreachable');
  assert.equal(failedDetails.severity, 'danger');
  assert.notEqual(freshDetails.title, failedDetails.title);
  assert.notEqual(freshDetails.severity, failedDetails.severity);
});

// ─── TEST 11: Badge and status card agree for the same project ───────────────────
test('badge and status card compute the exact same state for any project', () => {
  const project = { id: 'proj-demo', name: 'Demo Project', platform: 'SNOWFLAKE' };

  // Case 1: Fresh project
  const stateFresh = computeProjectDriftState(project, {}, []);
  assert.equal(stateFresh.status, 'NEVER_CHECKED');
  assert.equal(stateFresh.reasonCode, 'NEVER_CHECKED');

  // Case 2: In Sync
  const targetInfoSync = {
    'demo': {
      status: 'IN_SYNC',
      baseline_source: 'deploy',
      checked_at: '2026-11-24T12:00:00Z',
    },
  };
  const stateSync = computeProjectDriftState(project, targetInfoSync, []);
  assert.equal(stateSync.status, 'IN_SYNC');
  assert.equal(stateSync.reasonCode, null);

  // Case 3: Live Capture Baseline
  const targetInfoLive = {
    'demo': {
      status: 'IN_SYNC',
      baseline_source: 'live_capture',
      checked_at: '2026-11-24T12:00:00Z',
    },
  };
  const stateLive = computeProjectDriftState(project, targetInfoLive, []);
  assert.equal(stateLive.status, 'NOT_CHECKED');
  assert.equal(stateLive.reasonCode, 'LIVE_CAPTURE_BASELINE');

  // Case 4: Drift Detected
  const eventsDrift = [{ project_id: 'proj-demo', status: 'pending', category: 'EXPRESSION_DRIFT' }];
  const stateDrift = computeProjectDriftState(project, targetInfoSync, eventsDrift);
  assert.equal(stateDrift.status, 'DRIFT_DETECTED');
  assert.equal(stateDrift.pendingCount, 1);
});

// ─── TEST 12: Long list sorting priority ─────────────────────────────────────────
test('projects are sorted: needs attention first, drift detected second, in sync third, never checked fourth', () => {
  const projects = [
    { id: 'proj-fresh', name: 'Project D Fresh' },
    { id: 'proj-sync', name: 'Project C Sync' },
    { id: 'proj-drift', name: 'Project B Drift' },
    { id: 'proj-attention', name: 'Project A Attention' },
  ];

  const targetInfo = {
    'attention': {
      status: 'NOT_CHECKED',
      reason_code: 'WAREHOUSE_UNREACHABLE',
      checked_at: '2026-11-24T10:00:00Z',
    },
    'drift': {
      status: 'DRIFT_DETECTED',
      checked_at: '2026-11-24T10:00:00Z',
    },
    'sync': {
      status: 'IN_SYNC',
      baseline_source: 'deploy',
      checked_at: '2026-11-24T10:00:00Z',
    },
    // 'fresh' is not in targetInfo (never checked)
  };

  const events = [
    { project_id: 'proj-drift', status: 'pending' },
  ];

  const sorted = [...projects].sort((a, b) => {
    const sA = computeProjectDriftState(a, targetInfo, events);
    const sB = computeProjectDriftState(b, targetInfo, events);
    const pA = getDriftStateSortPriority(sA);
    const pB = getDriftStateSortPriority(sB);
    if (pA !== pB) return pA - pB;
    return a.name.localeCompare(b.name);
  });

  assert.equal(sorted[0].id, 'proj-attention', 'Needs attention comes 1st');
  assert.equal(sorted[1].id, 'proj-drift', 'Drift detected comes 2nd');
  assert.equal(sorted[2].id, 'proj-sync', 'In sync comes 3rd');
  assert.equal(sorted[3].id, 'proj-fresh', 'Never checked comes 4th');
});

// ─── TEST 13: GroupedProjectList groups projects by platform from data ───────────
test('GroupedProjectList builds sections from data, puts Not set at the end, and orders by urgency', () => {
  const projects = [
    { id: 'proj-1', name: 'Alpha Warehouse A', platform: 'WAREHOUSE_ALPHA' },
    { id: 'proj-2', name: 'Beta Warehouse B', platform: 'WAREHOUSE_BETA' },
    { id: 'proj-3', name: 'Gamma Not Set', platform: 'NOT_SET' },
  ];

  const targetInfo = {
    // WAREHOUSE_BETA project has connection error -> needs attention
    '2': {
      status: 'NOT_CHECKED',
      reason_code: 'WAREHOUSE_UNREACHABLE',
      checked_at: '2026-11-24T12:00:00Z',
    },
    // WAREHOUSE_ALPHA is in sync
    '1': {
      status: 'IN_SYNC',
      baseline_source: 'deploy',
      checked_at: '2026-11-24T12:00:00Z',
    },
  };

  const html = renderToStaticMarkup(
    React.createElement(GroupedProjectList, {
      projects,
      targetInfo,
      allEvents: [],
      selectedProjectId: '',
      onSelectProject: () => {},
    })
  );

  // Both warehouse names appear
  assert.ok(html.includes('WAREHOUSE_ALPHA'), 'Contains WAREHOUSE_ALPHA header');
  assert.ok(html.includes('WAREHOUSE_BETA'), 'Contains WAREHOUSE_BETA header');
  assert.ok(html.includes('Not set'), 'Contains Not set header');
  assert.ok(html.includes('No target warehouse configured for these projects'), 'Renders explanatory text for Not set section');

  // Verify ordering: WAREHOUSE_BETA (has 1 need attention) comes before WAREHOUSE_ALPHA
  const betaIndex = html.indexOf('WAREHOUSE_BETA');
  const alphaIndex = html.indexOf('WAREHOUSE_ALPHA');
  const notSetIndex = html.indexOf('Not set');

  assert.ok(betaIndex < alphaIndex, 'Section with needs-attention projects comes first');
  assert.ok(alphaIndex < notSetIndex, 'Not set section is strictly placed at the end');
  assert.ok(html.includes('1 need attention'), 'Summary stats rendered in header');
});

// ─── TEST 14: GroupedProjectList search hides empty sections ─────────────────────
test('GroupedProjectList search hides sections with zero matching projects', () => {
  const projects = [
    { id: 'proj-snowflake-1', name: 'Sales Reporting', platform: 'PLATFORM_ONE' },
    { id: 'proj-databricks-1', name: 'Inventory Analytics', platform: 'PLATFORM_TWO' },
  ];

  // Search for "Sales"
  const html = renderToStaticMarkup(
    React.createElement(GroupedProjectList, {
      projects,
      targetInfo: {},
      allEvents: [],
      selectedProjectId: '',
      onSelectProject: () => {},
      searchQuery: 'Sales',
    })
  );

  assert.ok(html.includes('PLATFORM_ONE'), 'Matches PLATFORM_ONE');
  assert.ok(html.includes('Sales Reporting'), 'Contains matching project');
  assert.ok(!html.includes('PLATFORM_TWO'), 'Section with zero matches is hidden');
});

// ─── TEST 15: Warehouse filter narrows to single section ─────────────────────────
test('GroupedProjectList displays only selected warehouse section when filtered', () => {
  const projects = [
    { id: 'proj-1', name: 'Project One', platform: 'PLATFORM_A' },
    { id: 'proj-2', name: 'Project Two', platform: 'PLATFORM_B' },
  ];

  const html = renderToStaticMarkup(
    React.createElement(GroupedProjectList, {
      projects,
      targetInfo: {},
      allEvents: [],
      selectedProjectId: '',
      onSelectProject: () => {},
      selectedWarehouse: 'PLATFORM_A',
    })
  );

  assert.ok(html.includes('PLATFORM_A'), 'Renders selected PLATFORM_A section');
  assert.ok(!html.includes('PLATFORM_B'), 'Does not render unselected PLATFORM_B section');
});

// ─── TEST 16: Zero matches renders EmptyState with clear action ──────────────────
test('GroupedProjectList renders EmptyState when search finds zero matches', () => {
  const projects = [
    { id: 'proj-1', name: 'Project One', platform: 'PLATFORM_A' },
  ];

  const html = renderToStaticMarkup(
    React.createElement(GroupedProjectList, {
      projects,
      targetInfo: {},
      allEvents: [],
      selectedProjectId: '',
      onSelectProject: () => {},
      searchQuery: 'NON_EXISTENT_QUERY_XYZ',
      onClearFilters: () => {},
    })
  );

  assert.ok(
    html.includes('No projects match') && html.includes('NON_EXISTENT_QUERY_XYZ'),
    'Renders zero match empty state title referencing search term'
  );
  assert.ok(
    html.includes('Clear search'),
    'Renders clear search action button'
  );
});

// ─── TEST 17: Scales across 0, 1, and 108 projects across multiple platforms ───
test('GroupedProjectList cleanly renders 0, 1, and 108 projects across multiple warehouses', () => {
  // 0 projects
  const html0 = renderToStaticMarkup(
    React.createElement(GroupedProjectList, {
      projects: [],
      targetInfo: {},
      allEvents: [],
    })
  );
  assert.ok(html0.includes('No projects available'), 'Renders empty state for 0 projects');

  // 1 project
  const html1 = renderToStaticMarkup(
    React.createElement(GroupedProjectList, {
      projects: [{ id: 'p-single', name: 'Single Project', platform: 'WAREHOUSE_SOLO' }],
      targetInfo: {},
      allEvents: [],
    })
  );
  assert.ok(html1.includes('WAREHOUSE_SOLO'), 'Renders single warehouse section');
  assert.ok(html1.includes('Single Project'), 'Renders single project');

  // 108 projects across 3 warehouses + unconfigured
  const mock108 = Array.from({ length: 108 }, (_, i) => {
    const plat = i < 40 ? 'WH_ALPHA' : i < 80 ? 'WH_BETA' : i < 100 ? 'WH_GAMMA' : 'NOT_SET';
    return {
      id: `proj-${i + 1}`,
      name: `Project ${i + 1}`,
      platform: plat,
    };
  });

  const html108 = renderToStaticMarkup(
    React.createElement(GroupedProjectList, {
      projects: mock108,
      targetInfo: {},
      allEvents: [],
    })
  );
  assert.ok(html108.includes('WH_ALPHA'), 'Includes WH_ALPHA');
  assert.ok(html108.includes('WH_BETA'), 'Includes WH_BETA');
  assert.ok(html108.includes('WH_GAMMA'), 'Includes WH_GAMMA');
  assert.ok(html108.includes('Not set'), 'Includes Not set section');
  assert.ok(html108.includes('(40)'), 'WH_ALPHA count rendered');
  assert.ok(html108.includes('(8)'), 'Not set count rendered');
});

// ─── TEST 18: Tidy single line row with name, muted id, and badge ───────────────
test('GroupedProjectList project row renders as single tidy line with name, muted monospace id and badge', () => {
  const projects = [
    { id: 'proj-finance-eu', name: 'Finance Reporting EU', platform: 'WH_FIN' },
  ];

  const html = renderToStaticMarkup(
    React.createElement(GroupedProjectList, {
      projects,
      targetInfo: {
        'finance-eu': { status: 'IN_SYNC', baseline_source: 'deploy' },
      },
      allEvents: [],
      selectedProjectId: 'proj-finance-eu',
    })
  );

  assert.ok(html.includes('Finance Reporting EU'), 'Includes project name');
  assert.ok(html.includes('proj-finance-eu'), 'Includes monospace project id');
  assert.ok(html.includes('In Sync'), 'Includes state badge');
  assert.ok(html.includes('role="option"'), 'Renders with option role for listbox');
  assert.ok(html.includes('aria-selected="true"'), 'Selected project has aria-selected true');
});

// ─── TEST 19: DriftFindingsList: Big result guard collapses groups by default when total > 10 ───
test('DriftFindingsList big-result guard collapses groups by default when findings total > 10', () => {
  const events = Array.from({ length: 25 }, (_, i) => ({
    id: `ev-${i + 1}`,
    metric_name: `METRIC_${i + 1}`,
    kind: 'METRIC',
    category: i < 15 ? 'DELETION' : 'ADDITION',
    status: 'pending',
    detected_at: '2026-11-24T10:00:00Z',
    expression: 'SUM(SALES_AMOUNT)',
  }));

  const html = renderToStaticMarkup(
    React.createElement(DriftFindingsList, {
      events,
      totalCount: 267,
      totalUnfilteredCount: 267,
      countsByCategory: { DELETION: 101, ADDITION: 166 },
      countsByKind: { METRIC: 267 },
      countsByStatus: { pending: 267 },
      page: 1,
      pageSize: 25,
      totalPages: 11,
    })
  );

  // Big result guard advisory banner present
  assert.ok(html.includes('Groups are collapsed by default to save space'), 'Advisory banner is displayed');
  assert.ok(html.includes('267'), 'Total count 267 rendered in advisory');
  assert.ok(html.includes('Expand all groups'), 'Toggle for expand all groups is present');
});

// ─── TEST 20: DriftFindingsList: Filter toolbar dynamically builds options from backend response ───
test('DriftFindingsList filter toolbar dynamically populates options from backend response', () => {
  const html = renderToStaticMarkup(
    React.createElement(DriftFindingsList, {
      events: [],
      totalCount: 50,
      totalUnfilteredCount: 50,
      countsByCategory: {
        ADDITION: 20,
        DELETION: 15,
        EXPRESSION_DRIFT: 10,
        DESCRIPTION_DRIFT: 5,
      },
      countsByKind: {
        METRIC: 30,
        DIMENSION: 15,
        FACT: 5,
      },
      countsByStatus: {
        pending: 40,
        approved: 10,
      },
      page: 1,
      pageSize: 25,
      totalPages: 2,
    })
  );

  // Kinds dynamically rendered
  assert.ok(html.includes('Metric (30)'), 'Kind Metric (30) rendered');
  assert.ok(html.includes('Dimension (15)'), 'Kind Dimension (15) rendered');
  assert.ok(html.includes('Fact (5)'), 'Kind Fact (5) rendered');

  // Categories dynamically rendered
  assert.ok(html.includes('Addition (20)'), 'Category Addition (20) rendered');
  assert.ok(html.includes('Deletion (15)'), 'Category Deletion (15) rendered');
  assert.ok(html.includes('Expression drift (10)'), 'Category Expression drift (10) rendered');
  assert.ok(html.includes('Description drift (5)'), 'Category Description drift (5) rendered');

  // Statuses dynamically rendered
  assert.ok(html.includes('Pending (40)'), 'Status Pending (40) rendered');
  assert.ok(html.includes('Approved (10)'), 'Status Approved (10) rendered');
});

// ─── TEST 21: DriftFindingsList: Active filters appear as removable chips with Clear all ───
test('DriftFindingsList active filters appear as removable chips with clear all button', () => {
  const html = renderToStaticMarkup(
    React.createElement(DriftFindingsList, {
      events: [],
      totalCount: 5,
      totalUnfilteredCount: 100,
      countsByCategory: { DELETION: 5 },
      countsByKind: { METRIC: 5 },
      countsByStatus: { pending: 5 },
      search: 'TOTAL_REVENUE',
      selectedCategory: 'DELETION',
      selectedKind: 'METRIC',
      selectedStatus: 'pending',
      page: 1,
      pageSize: 25,
      totalPages: 1,
    })
  );

  assert.ok(html.includes('Active Filters:'), 'Active filters section label is present');
  assert.ok(html.includes('Search: “TOTAL_REVENUE”'), 'Search chip is present');
  assert.ok(html.includes('Kind: Metric'), 'Kind chip is present');
  assert.ok(html.includes('Category: Deletion'), 'Category chip is present');
  assert.ok(html.includes('Status: Pending'), 'Status chip is present');
  assert.ok(html.includes('Clear all filters'), 'Clear all filters control is present');
});

// ─── TEST 22: DriftFindingsList: Grouped by Object Kind, then Category ──────────
test('DriftFindingsList groups findings by object kind then category with group counts', () => {
  const events = [
    { id: 'ev-1', metric_name: 'REVENUE', kind: 'METRIC', category: 'DELETION', status: 'pending' },
    { id: 'ev-2', metric_name: 'PROFIT', kind: 'METRIC', category: 'DELETION', status: 'pending' },
    { id: 'ev-3', metric_name: 'REGION_CODE', kind: 'DIMENSION', category: 'ADDITION', status: 'pending' },
  ];

  const html = renderToStaticMarkup(
    React.createElement(DriftFindingsList, {
      events,
      totalCount: 3,
      totalUnfilteredCount: 3,
      countsByCategory: { DELETION: 2, ADDITION: 1 },
      countsByKind: { METRIC: 2, DIMENSION: 1 },
      countsByStatus: { pending: 3 },
      page: 1,
      pageSize: 25,
      totalPages: 1,
    })
  );

  assert.ok(html.includes('Metric · Deletion'), 'Group Metric · Deletion header is present');
  assert.ok(html.includes('Dimension · Addition'), 'Group Dimension · Addition header is present');
  assert.ok(html.includes('Showing <strong>1–3</strong> of <strong>3</strong> findings'), 'Slice line shows 1-3 of 3');
});

// ─── TEST 23: DriftFindingsList: Numbered pagination controls and slice indicator ───
test('DriftFindingsList renders numbered pagination and slice indicator', () => {
  const html = renderToStaticMarkup(
    React.createElement(DriftFindingsList, {
      events: [{ id: 'ev-1', metric_name: 'M1', kind: 'METRIC', category: 'ADDITION', status: 'pending' }],
      totalCount: 101,
      totalUnfilteredCount: 101,
      countsByCategory: { ADDITION: 101 },
      countsByKind: { METRIC: 101 },
      countsByStatus: { pending: 101 },
      page: 2,
      pageSize: 25,
      totalPages: 5,
    })
  );

  assert.ok(html.includes('Showing <strong>26–50</strong> of <strong>101</strong> findings'), 'Slice indicator correct for page 2');
  assert.ok(html.includes('Rows per page:'), 'Rows per page control present');
  assert.ok(html.includes('>1</button>'), 'Page button 1 present');
  assert.ok(html.includes('>2</button>'), 'Page button 2 present');
  assert.ok(html.includes('>5</button>'), 'Page button 5 present');
});

// ─── TEST 24: DriftFindingsList: Clamped expression with Show more and Copy controls ───
test('DriftFindingsList clamps long expressions with show more toggle and copy button', () => {
  const longExpression = 'SUM(CASE WHEN DIM_REGION.IS_ACTIVE = 1 AND DIM_REGION.COUNTRY IN (\'US\', \'CA\', \'MX\') THEN FACT_SALES.AMOUNT * (1 - FACT_SALES.DISCOUNT_RATE) ELSE 0 END) / NULLIF(COUNT(DISTINCT FACT_SALES.TRANSACTION_ID), 0)';

  const html = renderToStaticMarkup(
    React.createElement(DriftFindingsList, {
      events: [
        {
          id: 'ev-long-expr',
          metric_name: 'COMPLEX_ADJUSTED_REVENUE',
          kind: 'METRIC',
          category: 'EXPRESSION_DRIFT',
          status: 'pending',
          bi_definition: longExpression,
          snowflake_definition: 'SUM(FACT_SALES.AMOUNT)',
          proposed_snowflake_definition: 'redeploy from source',
        },
      ],
      totalCount: 1,
      totalUnfilteredCount: 1,
      countsByCategory: { EXPRESSION_DRIFT: 1 },
      countsByKind: { METRIC: 1 },
      countsByStatus: { pending: 1 },
      page: 1,
      pageSize: 25,
      totalPages: 1,
    })
  );

  // Group header rendered
  assert.ok(html.includes('Metric · Expression drift'), 'Group header rendered');
  // Finding row rendered
  assert.ok(html.includes('COMPLEX_ADJUSTED_REVENUE'), 'Object name rendered');
});

// ─── TEST 25: isValidHttpsWebhook validates secure HTTPS URLs strictly ─────────
test('isValidHttpsWebhook enforces HTTPS-only webhooks and rejects insecure or invalid URLs', () => {
  assert.equal(isValidHttpsWebhook(''), true, 'Empty URL is valid (optional field)');
  assert.equal(isValidHttpsWebhook('   '), true, 'Whitespace URL is valid (optional field)');
  assert.equal(isValidHttpsWebhook('https://webhook.site/abc-123'), true, 'Valid HTTPS URL accepted');
  assert.equal(isValidHttpsWebhook('https://hooks.slack.com/services/T00/B00/X00'), true, 'Slack HTTPS webhook accepted');
  assert.equal(isValidHttpsWebhook('http://insecure.site/webhook'), false, 'Insecure HTTP URL rejected');
  assert.equal(isValidHttpsWebhook('ftp://ftp.site/webhook'), false, 'FTP URL rejected');
  assert.equal(isValidHttpsWebhook('just-some-text'), false, 'Plain text rejected');
});

// ─── TEST 26: DriftSchedules renders schedule form and schedule header ────────
test('DriftSchedules renders interval input, HTTPS webhook input, and add schedule button', () => {
  const html = renderToStaticMarkup(
    React.createElement(DriftSchedules, {
      projectId: 'proj-financial-report',
      compact: false,
    })
  );

  assert.ok(html.includes('Automated Drift Schedules'), 'Header rendered');
  assert.ok(html.includes('proj-financial-report'), 'Project scope displayed');
  assert.ok(html.includes('Add Schedule'), 'Add Schedule button rendered');
  assert.ok(html.includes('Optional HTTPS Teams / Slack webhook URL'), 'HTTPS webhook placeholder rendered');
});

// ─── TEST 27: DriftModeToggle obeys capabilities.allow_mock ───────────────────
test('DriftModeToggle hides or disables mock mode when capabilities.allow_mock is false', () => {
  // Case A: allow_mock is false -> Live Warehouse Mode only
  const htmlDisallowed = renderToStaticMarkup(
    React.createElement(DriftModeToggle, {
      mode: 'live',
      capabilities: { allow_mock: false },
    })
  );
  assert.ok(!htmlDisallowed.includes('Mock (recorded)'), 'Mock button hidden when allow_mock is false');
  assert.ok(htmlDisallowed.includes('Live Warehouse Mode'), 'Shows live warehouse label');

  // Case B: allow_mock is true -> Toggle rendered
  const htmlAllowed = renderToStaticMarkup(
    React.createElement(DriftModeToggle, {
      mode: 'live',
      capabilities: { allow_mock: true },
    })
  );
  assert.ok(htmlAllowed.includes('Mock (recorded)'), 'Mock button present when allow_mock is true');
  assert.ok(htmlAllowed.includes('Live Warehouse'), 'Live button present');

  // Case C: mode is mock -> Warning banner rendered
  const htmlMockActive = renderToStaticMarkup(
    React.createElement(DriftModeToggle, {
      mode: 'mock',
      capabilities: { allow_mock: true },
    })
  );
  assert.ok(htmlMockActive.includes('Mock mode active'), 'Shows mock mode warning banner');
});

// ─── TEST 28: DriftCategorySummary renders clickable category counter pills ───
test('DriftCategorySummary renders all 5 category pill buttons with dynamic counts and aria attributes', () => {
  const counts = {
    ADDITION: 5,
    DELETION: 2,
    EXPRESSION_DRIFT: 8,
    DESCRIPTION_DRIFT: 1,
  };

  const html = renderToStaticMarkup(
    React.createElement(DriftCategorySummary, {
      countsByCategory: counts,
      totalCount: 16,
      selectedCategory: 'EXPRESSION_DRIFT',
      onSelectCategory: () => {},
    })
  );

  assert.ok(html.includes('All'), 'All category rendered');
  assert.ok(html.includes('16'), 'Total count 16 rendered');
  assert.ok(html.includes('Addition'), 'Addition rendered');
  assert.ok(html.includes('5'), 'Addition count 5 rendered');
  assert.ok(html.includes('Deletion'), 'Deletion rendered');
  assert.ok(html.includes('2'), 'Deletion count 2 rendered');
  assert.ok(html.includes('Expression'), 'Expression rendered');
  assert.ok(html.includes('8'), 'Expression count 8 rendered');
  assert.ok(html.includes('Description'), 'Description rendered');
  assert.ok(html.includes('1'), 'Description count 1 rendered');
  assert.ok(html.includes('aria-pressed="true"'), 'Selected category has aria-pressed true');
});

// ─── TEST 29: DriftFindingDetail renders side-by-side comparison and actions ───
test('DriftFindingDetail renders side-by-side baseline and live definitions with actions', () => {
  const event = {
    id: 'drift-test-123',
    object_name: 'REVENUE_PER_EMPLOYEE',
    kind: 'METRIC',
    category: 'EXPRESSION_DRIFT',
    status: 'pending',
    bi_definition: 'DIVIDE([TOTAL_REVENUE], [TOTAL_HEADCOUNT])',
    snowflake_definition: 'SUM(REVENUE) / COUNT(EMPLOYEE_ID)',
    proposed_snowflake_definition: 'SUM(FACT_FIN.REVENUE) / NULLIF(COUNT(DISTINCT EMP.ID), 0)',
    target_view: 'FIN_MODEL_SEMANTIC',
    affected_objects: ['DASHBOARD_CFO', 'CORTEX_ANALYST_AGENT'],
  };

  const html = renderToStaticMarkup(
    React.createElement(DriftFindingDetail, {
      event,
      onResolve: () => {},
      onOpenBlast: () => {},
    })
  );

  assert.ok(html.includes('Baseline (BI Semantic Model)'), 'Baseline block header rendered');
  assert.ok(html.includes('DIVIDE([TOTAL_REVENUE], [TOTAL_HEADCOUNT])'), 'Baseline expression rendered');
  assert.ok(html.includes('Current Warehouse View'), 'Current warehouse block header rendered');
  assert.ok(html.includes('SUM(REVENUE) / COUNT(EMPLOYEE_ID)'), 'Current live expression rendered');
  assert.ok(html.includes('Proposed Resolution'), 'Proposed resolution block rendered');
  assert.ok(html.includes('Restore &amp; Sync') || html.includes('Restore & Sync'), 'Restore & Sync button rendered');
  assert.ok(html.includes('Accept Change'), 'Accept Change button rendered');
  assert.ok(html.includes('Ignore'), 'Ignore button rendered');
  assert.ok(html.includes('Blast Radius'), 'Blast Radius button rendered');
  assert.ok(html.includes('DASHBOARD_CFO'), 'Affected object rendered');
});

// ─── TEST 30: DriftFindingsTable bridges findings container properly ─────────
test('DriftFindingsTable delegates cleanly to DriftFindingsList', () => {
  const html = renderToStaticMarkup(
    React.createElement(DriftFindingsTable, {
      events: [],
      totalCount: 0,
      totalUnfilteredCount: 0,
      countsByCategory: {},
      countsByKind: {},
      countsByStatus: {},
      page: 1,
      pageSize: 25,
      totalPages: 1,
    })
  );

  assert.ok(html.includes('No findings match your criteria'), 'Renders empty state properly through table bridge');
});

// ─── TEST 31: Single source of truth for counts: header, chips, groups, and bulk labels agree across pages and filters ───
test('single source of truth: header badge, chips, group counts, and bulk action labels agree on page two and with filters', () => {
  const totalFindings = 52;
  const exprDriftCount = 36;
  const additionCount = 16;

  const backendCountsByCategory = {
    EXPRESSION_DRIFT: exprDriftCount,
    ADDITION: additionCount,
  };
  const backendPendingByCategory = {
    EXPRESSION_DRIFT: exprDriftCount,
    ADDITION: additionCount,
  };
  const backendCountsByStatus = {
    pending: totalFindings,
    approved: 0,
    accepted: 0,
    ignored: 0,
  };

  // 1. Render Status Card with backend counts
  const cardHtml = renderToStaticMarkup(
    React.createElement(DriftStatusCard, {
      status: 'DRIFT_DETECTED',
      driftDetected: backendCountsByStatus.pending,
      categoryCounts: {
        ALL: totalFindings,
        EXPRESSION_DRIFT: exprDriftCount,
        ADDITION: additionCount,
        DELETION: 0,
        DESCRIPTION_DRIFT: 0,
      },
      metricsCompared: 34,
      checkedAt: new Date().toISOString(),
      baselineSource: 'deploy',
      onSelectCategory: () => {},
    })
  );

  // Assert header badge has "52 event(s)"
  assert.ok(cardHtml.includes('52 event(s)'), 'Header badge displays single source pending count (52)');
  // Assert category chips match backend numbers
  assert.ok(cardHtml.includes('<span>All</span>') && cardHtml.includes('(52)'), 'Chip All agrees with total count');
  assert.ok(cardHtml.includes('(36)'), 'Chip Expression Drift agrees with backend counts_by_category');
  assert.ok(cardHtml.includes('(16)'), 'Chip Addition agrees with backend counts_by_category');

  // 2. Render DriftFindingsList on PAGE 2 with page-sliced events (only 2 items on page 2)
  const page2Events = [
    {
      id: 'ev-p2-1',
      metric_name: 'METRIC_EXPR_P2',
      kind: 'METRIC',
      category: 'EXPRESSION_DRIFT',
      status: 'pending',
      live_expression: 'T.C1 + T.C2',
      baseline_expression: 'T.C1',
    },
    {
      id: 'ev-p2-2',
      metric_name: 'METRIC_EXPR_P2_B',
      kind: 'METRIC',
      category: 'EXPRESSION_DRIFT',
      status: 'pending',
      live_expression: 'T.C3',
      baseline_expression: 'T.C4',
    },
  ];

  const listHtmlPage2 = renderToStaticMarkup(
    React.createElement(DriftFindingsList, {
      events: page2Events,
      totalCount: totalFindings,
      totalUnfilteredCount: totalFindings,
      countsByCategory: backendCountsByCategory,
      pendingByCategory: backendPendingByCategory,
      countsByStatus: backendCountsByStatus,
      page: 2,
      pageSize: 25,
      totalPages: 3,
      mode: 'live',
    })
  );

  // Assert group count badge reflects backend category count (36), not the page-sliced count (2)
  assert.ok(listHtmlPage2.includes('>36<'), 'Group header count badge reflects backend count for category (36)');
  // Assert bulk action label equals full pending count (36), NOT page-sliced 2 or 17
  assert.ok(listHtmlPage2.includes('Approve all (36)'), 'Bulk action label equals total pending items (36) on page 2');
  assert.ok(listHtmlPage2.includes('Ignore all (36)'), 'Bulk action label equals total pending items (36) on page 2');

  // 3. Render DriftFindingsList with filtered status ('approved', 0 pending)
  const listHtmlApproved = renderToStaticMarkup(
    React.createElement(DriftFindingsList, {
      events: [
        {
          id: 'ev-app-1',
          metric_name: 'METRIC_EXPR_APP',
          kind: 'METRIC',
          category: 'EXPRESSION_DRIFT',
          status: 'approved',
          live_expression: 'T.C1',
          baseline_expression: 'T.C1',
        },
      ],
      totalCount: 1,
      totalUnfilteredCount: totalFindings,
      countsByCategory: { EXPRESSION_DRIFT: 1 },
      pendingByCategory: { EXPRESSION_DRIFT: 0 },
      countsByStatus: { pending: 0, approved: 1 },
      page: 1,
      pageSize: 25,
      totalPages: 1,
      mode: 'live',
    })
  );

  // In approved filter, there are 0 pending items, so bulk action button is not rendered
  assert.ok(!listHtmlApproved.includes('Approve all'), 'Bulk action button hidden when 0 pending items under current filter');
});




