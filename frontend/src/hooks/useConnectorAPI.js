import { useState, useEffect, useCallback } from 'react';
import FlexSearch from 'flexsearch';

export function useConnectorAPI(workspaceId, sourcePlatform) {
  const [data, setData] = useState([]);
  const [index, setIndex] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchSemanticObjects = useCallback(async () => {
    if (!sourcePlatform) return;
    setLoading(true);
    setError(null);

    let apiUrl = '/api/discovery/';
    
    // Choose API endpoint based on platform type or sync
    if (sourcePlatform === 'fabric') {
      apiUrl += 'fabric'; // ?workspace_id could be added if needed
    } else if (sourcePlatform === 'snowflake') {
      apiUrl += 'snowflake';
    } else {
       apiUrl += 'semantic'; // fallback to both if not targeted
    }

    try {
      const resp = await fetch(apiUrl);
      if (!resp.ok) throw new Error(`API returned ${resp.status}`);
      const result = await resp.json();
      
      const objectsToIndex = result.fabric || result.snowflake || result || [];
      setData(objectsToIndex);

      // Build flexsearch Index
      const docIndex = new FlexSearch.Document({
        document: {
          id: "id",
          index: ["name", "description", "type"],
        },
        tokenize: "forward",
      });

      objectsToIndex.forEach(doc => {
        docIndex.add(doc);
      });
      setIndex(docIndex);
      
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [sourcePlatform, workspaceId]);

  useEffect(() => {
    fetchSemanticObjects();
  }, [fetchSemanticObjects]);

  const search = useCallback((query) => {
    if (!index || !query) return data;
    
    // Search across fields
    const results = index.search(query, { enrich: true });
    
    // Reconstruct results array. Flexsearch returns arrays of matches per field.
    const searchMap = new Map();
    results.forEach(resField => {
      resField.result.forEach(r => {
        searchMap.set(r.id, r.doc);
      });
    });

    return Array.from(searchMap.values());
  }, [index, data]);

  return { data, search, loading, error, refresh: fetchSemanticObjects };
}
