/**
 * TDD tests for clickable wizard stepper navigation (1..6) and early pre-Step-4 duplicate gating.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import esbuild from 'esbuild';

import {
  hasSelectedWizardSource,
  canJumpToWizardStep,
  resolveWizardStepJump,
  buildDuplicateCheckSignature,
} from './wizardStepNavigation.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const baseValidState = {
  currentStep: 3,
  editMode: false,
  name: 'Sales Pipeline',
  sourceConnector: 'fabric',
  targetConnectors: new Set(['databricks']),
  intermediateFormat: 'osi',
  selectedModelNames: ['Sales Exec'],
  effectiveSources: { sources: ['Sales Exec'], singlePbixPath: '' },
  selectedDatabricksTables: new Set(),
  pbixUploadPath: '',
  selectedPbixFilePath: '',
  mappingReadyToProceed: false,
  currentDuplicateSignature: 'sig-1',
  acknowledgedDuplicateSignature: '',
};

test('backward step clicks (e.g. 4 -> 1, 5 -> 2, 6 -> 3) are always clickable without re-blocking', () => {
  const stateAt4 = { ...baseValidState, currentStep: 4 };
  for (const target of [1, 2, 3]) {
    assert.equal(canJumpToWizardStep(target, stateAt4), true);
    const res = resolveWizardStepJump(target, stateAt4);
    assert.equal(res.allowed, true);
    assert.equal(res.targetStep, target);
    assert.equal(res.requiresDuplicateCheck, false);
  }
});

test('forward step clicks validate prerequisites and trigger pre-Step-4 duplicate check once per selection', () => {
  // Missing project name -> blocks jumping past Step 1
  const missingStep1 = { ...baseValidState, currentStep: 1, name: '   ' };
  assert.equal(canJumpToWizardStep(2, missingStep1), false);
  assert.deepEqual(resolveWizardStepJump(4, missingStep1), {
    allowed: false,
    fallbackStep: 1,
    reason: 'step1_invalid',
    requiresDuplicateCheck: false,
  });

  // Missing source selection -> allows Step 2 & 3, blocks Step 4+
  const missingSource = {
    ...baseValidState,
    currentStep: 2,
    selectedModelNames: [],
    effectiveSources: { sources: [], singlePbixPath: '' },
  };
  assert.equal(canJumpToWizardStep(3, missingSource), true);
  assert.equal(canJumpToWizardStep(4, missingSource), false);
  assert.deepEqual(resolveWizardStepJump(4, missingSource), {
    allowed: false,
    fallbackStep: 3,
    reason: 'no_source_selected',
    requiresDuplicateCheck: false,
  });

  // Valid source + target crossing from Step <= 3 into Step 4 or 5 -> requires pre-Step-4 duplicate check!
  assert.equal(canJumpToWizardStep(4, baseValidState), true);
  assert.equal(canJumpToWizardStep(5, baseValidState), true);
  const jump3To4 = resolveWizardStepJump(4, baseValidState);
  assert.equal(jump3To4.allowed, true);
  assert.equal(jump3To4.targetStep, 4);
  assert.equal(jump3To4.requiresDuplicateCheck, true);

  // Once user acknowledged this exact selection/target signature, no duplicate re-prompt
  const acked = { ...baseValidState, acknowledgedDuplicateSignature: 'sig-1' };
  assert.equal(resolveWizardStepJump(4, acked).requiresDuplicateCheck, false);

  // Step 6 in create mode requires Dry Run readiness (mappingReadyToProceed), unlocked in editMode
  assert.equal(canJumpToWizardStep(6, baseValidState), false);
  assert.equal(resolveWizardStepJump(6, baseValidState).fallbackStep, 5);
  assert.equal(canJumpToWizardStep(6, { ...baseValidState, mappingReadyToProceed: true }), true);
  assert.equal(canJumpToWizardStep(6, { ...baseValidState, editMode: true }), true);
});

test('hasSelectedWizardSource supports pbix, fabric, tableau, snowflake, and databricks', () => {
  assert.equal(
    hasSelectedWizardSource({ sourceConnector: 'pbix', effectiveSources: { sources: ['/tmp/a.pbix'] } }),
    true,
  );
  assert.equal(
    hasSelectedWizardSource({ sourceConnector: 'tableau', pbixUploadPath: '/tmp/orders.tdsx' }),
    true,
  );
  assert.equal(
    hasSelectedWizardSource({ sourceConnector: 'databricks', selectedDatabricksTables: new Set(['main.gold.orders']) }),
    true,
  );
  assert.equal(
    hasSelectedWizardSource({ sourceConnector: 'snowflake', snowflakeDatabase: 'DB', snowflakeSchema: 'SCH' }),
    true,
  );
});

test('buildDuplicateCheckSignature changes when source models or target location change', () => {
  const s1 = buildDuplicateCheckSignature({
    sourceConnector: 'pbix',
    sources: ['/tmp/a.pbix'],
    targetType: 'databricks',
    targetDatabase: 'workspace',
    targetSchema: 'default',
  });
  const s2 = buildDuplicateCheckSignature({
    sourceConnector: 'pbix',
    sources: ['/tmp/a.pbix'],
    targetType: 'databricks',
    targetDatabase: 'workspace',
    targetSchema: 'staging',
  });
  assert.notEqual(s1, s2);
});

test('WizardHeader wires step badge clicks through onSelectStep / currentStepIndex (not broken step key)', () => {
  const headerPath = path.resolve(__dirname, '../components/CreateProjectWizard/WizardHeader.jsx');
  const src = readFileSync(headerPath, 'utf8');
  assert.ok(!src.includes('setWizardState({ step:'), 'WizardHeader must not write non-existent wizard.step key');
  assert.ok(src.includes('onSelectStep'), 'WizardHeader must accept onSelectStep handler');
  const compiled = esbuild.transformSync(src, { loader: 'jsx', format: 'esm', jsx: 'automatic' });
  assert.ok(compiled.code.length > 0);
});

