/**
 * NotificationLogsPage — delivery log viewer with filtering and retry.
 */

import { useState, useEffect } from 'react';
import { RefreshCw, ChevronDown, ChevronUp, AlertCircle } from 'lucide-react';
import PageHeader from '../../components/common/PageHeader';
import { api } from '../../utils/api';
import { NotificationStatusBadge, LevelBadge, ChannelTypeBadge } from '../../components/notifications';
import NotificationSettingsTabs from '../../components/notifications/NotificationSettingsTabs';

const STATUSES = ['delivered', 'failed', 'retrying', 'dead'];
const LEVELS = [
  { value: 1, label: 'SYNC_RESULT' },
  { value: 2, label: 'CRITICAL' },
  { value: 4, label: 'ERROR' },
  { value: 8, label: 'WARNING' },
  { value: 16, label: 'INFO' },
  { value: 32, label: 'DEBUG' },
];

export default function NotificationLogsPage() {
  const [logs, setLogs] = useState([]);
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [page, setPage] = useState(0);
  const [pageSize] = useState(50);

  const [filters, setFilters] = useState({
    channel_id: '',
    status: [],
    level: null,
    since: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().split('T')[0],
    until: new Date().toISOString().split('T')[0],
  });

  const [retrying, setRetrying] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadData();
  }, [filters, page]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [logsResp, channelsResp] = await Promise.all([
        api.getNotificationLogs({
          skip: page * pageSize,
          limit: pageSize,
          ...filters,
          status: filters.status.length > 0 ? filters.status.join(',') : undefined,
        }),
        api.getNotificationChannels(0, 100),
      ]);
      setLogs(Array.isArray(logsResp) ? logsResp : logsResp.items || []);
      setChannels(Array.isArray(channelsResp) ? channelsResp : channelsResp.items || []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const retry = async (logId) => {
    setRetrying(logId);
    try {
      await api.retryNotificationLog(logId);
      await loadData();
    } catch (e) {
      setError(e.message);
    } finally {
      setRetrying(null);
    }
  };

  const getChannelName = (id) => channels.find((c) => c.id === id)?.name || id;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '20px', minHeight: '100vh' }}>
      <PageHeader
        title="Delivery Logs"
        subtitle="View notification delivery history with filtering and retry options"
      />
      <NotificationSettingsTabs />

      {error && (
        <div style={{
          padding: 12,
          borderRadius: 8,
          background: 'rgba(214, 48, 49, 0.12)',
          color: '#d63031',
          fontSize: 13,
        }}>
          {error}
        </div>
      )}

      {/* Filters */}
      <div style={{
        padding: 16,
        border: '1px solid var(--border-main)',
        borderRadius: 12,
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: 12,
      }}>
        <select
          value={filters.channel_id}
          onChange={(e) => { setFilters({ ...filters, channel_id: e.target.value }); setPage(0); }}
          style={{
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface-raised)',
            color: 'var(--text-primary)',
            fontSize: 13,
          }}
        >
          <option value="">All channels</option>
          {channels.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>

        <select
          multiple
          value={filters.status}
          onChange={(e) => {
            setFilters({
              ...filters,
              status: Array.from(e.target.selectedOptions, (o) => o.value),
            });
            setPage(0);
          }}
          style={{
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface-raised)',
            color: 'var(--text-primary)',
            fontSize: 13,
          }}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <input
          type="date"
          value={filters.since}
          onChange={(e) => { setFilters({ ...filters, since: e.target.value }); setPage(0); }}
          style={{
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface-raised)',
            color: 'var(--text-primary)',
            fontSize: 13,
          }}
        />

        <input
          type="date"
          value={filters.until}
          onChange={(e) => { setFilters({ ...filters, until: e.target.value }); setPage(0); }}
          style={{
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface-raised)',
            color: 'var(--text-primary)',
            fontSize: 13,
          }}
        />

        <button
          onClick={() => setFilters({
            channel_id: '',
            status: [],
            level: null,
            since: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().split('T')[0],
            until: new Date().toISOString().split('T')[0],
          })}
          style={{
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          Reset
        </button>
      </div>

      {/* Logs Table */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>Loading logs...</div>
      ) : logs.length === 0 ? (
        <div style={{
          padding: 40,
          textAlign: 'center',
          border: '1px dashed var(--border-main)',
          borderRadius: 12,
        }}>
          No logs found for this period.
        </div>
      ) : (
        <div style={{ border: '1px solid var(--border-main)', borderRadius: 12, overflow: 'hidden' }}>
          {logs.map((log) => (
            <div key={log.id} style={{ borderBottom: '1px solid var(--border-main)' }}>
              <div
                onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
                style={{
                  padding: 12,
                  display: 'grid',
                  gridTemplateColumns: 'auto 1fr auto auto auto auto auto',
                  gap: 12,
                  alignItems: 'center',
                  cursor: 'pointer',
                  background: 'var(--bg-surface)',
                  transition: 'background-color 0.15s',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--bg-surface-hover)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'var(--bg-surface)'; }}
              >
                <button style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--text-secondary)' }}>
                  {expandedId === log.id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {new Date(log.created_at).toLocaleString()}
                </div>
                <div style={{ fontSize: 12 }}>
                  {getChannelName(log.channel_id)}
                </div>
                <LevelBadge level="ERROR" />
                <NotificationStatusBadge status={log.status} />
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {log.duration_ms}ms
                </div>
                {(log.status === 'failed' || log.status === 'dead') && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      retry(log.id);
                    }}
                    disabled={retrying === log.id}
                    style={{
                      padding: '4px 8px',
                      fontSize: 12,
                      borderRadius: 4,
                      border: '1px solid var(--accent-blue)',
                      background: 'rgba(88, 166, 255, 0.12)',
                      color: 'var(--accent-blue)',
                      cursor: retrying === log.id ? 'not-allowed' : 'pointer',
                      opacity: retrying === log.id ? 0.6 : 1,
                    }}
                  >
                    {retrying === log.id ? '...' : 'Retry'}
                  </button>
                )}
              </div>

              {expandedId === log.id && (
                <div style={{
                  padding: 16,
                  background: 'var(--bg-surface-raised)',
                  borderTop: '1px solid var(--border-main)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 12,
                }}>
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--text-secondary)' }}>
                      Event ID
                    </div>
                    <code style={{ fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                      {log.event_id}
                    </code>
                  </div>

                  {log.error_message && (
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--text-secondary)' }}>
                        Error
                      </div>
                      <div style={{ fontSize: 12, color: '#d63031' }}>
                        {log.error_message}
                      </div>
                    </div>
                  )}

                  {log.response_body && (
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--text-secondary)' }}>
                        Response ({log.response_code})
                      </div>
                      <code style={{
                        display: 'block',
                        fontSize: 12,
                        padding: 8,
                        borderRadius: 4,
                        background: 'var(--bg-surface)',
                        border: '1px solid var(--border-main)',
                        color: 'var(--text-secondary)',
                        fontFamily: 'monospace',
                        maxHeight: 200,
                        overflow: 'auto',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                      }}>
                        {log.response_body}
                      </code>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      <div style={{ display: 'flex', justifyContent: 'center', gap: 8 }}>
        <button
          onClick={() => setPage(Math.max(0, page - 1))}
          disabled={page === 0}
          style={{
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: page === 0 ? 'not-allowed' : 'pointer',
            opacity: page === 0 ? 0.5 : 1,
          }}
        >
          Previous
        </button>
        <div style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text-secondary)' }}>
          Page {page + 1}
        </div>
        <button
          onClick={() => setPage(page + 1)}
          disabled={logs.length < pageSize}
          style={{
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: logs.length < pageSize ? 'not-allowed' : 'pointer',
            opacity: logs.length < pageSize ? 0.5 : 1,
          }}
        >
          Next
        </button>
      </div>
    </div>
  );
}
