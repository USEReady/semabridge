import { useState, useEffect } from 'react';
import { Save, AlertCircle, FileCode2, SlidersHorizontal } from 'lucide-react';
import { useConfiguration } from '../context/ConfigurationContext';

export default function ConfigEditor() {
  const { config, ghostConfig, saveConfig, isLoading } = useConfiguration();
  const [formData, setFormData] = useState({});
  const [activeTab, setActiveTab] = useState('project');

  useEffect(() => {
    if (config) {
      setFormData(config);
    }
  }, [config]);

  const handleChange = (path, value) => {
    const keys = path.split('.');
    setFormData(prev => {
      const cloned = JSON.parse(JSON.stringify(prev));
      let current = cloned;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!current[keys[i]]) current[keys[i]] = {};
        current = current[keys[i]];
      }
      current[keys[keys.length - 1]] = value;
      return cloned;
    });
  };

  const getValue = (sourceObj, path) => {
    return path.split('.').reduce((obj, key) => (obj && obj[key] !== 'undefined' ? obj[key] : undefined), sourceObj);
  };

  const handleSave = async () => {
    const result = await saveConfig(formData);
    if (!result.success) {
      alert("Failed saving: " + JSON.stringify(result.validationErrors || result.error));
    }
  };

  const renderInput = (label, path) => {
    const projectVal = getValue(formData, path) || '';
    const ghostVal = getValue(ghostConfig, path) || '';
    
    // Ghost indicates global config is overriding null project config
    const isGhosted = projectVal === '' && ghostVal !== '';
    const displayVal = projectVal !== '' ? projectVal : ghostVal;

    return (
      <div style={{ marginBottom: 16 }}>
        <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>
          {label}
        </label>
        <div style={{ position: 'relative' }}>
          <input 
            type="text" 
            value={displayVal}
            onChange={(e) => handleChange(path, e.target.value)}
            style={{ 
              width: '100%', 
              padding: '8px 12px', 
              borderRadius: 6, 
              border: '1px solid var(--border-color)',
              background: 'var(--bg-app)',
              color: isGhosted ? 'var(--text-tertiary)' : 'var(--text-primary)',
              fontStyle: isGhosted ? 'italic' : 'normal'
            }}
          />
          {isGhosted && (
             <div style={{ position: 'absolute', right: 10, top: 10, fontSize: 10, color: 'var(--text-tertiary)' }}>
               Inherited from Global
             </div>
          )}
        </div>
      </div>
    );
  };

  const handleExport = async () => {
    try {
      const { api } = await import('../utils/api');
      const { stringify } = await import('yaml');
      
      const status = await api.getConnectionsStatus();
      const exportData = { connections: {} };
      
      for (const [key, conn] of Object.entries(status)) {
        if (conn.configured || (conn.credentials && Object.keys(conn.credentials).length > 0)) {
          const safeConn = { type: key };
          if (conn.credentials) {
            const safeCreds = { ...conn.credentials };
            const secretKeys = ['password', 'private_key', 'oauth_client_secret', 'token', 'access_token', 'refresh_token'];
            secretKeys.forEach(k => {
              if (safeCreds[k]) {
                delete safeCreds[k];
              }
            });
            safeConn.credentials = safeCreds;
          }
          exportData.connections[key] = safeConn;
        }
      }
      
      const yamlStr = stringify(exportData);
      const blob = new Blob([yamlStr], { type: 'text/yaml' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'connections_export.yaml';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      alert("Failed to export connections: " + err.message);
    }
  };

  const handleImport = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    try {
        const resp = await fetch('/api/project/import', { method: 'POST', body: formData });
        if (resp.ok) {
           window.location.reload();
        } else {
           const err = await resp.json();
           alert('Failed importing: ' + JSON.stringify(err));
        }
    } catch (err) {
        alert('Error importing');
    }
  };

  if (isLoading) return <div style={{ color: 'var(--text-tertiary)', padding: 12 }}>Loading configuration state...</div>;

  return (
    <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', borderRadius: 12, padding: 24, fontSize: 13 }}>
       
       <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 24, borderBottom: '1px solid var(--border-color)', paddingBottom: 12 }}>
          <div style={{ display: 'inline-flex', border: '1px solid var(--border-color)', borderRadius: 10, overflow: 'hidden' }}>
            <button
              type="button"
              onClick={() => setActiveTab('project')}
              aria-label="Switch to form view"
              title="Form view"
              style={{
                width: 38,
                height: 34,
                background: activeTab === 'project' ? 'var(--accent-blue)' : 'transparent',
                border: 'none',
                color: activeTab === 'project' ? '#fff' : 'var(--text-primary)',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <SlidersHorizontal size={14} />
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('yaml')}
              aria-label="Switch to YAML view"
              title="YAML view"
              style={{
                width: 38,
                height: 34,
                background: activeTab === 'yaml' ? 'var(--accent-blue)' : 'transparent',
                border: 'none',
                color: activeTab === 'yaml' ? '#fff' : 'var(--text-primary)',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderLeft: '1px solid var(--border-color)',
              }}
            >
              <FileCode2 size={14} />
            </button>
          </div>
       </div>

       {activeTab === 'project' && (
         <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
           <div>
              <h3 style={{ fontSize: 14, marginBottom: 16 }}>Source Definition</h3>
              {renderInput('Source Type', 'source.type')}
              {renderInput('Workspace', 'source.workspace')}
              {renderInput('Model Pattern', 'source.model')}
           </div>
           <div>
              <h3 style={{ fontSize: 14, marginBottom: 16 }}>Core Settings</h3>
              {renderInput('Repository Path', 'core.repository_path')}
              {renderInput('Concurrency Workers', 'concurrency.max_workers')}
              {renderInput('Log Level', 'logging.level')}
           </div>
         </div>
       )}

       {activeTab === 'yaml' && (
         <div style={{ background: '#1e1e1e', padding: 16, borderRadius: 8, color: '#d4d4d4', fontFamily: 'monospace', whiteSpace: 'pre', overflowX: 'auto' }}>
            {JSON.stringify(formData, null, 2)}
         </div>
       )}

       <div style={{ marginTop: 24, paddingTop: 16, borderTop: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: 12 }}>
             <button onClick={handleExport} style={{ background: 'transparent', border: '1px solid var(--border-color)', color: 'var(--text-primary)', padding: '6px 12px', borderRadius: 6, cursor: 'pointer' }}>Export YAML</button>
             <label style={{ background: 'transparent', border: '1px solid var(--border-color)', color: 'var(--text-primary)', padding: '6px 12px', borderRadius: 6, cursor: 'pointer' }}>
                Import YAML
                <input type="file" accept=".yml,.yaml" onChange={handleImport} style={{ display: 'none' }} />
             </label>
          </div>
          <button 
             onClick={handleSave} 
             style={{ background: 'var(--accent-blue)', color: '#fff', border: 'none', padding: '6px 16px', borderRadius: 6, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer', fontWeight: 600 }}
          >
             <Save size={14} /> Save Configuration
          </button>
       </div>

    </div>
  );
}
