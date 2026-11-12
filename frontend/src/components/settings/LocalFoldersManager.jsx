import { useState, useCallback } from 'react';
import { Plus, FolderOpen } from 'lucide-react';
import Modal from '../common/Modal';
import StatusBadge from '../common/StatusBadge';
import { api } from '../../utils/api';
import { ActionButton } from './settingsUi';

function isLikelyAbsolutePath(value) {
  const normalized = String(value || '').trim().replace(/\\/g, '/');
  return /^(?:[A-Za-z]:\/|\/\/|\/)/.test(normalized);
}

function buildBreadcrumbs(fullPath) {
  const normalized = String(fullPath || '').replace(/\\/g, '/').trim();
  if (!normalized) return [{ label: 'Home', path: '' }];

  const crumbs = [];
  const windowsDriveMatch = normalized.match(/^([A-Za-z]:)(\/.*)?$/);
  if (windowsDriveMatch) {
    const drive = windowsDriveMatch[1];
    crumbs.push({ label: drive, path: `${drive}/` });
    const rest = (windowsDriveMatch[2] || '').split('/').filter(Boolean);
    let current = `${drive}/`;
    rest.forEach((part) => {
      current = `${current}${part}/`;
      crumbs.push({ label: part, path: current });
    });
    return crumbs;
  }

  const parts = normalized.split('/').filter(Boolean);
  let current = normalized.startsWith('/') ? '/' : '';
  if (normalized.startsWith('/')) crumbs.push({ label: '/', path: '/' });
  parts.forEach((part) => {
    current = current ? `${current.replace(/\/$/, '')}/${part}` : part;
    crumbs.push({ label: part, path: current });
  });
  return crumbs;
}

const inputStyle = {
  width: '100%', padding: '10px 12px', borderRadius: 8,
  border: '1px solid var(--border-main)', background: 'var(--bg-input)',
  color: 'var(--text-primary)', outline: 'none',
};

/**
 * Registered local folders that hold .pbix files (Settings › Connections › Power BI files).
 * Lists folders and lets admins add one, with a server-side directory browser.
 */
