import { useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import GlobalConfigEditor from '../components/GlobalConfigEditor';

export default function GlobalConfigPage() {
    const navigate = useNavigate();

    return (
        <div style={{ padding: '28px 16px', minHeight: '100%', maxWidth: 1400, margin: '0 auto' }} className="md:px-10">
            <div style={{ marginBottom: 20 }}>
                <button
                    onClick={() => navigate('/settings')}
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
                title="Global Configuration"
                description="Manage environment variables, default credentials, and system-wide settings in ~/.semabridge/config.yaml."
            />

            <div style={{ marginTop: 24 }}>
                <GlobalConfigEditor />
            </div>
        </div>
    );
}
