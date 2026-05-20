import { useState, useEffect, useCallback } from 'react';
import { api } from '../utils/api';

/**
 * useConnectorAccounts
 * Fetches Fabric / Snowflake / Databricks accounts whenever step 2 opens.
 * Also owns workspace discovery triggered by account-id changes.
 */
export function useConnectorAccounts({
  step,
  sourceConnector,
  targetConnectors,
  selectedConnectionId,
  snowflakeAccountId,
  databricksAccountId,
  setSelectedConnectionId,
  setFabricAccountId,
  setSnowflakeAccountId,
  setDatabricksAccountId,
  setFabricWorkspaceId,
  setAllWorkspacesFromApi,
  setWorkspaces,
}) {
  const [fabricAccounts, setFabricAccounts]       = useState([]);
  const [snowflakeAccounts, setSnowflakeAccounts] = useState([]);
  const [databricksAccounts, setDatabricksAccounts] = useState([]);
  const [isRefreshingWorkspaces, setIsRefreshingWorkspaces] = useState(false);

  const fetchFabricWorkspaces = useCallback(async (accountId) => {
    if (!accountId) {
      setAllWorkspacesFromApi([]);
      setWorkspaces([]);
      return;
    }
    setIsRefreshingWorkspaces(true);
    try {
      const listData = await api.fabricListWorkspaces(accountId);
      const discovered = Array.isArray(listData?.workspaces) ? listData.workspaces : [];
      const apiWorkspaces = discovered
        .map(ws => ({
          id: ws.id || ws.workspace_id,
          name: ws.name || ws.displayName || ws.workspace_name || ws.id || ws.workspace_id,
          displayName: ws.name || ws.displayName || ws.workspace_name,
          type: ws.type,
        }))
        .filter(ws => ws.id);

      const deduped = [];
      const seen = new Set();
      for (const ws of apiWorkspaces) {
        if (seen.has(ws.id)) continue;
        seen.add(ws.id);
        deduped.push(ws);
      }
      console.log('[SemaBridge] Resolved workspace list:', deduped);
      setAllWorkspacesFromApi(deduped);
    } catch {
      setAllWorkspacesFromApi([]);
    } finally {
      setIsRefreshingWorkspaces(false);
    }
  }, [setAllWorkspacesFromApi, setWorkspaces]);

  // Fetch accounts when entering Step 2
  useEffect(() => {
    const needsFabric     = sourceConnector === 'fabric'     || targetConnectors.has('fabric');
    const needsSnowflake  = sourceConnector === 'snowflake'  || targetConnectors.has('snowflake');
    const needsDatabricks = sourceConnector === 'databricks' || targetConnectors.has('databricks');

    if (step !== 2) return;

    if (needsFabric) {
      api.getAccounts('FABRIC').then(res => {
        const list = Array.isArray(res) ? res : (res?.accounts || []);
        setFabricAccounts(list);
        if (!list.length) { setSelectedConnectionId(''); setFabricAccountId(''); return; }
        const hasSelection = list.some(acc => String(acc?.id || '') === String(selectedConnectionId || ''));
        const nextId = hasSelection ? selectedConnectionId : String(list[0]?.id || '');
        if (nextId) { setSelectedConnectionId(nextId); setFabricAccountId(nextId); }
      }).catch(e => console.warn('[SemaBridge] Failed to fetch Fabric Accounts', e));
    }

    if (needsSnowflake) {
      api.getAccounts('SNOWFLAKE').then(res => {
        const list = Array.isArray(res) ? res : (res?.accounts || []);
        setSnowflakeAccounts(list);
        if (list.length > 0 && !snowflakeAccountId) setSnowflakeAccountId(String(list[0]?.id || ''));
      }).catch(e => console.warn('[SemaBridge] Failed to fetch Snowflake Accounts', e));
    }

    if (needsDatabricks) {
      api.getAccounts('DATABRICKS').then(res => {
        const list = Array.isArray(res) ? res : (res?.accounts || []);
        setDatabricksAccounts(list);
        if (list.length > 0 && !databricksAccountId) setDatabricksAccountId(String(list[0]?.id || ''));
      }).catch(e => console.warn('[SemaBridge] Failed to fetch Databricks Accounts', e));
    }
  }, [step, sourceConnector, targetConnectors, selectedConnectionId]);  // eslint-disable-line

  // Re-fetch workspaces when connection account changes
  useEffect(() => {
    const needsFabric = sourceConnector === 'fabric' || targetConnectors.has('fabric');
    if (step !== 2 || !needsFabric) return;

    setAllWorkspacesFromApi([]);
    setWorkspaces([]);
    setFabricWorkspaceId('');
    if (!selectedConnectionId) return;

    setFabricAccountId(selectedConnectionId);
    fetchFabricWorkspaces(selectedConnectionId);
  }, [step, sourceConnector, targetConnectors, selectedConnectionId, fetchFabricWorkspaces]); // eslint-disable-line

  return {
    fabricAccounts,
    snowflakeAccounts,
    databricksAccounts,
    isRefreshingWorkspaces,
    fetchFabricWorkspaces,
  };
}
