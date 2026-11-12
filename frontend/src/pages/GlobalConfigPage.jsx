import { useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import GlobalConfigEditor from '../components/GlobalConfigEditor';

/**
 * Global configuration — raw YAML editor for ~/.semabridge/config.yaml.
 * Standalone at /global-config; rendered with `embedded` under Settings › Advanced.
 */
export default function GlobalConfigPage({ embedded = false }) {
    const navigate = useNavigate();

    if (embedded) {
        return <GlobalConfigEditor />;
    }

    return (
        <div style={{ padding: '28px 16px', minHeight: '100%', maxWidth: 1400, margin: '0 auto' }} className="md:px-10">
            <div style={{ marginBottom: 20 }}>
                <button
                    onClick={() => navigate('/settings?section=advanced')}
                    style={{
                        background: 'none',
                        border: 'none',
                        cursor: 'pointer',
                        color: 'var(--text-tertiary)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        fontSize: 13,
                        padding: 0
                    }}
                >
                    <ArrowLeft size={14} /> Back to Settings
                </button>
            </div>

            <PageHeader
                title="Global configuration"
                description="Environment variables, default credentials and system-wide settings in ~/.semabridge/config.yaml."
            />

            <div style={{ marginTop: 24 }}>
                <GlobalConfigEditor />
            </div>
        </div>
    );
}
