/**
 * useHPSearch — High-Performance Search Hook
 *
 * Provides fast indexing + search over large datasets (100k+ objects)
 * using MiniSearch with regex and strict-prefix post-filters layered on top.
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
 *   p:term      → strict prefix mode (applied as post-filter)
 *   anything else → standard MiniSearch fuzzy + prefix search
 *
 * Returns:
 *   results     — filtered/ranked array from `items`
 *   query       — current query string
 *   setQuery    — setter for query string
 *   isSearching — true while index is being built
 */

import { useState, useEffect, useMemo } from 'react';
import MiniSearch from 'minisearch';
import { getSmartQueryMode } from '../components/common/smartSearchQuery.js';

const DEFAULT_OPTS = {
  idField: 'id',
  prefix: true,
  fuzzy: 0.15,
  boost: {},
  maxResults: 500,
};

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

function applyPrefixFilter(items, prefix, fields, maxResults) {
  const out = [];
  if (!prefix) return items.slice(0, maxResults);

  for (const item of items) {
    if (out.length >= maxResults) break;
    for (const field of fields) {
      const val = item[field];
      const words = String(val ?? '').toLowerCase().split(/\s+/).filter(Boolean);
      if (words.some(word => word.startsWith(prefix))) {
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
  const [searchIndex, setSearchIndex] = useState(null);

  // Build MiniSearch index whenever items or fields change.
  // Debounced by 150 ms so rapid item-list changes (e.g. live filter updates)
  // don't rebuild the index on every intermediate render.
  useEffect(() => {
    let cancelled = false;

    const tid = setTimeout(() => {
      if (cancelled) return;

      if (!items || items.length === 0) {
        setSearchIndex(null);
        setIsSearching(false);
        return;
      }

      setIsSearching(true);

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
      setSearchIndex(ms);
      setIsSearching(false);
    }, 150); // 150 ms debounce — avoids blocking on rapid item changes

    return () => {
      cancelled = true;
      clearTimeout(tid);
    };
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
    const mode = getSmartQueryMode(q);

    if (!q || !items || items.length === 0) return items ?? [];

    if (mode.mode === 'prefix') {
      return applyPrefixFilter(items, mode.term?.trim().toLowerCase() || '', fields, maxResults);
    }

    if (mode.mode === 'regex') {
      try {
        const regex = new RegExp(mode.pattern, mode.flags);
        return applyPatternFilter(items, regex, fields, maxResults);
      } catch {
        return items;
      }
    }

    // Standard MiniSearch mode
    if (!searchIndex) return items;
    const hits = searchIndex.search(q, { limit: maxResults });
    return hits.map(h => itemById[h.id]).filter(Boolean);
  }, [query, items, itemById, fields, maxResults, searchIndex]);

  return { results, query, setQuery, isSearching };
}

export default useHPSearch;
