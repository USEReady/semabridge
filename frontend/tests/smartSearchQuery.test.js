import test from 'node:test';
import assert from 'node:assert/strict';

import { getSmartQueryMode, matchesSmartQuery, getSmartQueryError } from '../src/components/common/smartSearchQuery.js';

test('default fuzzy prefix matches a typo in the first word', () => {
  assert.equal(matchesSmartQuery('hello world', 'helo'), true);
  assert.equal(matchesSmartQuery('helo there', 'hello'), true);
});

test('explicit prefix mode stays strict', () => {
  assert.equal(getSmartQueryMode('p:helo').strictPrefix, true);
  assert.equal(matchesSmartQuery('hello world', 'p:helo'), false);
  assert.equal(matchesSmartQuery('helo there', 'p:helo'), true);
});

test('regex mode via re: prefix matches correctly', () => {
  assert.equal(getSmartQueryMode('re:hello.*abc').mode, 'regex');
  assert.equal(matchesSmartQuery('HelloXXXabc', 're:hello.*abc'), true);
  assert.equal(matchesSmartQuery('no match here', 're:hello.*abc'), false);
});

test('regex mode via slash syntax and flags is case-insensitive by default', () => {
  assert.equal(getSmartQueryMode('/METRIC_[A-Z]+/i').mode, 'regex');
  assert.equal(matchesSmartQuery('metric_ABC', '/METRIC_[A-Z]+/i'), true);
});

test('invalid regex produces an error and match returns false', () => {
  const err = getSmartQueryError('re:(');
  assert.ok(err && typeof err === 'string');
  assert.equal(matchesSmartQuery('anything', 're:('), false);
});

test('bare query with wildcard chars is treated as fuzzy prefix (not regex)', () => {
  assert.equal(getSmartQueryMode('hello*abc').mode, 'prefix');
  assert.equal(matchesSmartQuery('hello*abc world', 'hello*abc'), true);
});

test('fuzzy prefix matches examples from doc (cust -> customer/CustID)', () => {
  assert.equal(matchesSmartQuery('customer', 'cust'), true);
  assert.equal(matchesSmartQuery('custom metric', 'cust'), true);
  assert.equal(matchesSmartQuery('CustID', 'cust'), true);
});

test('strict prefix (p:) matches starts-with and excludes inside words', () => {
  assert.equal(matchesSmartQuery('sales_q1', 'p:sale'), true);
  assert.equal(matchesSmartQuery('sale_manager', 'p:sale'), true);
  assert.equal(matchesSmartQuery('wholesale', 'p:sale'), false);
});

test('useRegex=true treats bare query as regex', () => {
  const parsed = getSmartQueryMode('a.c1', true);
  assert.equal(parsed.mode, 'regex');
  assert.equal(matchesSmartQuery('abc1', 'a.c1', true), true);
  assert.equal(matchesSmartQuery('axc1', 'a.c1', true), true);
  assert.equal(matchesSmartQuery('ac1', 'a.c1', true), false);
});

test('regex flags: case-sensitive when only `g` provided, case-insensitive with `gi`', () => {
  // 'g' only should be case-sensitive -> no match for lowercase 'metric_ABC'
  assert.equal(matchesSmartQuery('metric_ABC', '/METRIC_[A-Z]+/g'), false);
  // 'gi' should match case-insensitively
  assert.equal(matchesSmartQuery('metric_ABC', '/METRIC_[A-Z]+/gi'), true);
});

test('regex with .* matches across multiple words', () => {
  assert.equal(matchesSmartQuery('hello xyz abc', 're:hello.*abc'), true);
});

test('fuzzy prefix matches hyphenated words and multi-word haystacks', () => {
  assert.equal(matchesSmartQuery('hello-world', 'hello'), true);
  assert.equal(matchesSmartQuery('say hello there', 'hello'), true);
});

test('strict prefix with prefix: behaves as documented for multi-word and underscored words', () => {
  assert.equal(matchesSmartQuery('gross_revenue', 'prefix:rev'), false);
  assert.equal(matchesSmartQuery('revenue', 'prefix:rev'), true);
});

test('useRegex=true matches across spaces with dot-star', () => {
  assert.equal(matchesSmartQuery('a x y b', 'a.*b', true), true);
});
