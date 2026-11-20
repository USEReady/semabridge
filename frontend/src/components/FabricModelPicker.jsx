import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../utils/api';
import {
  classifyModelDiscovery,
  modelPickerOptions,
  parseModelList,
  selectedModelNames,
} from '../utils/fabricProjectState';
import ConnectionsPanel from './ConnectionsPanel';

/**
 * Fabric model picker (edit mode): the workspace's semantic models, listed with
 * the project's saved connection, instead of a free-text model box.
 *
 * value/onChange use the same comma-separated string the form already saves
 * (source.models). Saved models that are no longer in the workspace stay
 * selected and are marked "not in workspace" -- nothing is guessed. A Fabric
 * re-authentication answer shows "Sign in again" (ConnectionsPanel device-code
 * flow) and reloads after it closes. No tokens are stored here.
 */
export default function FabricModelPicker({ workspaceId, connectionId, value, onChange, inputStyle, labelStyle }) {
  const [loaded, setLoaded] = useState({ key: null, state: 'idle', models: [], message: '' });
  const [signInOpen, setSignInOpen] = useState(false);
  const [nonce, setNonce] = useState(0);
  const requestKey = `${workspaceId || ''}|${connectionId || ''}|${nonce}`;

  useEffect(() => {
    if (!workspaceId) return undefined;
    let cancelled = false;
    api.discoverFabricModels(workspaceId, connectionId)
      .then((result) => { if (!cancelled) setLoaded({ key: requestKey, ...classifyModelDiscovery({ result }) }); })
      .catch((error) => { if (!cancelled) setLoaded({ key: requestKey, ...classifyModelDiscovery({ error }) }); });
    return () => { cancelled = true; };
  }, [workspaceId, connectionId, requestKey]);

  // idle / loading are derived (no setState inside the effect body)
  const discovery = !workspaceId
    ? { state: 'idle', models: [], message: 'Choose the project workspace to list its models.' }
    : loaded.key === requestKey ? loaded : { state: 'loading', models: [], message: '' };

  const rows = useMemo(() => modelPickerOptions(discovery.models, parseModelList(value)), [discovery.models, value]);

  const toggle = (row) => {
    const next = rows.map((r) => (r === row ? { ...r, checked: !r.checked } : r));
    onChange(selectedModelNames(next).join(', '));
  };

  return (
    <div>
      <label style={labelStyle}>Models</label>
      {discovery.state === 'loading' && <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Loading semantic models…</div>}
      {discovery.state === 'reauth' && (
        <div role="alert" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, padding: 8, border: '1px solid var(--border-main)', borderRadius: 6 }}>
          <span style={{ flex: 1 }}>{discovery.message}</span>
          <button type="button" onClick={() => setSignInOpen(true)} style={{ cursor: 'pointer' }}>Sign in again</button>
        </div>
      )}
      {['error', 'empty', 'idle'].includes(discovery.state) && discovery.message && (
        <div style={{ fontSize: 12, color: discovery.state === 'error' ? 'var(--color-error)' : 'var(--text-tertiary)' }}>{discovery.message}</div>
      )}
      <div role="listbox" aria-label="Fabric models" style={{ ...(inputStyle || {}), height: 'auto', maxHeight: 220, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {rows.map((row) => (
          <label key={`${row.id}-${row.missing ? 'missing' : 'ws'}`} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <input type="checkbox" checked={row.checked} onChange={() => toggle(row)} />
            <span>{row.name}</span>
            {row.missing && <span style={{ color: 'var(--color-error)', fontSize: 11 }}>(not in workspace)</span>}
          </label>
        ))}
      </div>
      <ConnectionsPanel
        isOpen={signInOpen}
        selectedConnector="fabric"
        onClose={() => { setSignInOpen(false); setNonce((n) => n + 1); }}
      />
    </div>
  );
}
