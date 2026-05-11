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
            height: 56,
            padding: '0 16px',
            borderBottom: '1px solid var(--border-color)',
            background: 'var(--bg-surface)',
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            flexShrink: 0,
            zIndex: 100,
        }}>


            {/* 4. Global Search */}
            <div style={{ flex: 1, display: 'flex', justifyContent: 'center', maxWidth: 400, marginLeft: 'auto' }}>
                <div 
                    onClick={() => onShowCommandPalette(true)}
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        width: '100%',
                        height: 34,
                        padding: '0 12px',
                        background: 'var(--bg-app)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 8,
                        cursor: 'text',
                        color: 'var(--text-tertiary)',
                        fontSize: 13,
                    }}
                >
                    <Search size={14} />
                    <span>Quick Search...</span>
                    <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
                        <kbd style={{ 
                            background: 'var(--bg-surface)', 
                            border: '1px solid var(--border-color)', 
                            borderRadius: 4, 
                            padding: '1px 5px',
                            fontSize: 10,
                            fontWeight: 700,
                        }}>Ctrl</kbd>
                        <kbd style={{ 
                            background: 'var(--bg-surface)', 
                            border: '1px solid var(--border-color)', 
                            borderRadius: 4, 
                            padding: '1px 5px',
                            fontSize: 10,
                            fontWeight: 700,
                        }}>K</kbd>
                    </div>
                </div>
            </div>

            {/* Actions */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
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
