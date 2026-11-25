import React, { useState, useEffect, useCallback } from 'react';
import { RefreshCw, AlertCircle, AlertTriangle, CheckCircle2, Monitor, Upload, ExternalLink } from 'lucide-react';
import { api } from '../../utils/api';

export default function PbiDesktopPicker({
  selectedInstance,
  onSelectInstance,
  onSwitchToPbix,
  preselectedServer = null,
  preselectedDatabase = null,
}) {
  const [instances, setInstances] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [fileClosedWarning, setFileClosedWarning] = useState('');
  const [lastRefreshed, setLastRefreshed] = useState(null);

  const fetchInstances = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // If pre-selected server & database are provided (e.g. from External Tools launcher)
      if (preselectedServer && preselectedDatabase) {
        try {
          const checkRes = await api.get('/sources/pbi-desktop/validate-preselection', {
            params: { server: preselectedServer, database: preselectedDatabase },
          });
          if (checkRes?.valid && checkRes?.instance) {
            setFileClosedWarning('');
            onSelectInstance(checkRes.instance);
          } else {
            setFileClosedWarning("That file isn't open anymore. Please select an open Power BI Desktop model from the list below.");
          }
        } catch (checkErr) {
          const detail = checkErr?.response?.data?.detail || checkErr?.message || '';
          if (detail.toLowerCase().includes('non-local')) {
            setError(detail);
            setLoading(false);
            return;
          }
          setFileClosedWarning("That file isn't open anymore. Please select an open Power BI Desktop model from the list below.");
        }
      }

      const res = await api.get('/sources/pbi-desktop/instances');
      const list = res?.instances || [];
      setInstances(list);
      setLastRefreshed(new Date());

      // If there was a previously selected instance, check if it's still running
      if (selectedInstance) {
        const stillOpen = list.find(
          (inst) => inst.port === selectedInstance.port && inst.database_name === selectedInstance.database_name
        );
        if (!stillOpen) {
          // File closed
          setFileClosedWarning("That file isn't open anymore. Please select an open Power BI Desktop model from the list below.");
          onSelectInstance(null, { fileClosed: true });
        } else {
          // Update instance with latest live status
          onSelectInstance(stillOpen);
        }
      } else if (!preselectedServer && list.length === 1) {
        // Auto-select if only 1 instance is open and not pre-selected
        onSelectInstance(list[0]);
      }
    } catch (err) {
      setError(err?.message || 'Failed to detect running Power BI Desktop instances.');
    } finally {
      setLoading(false);
    }
  }, [selectedInstance, onSelectInstance, preselectedServer, preselectedDatabase]);

  useEffect(() => {
    fetchInstances();
  }, [fetchInstances]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            Open Power BI Desktop Models
          </h3>
          <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '4px 0 0' }}>
            Select an active semantic model from a running Power BI Desktop session.
          </p>
        </div>
        <button
          type="button"
          onClick={fetchInstances}
          disabled={loading}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '6px 12px',
            borderRadius: 8,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface-elevated, var(--bg-surface))',
            color: 'var(--text-primary)',
            fontSize: 12,
            fontWeight: 500,
            cursor: loading ? 'not-allowed' : 'pointer',
          }}
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          {loading ? 'Detecting...' : 'Refresh'}
        </button>
      </div>

      {fileClosedWarning && (
        <div style={{
          padding: '12px 16px',
          borderRadius: 8,
          background: 'rgba(245, 158, 11, 0.1)',
          border: '1px solid var(--color-warning, #f59e0b)',
          color: 'var(--color-warning, #f59e0b)',
          fontSize: 12,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <AlertTriangle size={16} />
          <span>{fileClosedWarning}</span>
        </div>
      )}

      {error && (
        <div style={{
          padding: '12px 16px',
          borderRadius: 8,
          background: 'rgba(239, 68, 68, 0.1)',
          border: '1px solid var(--color-error, #ef4444)',
          color: 'var(--color-error, #ef4444)',
          fontSize: 12,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <AlertCircle size={15} />
          <span>{error}</span>
        </div>
      )}

      {!loading && instances.length === 0 && (
        <div style={{
          padding: '24px 20px',
          borderRadius: 12,
          border: '1px dashed var(--border-main)',
          background: 'var(--bg-surface)',
          textAlign: 'center',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 12,
        }}>
          <Monitor size={32} style={{ color: 'var(--text-tertiary)' }} />
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
              No running Power BI Desktop instances found
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
              Make sure Power BI Desktop is open with a model loaded, or upload a .pbix file directly.
            </div>
          </div>
          {onSwitchToPbix && (
            <button
              type="button"
              onClick={onSwitchToPbix}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '8px 16px',
                borderRadius: 8,
                background: 'var(--color-primary, #3b82f6)',
                color: '#fff',
                fontSize: 12,
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
                marginTop: 4,
              }}
            >
              <Upload size={14} />
              Upload PBIX File Instead
            </button>
          )}
        </div>
      )}

      {instances.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {instances.map((inst) => {
            const isSelected =
              selectedInstance &&
              selectedInstance.port === inst.port &&
              selectedInstance.database_name === inst.database_name;

            return (
              <div
                key={`${inst.port}-${inst.database_name}`}
                onClick={() => onSelectInstance(inst)}
                style={{
                  padding: '16px 18px',
                  borderRadius: 12,
                  border: isSelected ? '2px solid var(--color-primary, #3b82f6)' : '1px solid var(--border-main)',
                  background: isSelected ? 'var(--bg-surface-elevated, var(--bg-surface))' : 'var(--bg-surface)',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Monitor size={16} color={isSelected ? 'var(--color-primary, #3b82f6)' : 'var(--text-tertiary)'} />
                    <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
                      {inst.file_name}
                    </span>
                    {inst.has_unsaved_changes && (
                      <span style={{
                        fontSize: 11,
                        padding: '2px 8px',
                        borderRadius: 12,
                        background: 'rgba(245, 158, 11, 0.15)',
                        color: 'var(--color-warning, #f59e0b)',
                        border: '1px solid rgba(245, 158, 11, 0.3)',
                        fontWeight: 600,
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                      }}>
                        <AlertTriangle size={11} />
                        Includes unsaved changes
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                      Port: {inst.port}
                    </span>
                    {isSelected && <CheckCircle2 size={16} color="var(--color-primary, #3b82f6)" />}
                  </div>
                </div>

                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', wordBreak: 'break-all' }}>
                  {inst.file_path || (inst.file_method === 'window_title' ? 'Detected from window title (unsaved file)' : 'Local Analysis Services Workspace')}
                </div>

                {inst.is_live_connected && (
                  <div style={{
                    padding: '8px 12px',
                    borderRadius: 8,
                    background: 'rgba(59, 130, 246, 0.1)',
                    border: '1px solid rgba(59, 130, 246, 0.25)',
                    color: 'var(--color-info, #3b82f6)',
                    fontSize: 11,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    marginTop: 4,
                  }}>
                    <AlertCircle size={13} />
                    <span>Live-connected report (no local model). Please use the Fabric connector instead.</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
