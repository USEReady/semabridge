/**
 * Databricks naming, policy revision 2 (lowercase snake_case) -- the browser
 * copy of the backend's normaliser (src/semabridge/target_rules/databricks/naming.py).
 * Used for previews and validation on the dry-run screen; the backend stays
 * authoritative (it re-normalises every saved name). Names the backend can only
 * produce with its identity hash (unsupported scripts) are not reproduced here:
 * such input returns '' and the backend decides.
 */

export const DATABRICKS_MAX_LEN = 255;
export const DATABRICKS_NAME_PATTERN = /^[a-z][a-z0-9_]*$/;

const USABILITY_KEYWORDS = new Set([
  'select', 'from', 'where', 'group', 'by', 'order', 'having', 'limit', 'offset', 'join', 'on',
  'using', 'union', 'intersect', 'except', 'minus', 'with', 'as', 'case', 'when', 'then', 'else',
  'end', 'distinct', 'all', 'and', 'or', 'not', 'in', 'is', 'like', 'ilike', 'rlike', 'between',
  'exists', 'any', 'some', 'into', 'values', 'table', 'create', 'drop', 'alter', 'insert',
  'update', 'delete', 'merge', 'grant', 'revoke',
  'null', 'true', 'false', 'default', 'lateral', 'interval', 'cast',
  'anti', 'semi', 'cross', 'full', 'inner', 'left', 'right', 'natural', 'outer',
  'source',
]);
const PROTECTED_PREFIXES = ['current_', 'metadata'];
const BRAND_WORDS = ['iphone', 'ipad', 'ipod', 'imac', 'ios', 'macos', 'ebay', 'youtube', 'linkedin',
  'powerbi', 'github', 'gitlab', 'javascript', 'typescript', 'postgresql', 'mysql', 'openai', 'pyspark',
  'databricks', 'snowflake'];
const LATIN = { ß: 'ss', ẞ: 'SS', Ł: 'L', ł: 'l', Æ: 'AE', æ: 'ae', Ø: 'O', ø: 'o', Œ: 'OE', œ: 'oe',
  Đ: 'D', đ: 'd', Þ: 'TH', þ: 'th', Ð: 'D', ð: 'd', ı: 'i' };
const SYMBOLS = [
  ['>=', 'gte'], ['<=', 'lte'], ['!=', 'ne'], ['<>', 'ne'], ['≥', 'gte'], ['≤', 'lte'], ['≠', 'ne'],
  ['%', 'pct'], ['$', 'dollars'], ['€', 'eur'], ['£', 'gbp'], ['₹', 'inr'], ['¥', 'yen_yuan'],
  ['#', 'num'], ['@', 'at'], ['&', 'and'], ['+', 'plus'], ['*', 'star'], ['=', 'eq'],
  ['<', 'lt'], ['>', 'gt'], ['?', 'question'], ['!', 'excl'],
];
const TOKEN = /[A-Z]{2,}s(?=[A-Z0-9]|$)|[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+[0-9]*|[A-Z]+[0-9]*|[0-9]+[a-z]*/y;

function fold(text) {
  let out = '';
  let lost = false;
  for (const ch of text) {
    if (LATIN[ch]) { out += LATIN[ch]; continue; }
    const base = ch.normalize('NFKD').replace(/\p{M}/gu, '');
    for (const c of base) {
      if (c.charCodeAt(0) < 128) out += c;
      else { if (/[\p{L}\p{N}]/u.test(c)) lost = true; out += ' '; }
    }
  }
  return { text: out, lost };
}

function tokens(chunk) {
  const out = [];
  let i = 0;
  while (i < chunk.length) {
    const low = chunk.slice(i).toLowerCase();
    const brand = BRAND_WORDS.find((b) => low.startsWith(b)
      && (chunk.length === i + b.length || !/[a-z]/.test(chunk[i + b.length])));
    if (brand && (i === 0 || !/[A-Za-z]/.test(chunk[i - 1]) || /[A-Z]/.test(chunk[i]) || /[a-z]/.test(chunk[i - 1]))) {
      out.push(chunk.slice(i, i + brand.length));
      i += brand.length;
      continue;
    }
    TOKEN.lastIndex = i;
    const m = TOKEN.exec(chunk);
    if (!m || !m[0]) { out.push(chunk[i]); i += 1; continue; }
    out.push(m[0]);
    i = TOKEN.lastIndex;
  }
  return out;
}

function kindWord(kind) {
  const k = String(kind || '').toLowerCase();
  return ['metric', 'measure'].includes(k) ? 'measure' : 'field';
}

/** Lowercase snake_case the rule leaves as it is (backend is_final_name). */
export function isDatabricksFinalName(value) {
  const v = String(value || '');
  return isDatabricksName(v) && !USABILITY_KEYWORDS.has(v) && !PROTECTED_PREFIXES.some((p) => v.startsWith(p));
}

/** The Databricks (v2) name for a label; '' when only the backend can name it
 * (unsupported scripts need its identity hash; names over 255 characters its hashed shortening). */
export function toDatabricksName(value, kind = 'column') {
  const label = String(value ?? '');
  if (!label.trim()) return '';
  if (isDatabricksFinalName(label)) return label;
  let { text, lost } = fold(label);
  if (lost) return '';
  SYMBOLS.forEach(([sym, word]) => { text = text.split(sym).join(` ${word} `); });
  text = text.replace(/['’‘`]/g, '');
  const words = [];
  text.split(/[^A-Za-z0-9]+/).forEach((chunk) => { if (chunk) words.push(...tokens(chunk)); });
  let name = words.map((w) => w.toLowerCase()).join('_').replace(/_+/g, '_').replace(/^_+|_+$/g, '');
  if (!name) return '';
  const kw = kindWord(kind);
  if (/^[0-9]/.test(name) || PROTECTED_PREFIXES.some((p) => name.startsWith(p))) name = `${kw}_${name}`;
  if (USABILITY_KEYWORDS.has(name)) name = `${name}_${kw}`;
  return name.length > DATABRICKS_MAX_LEN ? '' : name;
}

export function isDatabricksName(value) {
  const v = String(value || '');
  return DATABRICKS_NAME_PATTERN.test(v) && v.length <= DATABRICKS_MAX_LEN;
}
