/**
 * NotificationRoutingRulesPage — rule-based routing engine.
 */

import { useState, useEffect } from 'react';
import { Plus, Edit2, Trash2, AlertCircle } from 'lucide-react';
import PageHeader from '../../components/common/PageHeader';
import Modal from '../../components/common/Modal';
import { api } from '../../utils/api';
import NotificationSettingsTabs from '../../components/notifications/NotificationSettingsTabs';
import LevelMatrix from '../../components/notifications/LevelMatrix';

const DEFAULT_RULE = {
  name: '',
  priority: 10,
  conditions: {
    level_mask: null,
    project_ids: [],
    source_pattern: '',
    title_contains: '',
    payload_key_exists: '',
  },
  channel_ids: [],
  stop_on_match: false,
  enabled: true,
};

export default function NotificationRoutingRulesPage() {
  const [rules, setRules] = useState([]);
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [formData, setFormData] = useState({ ...DEFAULT_RULE });
  const [evaluateOpen, setEvaluateOpen] = useState(false);
  const [evaluatePayload, setEvaluatePayload] = useState('');
  const [evaluateResult, setEvaluateResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [rulesResp, channelsResp] = await Promise.all([
        api.getNotificationRoutingRules(0, 100),
        api.getNotificationChannels(0, 100),
      ]);
      setRules(Array.isArray(rulesResp) ? rulesResp : rulesResp.items || []);
      setChannels(Array.isArray(channelsResp) ? channelsResp : channelsResp.items || []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const saveRule = async () => {
    try {
      if (editingId) {
        await api.updateNotificationRoutingRule(editingId, formData);
      } else {
        await api.createNotificationRoutingRule(formData);
      }
      await loadData();
      setModalOpen(false);
    } catch (e) {
      setError(e.message);
    }
  };

  const deleteRule = async (id) => {
    if (!window.confirm('Delete this routing rule?')) return;
    try {
      await api.deleteNotificationRoutingRule(id);
      setRules(rules.filter((r) => r.id !== id));
    } catch (e) {
      setError(e.message);
    }
  };

  const evaluateRules = async () => {
    try {
      const payload = JSON.parse(evaluatePayload);
      const result = await api.evaluateNotificationRoutingRules(payload);
      setEvaluateResult(result);
    } catch (e) {
      setError(e.message || 'Invalid JSON payload');
    }
  };

  const openEditModal = (rule) => {
    setFormData(rule);
    setEditingId(rule.id);
    setModalOpen(true);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '20px', minHeight: '100vh' }}>
      <PageHeader
        title="Routing Rules"
        subtitle="Define rule-based routing for notifications based on conditions like level, project, and payload"
        action={{ label: 'Add Rule', icon: Plus, onClick: () => { setFormData({ ...DEFAULT_RULE }); setEditingId(null); setModalOpen(true); } }}
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

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <button
          onClick={() => setEvaluateOpen(true)}
          style={{
            padding: '12px 16px',
            borderRadius: 8,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          Evaluate Rules (Dry-Run)
        </button>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>Loading rules...</div>
      ) : rules.length === 0 ? (
        <div style={{
          padding: 40,
          textAlign: 'center',
          border: '1px dashed var(--border-main)',
          borderRadius: 12,
        }}>
          <p>No routing rules configured. Create one to get started.</p>
        </div>
      ) : (
        <div style={{
          border: '1px solid var(--border-main)',
          borderRadius: 12,
          overflow: 'hidden',
        }}>
          <table style={{ width: '100%' }}>
            <thead>
              <tr style={{ background: 'var(--bg-surface-raised)', borderBottom: '1px solid var(--border-main)' }}>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Priority</th>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Name</th>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Channels</th>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Enabled</th>
                <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rules
                .sort((a, b) => a.priority - b.priority)
                .map((rule) => (
                  <tr key={rule.id} style={{ borderBottom: '1px solid var(--border-main)' }}>
                    <td style={{ padding: 12, fontSize: 13 }}>{rule.priority}</td>
                    <td style={{ padding: 12, fontSize: 13 }}>{rule.name}</td>
                    <td style={{ padding: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
                      {channels
                        .filter((c) => rule.channel_ids.includes(c.id))
                        .map((c) => c.name)
                        .join(', ') || 'None'}
                    </td>
                    <td style={{ padding: 12, fontSize: 13 }}>
                      {rule.enabled ? '✓' : '✗'}
                    </td>
                    <td style={{ padding: 12, display: 'flex', gap: 6 }}>
                      <button
                        onClick={() => openEditModal(rule)}
                        style={{
                          padding: '4px 8px',
                          fontSize: 12,
                          borderRadius: 4,
                          border: '1px solid var(--border-main)',
                          background: 'var(--bg-surface)',
                          color: 'var(--text-primary)',
                          cursor: 'pointer',
                        }}
                      >
                        <Edit2 size={12} style={{ display: 'inline' }} /> Edit
                      </button>
                      <button
                        onClick={() => deleteRule(rule.id)}
                        style={{
                          padding: '4px 8px',
                          fontSize: 12,
                          borderRadius: 4,
                          border: '1px solid #d63031',
                          background: 'rgba(214, 48, 49, 0.12)',
                          color: '#d63031',
                          cursor: 'pointer',
                        }}
                      >
                        <Trash2 size={12} style={{ display: 'inline' }} /> Delete
                      </button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="Routing Rule" size="lg">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: 20 }}>
          <input
            type="text"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            placeholder="Rule name"
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

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <input
              type="number"
              value={formData.priority}
              onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) })}
              placeholder="Priority"
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
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={formData.enabled}
                onChange={(e) => setFormData({ ...formData, enabled: e.target.checked })}
              />
              <span style={{ fontSize: 13 }}>Enabled</span>
            </label>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 8 }}>
              Notification Levels
            </label>
            <LevelMatrix
              value={formData.conditions.level_mask || 0}
              onChange={(mask) => setFormData({
                ...formData,
                conditions: { ...formData.conditions, level_mask: mask },
              })}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
              Target Channels
            </label>
            <select
              multiple
              value={formData.channel_ids}
              onChange={(e) => setFormData({
                ...formData,
                channel_ids: Array.from(e.target.selectedOptions, (o) => o.value),
              })}
              style={{
                width: '100%',
                padding: '8px 12px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-primary)',
                fontSize: 13,
                minHeight: 100,
              }}
            >
              {channels.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>

          <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox"
              checked={formData.stop_on_match}
              onChange={(e) => setFormData({ ...formData, stop_on_match: e.target.checked })}
            />
            <span style={{ fontSize: 13 }}>Stop on match (don't evaluate further rules)</span>
          </label>

          <input
            type="text"
            value={formData.conditions.source_pattern}
            onChange={(e) => setFormData({
              ...formData,
              conditions: { ...formData.conditions, source_pattern: e.target.value },
            })}
            placeholder="Source pattern (e.g., sync_*)"
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
            type="text"
            value={formData.conditions.title_contains}
            onChange={(e) => setFormData({
              ...formData,
              conditions: { ...formData.conditions, title_contains: e.target.value },
            })}
            placeholder="Title contains (case-insensitive)"
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

        <div style={{ display: 'flex', gap: 8, padding: '16px 20px', borderTop: '1px solid var(--border-main)' }}>
          <button
            onClick={saveRule}
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

      {/* Evaluate Modal */}
      <Modal open={evaluateOpen} onClose={() => setEvaluateOpen(false)} title="Evaluate Rules (Dry-Run)" size="xl">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: 20 }}>
          <textarea
            value={evaluatePayload}
            onChange={(e) => setEvaluatePayload(e.target.value)}
            placeholder={'{\n  "level": 4,\n  "title": "Sync failed",\n  "project_id": "proj-123"\n}'}
            style={{
              width: '100%',
              padding: '12px',
              borderRadius: 6,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface-raised)',
              color: 'var(--text-primary)',
              fontFamily: 'monospace',
              fontSize: 12,
              minHeight: 150,
            }}
          />

          <button
            onClick={evaluateRules}
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
            Evaluate
          </button>

          {evaluateResult && (
            <div style={{
              padding: 12,
              borderRadius: 6,
              background: 'var(--bg-surface-raised)',
              border: '1px solid var(--border-main)',
            }}>
              <p style={{ margin: 0, marginBottom: 8, fontSize: 12, fontWeight: 600 }}>Result:</p>
              <pre style={{
                margin: 0,
                fontSize: 11,
                fontFamily: 'monospace',
                color: 'var(--text-secondary)',
                maxHeight: 150,
                overflow: 'auto',
              }}>
                {JSON.stringify(evaluateResult, null, 2)}
              </pre>
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, padding: '16px 20px', borderTop: '1px solid var(--border-main)' }}>
          <button
            onClick={() => setEvaluateOpen(false)}
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
            Close
          </button>
        </div>
      </Modal>
    </div>
  );
}