export default function LocalFoldersManager({ folders = [], onChanged }) {
  const [modalOpen, setModalOpen] = useState(false);
  const [tagInput, setTagInput] = useState('');
  const [pathInput, setPathInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [dirPath, setDirPath] = useState('');
  const [dirEntries, setDirEntries] = useState([]);
  const [dirLoading, setDirLoading] = useState(false);
  const [dirError, setDirError] = useState('');

  const openModal = () => {
    setTagInput('');
    setPathInput('');
    setError('');
    setSuccess('');
    setPickerOpen(false);
    setDirPath('');
    setDirEntries([]);
    setDirError('');
    setModalOpen(true);
  };

  const loadDirectoryEntries = useCallback(async (nextPath = '') => {
    setDirLoading(true);
    setDirError('');
    try {
      const rows = await api.browseDirectory(nextPath);
      const list = Array.isArray(rows) ? rows : [];
      setDirEntries(list);
      if (nextPath) {
        setDirPath(nextPath.replace(/\\/g, '/'));
      } else if (list.length > 0) {
        const firstPath = String(list[0].path || '').replace(/\\/g, '/');
        setDirPath(firstPath.includes('/') ? firstPath.slice(0, firstPath.lastIndexOf('/')) : firstPath);
      } else {
        setDirPath('');
      }
    } catch (err) {
      setDirEntries([]);
      setDirError(err?.message || 'Failed to browse directories.');
    } finally {
      setDirLoading(false);
    }
  }, []);

  const openPicker = async () => {
    const basePath = isLikelyAbsolutePath(pathInput) ? pathInput.trim() : '';
    setPickerOpen(true);
    await loadDirectoryEntries(basePath);
  };

  const save = async () => {
    const tag = tagInput.trim();
    const absolutePath = pathInput.trim();
    if (!tag) { setError('Folder tag is required.'); return; }
    if (!isLikelyAbsolutePath(absolutePath)) {
      setError('Enter an absolute folder path such as C:/Models/Finance.');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await api.saveLocalFolder({ tag_name: tag, absolute_path: absolutePath, is_active: true });
      setSuccess(`Saved ${tag}.`);
      setModalOpen(false);
      await onChanged?.();
    } catch (err) {
      setError(err?.message || 'Failed to save local folder.');
    } finally {
      setSubmitting(false);
    }
  };

  const th = { textAlign: 'left', padding: '10px 14px', fontSize: 11, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em' };

  return (
    <div style={{ border: '1px solid var(--border-main)', borderRadius: 12, background: 'var(--bg-surface)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '12px 14px', borderBottom: '1px solid var(--border-main)' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>Power BI file folders</div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
            Register trusted folders once and reuse them in the new-project wizard.
          </div>
        </div>
        <ActionButton primary onClick={openModal}><Plus size={12} /> Add folder</ActionButton>
      </div>

      {success && (
        <div style={{ margin: 12, padding: '8px 12px', borderRadius: 8, background: 'var(--color-success-bg)', color: 'var(--color-success)', fontSize: 12 }}>
          {success}
        </div>
      )}

      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ background: 'var(--bg-surface-raised)' }}>
            <th style={th}>Tag</th>
            <th style={th}>Folder</th>
            <th style={th}>Status</th>
          </tr>
        </thead>
        <tbody>
          {folders.length === 0 ? (
            <tr>
              <td colSpan="3" style={{ padding: '16px 14px', color: 'var(--text-tertiary)', fontSize: 12 }}>No folders registered yet.</td>
            </tr>
          ) : folders.map((folder) => (
            <tr key={folder.id || folder.tag_name} style={{ borderTop: '1px solid var(--border-main)' }}>
              <td style={{ padding: '10px 14px', fontSize: 13, color: 'var(--text-primary)', fontWeight: 600 }}>{folder.tag_name}</td>
              <td style={{ padding: '10px 14px', fontSize: 12, color: 'var(--text-secondary)', wordBreak: 'break-all' }}>{folder.absolute_path}</td>
              <td style={{ padding: '10px 14px', fontSize: 12 }}>
                <StatusBadge status={folder.is_active ? 'connected' : 'draft'} label={folder.is_active ? 'Active' : 'Inactive'} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title="Add folder"
        size="md"
        footer={(
          <>
            <ActionButton onClick={() => setModalOpen(false)}>Cancel</ActionButton>
            <ActionButton primary onClick={save} disabled={submitting}>{submitting ? 'Saving…' : 'Save folder'}</ActionButton>
          </>
        )}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>Folder tag</label>
            <input value={tagInput} onChange={(e) => setTagInput(e.target.value)} placeholder="Finance_PBIX" style={inputStyle} />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>Absolute path</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input value={pathInput} onChange={(e) => setPathInput(e.target.value)} placeholder="C:/Models/Finance" style={{ ...inputStyle, flex: 1 }} />
              <ActionButton onClick={openPicker}><FolderOpen size={14} /> Browse</ActionButton>
            </div>
            <p style={{ margin: '6px 0 0', fontSize: 11, color: 'var(--text-tertiary)' }}>
              The path must already exist on the machine running the backend.
            </p>
          </div>
          {error && (
            <div style={{ padding: '10px 12px', borderRadius: 8, background: 'var(--color-error-bg)', color: 'var(--color-error)', fontSize: 12 }}>
              {error}
            </div>
          )}
        </div>
      </Modal>

      <Modal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        title="Select folder"
        size="md"
        footer={(
          <>
            <ActionButton onClick={() => setPickerOpen(false)}>Cancel</ActionButton>
            <ActionButton
              primary
              disabled={!dirPath}
              onClick={() => {
                if (!dirPath) return;
                setPathInput(dirPath.replace(/\\/g, '/').replace(/\/+$/, ''));
                setPickerOpen(false);
              }}
            >
              Select this folder
            </ActionButton>
          </>
        )}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {buildBreadcrumbs(dirPath).map((crumb, index) => (
              <button
                key={`${crumb.path || 'home'}-${index}`}
                type="button"
                onClick={() => loadDirectoryEntries(crumb.path)}
                style={{ border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-primary)', borderRadius: 999, padding: '4px 10px', fontSize: 11, cursor: 'pointer' }}
              >
                {crumb.label}
              </button>
            ))}
          </div>
          <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, maxHeight: 280, overflow: 'auto', background: 'var(--bg-surface)' }}>
            {dirLoading ? (
              <div style={{ padding: 12, fontSize: 12, color: 'var(--text-tertiary)' }}>Loading folders…</div>
            ) : dirEntries.length === 0 ? (
              <div style={{ padding: 12, fontSize: 12, color: 'var(--text-tertiary)' }}>No subfolders found.</div>
            ) : dirEntries.map((entry) => (
              <button
                key={entry.path}
                type="button"
                onClick={() => loadDirectoryEntries(entry.path)}
                style={{ width: '100%', textAlign: 'left', border: 'none', borderBottom: '1px solid var(--border-subtle)', background: 'transparent', color: 'var(--text-primary)', padding: '10px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}
              >
                <FolderOpen size={14} />
                <span style={{ fontSize: 12 }}>{entry.name}</span>
              </button>
            ))}
          </div>
          {dirError && (
            <div style={{ padding: '10px 12px', borderRadius: 8, background: 'var(--color-error-bg)', color: 'var(--color-error)', fontSize: 12 }}>
              {dirError}
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}
