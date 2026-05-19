/**
 * NotificationTemplatesPage — Jinja2 template management.
 */

import { useState, useEffect } from 'react';
import { Plus, Edit2, Trash2, AlertCircle } from 'lucide-react';
import PageHeader from '../../components/common/PageHeader';
import Modal from '../../components/common/Modal';
import { api } from '../../utils/api';
import NotificationSettingsTabs from '../../components/notifications/NotificationSettingsTabs';
import LevelMatrix from '../../components/notifications/LevelMatrix';

const DEFAULT_TEMPLATE = {
  channel_id: '',
  level_mask: 0,
  title_template: '',
  body_template: '',
  is_default: false,
};

export default function NotificationTemplatesPage() {
  const [templates, setTemplates] = useState([]);
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [formData, setFormData] = useState({ ...DEFAULT_TEMPLATE });
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [templatesResp, channelsResp] = await Promise.all([
        api.getNotificationTemplates(),
        api.getNotificationChannels(0, 100),
      ]);
      setTemplates(Array.isArray(templatesResp) ? templatesResp : templatesResp.items || []);
      setChannels(Array.isArray(channelsResp) ? channelsResp : channelsResp.items || []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const saveTemplate = async () => {
    try {
      if (editingId) {
        await api.updateNotificationTemplate(editingId, formData);
      } else {
        await api.createNotificationTemplate(formData);
      }
      await loadData();
      setModalOpen(false);
    } catch (e) {
      setError(e.message);
    }
  };

  const deleteTemplate = async (id) => {
    if (!window.confirm('Delete this template?')) return;
    try {
      await api.deleteNotificationTemplate(id);
      setTemplates(templates.filter((t) => t.id !== id));
    } catch (e) {
      setError(e.message);
    }
  };

  const updatePreview = async () => {
    try {
      const result = await api.previewNotificationTemplate(formData);
      setPreview(result);
    } catch (e) {
      setPreview({ error: e.message });
    }
  };

  const openEditModal = (template) => {
    setFormData(template);
    setEditingId(template.id);
    setPreview(null);
    setModalOpen(true);
  };

  const channelsGrouped = templates.reduce((acc, t) => {
    const ch = channels.find((c) => c.id === t.channel_id);
    const chName = ch?.name || t.channel_id;
    if (!acc[chName]) acc[chName] = [];
    acc[chName].push(t);
    return acc;
  }, {});

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '20px', minHeight: '100vh' }}>
      <PageHeader
        title="Notification Templates"
        subtitle="Create Jinja2 templates with variable substitution for each channel and notification level"
        action={{ label: 'Add Template', icon: Plus, onClick: () => { setFormData({ ...DEFAULT_TEMPLATE }); setEditingId(null); setModalOpen(true); } }}
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
        <div style={{ textAlign: 'center', padding: 40 }}>Loading templates...</div>
      ) : Object.keys(channelsGrouped).length === 0 ? (
        <div style={{
          padding: 40,
          textAlign: 'center',
          border: '1px dashed var(--border-main)',
          borderRadius: 12,
        }}>
          <p>No templates configured. Create one to get started.</p>
        </div>
      ) : (
        Object.entries(channelsGrouped).map(([chName, chTemplates]) => (
          <div key={chName} style={{ border: '1px solid var(--border-main)', borderRadius: 12, overflow: 'hidden' }}>
            <div style={{
              padding: 12,
              background: 'var(--bg-surface-raised)',
              borderBottom: '1px solid var(--border-main)',
              fontSize: 13,
              fontWeight: 600,
            }}>
              {chName}
            </div>
            <table style={{ width: '100%' }}>
              <thead>
                <tr style={{ background: 'var(--bg-surface)', borderBottom: '1px solid var(--border-main)' }}>
                  <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Levels</th>
                  <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Default</th>
                  <th style={{ padding: 12, textAlign: 'left', fontSize: 12, fontWeight: 600 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {chTemplates.map((tmpl) => (
                  <tr key={tmpl.id} style={{ borderBottom: '1px solid var(--border-main)' }}>
                    <td style={{ padding: 12, fontSize: 13 }}>{tmpl.level_mask || '—'}</td>
                    <td style={{ padding: 12, fontSize: 13 }}>{tmpl.is_default ? '✓' : '—'}</td>
                    <td style={{ padding: 12, display: 'flex', gap: 6 }}>
                      <button
                        onClick={() => openEditModal(tmpl)}
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
                        onClick={() => deleteTemplate(tmpl.id)}
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
        ))
      )}

      {/* Create/Edit Modal */}
      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="Template Editor" size="xl">
        <div style={{ display: 'flex', gap: 16, padding: 20 }}>
          {/* Left: Editor */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <select
              value={formData.channel_id}
              onChange={(e) => setFormData({ ...formData, channel_id: e.target.value })}
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
              <option value="">Select channel...</option>
              {channels.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>

            <div>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 8 }}>
                Notification Levels
              </label>
              <LevelMatrix
                value={formData.level_mask}
                onChange={(mask) => setFormData({ ...formData, level_mask: mask })}
              />
            </div>

            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={formData.is_default}
                onChange={(e) => setFormData({ ...formData, is_default: e.target.checked })}
              />
              <span style={{ fontSize: 13 }}>Use as default template</span>
            </label>

            <input
              type="text"
              value={formData.title_template}
              onChange={(e) => {
                setFormData({ ...formData, title_template: e.target.value });
                updatePreview();
              }}
              placeholder="Title template..."
              style={{
                width: '100%',
                padding: '8px 12px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-primary)',
                fontSize: 13,
                fontFamily: 'monospace',
              }}
            />

            <textarea
              value={formData.body_template}
              onChange={(e) => {
                setFormData({ ...formData, body_template: e.target.value });
                updatePreview();
              }}
              placeholder="Body template..."
              style={{
                width: '100%',
                padding: '8px 12px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-primary)',
                fontSize: 13,
                fontFamily: 'monospace',
                minHeight: 150,
              }}
            />
          </div>

          {/* Right: Variables + Preview */}
          <div style={{ width: 280, display: 'flex', flexDirection: 'column', gap: 12, borderLeft: '1px solid var(--border-main)', paddingLeft: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
              AVAILABLE VARIABLES
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              <code style={{ display: 'block', marginBottom: 4 }}>&#123;&#123; title &#125;&#125;</code>
              <code style={{ display: 'block', marginBottom: 4 }}>&#123;&#123; message &#125;&#125;</code>
              <code style={{ display: 'block', marginBottom: 4 }}>&#123;&#123; level &#125;&#125;</code>
              <code style={{ display: 'block', marginBottom: 4 }}>&#123;&#123; level_str &#125;&#125;</code>
              <code style={{ display: 'block', marginBottom: 4 }}>&#123;&#123; project_id &#125;&#125;</code>
              <code style={{ display: 'block', marginBottom: 4 }}>&#123;&#123; sync_job_id &#125;&#125;</code>
              <code style={{ display: 'block', marginBottom: 4 }}>&#123;&#123; source &#125;&#125;</code>
              <code style={{ display: 'block' }}>&#123;&#123; payload.key &#125;&#125;</code>
            </div>

            {preview && (
              <div style={{
                padding: 12,
                borderRadius: 6,
                background: 'var(--bg-surface-raised)',
                border: '1px solid var(--border-main)',
              }}>
                <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, color: 'var(--text-secondary)' }}>
                  PREVIEW
                </div>
                {preview.error ? (
                  <div style={{ fontSize: 11, color: '#d63031' }}>{preview.error}</div>
                ) : (
                  <>
                    <div style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 6 }}>
                      <strong>Title:</strong> {preview.title || '—'}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      <strong>Body:</strong> {preview.body || '—'}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, padding: '16px 20px', borderTop: '1px solid var(--border-main)' }}>
          <button
            onClick={saveTemplate}
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
