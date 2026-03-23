/**
 * useHPSearch — High-Performance Search Hook
 *
 * Provides fast indexing + search over large datasets (100k+ objects)
 * using MiniSearch with additional glob and regex support layered on top.
 *
 * API:
 *   const { results, query, setQuery, isSearching } = useHPSearch(items, fields, options)
 *
 * Parameters:
 *   items    — array of objects to search (must each have a unique `id` field,
 *              OR supply options.idField to specify the unique key)
 *   fields   — array of field names to index and search
 *   options  — optional overrides:
 *     idField      (string)  — field used as document ID (default "id")
 *     prefix       (bool)    — enable prefix / starts-with matching (default true)
 *     fuzzy        (number)  — fuzzy tolerance 0–1 (default 0.15)
 *     boost        (object)  — per-field boost weights
 *     maxResults   (number)  — maximum results returned (default 500)
 *
 * Query modes (auto-detected from query string):
 *   /pattern/   → regex mode (wraps in RegExp, applied as post-filter)
 *   *.glob?     → glob mode  (converted to regex, applied as post-filter)
 *   anything else → standard MiniSearch fuzzy + prefix search
 *
 * Returns:
 *   results     — filtered/ranked array from `items`
 *   query       — current query string
 *   setQuery    — setter for query string
 *   isSearching — true while index is being built
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import MiniSearch from 'minisearch';

const DEFAULT_OPTS = {
  idField: 'id',
  prefix: true,
  fuzzy: 0.15,
  boost: {},
  maxResults: 500,
};

// ---------------------------------------------------------------------------
// Glob → RegExp conversion
// ---------------------------------------------------------------------------
function globToRegex(glob) {
  const escaped = glob
    .replace(/[.+^${}()|[\]\\]/g, '\\$&') // escape special regex chars
    .replace(/\*/g, '.*')                   // * → .*
    .replace(/\?/g, '.');                   // ? → .
  return new RegExp(`^${escaped}$`, 'i');
}

function isGlob(q) {
  return q.includes('*') || q.includes('?');
}

function isRegexQuery(q) {
  return q.startsWith('/') && q.length > 2 && q.lastIndexOf('/') > 0;
}

function parseRegexQuery(q) {
  const lastSlash = q.lastIndexOf('/');
  const pattern = q.slice(1, lastSlash);
  const flags = q.slice(lastSlash + 1) || 'i';
  try {
    return new RegExp(pattern, flags);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Post-filter: apply regex/glob against the *full* items array (not index)
// ---------------------------------------------------------------------------
function applyPatternFilter(items, regex, fields, maxResults) {
  const out = [];
  for (const item of items) {
    if (out.length >= maxResults) break;
    for (const field of fields) {
      const val = item[field];
      if (val != null && regex.test(String(val))) {
        out.push(item);
        break;
      }
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Main hook
// ---------------------------------------------------------------------------
export function useHPSearch(items, fields, options = {}) {
  const opts = { ...DEFAULT_OPTS, ...options };
  const {
    idField,
    prefix: enablePrefix,
    fuzzy: fuzzyThreshold,
    boost,
    maxResults,
  } = opts;

  const [query, setQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const indexRef = useRef(null);
  const itemsRef = useRef(items);

  // Keep itemsRef fresh for pattern-filter (no index needed)
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  // Build MiniSearch index whenever items or fields change
  useEffect(() => {
    if (!items || items.length === 0) {
      indexRef.current = null;
      return;
    }

    setIsSearching(true);

    // Use a microtask to avoid blocking the render thread on large datasets
    const tid = setTimeout(() => {
      const ms = new MiniSearch({
        idField,
        fields,
        storeFields: [idField],
        searchOptions: {
          prefix: enablePrefix,
          fuzzy: fuzzyThreshold,
          boost,
        },
      });

      // MiniSearch requires unique IDs — deduplicate if needed
      const seen = new Set();
      const docs = [];
      for (const item of items) {
        const key = item[idField];
        if (key != null && !seen.has(key)) {
          seen.add(key);
          docs.push(item);
        }
      }

      ms.addAll(docs);
      indexRef.current = ms;
      setIsSearching(false);
    }, 0);

    return () => clearTimeout(tid);
  }, [items, fields.join(','), idField, enablePrefix, fuzzyThreshold]); // eslint-disable-line

  // Build id→item lookup for fast result hydration
  const itemById = useMemo(() => {
    if (!items) return {};
    const map = {};
    for (const item of items) {
      if (item[idField] != null) map[item[idField]] = item;
    }
    return map;
  }, [items, idField]);

  // Compute results
  const results = useMemo(() => {
    const q = query.trim();

    if (!q || !items || items.length === 0) return items ?? [];

    // Regex mode
    if (isRegexQuery(q)) {
      const regex = parseRegexQuery(q);
      if (!regex) return items;
      return applyPatternFilter(items, regex, fields, maxResults);
    }

    // Glob mode
    if (isGlob(q)) {
      const regex = globToRegex(q);
      return applyPatternFilter(items, regex, fields, maxResults);
    }

    // Standard MiniSearch mode
    if (!indexRef.current) return items;
    const hits = indexRef.current.search(q, { limit: maxResults });
    return hits.map(h => itemById[h.id]).filter(Boolean);
  }, [query, items, itemById, fields, maxResults]);

  return { results, query, setQuery, isSearching };
}

export default useHPSearch;
