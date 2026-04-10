/**
 * Pre-Sync Summary Component
 * 
 * Displays:
 * - Connection breadcrumb (Source → Target)
 * - Table audit list (with highlighting for system tables)
 * - Fabric token validity status
 * - Pre-sync warnings and validations
 */

import { useState, useEffect } from 'react';
import { AlertCircle, CheckCircle, ChevronDown, ChevronUp, Database, Lock } from 'lucide-react';
import { api } from '../../utils/api';

const SYSTEM_TABLE_KEYWORDS = ['_SEMANTIC', '_CREDENTIALS', '_SNAPSHOTS', '_RUNS', '_LOGS', 'SEMABRIDGE'];

function isSystemTable(tableName) {
  const upper = String(tableName || '').toUpperCase();
  return SYSTEM_TABLE_KEYWORDS.some(keyword => upper.includes(keyword));
}

export default function PreSyncSummary({ config, projectName }) {
  const [tokenValid, setTokenValid] = useState(null);
  const [tokenLoading, setTokenLoading] = useState(true);
  const [expandedAudit, setExpandedAudit] = useState(false);
  const [extractedTables, setExtractedTables] = useState([]);
  const [loadingTables, setLoadingTables] = useState(false);

  // Verify Fabric token validity
  useEffect(() => {
    const checkToken = async () => {
      setTokenLoading(true);
      try {
        const result = await api.validateLive({
          source_type: config?.source?.type,
          workspace_id: config?.source?.workspace_id,
        });
        const hasTokenError = result?.errors?.some(
          e => e.message && (e.message.includes('401') || e.message.includes('expired'))
        );
        setTokenValid(!hasTokenError);
      } catch (err) {
        console.error('Token validation error:', err);
        setTokenValid(false);
      } finally {
        setTokenLoading(false);
      }
    };

    if (config?.source?.type === 'fabric') {
      checkToken();
    }
  }, [config?.source?.type, config?.source?.workspace_id]);

  // Fetch extracted table list for audit
  const loadTableAudit = async () => {
    setLoadingTables(true);
    try {
      const database = config?.source?.database || 'N/A';
      const schema = config?.source?.schema || 'N/A';
      const models = config?.source?.models || [];
      
      // In a real implementation, this would call the backend to fetch actual extracted tables
      // For now, we'll show the configured models
      setExtractedTables(models);
    } catch (err) {
      console.error('Failed to load table audit:', err);
      setExtractedTables([]);
    } finally {
      setLoadingTables(false);
    }
  };

  useEffect(() => {
    if (expandedAudit && extractedTables.length === 0 && !loadingTables) {
      loadTableAudit();
    }
  }, [expandedAudit]);

  // Extract source/target info
  const sourceDb = config?.source?.database || 'Unknown';
  const sourceSchema = config?.source?.schema || 'PUBLIC';
  const targetWorkspace = config?.target?.workspace_name || 'Fabric Workspace';
  const sourceType = (config?.source?.type || 'snowflake').toUpperCase();
  const targetType = (config?.target?.type || 'fabric').toUpperCase();

  const systemTablesCount = extractedTables.filter(isSystemTable).length;
  const regularTablesCount = extractedTables.length - systemTablesCount;

  return (
    <div style={{
      borderRadius: 10,
      border: '1px solid var(--border-main)',
      background: 'var(--bg-surface)',
      padding: 16,
      marginBottom: 16,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <Database size={16} style={{ color: 'var(--accent-blue)' }} />
        <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
          Pre-Sync Summary
        </h3>
      </div>

      {/* Connection Breadcrumb */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '12px 0',
        borderBottom: '1px solid var(--border-main)',
        marginBottom: 12,
        fontSize: 12,
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 10px',
          borderRadius: 6,
          background: 'var(--bg-main)',
          color: 'var(--text-secondary)',
        }}>
          <span style={{ fontWeight: 500 }}>{sourceType}</span>
          <span style={{ color: 'var(--text-tertiary)', fontSize: 11 }}>
            {sourceDb}.{sourceSchema}
          </span>
        </div>
        <span style={{ color: 'var(--text-tertiary)' }}>→</span>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 10px',
          borderRadius: 6,
          background: 'var(--accent-blue)08',
          border: '1px solid var(--accent-blue)20',
          color: 'var(--accent-blue)',
        }}>
          <span style={{ fontWeight: 500 }}>{targetType}</span>
          <span style={{ color: 'var(--accent-blue)', opacity: 0.8, fontSize: 11 }}>
            {targetWorkspace}
          </span>
        </div>
      </div>

      {/* Fabric Token Status */}
      {config?.source?.type === 'fabric' && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '10px 12px',
          borderRadius: 8,
          background: tokenLoading ? 'var(--bg-main)' : tokenValid ? 'var(--color-success)08' : 'var(--color-danger)08',
          border: `1px solid ${tokenLoading ? 'var(--border-main)' : tokenValid ? 'var(--color-success)30' : 'var(--color-danger)30'}`,
          marginBottom: 12,
        }}>
          {tokenLoading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
              <div style={{ width: 12, height: 12, borderRadius: '50%', border: '2px solid var(--text-tertiary)', borderTopColor: 'var(--accent-blue)', animation: 'spin 1s linear infinite' }} />
              <span style={{ color: 'var(--text-secondary)' }}>Checking Fabric token…</span>
            </div>
          ) : tokenValid ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
              <CheckCircle size={14} style={{ color: 'var(--color-success)' }} />
              <span style={{ color: 'var(--color-success)', fontWeight: 500 }}>Fabric token valid</span>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
              <AlertCircle size={14} style={{ color: 'var(--color-danger)' }} />
              <span style={{ color: 'var(--color-danger)', fontWeight: 500 }}>Token expired or invalid</span>
              <span style={{ color: 'var(--color-danger)', opacity: 0.7, fontSize: 11 }}>
                (Sync may fail at Step 9)
              </span>
            </div>
          )}
        </div>
      )}

      {/* Table Audit */}
      <div style={{
        border: '1px solid var(--border-main)',
        borderRadius: 8,
        overflow: 'hidden',
      }}>
        <button
          onClick={() => setExpandedAudit(!expandedAudit)}
          style={{
            width: '100%',
            padding: '12px',
            background: expandedAudit ? 'var(--bg-main)' : 'transparent',
            border: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            fontSize: 12,
            fontWeight: 600,
            color: 'var(--text-primary)',
            transition: 'background 0.2s',
          }}
          onMouseEnter={e => { if (!expandedAudit) e.currentTarget.style.background = 'var(--bg-main)20'; }}
          onMouseLeave={e => { if (!expandedAudit) e.currentTarget.style.background = 'transparent'; }}
        >
          <Lock size={14} style={{ color: 'var(--accent-blue)' }} />
          <span>Security Audit: {regularTablesCount} table{regularTablesCount !== 1 ? 's' : ''}</span>
          {systemTablesCount > 0 && (
            <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--color-warning)', fontWeight: 500 }}>
              {systemTablesCount} system table{systemTablesCount !== 1 ? 's' : ''} blocked
            </span>
          )}
          {expandedAudit ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>

        {expandedAudit && (
          <div style={{
            padding: '12px',
            borderTop: '1px solid var(--border-main)',
            background: 'var(--bg-main)',
            maxHeight: 300,
            overflowY: 'auto',
          }}>
            {loadingTables ? (
              <div style={{ textAlign: 'center', padding: '16px', color: 'var(--text-tertiary)', fontSize: 12 }}>
                Loading table list…
              </div>
            ) : extractedTables.length === 0 ? (
              <div style={{ padding: '16px', color: 'var(--text-tertiary)', fontSize: 12 }}>
                No tables configured. Configure source.models in your project settings.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {extractedTables.map((table, idx) => {
                  const isSystem = isSystemTable(table);
                  return (
                    <div
                      key={idx}
                      style={{
                        padding: '8px 10px',
                        borderRadius: 6,
                        background: isSystem ? 'var(--color-danger)08' : 'var(--accent-blue)08',
                        border: `1px solid ${isSystem ? 'var(--color-danger)20' : 'var(--accent-blue)10'}`,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        fontSize: 11,
                      }}
                    >
                      {isSystem && (
                        <AlertCircle size={12} style={{ color: 'var(--color-danger)', flexShrink: 0 }} />
                      )}
                      <span style={{ color: isSystem ? 'var(--color-danger)' : 'var(--accent-blue)', fontFamily: 'monospace' }}>
                        {table}
                      </span>
                      {isSystem && (
                        <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--color-danger)', opacity: 0.7 }}>
                          system
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer info */}
      <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
        ℹ️ Tables are fetched from the configured project scope. System tables (SEMABRIDGE_*, *_CREDENTIALS, etc.) are automatically excluded. 
        <strong> Verify the Fabric token is valid before clicking Sync Now.</strong>
      </div>
    </div>
  );
}
