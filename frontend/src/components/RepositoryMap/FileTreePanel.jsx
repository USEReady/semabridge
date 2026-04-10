import { useState, memo } from 'react';
import {
    ChevronRight, ChevronDown,
    Folder, FolderOpen,
    FileCode, FileJson, FileText, FileType,
    Database, Settings, Globe, Hash,
} from 'lucide-react';

const ICON_MAP = {
    folder: Folder,
    python: FileCode,
    yaml: FileType,
    json: FileJson,
    sql: Database,
    markdown: FileText,
    config: Settings,
    javascript: FileCode,
    typescript: FileCode,
    css: Globe,
    html: Globe,
    text: FileText,
    file: Hash,
};

const COLOR_MAP = {
    python: '#3B82F6',
    yaml: '#F59E0B',
    json: '#8B5CF6',
    sql: '#10B981',
    markdown: '#6B7280',
    config: '#EF4444',
    javascript: '#EAB308',
    typescript: '#3B82F6',
    css: '#EC4899',
    html: '#F97316',
    folder: '#818CF8',
};

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
}

function formatDate(iso) {
    if (!iso) return '';
    try {
        return new Date(iso).toLocaleDateString(undefined, {
            year: 'numeric', month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
        });
    } catch { return ''; }
}

/**
 * A single tree node (file or folder).
 */
const TreeNode = memo(function TreeNode({
    node, depth, onFileClick, selectedPath,
}) {
    const [expanded, setExpanded] = useState(depth < 1);

    if (!node) return null;

    const isDir = node.type === 'directory';
    const isSelected = node.path === selectedPath;
    const IconComp = isDir
        ? (expanded ? FolderOpen : Folder)
        : (ICON_MAP[node.icon] || FileText);
    const iconColor = isDir
        ? (expanded ? '#818CF8' : '#6366F1')
        : (COLOR_MAP[node.icon] || '#6B7280');

    const handleClick = () => {
        if (isDir) {
            setExpanded(e => !e);
        } else {
            onFileClick(node.path);
        }
    };

    return (
        <>
            <div
                onClick={handleClick}
                title={
                    isDir
                        ? node.name
                        : `${node.name}\n${formatSize(node.size || 0)}\n${formatDate(node.modified)}`
                }
                style={{
                    display: 'flex', alignItems: 'center', gap: 4,
                    padding: '3px 8px 3px ' + (12 + depth * 16) + 'px',
                    cursor: 'pointer',
                    fontSize: 12,
                    color: isSelected ? '#fff' : 'var(--text-primary)',
                    background: isSelected
                        ? 'linear-gradient(90deg, var(--accent-blue-dark) 0%, var(--accent-blue) 100%)'
                        : 'transparent',
                    borderRadius: isSelected ? 4 : 0,
                    transition: 'background .12s, color .12s',
                    userSelect: 'none',
                    overflow: 'hidden',
                    whiteSpace: 'nowrap',
                }}
                onMouseEnter={e => {
                    if (!isSelected) e.currentTarget.style.background = 'var(--bg-surface-hover)';
                }}
                onMouseLeave={e => {
                    if (!isSelected) e.currentTarget.style.background = 'transparent';
                }}
            >
                {/* Chevron for directories */}
                {isDir ? (
                    expanded
                        ? <ChevronDown size={12} style={{ flexShrink: 0, opacity: .5 }} />
                        : <ChevronRight size={12} style={{ flexShrink: 0, opacity: .5 }} />
                ) : (
                    <span style={{ width: 12, flexShrink: 0 }} />
                )}

                <IconComp
                    size={14}
                    style={{ flexShrink: 0, color: isSelected ? '#fff' : iconColor }}
                />

                <span style={{
                    overflow: 'hidden', textOverflow: 'ellipsis',
                    fontWeight: isDir ? 600 : 400,
                    minWidth: 0,
                    flex: 1,
                }}>
                    {node.name}
                </span>

                {/* File size badge */}
                {!isDir && node.size != null && (
                    <span style={{
                        fontSize: 9, opacity: .45,
                        flexShrink: 0,
                        marginLeft: 4,
                    }}>
                        {formatSize(node.size)}
                    </span>
                )}
            </div>

            {/* Children */}
            {isDir && expanded && node.children && (
                <div>
                    {node.children.map((child, i) => (
                        <TreeNode
                            key={child.path || i}
                            node={child}
                            depth={depth + 1}
                            onFileClick={onFileClick}
                            selectedPath={selectedPath}
                        />
                    ))}
                </div>
            )}
        </>
    );
});

/**
 * FileTreePanel — the left sidebar showing the repo file tree.
 */
export default function FileTreePanel({ tree, onFileClick, selectedPath }) {
    if (!tree) {
        return (
            <div style={{ padding: 16, fontSize: 12, color: 'var(--text-tertiary)' }}>
                No tree data available.
            </div>
        );
    }

    return (
        <div style={{ paddingTop: 6, paddingBottom: 12 }}>
            {/* Section header */}
            <div style={{
                padding: '6px 12px',
                fontSize: 10, fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
                color: 'var(--text-tertiary)',
            }}>
                Explorer
            </div>

            {/* Render the root's children (skip root node itself) */}
            {tree.children
                ? tree.children.map((child, i) => (
                    <TreeNode
                        key={child.path || i}
                        node={child}
                        depth={0}
                        onFileClick={onFileClick}
                        selectedPath={selectedPath}
                    />
                ))
                : <TreeNode
                    node={tree}
                    depth={0}
                    onFileClick={onFileClick}
                    selectedPath={selectedPath}
                />
            }
        </div>
    );
}
