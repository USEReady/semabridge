const REGEX_PREFIX_RE = /^(?:re|regex):(.+)$/i;
const PREFIX_PREFIX_RE = /^(?:p|prefix):(.+)$/i;

function levenshteinDistance(left, right, maxDistance) {
  const a = String(left || '');
  const b = String(right || '');

  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  if (Math.abs(a.length - b.length) > maxDistance) return maxDistance + 1;

  let previous = Array.from({ length: b.length + 1 }, (_, index) => index);

  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    let rowMin = current[0];

    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      const value = Math.min(
        previous[j] + 1,
        current[j - 1] + 1,
        previous[j - 1] + cost,
      );
      current[j] = value;
      if (value < rowMin) rowMin = value;
    }

    if (rowMin > maxDistance) return maxDistance + 1;
    previous = current;
  }

  return previous[b.length];
}

function fuzzyPrefixMatches(word, query) {
  const lowerWord = String(word || '').toLowerCase();
  const lowerQuery = String(query || '').toLowerCase();

  if (!lowerQuery) return true;
  if (lowerWord.startsWith(lowerQuery)) return true;

  const prefix = lowerWord.slice(0, lowerQuery.length);
  const maxEdits = Math.max(1, Math.floor(lowerQuery.length * 0.15));
  return levenshteinDistance(lowerQuery, prefix, maxEdits) <= maxEdits;
}

export function getSmartQueryMode(query, useRegex = false) {
  const q = String(query || '').trim();
  if (!q) return { mode: 'empty', term: '', strictPrefix: false };

  const prefixMatch = q.match(PREFIX_PREFIX_RE);
  if (prefixMatch) {
    return { mode: 'prefix', term: prefixMatch[1].trim(), strictPrefix: true };
  }

  const regexMatch = q.match(REGEX_PREFIX_RE);
  if (regexMatch) return { mode: 'regex', pattern: regexMatch[1].trim(), flags: 'i' };

  if (q.startsWith('/') && q.length > 2 && q.lastIndexOf('/') > 0) {
    const lastSlash = q.lastIndexOf('/');
    return {
      mode: 'regex',
      pattern: q.slice(1, lastSlash),
      flags: q.slice(lastSlash + 1) || 'i',
    };
  }

  if (useRegex) return { mode: 'regex', pattern: q, flags: 'i' };
  return { mode: 'prefix', term: q, strictPrefix: false };
}

export function getSmartQueryError(query, useRegex = false) {
  const parsed = getSmartQueryMode(query, useRegex);
  if (parsed.mode !== 'regex' || !parsed.pattern) return '';

  try {
    new RegExp(parsed.pattern, parsed.flags);
    return '';
  } catch (err) {
    return err?.message || 'Invalid regex';
  }
}

export function matchesSmartQuery(value, query, useRegex) {
  const haystack = String(value || '');
  const parsed = getSmartQueryMode(query, useRegex);

  if (parsed.mode === 'empty') return true;

  if (parsed.mode === 'regex') {
    try {
      return new RegExp(parsed.pattern, parsed.flags).test(haystack);
    } catch {
      return false;
    }
  }

  const lowerQuery = String(parsed.term || '').trim();
  if (!lowerQuery) return true;

  const words = haystack.toLowerCase().split(/\s+/).filter(Boolean);
  if (parsed.strictPrefix) {
    return words.some(word => word.startsWith(lowerQuery.toLowerCase()));
  }

  return words.some(word => fuzzyPrefixMatches(word, lowerQuery));
}
