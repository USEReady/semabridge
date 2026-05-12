import { useState } from 'react';
import { 
    ChevronDown, 
    Search, 
    Database, 
    Globe, 
    Box,
    Layers,
    Command,
    Bell
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import SearchInput from './common/SearchInput';
import ThemeToggle from './ThemeToggle';
import usePageCache from '../hooks/usePageCache';

export default function ProjectContextHeader({ 
    onShowLogs, 
    onShowVersionControl, 
    unreadCount,
    onShowCommandPalette,
    searchQuery,
    setSearchQuery,
    searchUseRegex,
    setSearchUseRegex
}) {
    const { workspaces, activeWorkspaceId, selectWorkspace, activeWorkspace } = useWorkspace();
    
    // Mapped from recommendation: Environment | Connector
    const [env, setEnv] = usePageCache('header:env', 'Development');
    const [connector, setConnector] = usePageCache('header:connector', 'Snowflake');

    const envs = ['Development', 'Staging', 'Production'];
    const connectors = ['Snowflake', 'BigQuery', 'Postgres', 'Fabric'];

    const selectorStyle = {
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 10px',
        borderRadius: 6,
        border: '1px solid var(--border-color)',
        background: 'var(--bg-app)',
        cursor: 'pointer',
        fontSize: 12,
        fontWeight: 500,
        color: 'var(--text-secondary)',
        transition: 'all 0.15s',
    };

    const labelStyle = {
        fontSize: 10,
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: '0.04em',
        color: 'var(--text-tertiary)',
        marginBottom: -2,
    };

    return (
        <header style={{
            height: 64,
            padding: '0 24px',
            borderBottom: '1px solid var(--border-color)',
            background: 'var(--bg-surface)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            flexShrink: 0,
            zIndex: 100,
        }}>
            {/* Left Spacer to balance the layout (could be used for breadcrumbs later) */}
            <div style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
                {/* Future breadcrumbs or context info */}
            </div>

            {/* 4. Global Search - Centered */}
            <div style={{ 
                width: 800,
                flexShrink: 0,
            }}>
                <div 
                    onClick={() => onShowCommandPalette(true)}
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        width: '100%',
                        height: 38,
                        padding: '0 16px',
                        background: 'var(--bg-app)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 10,
                        cursor: 'text',
                        color: 'var(--text-tertiary)',
                        fontSize: 13,
                        transition: 'all 0.2s ease',
                    }}
                    onMouseOver={(e) => {
                        e.currentTarget.style.borderColor = 'var(--accent-blue)';
                        e.currentTarget.style.background = 'var(--bg-surface-raised)';
                    }}
                    onMouseOut={(e) => {
                        e.currentTarget.style.borderColor = 'var(--border-color)';
                        e.currentTarget.style.background = 'var(--bg-app)';
                    }}
                >
                    <Search size={16} />
                    <span>Search everything...</span>
                    <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
                        <kbd style={{ 
                            background: 'var(--bg-surface)', 
                            border: '1px solid var(--border-color)', 
                            borderRadius: 4, 
                            padding: '2px 6px',
                            fontSize: 11,
                            fontWeight: 700,
                            color: 'var(--text-secondary)',
                        }}>⌘</kbd>
                        <kbd style={{ 
                            background: 'var(--bg-surface)', 
                            border: '1px solid var(--border-color)', 
                            borderRadius: 4, 
                            padding: '2px 6px',
                            fontSize: 11,
                            fontWeight: 700,
                            color: 'var(--text-secondary)',
                        }}>K</kbd>
                    </div>
                </div>
            </div>

            {/* Actions - Right Side */}
            <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12 }}>
                <ThemeToggle />
                
                <button
                    onClick={() => onShowLogs()}
                    style={{
                        ...selectorStyle,
                        width: 34,
                        height: 34,
                        padding: 0,
                        justifyContent: 'center',
                        position: 'relative',
                    }}
                >
                    <Bell size={16} />
                    {unreadCount > 0 && (
                        <span style={{
                            position: 'absolute',
                            top: -2,
                            right: -2,
                            width: 14,
                            height: 14,
                            background: '#EF4444',
                            borderRadius: '50%',
                            color: '#fff',
                            fontSize: 8,
                            fontWeight: 800,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                        }}>
                            {unreadCount}
                        </span>
                    )}
                </button>

                <button
                    onClick={() => onShowVersionControl()}
                    style={{
                        ...selectorStyle,
                        width: 34,
                        height: 34,
                        padding: 0,
                        justifyContent: 'center',
                    }}
                >
                    <Layers size={16} />
                </button>
            </div>
        </header>
    );
}
