/**
 * DryRunMappingTable.guardrail.test.js
 *
 * Static-analysis guardrail for the "un-normalized data reaches
 * DryRunMappingTable" bug class -- the fourth confirmed occurrence of the
 * silent-field-drop pattern documented in utils/normalizeRows.js, except this
 * time an entire caller (MultiFileDryRunStatus.jsx's multi-file drill-in)
 * skipped normalizeRows() rather than one field being missed inside it. The
 * result: blank "Source Expression (DAX)" boxes and "fx Measure"/"unknown"/
 * "-- unmapped --" placeholders on every row, even for measures that mapped
 * and translated successfully.
 *
 * DryRunMappingTable itself can't tell a normalized row from a raw one (both
 * are plain objects), and there's no jsdom/React Testing Library in this
 * project to mount the component and assert on rendered text. So this test
 * does the next best thing: it scans every source file under src/ for a
 * `<DryRunMappingTable ... mappings={...}>` usage and asserts the `mappings`
 * expression never accesses `.entity_mappings` directly -- that literal
 * pattern (`mappings={response.entity_mappings || []}`) IS the bug. The
 * fixed shape is either `mappings={normalizeRows(response)}` (compute inline,
 * as MultiFileDryRunStatus.jsx now does) or a plain variable/prop, e.g.
 * `mappings={detectedMappings}` (StepMappingOptions.jsx), that some ancestor
 * component already built via normalizeRows() before threading it down as a
 * prop -- normalization doesn't have to happen in the same file that renders
 * the table, it just must never be skipped between the raw API response and
 * this component.
 *
 * (See DryRunMappingTable.jsx's dev-only runtime console.error for the
 * complementary belt-and-suspenders check that fires at render time if this
 * static check is ever bypassed, e.g. by a `mappings` value built through
 * some other raw-passthrough path this scan doesn't recognize.)
 *
 * Run: node --test src/components/DryRunMappingTable.guardrail.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = path.resolve(__dirname, '..'); // frontend/src

function listSourceFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      out.push(...listSourceFiles(full));
    } else if (/\.(jsx?|tsx?)$/.test(entry) && !entry.endsWith('.test.js')) {
      out.push(full);
    }
  }
  return out;
}

// Pulls out the value expression bound to `mappings=` for every
// `<DryRunMappingTable ...>` usage in a file's source text. Handles the
// `mappings={<expr>}` JSX-attribute shape used throughout this codebase by
// tracking brace depth (a plain regex can't safely stop at the first `}`
// when the expression itself contains nested braces, e.g. `foo || []`
// wouldn't, but `normalizeRows(x)` calls or object literals could).
function findMappingsPropExpressions(contents) {
  const exprs = [];
  const attrRegex = /mappings=\{/g;
  let match;
  while ((match = attrRegex.exec(contents)) !== null) {
    let depth = 1;
    let i = match.index + match[0].length;
    const start = i;
    while (i < contents.length && depth > 0) {
      if (contents[i] === '{') depth += 1;
      else if (contents[i] === '}') depth -= 1;
      i += 1;
    }
    exprs.push(contents.slice(start, i - 1));
  }
  return exprs;
}

test('no <DryRunMappingTable mappings={...}> expression accesses a raw *.entity_mappings field directly', () => {
  const files = listSourceFiles(SRC_ROOT);
  const offenders = [];

  for (const file of files) {
    const contents = readFileSync(file, 'utf8');
    if (!contents.includes('<DryRunMappingTable')) continue;
    if (path.basename(file) === 'DryRunMappingTable.jsx') continue; // defines the prop, not a caller

    const mappingsExprs = findMappingsPropExpressions(contents);
    const rawOffenders = mappingsExprs.filter((expr) => expr.includes('.entity_mappings') && !expr.includes('normalizeRows('));
    if (rawOffenders.length > 0) {
      offenders.push({ file: path.relative(SRC_ROOT, file), exprs: rawOffenders });
    }
  }

  assert.deepEqual(
    offenders,
    [],
    `Found <DryRunMappingTable mappings={...}> expression(s) that access a raw entity_mappings field directly: ` +
    `${JSON.stringify(offenders)}. This is the exact bug that produced blank DAX expressions and ` +
    `"fx Measure"/"unknown"/"-- unmapped --" placeholders on every row despite successful dry runs. ` +
    `Fix: wrap the raw API response in normalizeRows(...) (see utils/normalizeRows.js) and pass the result ` +
    `(or a variable an ancestor component already normalized) as the \`mappings\` prop instead.`,
  );
});

test('sanity check: the guardrail actually finds real <DryRunMappingTable> callers (not a vacuously-passing scan)', () => {
  const files = listSourceFiles(SRC_ROOT);
  const callers = files.filter((file) => {
    if (path.basename(file) === 'DryRunMappingTable.jsx') return false;
    return readFileSync(file, 'utf8').includes('<DryRunMappingTable');
  });
  assert.ok(
    callers.length >= 2,
    'Expected at least 2 real callers of <DryRunMappingTable> (StepMappingOptions.jsx, MultiFileDryRunStatus.jsx) -- ' +
    'if this is 0, the scan itself is broken and the guardrail above is passing vacuously.',
  );
});

test('sanity check: the scan itself correctly flags the ORIGINAL bug pattern, not just the fixed one', () => {
  const buggySource = `
    <DryRunMappingTable
      mappings={detail.result.entity_mappings || []}
      summary={detail.result.summary}
    />
  `;
  const exprs = findMappingsPropExpressions(buggySource);
  assert.equal(exprs.length, 1);
  assert.ok(exprs[0].includes('.entity_mappings'), 'the extractor must capture the raw entity_mappings access from the original bug');

  const fixedSource = `
    <DryRunMappingTable
      mappings={normalizeRows(detail.result)}
      summary={detail.result.summary}
    />
  `;
  const fixedExprs = findMappingsPropExpressions(fixedSource);
  assert.equal(fixedExprs.length, 1);
  assert.ok(!fixedExprs[0].includes('.entity_mappings'), 'the fixed call site must not read as a raw entity_mappings access');
});
