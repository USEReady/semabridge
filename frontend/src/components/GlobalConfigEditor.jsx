import { useState, useEffect } from 'react';
import {
    FileCode2,
    RotateCcw,
    Loader2,
    Save,
    AlertCircle,
    CheckCircle2,
    ShieldCheck,
} from 'lucide-react';
import { api } from '../utils/api';
import { useLogs } from '../context/LogsContext';

export default function GlobalConfigEditor() {
    const [content, setContent] = useState('');
    const [originalContent, setOriginalContent] = useState('');
    const [configPath, setConfigPath] = useState('');
    const [configExists, setConfigExists] = useState(false);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const [success, setSuccess] = useState(null);
    const { addLog } = useLogs();

    useEffect(() => {
        loadConfig();
    }, []);

    const loadConfig = async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await api.getGlobalConfig();
            setContent(data.content || '');
            setOriginalContent(data.content || '');
            setConfigPath(data.path || '~/.semabridge/config.yaml');
            setConfigExists(data.exists || false);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        setError(null);
        setSuccess(null);
        try {
            const result = await api.saveGlobalConfig(content);
            setOriginalContent(content);
            setConfigExists(true);
            setSuccess('Config saved successfully');
            addLog('info', 'Global Config', `Saved to ${result.path}`);
            setTimeout(() => setSuccess(null), 3000);
        } catch (err) {
            const msg = err.message || 'Failed to save';
            setError(msg);
            addLog('error', 'Global Config', msg);
        } finally {
            setSaving(false);
        }
    };

    const isDirty = content !== originalContent;

    if (loading) {
        return (
            <div className="flex items-center justify-center py-12">
                <Loader2 size={24} className="animate-spin" style={{ color: 'var(--color-accent)' }} />
            </div>
        );
    }

    return (
        <div className="space-y-3">
            {/* Path info */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <FileCode2 size={14} style={{ color: 'var(--text-tertiary)' }} />
                    <span className="text-[11px] font-mono" style={{ color: 'var(--text-secondary)' }}>
                        {configPath}
                    </span>
                    {!configExists && (
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-bold"
                            style={{ background: 'rgba(245,158,11,0.15)', color: '#d97706' }}>
                            NEW
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={loadConfig}
                        className="p-1.5 rounded-lg hover:bg-surface-hover"
                        style={{ color: 'var(--text-tertiary)' }}
                        title="Reload"
                    >
                        <RotateCcw size={14} />
                    </button>
                    <button
                        onClick={handleSave}
                        disabled={!isDirty || saving}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all"
                        style={{
                            background: isDirty ? 'var(--accent-blue)' : 'var(--bg-surface)',
                            color: isDirty ? '#fff' : 'var(--text-tertiary)',
                            opacity: (!isDirty || saving) ? 0.5 : 1,
                            cursor: (!isDirty || saving) ? 'not-allowed' : 'pointer',
                        }}
                    >
                        {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                        {saving ? 'Saving...' : 'Save'}
                    </button>
                </div>
            </div>

            {/* Error / Success messages */}
            {error && (
                <div className="flex items-start gap-2 p-3 rounded-lg text-[11px]"
                    style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)' }}>
                    <AlertCircle size={14} className="shrink-0 mt-0.5" style={{ color: '#ef4444' }} />
                    <span style={{ color: '#fca5a5' }}>{error}</span>
                </div>
            )}
            {success && (
                <div className="flex items-start gap-2 p-3 rounded-lg text-[11px]"
                    style={{ background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.2)' }}>
                    <CheckCircle2 size={14} className="shrink-0 mt-0.5" style={{ color: '#10b981' }} />
                    <span style={{ color: '#6ee7b7' }}>{success}</span>
                </div>
            )}

            {/* Editor */}
            <div className="rounded-lg overflow-hidden border" style={{ borderColor: 'var(--border-main)' }}>
                <textarea
                    value={content}
                    onChange={e => setContent(e.target.value)}
                    className="w-full font-mono text-[12px] leading-relaxed p-4 custom-scrollbar"
                    style={{
                        background: 'var(--bg-app)',
                        color: 'var(--text-primary)',
                        border: 'none',
                        outline: 'none',
                        resize: 'vertical',
                        minHeight: 300,
                        tabSize: 2,
                    }}
                    spellCheck={false}
                />
            </div>

            {/* Security note */}
            <div className="flex items-start gap-2 px-4 py-3 rounded-lg text-[11px]"
                style={{ background: 'var(--bg-surface)', color: 'var(--text-tertiary)' }}>
                <ShieldCheck size={14} className="shrink-0 mt-0.5" style={{ color: '#34d399' }} />
                <div>
                    <strong>Security:</strong> Never put passwords or tokens directly in config.
                    Use <code style={{ color: 'var(--accent-blue)' }}>_env</code> suffix keys
                    (e.g. <code style={{ color: 'var(--accent-blue)' }}>client_secret_env: MY_SECRET</code>)
                    to reference environment variables.
                </div>
            </div>
        </div>
    );
}
