/**
 * NotificationChannelsPage — CRUD interface for notification channels.
 */

import { useState, useEffect } from 'react';
import { Plus, Edit2, Trash2, Send, AlertCircle, Check, X } from 'lucide-react';
import PageHeader from '../../components/common/PageHeader';
import Modal from '../../components/common/Modal';
import { api } from '../../utils/api';
import { ChannelTypeBadge, NotificationStatusBadge } from '../../components/notifications';
import NotificationSettingsTabs from '../../components/notifications/NotificationSettingsTabs';
import LevelMatrix from '../../components/notifications/LevelMatrix';
import SecretField from '../../components/notifications/SecretField';

const CHANNEL_TYPES = [
  { value: 'slack', label: 'Slack' },
  { value: 'email', label: 'Email' },
  { value: 'teams', label: 'Teams' },
  { value: 'webhook', label: 'Webhook' },
  { value: 'pagerduty', label: 'PagerDuty' },
];

function levelMaskToString(mask) {
  const levels = [];
  if (mask & 1) levels.push('SYNC');
  if (mask & 2) levels.push('CRITICAL');
  if (mask & 4) levels.push('ERROR');
  if (mask & 8) levels.push('WARNING');
  if (mask & 16) levels.push('INFO');
  if (mask & 32) levels.push('DEBUG');
  return levels.join(' · ') || 'None';
}

const DEFAULT_FORM = {
  name: '',
  channel_type: 'slack',
  level_mask: 7, // SYNC + CRITICAL + ERROR
  project_scope: '',
  enabled: true,
  quiet_hours_enabled: false,
  quiet_hours_start: '18:00',
  quiet_hours_end: '09:00',
  timezone: 'UTC',
  digest_enabled: false,
  config_json: {},
};

