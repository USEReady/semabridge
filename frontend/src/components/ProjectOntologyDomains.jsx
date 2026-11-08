import { useEffect, useState } from 'react';
import { Layers } from 'lucide-react';
import { api } from '../utils/api';

function errorText(err) {
  const payload = err?.payload?.detail ?? err?.payload;
  if (payload?.errors?.length) return payload.errors.join('; ');
  if (typeof payload === 'string') return payload;
  return err?.message || 'Request failed';
}

/**
 * Attach domain ontologies to a project. Attached domains are applied on the
 * next run (order = precedence; per-element user overrides always win).
 */
export default function ProjectOntologyDomains({ projectId }) {
  const [available, setAvailable] = useState([]);
  const [attached, setAttached] = useState([]);
  const [missing, setMissing] = useState([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([api.listOntologyDomains(), api.getProjectOntologyDomains(projectId)])
      .then(([all, current]) => {
        setAvailable(all?.domains || []);
        setAttached((current?.domains || []).map((d) => d.id));
        setMissing(current?.missing_domain_ids || []);
      })
      .catch((err) => setMessage({ type: 'error', text: errorText(err) }));
  }, [projectId]);

  function toggle(id) {
    setAttached((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      const res = await api.setProjectOntologyDomains(projectId, attached);
      setAttached((res?.domains || []).map((d) => d.id));
      setMissing(res?.missing_domain_ids || []);
      setMessage({ type: 'success', text: 'Saved. Applied on the next run.' });
    } catch (err) {
      setMessage({ type: 'error', text: errorText(err) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-surface rounded-xl border border-main p-6">
      <div className="flex items-center gap-2 mb-3">
        <Layers size={16} className="text-accent" />
        <h2 className="text-primary font-semibold text-sm m-0">Domain Ontologies</h2>
      </div>
      {available.length === 0 ? (
        <span className="text-tertiary text-xs italic">No domain ontologies uploaded yet.</span>
      ) : (
        <div className="flex flex-col gap-2">
          {available.map((d) => (
            <label key={d.id || d.domain} className="flex items-center gap-2 text-xs text-primary">
              <input
                type="checkbox"
                checked={attached.includes(d.id)}
                onChange={() => toggle(d.id)}
              />
              <span>{d.domain}</span>
              {attached.includes(d.id) && (
                <span className="text-tertiary">#{attached.indexOf(d.id) + 1}</span>
              )}
            </label>
          ))}
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="mt-2 text-xs font-semibold py-2 rounded-lg border border-main text-secondary"
          >
            {saving ? 'Saving…' : 'Save attachments'}
          </button>
        </div>
      )}
      {missing.length > 0 && (
        <div className="text-xs mt-2" style={{ color: '#f59e0b' }}>
          Attached domain(s) no longer exist: {missing.join(', ')}
        </div>
      )}
      {message && (
        <div className="text-xs mt-2" style={{ color: message.type === 'error' ? '#ef4444' : '#10b981' }}>
          {message.text}
        </div>
      )}
    </div>
  );
}
