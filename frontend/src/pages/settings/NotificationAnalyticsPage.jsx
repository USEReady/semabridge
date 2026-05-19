/**
 * NotificationAnalyticsPage — delivery analytics, charts, and health.
 */

import { useState, useEffect } from 'react';
import { AlertCircle } from 'lucide-react';
import PageHeader from '../../components/common/PageHeader';
import { api } from '../../utils/api';
import NotificationSettingsTabs from '../../components/notifications/NotificationSettingsTabs';

export default function NotificationAnalyticsPage() {
  const [stats, setStats] = useState(null);
  const [health, setHealth] = useState([]);
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dateRange, setDateRange] = useState('24h');
  const [error, setError] = useState(null);

  const DATE_RANGES = [
    { value: '1h', label: 'Last 1 hour' },
    { value: '24h', label: 'Last 24 hours' },
    { value: '7d', label: 'Last 7 days' },
    { value: '30d', label: 'Last 30 days' },
  ];

  useEffect(() => {
    loadData();
  }, [dateRange]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [statsResp, channelsResp] = await Promise.all([
        api.getNotificationDeliveryStats({ range: dateRange }),
        api.getNotificationChannels(0, 100),
      ]);
      setStats(statsResp);
      setChannels(Array.isArray(channelsResp) ? channelsResp : channelsResp.items || []);

      const healthData = await Promise.all(
        (Array.isArray(channelsResp) ? channelsResp : channelsResp.items || []).map((c) =>
          api.getNotificationChannelHealth(c.id).catch(() => ({}))
        )
      );
      setHealth(healthData);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '20px', minHeight: '100vh' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <PageHeader
          title="Notification Analytics"
          subtitle="Overview of notification delivery performance and channel health"
        />
        <div style={{ display: 'flex', gap: 8 }}>
          {DATE_RANGES.map((range) => (
            <button
              key={range.value}
              onClick={() => setDateRange(range.value)}
              style={{
                padding: '8px 12px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: dateRange === range.value ? 'var(--accent-blue)' : 'var(--bg-surface)',
                color: dateRange === range.value ? 'white' : 'var(--text-primary)',
                fontSize: 12,
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              {range.label}
            </button>
          ))}
        </div>
      </div>
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

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, fontSize: 14, color: 'var(--text-secondary)' }}>
          Loading analytics...
        </div>
      ) : (
        <>
          {/* Summary Cards */}
          {stats && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 }}>
              <div style={{
                padding: 16,
                borderRadius: 12,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface)',
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                  Total Sent
                </div>
                <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--accent-blue)' }}>
                  {stats.total_sent || 0}
                </div>
              </div>

              <div style={{
                padding: 16,
                borderRadius: 12,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface)',
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                  Success Rate
                </div>
                <div style={{ fontSize: 28, fontWeight: 700, color: '#00b894' }}>
                  {stats.success_rate ? stats.success_rate.toFixed(1) : 0}%
                </div>
              </div>

              <div style={{
                padding: 16,
                borderRadius: 12,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface)',
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                  Avg Latency
                </div>
                <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--text-primary)' }}>
                  {stats.avg_latency_ms ? stats.avg_latency_ms.toFixed(0) : 0}ms
                </div>
              </div>

              <div style={{
                padding: 16,
                borderRadius: 12,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface)',
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                  Dead Letters
                </div>
                <div style={{ fontSize: 28, fontWeight: 700, color: '#d63031' }}>
                  {stats.dead_letters || 0}
                </div>
              </div>
            </div>
          )}

          {/* Channel Health Table */}
          <div style={{ marginTop: 12 }}>
            <h2 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Channel Health</h2>
            {channels.length === 0 ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
                No channels configured.
              </div>
            ) : (
              <div style={{ border: '1px solid var(--border-main)', borderRadius: 12, overflow: 'hidden' }}>
                <table style={{ width: '100%' }}>
                  <thead>
                    <tr style={{ background: 'var(--bg-surface-raised)', borderBottom: '1px solid var(--border-main)' }}>
                      <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Channel</th>
                      <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Success Rate</th>
                      <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Avg Latency</th>
                      <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>P95 Latency</th>
                      <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {channels.map((ch, idx) => {
                      const h = health[idx] || {};
                      const successRate = h.success_rate || 0;
                      const status = h.circuit_state === 'open' ? 'Circuit Open' : 'Healthy';
                      return (
                        <tr key={ch.id} style={{ borderBottom: '1px solid var(--border-main)' }}>
                          <td style={{ padding: 12, fontSize: 13 }}>{ch.name}</td>
                          <td style={{ padding: 12, fontSize: 13 }}>
                            <div style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 8,
                            }}>
                              <div style={{
                                width: 120,
                                height: 8,
                                borderRadius: 4,
                                background: 'var(--border-main)',
                                overflow: 'hidden',
                              }}>
                                <div style={{
                                  height: '100%',
                                  width: `${successRate}%`,
                                  background: successRate > 80 ? '#00b894' : successRate > 50 ? '#fdcb6e' : '#d63031',
                                }} />
                              </div>
                              <span style={{ fontSize: 12, minWidth: 30 }}>{successRate.toFixed(1)}%</span>
                            </div>
                          </td>
                          <td style={{ padding: 12, fontSize: 13 }}>
                            {h.avg_latency_ms ? `${h.avg_latency_ms.toFixed(0)}ms` : '—'}
                          </td>
                          <td style={{ padding: 12, fontSize: 13 }}>
                            {h.p95_latency_ms ? `${h.p95_latency_ms.toFixed(0)}ms` : '—'}
                          </td>
                          <td style={{ padding: 12, fontSize: 13 }}>
                            <div style={{
                              display: 'inline-block',
                              padding: '4px 8px',
                              borderRadius: 12,
                              fontSize: 11,
                              fontWeight: 500,
                              background: status === 'Healthy' ? 'rgba(0, 184, 148, 0.12)' : 'rgba(214, 48, 49, 0.12)',
                              color: status === 'Healthy' ? '#00b894' : '#d63031',
                            }}>
                              {status}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
