/**
 * WizardUIComponents.test.js
 *
 * Regression test for the bug: ToggleOption's onClick called
 * `onChange(v => !v)` -- a React "functional updater" style call that only
 * behaves correctly when `onChange` is a raw useState setter. Most
 * consumers pass a plain value-setter instead (e.g. CreateProjectPage.jsx's
 * `setAutoRelationships = (val) => setWizardState({ autoRelationships:
 * !!val })`), which received the FUNCTION itself as `val`. A function is
 * always truthy, so `!!val` was always `true` -- the "Auto-detect
 * Relationships" toggle in StepMappingOptions.jsx could never be turned
 * off, regardless of click direction.
 *
 * "Auto-Detect Mapping" (the toggle right above it, same ToggleOption
 * component) happened to keep working throughout, because its handler
 * (toggleAutoMappingMode) takes zero parameters and ignores whatever
 * ToggleOption passes it entirely -- it was never actually exercising the
 * broken calling convention.
 *
 * No React rendering/DOM needed to catch this: ToggleOption is a plain
 * function that returns a React element description. Calling it directly
 * and invoking the returned element's onClick prop is enough to observe
 * exactly what gets passed to `onChange` -- which is the whole bug.
 *
 * This project has no JSX-capable test runner configured (no Jest/Vitest),
 * so this file transforms the .jsx source with esbuild (already present
 * transitively via Vite -- no new dependency) before requiring it.
 *
 * Run: node --test src/components/WizardUIComponents.test.js   (from frontend/)
 */
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import esbuild from 'esbuild';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// WizardUIComponents.jsx as a whole imports several other .jsx/ESM-only
// project modules (projectHelpers.jsx etc., used by OTHER exports in the
// same file) that a plain CommonJS require() can't load without its own
// cascading transform pass. ToggleOption itself doesn't use any of them,
// so this extracts and compiles ONLY the ToggleOption function's own source
// text (via brace-matching from its `export function ToggleOption(`
// header) instead of the whole file, sidestepping that entirely.
const compiledFile = path.join(__dirname, '.wizarduicomponents.compiled.test.cjs');

function extractToggleOptionSource(fileSource) {
  const header = 'export function ToggleOption(';
  const start = fileSource.indexOf(header);
  if (start === -1) throw new Error('ToggleOption not found in WizardUIComponents.jsx -- did it get renamed?');

  // Skip past the parameter list first (its own destructuring "{ ... }"
  // would otherwise be mistaken for the function body by naive brace
  // matching) by counting parens from the "(" right after the header.
  const parenStart = start + header.length - 1;
  let parenDepth = 0;
  let afterParams = -1;
  for (let i = parenStart; i < fileSource.length; i++) {
    if (fileSource[i] === '(') parenDepth++;
    else if (fileSource[i] === ')') {
      parenDepth--;
      if (parenDepth === 0) { afterParams = i + 1; break; }
    }
  }
  if (afterParams === -1) throw new Error('Could not find the end of ToggleOption\'s parameter list.');

  const braceStart = fileSource.indexOf('{', afterParams);
  let depth = 0;
  for (let i = braceStart; i < fileSource.length; i++) {
    if (fileSource[i] === '{') depth++;
    else if (fileSource[i] === '}') {
      depth--;
      if (depth === 0) return fileSource.slice(start, i + 1);
    }
  }
  throw new Error('Could not find the end of ToggleOption -- unbalanced braces?');
}

function loadToggleOption() {
  const sourcePath = path.join(__dirname, 'WizardUIComponents.jsx');
  const fileSource = readFileSync(sourcePath, 'utf8');
  const fnSource = extractToggleOptionSource(fileSource)
    .replace('export function ToggleOption', 'exports.ToggleOption = function ToggleOption');
  const { code } = esbuild.transformSync(fnSource, {
    loader: 'jsx',
    format: 'cjs',
    // Automatic runtime, matching how @vitejs/plugin-react actually compiles
    // this file for real -- the source has no explicit `import React from
    // 'react'` (only named imports), so the classic
    // React.createElement-in-scope transform would fail with a
    // ReferenceError here even though it works fine in the real app.
    jsx: 'automatic',
  });
  writeFileSync(compiledFile, code);
  return require(compiledFile).ToggleOption;
}

const ToggleOption = loadToggleOption();

after(() => {
  if (existsSync(compiledFile)) rmSync(compiledFile, { force: true });
});

test('ToggleOption calls onChange with a plain boolean, never a functional updater', () => {
  let received;
  const element = ToggleOption({
    label: 'Auto-detect Relationships',
    description: 'Infers FK constraints automatically.',
    checked: true,
    onChange: (val) => { received = val; },
  });

  element.props.onClick();

  assert.equal(typeof received, 'boolean', `onChange must receive a boolean, got ${typeof received}`);
  assert.equal(received, false, 'clicking while checked=true must call onChange(false)');
});

test('ToggleOption toggles the other direction too', () => {
  let received;
  const element = ToggleOption({
    label: 'Auto-detect Relationships',
    checked: false,
    onChange: (val) => { received = val; },
  });

  element.props.onClick();

  assert.equal(received, true, 'clicking while checked=false must call onChange(true)');
});

test('regression: a plain value-setter (not a useState functional-updater consumer) can actually turn the toggle off', () => {
  // Mirrors the real bug: setAutoRelationships(val) => setWizardState({ autoRelationships: !!val })
  // -- before the fix, `val` was the updater function itself, and !!fn is
  // always true, so the toggle could never be switched off.
  let stored = true;
  const setAutoRelationships = (val) => { stored = !!val; };

  const element = ToggleOption({ label: 'Auto-detect Relationships', checked: stored, onChange: setAutoRelationships });
  element.props.onClick();

  assert.equal(stored, false, 'turning off a plain-value setter must actually turn it off, not stay stuck on true');
});

test('a zero-argument toggle callback (e.g. toggleAutoMappingMode) still works correctly after the fix', () => {
  // "Auto-Detect Mapping" uses onChange={toggleAutoMappingMode}, a
  // useCallback that ignores its argument entirely and does its own
  // internal setAutoMappingMode(prev => !prev). It "worked by accident"
  // against the old buggy call (`onChange(v => !v)`) purely because it
  // never looked at what was passed -- confirm that still holds true now
  // that ToggleOption passes a plain boolean instead.
  let internalState = true;
  const toggleAutoMappingMode = () => { internalState = !internalState; };

  const element = ToggleOption({ label: 'Auto-Detect Mapping', checked: internalState, onChange: toggleAutoMappingMode });
  element.props.onClick();
  assert.equal(internalState, false);

  const element2 = ToggleOption({ label: 'Auto-Detect Mapping', checked: internalState, onChange: toggleAutoMappingMode });
  element2.props.onClick();
  assert.equal(internalState, true);
});
