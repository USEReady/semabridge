import { useState, useEffect, useCallback, useRef } from 'react';
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
  const resolveAccountId = useCallback((account) => (
    String(account?.id || account?.identity_id || account?.account_id || '')
  ), []);

  const [fabricAccounts, setFabricAccounts]       = useState([]);
  const [snowflakeAccounts, setSnowflakeAccounts] = useState([]);
  const [databricksAccounts, setDatabricksAccounts] = useState([]);
  const [isRefreshingWorkspaces, setIsRefreshingWorkspaces] = useState(false);
  const [workspaceDiscoveryError, setWorkspaceDiscoveryError] = useState('');
  const workspaceFetchSeqRef = useRef(0);

  const fetchFabricWorkspaces = useCallback(async (accountId) => {
    const resolvedAccountId = String(accountId || '').trim();
    const requestSeq = ++workspaceFetchSeqRef.current;

    if (!resolvedAccountId) {
      setAllWorkspacesFromApi([]);
      setWorkspaces([]);
      setWorkspaceDiscoveryError('');
      console.warn('[SemaBridge][FabricDiscovery] hook:skip-empty-account', { requestSeq });
      return;
    }

    setWorkspaceDiscoveryError('');
    setIsRefreshingWorkspaces(true);
    try {
      const listData = await api.fabricListWorkspaces(resolvedAccountId);

      if (requestSeq !== workspaceFetchSeqRef.current) {
        console.info('[SemaBridge][FabricDiscovery] hook:stale-response-ignored', {
          accountId: resolvedAccountId,
          requestSeq,
          latestSeq: workspaceFetchSeqRef.current,
        });
        return;
      }

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
      console.info('[SemaBridge][FabricDiscovery] hook:success', {
        accountId: resolvedAccountId,
        requestSeq,
        discoveredCount: discovered.length,
        resolvedCount: deduped.length,
      });
      setWorkspaceDiscoveryError('');
      setAllWorkspacesFromApi(deduped);
    } catch (err) {
      if (requestSeq !== workspaceFetchSeqRef.current) {
        console.info('[SemaBridge][FabricDiscovery] hook:stale-error-ignored', {
          accountId: resolvedAccountId,
          requestSeq,
          latestSeq: workspaceFetchSeqRef.current,
          error: err?.message || String(err),
        });
        return;
      }

      console.error('[SemaBridge][FabricDiscovery] hook:error', {
        accountId: resolvedAccountId,
        requestSeq,
        message: err?.message || 'Unknown error',
        status: err?.status,
        payload: err?.payload,
      });
      setWorkspaceDiscoveryError(err?.message || 'Failed to discover Fabric workspaces for the selected account.');
      setAllWorkspacesFromApi([]);
    } finally {
      if (requestSeq === workspaceFetchSeqRef.current) {
        setIsRefreshingWorkspaces(false);
      }
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
        const hasSelection = list.some(acc => resolveAccountId(acc) === String(selectedConnectionId || ''));
        const nextId = hasSelection ? selectedConnectionId : resolveAccountId(list[0]);
        if (nextId) { setSelectedConnectionId(nextId); setFabricAccountId(nextId); }
      }).catch(e => console.warn('[SemaBridge] Failed to fetch Fabric Accounts', e));
    }

    if (needsSnowflake) {
      api.getAccounts('SNOWFLAKE').then(res => {
        const list = Array.isArray(res) ? res : (res?.accounts || []);
        setSnowflakeAccounts(list);
        if (!list.length) { setSnowflakeAccountId(''); return; }
        const hasSelection = list.some(acc => resolveAccountId(acc) === String(snowflakeAccountId || ''));
        if (!hasSelection) setSnowflakeAccountId(resolveAccountId(list[0]));
      }).catch(e => console.warn('[SemaBridge] Failed to fetch Snowflake Accounts', e));
    }

    if (needsDatabricks) {
      api.getAccounts('DATABRICKS').then(res => {
        const list = Array.isArray(res) ? res : (res?.accounts || []);
        setDatabricksAccounts(list);
        if (!list.length) { setDatabricksAccountId(''); return; }
        const hasSelection = list.some(acc => resolveAccountId(acc) === String(databricksAccountId || ''));
        if (!hasSelection) setDatabricksAccountId(resolveAccountId(list[0]));
      }).catch(e => console.warn('[SemaBridge] Failed to fetch Databricks Accounts', e));
    }
  }, [
    step,
    sourceConnector,
    targetConnectors,
    selectedConnectionId,
    snowflakeAccountId,
    databricksAccountId,
    resolveAccountId,
  ]);  // eslint-disable-line

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
    workspaceDiscoveryError,
  };
}