export default function NotificationChannelsPage() {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [formData, setFormData] = useState({ ...DEFAULT_FORM });
  const [configErrors, setConfigErrors] = useState({});
  const [testing, setTesting] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadChannels();
  }, []);

  const loadChannels = async () => {
    setLoading(true);
    try {
      const data = await api.getNotificationChannels(0, 100);
      setChannels(Array.isArray(data) ? data : data.items || []);
      setError(null);
    } catch (e) {
      setError(e.message || 'Failed to load channels');
    } finally {
      setLoading(false);
    }
  };

  const openCreateModal = () => {
    setFormData({ ...DEFAULT_FORM });
    setEditingId(null);
    setConfigErrors({});
    setModalOpen(true);
  };

  const openEditModal = (channel) => {
    setFormData({
      ...DEFAULT_FORM,
      ...channel,
      config_json: typeof channel.config_json === 'string'
        ? JSON.parse(channel.config_json)
        : channel.config_json,
    });
    setEditingId(channel.id);
    setConfigErrors({});
    setModalOpen(true);
  };

  const saveChannel = async () => {
    try {
      const payload = {
        ...formData,
        config_json: formData.config_json,
      };

      if (editingId) {
        await api.updateNotificationChannel(editingId, payload);
      } else {
        await api.createNotificationChannel(payload);
      }

      await loadChannels();
      setModalOpen(false);
      setError(null);
    } catch (e) {
      setError(e.message || 'Failed to save channel');
    }
  };

  const deleteChannel = async (id) => {
    if (!window.confirm('Are you sure? This cannot be undone.')) return;
    setDeleting(id);
    try {
      await api.deleteNotificationChannel(id);
      setChannels(channels.filter((ch) => ch.id !== id));
      setError(null);
    } catch (e) {
      setError(e.message || 'Failed to delete channel');
    } finally {
      setDeleting(null);
    }
  };

  const testChannel = async (id) => {
    setTesting(id);
    try {
      await api.testNotificationChannel(id);
      setError(null);
    } catch (e) {
      setError(e.message || 'Test failed');
    } finally {
      setTesting(null);
    }
  };

  const toggleEnabled = async (channel) => {
    try {
      await api.updateNotificationChannel(channel.id, {
        enabled: !channel.enabled,
      });
      await loadChannels();
    } catch (e) {
      setError(e.message || 'Failed to update channel');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '20px', minHeight: '100vh' }}>
      <PageHeader
        title="Notification Channels"
        subtitle="Configure channels for sending notifications (Slack, Email, Teams, PagerDuty, Webhooks)"
        action={{ label: 'Add Channel', icon: Plus, onClick: openCreateModal }}
      />
      <NotificationSettingsTabs />

      {error && (
        <div style={{
          padding: 12,
          borderRadius: 8,
          background: 'rgba(214, 48, 49, 0.12)',
          color: '#d63031',
          fontSize: 13,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-secondary)' }}>
          Loading channels...
        </div>
      ) : channels.length === 0 ? (
        <div style={{
          padding: 40,
          textAlign: 'center',
          border: '1px dashed var(--border-main)',
          borderRadius: 12,
          color: 'var(--text-secondary)',
        }}>
          <p style={{ margin: 0, marginBottom: 12 }}>No notification channels configured.</p>
          <button
            onClick={openCreateModal}
            style={{
              padding: '8px 16px',
              borderRadius: 6,
              border: 'none',
              background: 'var(--accent-blue)',
              color: 'white',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            Add your first channel
          </button>
        </div>
      ) : (
        <div style={{
          border: '1px solid var(--border-main)',
          borderRadius: 12,
          overflow: 'hidden',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)' }}>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>Name</th>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>Type</th>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>Levels</th>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>Status</th>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((ch) => (
                <tr key={ch.id} style={{ borderBottom: '1px solid var(--border-main)', transition: 'background-color 0.15s' }}
                  onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--bg-surface-hover)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
                >
                  <td style={{ padding: 12, fontSize: 13, color: 'var(--text-primary)' }}>{ch.name}</td>
                  <td style={{ padding: 12, fontSize: 13 }}><ChannelTypeBadge type={ch.channel_type} /></td>
                  <td style={{ padding: 12, fontSize: 13, color: 'var(--text-secondary)' }}>{levelMaskToString(ch.level_mask)}</td>
                  <td style={{ padding: 12, fontSize: 13 }}>
                    <NotificationStatusBadge status={ch.enabled ? 'delivered' : 'skipped_quiet'} />
                  </td>
                  <td style={{ padding: 12, display: 'flex', gap: 6 }}>
                    <button
                      onClick={() => testChannel(ch.id)}
                      disabled={testing === ch.id}
                      style={{
                        padding: '4px 8px',
                        fontSize: 12,
                        fontWeight: 500,
                        borderRadius: 4,
                        border: 'none',
                        background: 'var(--accent-blue)',
                        color: 'white',
                        cursor: testing === ch.id ? 'not-allowed' : 'pointer',
                        opacity: testing === ch.id ? 0.6 : 1,
                      }}
                    >
                      {testing === ch.id ? '...' : 'Test'}
                    </button>
                    <button
                      onClick={() => openEditModal(ch)}
                      style={{
                        padding: '4px 8px',
                        fontSize: 12,
                        fontWeight: 500,
                        borderRadius: 4,
                        border: '1px solid var(--border-main)',
                        background: 'var(--bg-surface)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer',
                      }}
                    >
                      <Edit2 size={12} style={{ display: 'inline', marginRight: 2 }} />
                      Edit
                    </button>
                    <button
                      onClick={() => deleteChannel(ch.id)}
                      disabled={deleting === ch.id}
                      style={{
                        padding: '4px 8px',
                        fontSize: 12,
                        fontWeight: 500,
                        borderRadius: 4,
                        border: '1px solid var(--color-error)',
                        background: 'rgba(214, 48, 49, 0.12)',
                        color: '#d63031',
                        cursor: deleting === ch.id ? 'not-allowed' : 'pointer',
                        opacity: deleting === ch.id ? 0.6 : 1,
                      }}
                    >
                      <Trash2 size={12} style={{ display: 'inline', marginRight: 2 }} />
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editingId ? 'Edit Channel' : 'Create Channel'}
        size="lg"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: '20px' }}>
          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>
              Channel Name *
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="My Slack Workspace"
              style={{
                width: '100%',
                padding: '8px 12px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-primary)',
                fontSize: 13,
                fontFamily: 'inherit',
              }}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>
              Channel Type *
            </label>
            <select
              value={formData.channel_type}
              onChange={(e) => setFormData({ ...formData, channel_type: e.target.value })}
              style={{
                width: '100%',
                padding: '8px 12px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-primary)',
                fontSize: 13,
              }}
            >
              {CHANNEL_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 8, color: 'var(--text-secondary)' }}>
              Notification Levels *
            </label>
            <LevelMatrix
              value={formData.level_mask}
              onChange={(mask) => setFormData({ ...formData, level_mask: mask })}
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>
                Enabled
              </label>
              <input
                type="checkbox"
                checked={formData.enabled}
                onChange={(e) => setFormData({ ...formData, enabled: e.target.checked })}
                style={{ cursor: 'pointer' }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>
                Project Scope (optional)
              </label>
              <input
                type="text"
                value={formData.project_scope || ''}
                onChange={(e) => setFormData({ ...formData, project_scope: e.target.value || null })}
                placeholder="Leave blank for all projects"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: 6,
                  border: '1px solid var(--border-main)',
                  background: 'var(--bg-surface-raised)',
                  color: 'var(--text-primary)',
                  fontSize: 13,
                }}
              />
            </div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>
              Quiet Hours
            </label>
            <input
              type="checkbox"
              checked={formData.quiet_hours_enabled}
              onChange={(e) => setFormData({ ...formData, quiet_hours_enabled: e.target.checked })}
              style={{ cursor: 'pointer' }}
            />
            {formData.quiet_hours_enabled && (
              <div style={{ marginTop: 12, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                <input
                  type="time"
                  value={formData.quiet_hours_start}
                  onChange={(e) => setFormData({ ...formData, quiet_hours_start: e.target.value })}
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
                  type="time"
                  value={formData.quiet_hours_end}
                  onChange={(e) => setFormData({ ...formData, quiet_hours_end: e.target.value })}
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
                  type="text"
                  value={formData.timezone}
                  onChange={(e) => setFormData({ ...formData, timezone: e.target.value })}
                  placeholder="UTC"
                  style={{
                    padding: '8px 12px',
                    borderRadius: 6,
                    border: '1px solid var(--border-main)',
                    background: 'var(--bg-surface-raised)',
                    color: 'var(--text-primary)',
                    fontSize: 13,
                  }}
                />
              </div>
            )}
          </div>

          {formData.quiet_hours_enabled && (
            <div>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>
                Enable Digest
              </label>
              <input
                type="checkbox"
                checked={formData.digest_enabled}
                onChange={(e) => setFormData({ ...formData, digest_enabled: e.target.checked })}
                style={{ cursor: 'pointer' }}
              />
            </div>
          )}

          {/* Channel-type-specific config fields */}
          {formData.channel_type === 'slack' && (
            <SecretField
              label="Webhook URL"
              maskedValue={formData.config_json?.webhook_url ? '•'.repeat(8) + formData.config_json.webhook_url.slice(-10) : undefined}
              onUpdate={(val) => setFormData({
                ...formData,
                config_json: { ...formData.config_json, webhook_url: val },
              })}
            />
          )}

          {formData.channel_type === 'email' && (
            <>
              <input
                type="text"
                value={formData.config_json?.smtp_host || ''}
                onChange={(e) => setFormData({
                  ...formData,
                  config_json: { ...formData.config_json, smtp_host: e.target.value },
                })}
                placeholder="SMTP Host"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: 6,
                  border: '1px solid var(--border-main)',
                  background: 'var(--bg-surface-raised)',
                  color: 'var(--text-primary)',
                  fontSize: 13,
                }}
              />
              <input
                type="number"
                value={formData.config_json?.smtp_port || 587}
                onChange={(e) => setFormData({
                  ...formData,
                  config_json: { ...formData.config_json, smtp_port: parseInt(e.target.value) },
                })}
                placeholder="SMTP Port"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: 6,
                  border: '1px solid var(--border-main)',
                  background: 'var(--bg-surface-raised)',
                  color: 'var(--text-primary)',
                  fontSize: 13,
                }}
              />
              <SecretField
                label="SMTP Password"
                maskedValue={formData.config_json?.smtp_password ? '•'.repeat(8) : undefined}
                onUpdate={(val) => setFormData({
                  ...formData,
                  config_json: { ...formData.config_json, smtp_password: val },
                })}
              />
            </>
          )}

          {formData.channel_type === 'webhook' && (
            <SecretField
              label="Webhook URL"
              maskedValue={formData.config_json?.url ? '•'.repeat(8) + formData.config_json.url.slice(-10) : undefined}
              onUpdate={(val) => setFormData({
                ...formData,
                config_json: { ...formData.config_json, url: val },
              })}
            />
          )}

          {formData.channel_type === 'teams' && (
            <SecretField
              label="Webhook URL"
              maskedValue={formData.config_json?.webhook_url ? '•'.repeat(8) + formData.config_json.webhook_url.slice(-10) : undefined}
              onUpdate={(val) => setFormData({
                ...formData,
                config_json: { ...formData.config_json, webhook_url: val },
              })}
            />
          )}

          {formData.channel_type === 'pagerduty' && (
            <SecretField
              label="Routing Key"
              maskedValue={formData.config_json?.routing_key ? '•'.repeat(8) : undefined}
              onUpdate={(val) => setFormData({
                ...formData,
                config_json: { ...formData.config_json, routing_key: val },
              })}
            />
          )}
        </div>

        <div style={{
          display: 'flex',
          gap: 8,
          padding: '16px 20px',
          borderTop: '1px solid var(--border-main)',
          background: 'var(--bg-surface-raised)',
        }}>
          <button
            onClick={saveChannel}
            style={{
              flex: 1,
              padding: '8px 16px',
              borderRadius: 6,
              border: 'none',
              background: 'var(--accent-blue)',
              color: 'white',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            {editingId ? 'Update' : 'Create'}
          </button>
          <button
            onClick={() => setModalOpen(false)}
            style={{
              flex: 1,
              padding: '8px 16px',
              borderRadius: 6,
              border: '1px solid var(--border-main)',
              background: 'transparent',
              color: 'var(--text-primary)',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
        </div>
      </Modal>
    </div>
  );
}
