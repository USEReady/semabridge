import { useMemo, useState } from 'react';
import { Copy, Search } from 'lucide-react';

  export default function ModelDataPanel({
      graphData,
      // diffReport = null,
      selectedModelId = '__all__',
      selectedTableId = '__all__',
      onSelectTable,
      onOpenTableER,
      erMode = false,
      activeTab: controlledTab = null,
      onTabChange,
      compact = false,
      showTabHeader = true,
  }) {
      const [internalTab, setInternalTab] = useState('model-data');
      const [search, setSearch] = useState('');
      const [filter, setFilter] = useState('all');
      const [selectedKeys, setSelectedKeys] = useState(new Set());
      const [selectedSchema, setSelectedSchema] = useState('all');
      const [selectedEntityKey, setSelectedEntityKey] = useState('');

      const activeTab = controlledTab ?? internalTab;
      const setActiveTab = (tab) => {
          if (!controlledTab) setInternalTab(tab);
          onTabChange?.(tab);
      };

    const nodes = useMemo(() => (Array.isArray(graphData?.nodes) ? graphData.nodes : []), [graphData]);
    const edges = useMemo(() => (Array.isArray(graphData?.edges) ? graphData.edges : []), [graphData]);

    const tables = useMemo(() => nodes.filter(n => n?.data?.nodeType === 'table'), [nodes]);
    const measures = useMemo(() => nodes.filter(n => n?.data?.nodeType === 'measure'), [nodes]); // still called 'measures' in code, but UI will say 'Metrics'
      const relationships = useMemo(() => edges.filter(e => String(e?.id || '').startsWith('rel-')), [edges]);

      const modelRows = useMemo(() => {
          const rows = [];
          const modelScope = erMode && selectedModelId !== '__all__' ? selectedModelId : null;
          tables.forEach((t) => {
              if (modelScope && String(t?.data?.model_id) !== modelScope) return;
              rows.push({
              key: `table:${t.id}`,
              type: 'table',
              name: t?.data?.label || t.id,
              schema: String(t?.data?.schema || 'PUBLIC'),
              details: `${(t?.data?.columns || []).length} columns`,
              raw: t,
          });
          });
           measures.forEach((m) => {
              if (modelScope && String(m?.data?.model_id) !== modelScope) return;
              rows.push({ key: `measure:${m.id}`, type: 'measure', name: m?.data?.label || m.id, details: m?.data?.data_type || 'metric' });
          });
          relationships.forEach((r) => {
              const card = r?.data?.cardinality ? ` [${String(r.data.cardinality)}]` : '';
              const fromCol = r?.data?.from_column || '';
              const toCol = r?.data?.to_column || '';
              const joinCols = fromCol && toCol ? `${fromCol} → ${toCol}` : fromCol;
              const detail = `${r.label || 'relationship'}${card}${joinCols ? ` • ${joinCols}` : ''}`;
              rows.push({
                  key: `relationship:${r.id}`,
                  type: 'relationship',
                  name: `${r.source} -> ${r.target}`,
                  details: detail,
              });
          });
          const q = search.trim().toLowerCase();
          return rows.filter((r) => {
              if (filter !== 'all' && r.type !== filter) return false;
              if (selectedSchema !== 'all' && r.type === 'table' && String(r.schema || '').toLowerCase() !== String(selectedSchema).toLowerCase()) return false;
              if (!q) return true;
              return r.name.toLowerCase().includes(q) || String(r.details).toLowerCase().includes(q);
          });
      }, [tables, measures, relationships, search, filter, selectedSchema, selectedModelId, erMode]);

      const schemaStats = useMemo(() => {
          const map = new Map();
          tables.forEach((t) => {
              const modelScope = erMode && selectedModelId !== '__all__' ? selectedModelId : null;
              if (modelScope && String(t?.data?.model_id) !== modelScope) return;
              const schema = String(t?.data?.schema || 'PUBLIC');
              map.set(schema, (map.get(schema) || 0) + 1);
          });
          return [...map.entries()].sort((a, b) => b[1] - a[1]);
      }, [tables, selectedModelId, erMode]);

      const activeEntity = useMemo(
          () => modelRows.find(r => r.key === selectedEntityKey) || modelRows[0] || null,
          [modelRows, selectedEntityKey]
      );

      const activeTableId = useMemo(() => {
          if (!activeEntity || activeEntity.type !== 'table') return null;
          return activeEntity.raw?.id ? String(activeEntity.raw.id) : null;
      }, [activeEntity]);

      const activeEntityColumns = useMemo(() => {
          if (!activeEntity || activeEntity.type !== 'table') return [];
          const raw = activeEntity.raw;
          return Array.isArray(raw?.data?.columns) ? raw.data.columns : [];
      }, [activeEntity]);

      const selectedRows = useMemo(() => modelRows.filter(r => selectedKeys.has(r.key)), [modelRows, selectedKeys]);

      const copySelected = async () => {
          const text = selectedRows.map(r => `${r.type}\t${r.name}\t${r.details}`).join('\n');
          try { await navigator.clipboard.writeText(text || ''); } catch { /* ignore */ }
      };

    return (
        <div style={{
            borderTop: compact ? 'none' : '1px solid var(--border-color)',
            background: 'var(--bg-surface)',
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
            maxHeight: '100%',
            height: '100%',
        }}>
            {showTabHeader && (
            <div style={{ display: 'flex', gap: 6, padding: '12px 16px', borderBottom: '1px solid var(--border-color)' }}>
                {[{ id: 'model-data', label: 'Schema Explorer' }].map(t => (
                    <button 
                        key={t.id} 
                        onClick={() => setActiveTab(t.id)} 
                        style={{ 
                            border: '1px solid var(--border-color)', 
                            background: activeTab === t.id ? 'rgba(37, 99, 235, 0.10)' : 'var(--bg-app)', 
                            color: activeTab === t.id ? '#2563EB' : 'var(--text-secondary)', 
                            borderRadius: 8, 
                            fontSize: 13, 
                            fontWeight: 600, 
                            padding: '6px 14px', 
                            cursor: 'pointer',
                            transition: 'all 0.2s'
                        }}
                    >
                        {t.label}
                    </button>
                ))}
            </div>
            )}

            {activeTab === 'model-data' && (
                <div style={{ padding: '16px', overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: 12, flex: 1, minHeight: 0 }}>
                    {/* Controls Row */}
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexShrink: 0, flexWrap: 'wrap' }}>
                        <div style={{ 
                            display: 'flex', 
                            alignItems: 'center', 
                            gap: 10, 
                            border: '1px solid var(--border-color)', 
                            borderRadius: 8, 
                            padding: '8px 12px', 
                            background: 'var(--bg-app)', 
                            flex: 2,
                            boxShadow: '0 1px 2px rgba(0,0,0,0.05)',
                            transition: 'border-color 0.2s'
                        }}>
                            <Search size={16} style={{ color: 'var(--text-tertiary)' }} />
                            <input 
                                value={search} 
                                onChange={(e) => setSearch(e.target.value)} 
                                placeholder="Search tables, metrics, or relationships..." 
                                style={{ 
                                    border: 'none', 
                                    outline: 'none', 
                                    background: 'transparent', 
                                    color: 'var(--text-primary)', 
                                    width: '100%', 
                                    fontSize: 14 
                                }} 
                            />
                        </div>
                        <select 
                            value={filter} 
                            onChange={(e) => setFilter(e.target.value)} 
                            style={selectStyle}
                        >
                            <option value="all">All Types</option>
                            <option value="table">Tables</option>
                            <option value="measure">Metrics</option>
                            <option value="relationship">Relationships</option>
                        </select>
                        <select 
                            value={selectedSchema} 
                            onChange={(e) => setSelectedSchema(e.target.value)} 
                            style={selectStyle}
                        >
                            <option value="all">All Schemas</option>
                            {schemaStats.map(([schema, count]) => (
                                <option key={schema} value={schema}>{schema} ({count})</option>
                            ))}
                        </select>
                        <button 
                            onClick={copySelected} 
                            style={{...btnPrimary, background: 'var(--bg-app)', color: 'var(--text-primary)'}}
                        >
                            <Copy size={14} /> Copy Selected
                        </button>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr)', gap: 16, flex: 1, minHeight: 0 }}>
                        {/* List Column */}
                        <div style={{ 
                            border: '1px solid var(--border-color)', 
                            borderRadius: 10, 
                            overflowY: 'auto', 
                            overflowX: 'hidden', 
                            background: 'var(--bg-app)',
                            display: 'flex',
                            flexDirection: 'column'
                        }}>
                            <div style={{ 
                                padding: '12px 14px', 
                                borderBottom: '1px solid var(--border-color)', 
                                fontSize: 11, 
                                color: 'var(--text-tertiary)', 
                                textTransform: 'uppercase', 
                                letterSpacing: '.08em', 
                                fontWeight: 700,
                                background: 'var(--bg-surface)'
                            }}>
                                Schema Explorer
                            </div>
                            <div style={{ flex: 1, overflowY: 'auto' }}>
                                {modelRows.map((r) => {
                                    const active = activeEntity?.key === r.key;
                                    return (
                                        <div
                                            key={r.key}
                                            onClick={() => {
                                                setSelectedEntityKey(r.key);
                                                if (r.type === 'table' && r.raw?.id) {
                                                    onSelectTable?.(String(r.raw.id));
                                                }
                                            }}
                                            style={{
                                                padding: '12px 14px',
                                                borderBottom: '1px solid var(--border-color)',
                                                cursor: 'pointer',
                                                background: active ? 'rgba(37, 99, 235, 0.08)' : 'transparent',
                                                transition: 'background 0.15s'
                                            }}
                                            className="hover:bg-surface-hover"
                                        >
                                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                                                <span style={{ 
                                                    fontSize: 13, 
                                                    fontWeight: 600, 
                                                    color: active ? '#2563EB' : 'var(--text-primary)',
                                                    lineHeight: 1.4
                                                }}>
                                                    {r.name}
                                                </span>
                                                <span style={{ 
                                                    fontSize: 10, 
                                                    color: 'var(--text-tertiary)', 
                                                    background: 'var(--bg-surface)', 
                                                    padding: '2px 6px', 
                                                    borderRadius: 4,
                                                    border: '1px solid var(--border-color)',
                                                    textTransform: 'uppercase'
                                                }}>
                                                    {r.type}
                                                </span>
                                            </div>
                                            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
                                                {r.schema && <span style={{ opacity: 0.8 }}>{r.schema}</span>}
                                                {r.schema && <span style={{ opacity: 0.4 }}>•</span>}
                                                <span>{r.details}</span>
                                            </div>
                                            <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                                                <input 
                                                    type="checkbox" 
                                                    checked={selectedKeys.has(r.key)} 
                                                    onClick={(e) => e.stopPropagation()}
                                                    onChange={(e) => { 
                                                        const next = new Set(selectedKeys); 
                                                        if (e.target.checked) next.add(r.key); 
                                                        else next.delete(r.key); 
                                                        setSelectedKeys(next); 
                                                    }} 
                                                    style={{ cursor: 'pointer' }}
                                                />
                                                <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Select to Copy</span>
                                            </div>
                                        </div>
                                    );
                                })}
                                {!modelRows.length && (
                                    <div style={{ padding: 24, textAlign: 'center', fontSize: 13, color: 'var(--text-tertiary)' }}>
                                        No entities found matching your search.
                                    </div>
                                )}
                            </div>
                        </div>

                        {/* Detail Column */}
                        <div style={{ 
                            border: '1px solid var(--border-color)', 
                            borderRadius: 10, 
                            overflow: 'auto', 
                            background: 'var(--bg-app)', 
                            minWidth: 0,
                            display: 'flex',
                            flexDirection: 'column'
                        }}>
                            <div style={{ 
                                padding: '14px 16px', 
                                borderBottom: '1px solid var(--border-color)', 
                                display: 'flex', 
                                alignItems: 'center', 
                                justifyContent: 'space-between',
                                background: 'var(--bg-surface)'
                            }}>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                                    <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>
                                        {activeEntity ? activeEntity.name : 'Entity Details'}
                                    </span>
                                    {activeEntity?.schema && (
                                        <span style={{ fontSize: 11, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.04em' }}>
                                            Schema: {activeEntity.schema}
                                        </span>
                                    )}
                                </div>
                                {activeEntity?.type === 'table' && (
                                    <div style={{ display: 'flex', gap: 8 }}>
                                        <button
                                            onClick={() => activeTableId && onSelectTable?.(activeTableId)}
                                            style={btnPrimary}
                                        >
                                            Focus in Graph
                                        </button>
                                        <button
                                            onClick={() => activeTableId && onOpenTableER?.(activeTableId)}
                                            style={{ ...btnPrimary, background: 'rgba(37, 99, 235, 0.1)', color: '#2563EB', border: '1px solid rgba(37,99,235,0.2)' }}
                                        >
                                            Open Table ER
                                        </button>
                                    </div>
                                )}
                            </div>

                            {activeEntity ? (
                                <div style={{ padding: 20, flex: 1 }}>
                                    <div style={{ 
                                        display: 'grid', 
                                        gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', 
                                        gap: 16, 
                                        marginBottom: 24 
                                    }}>
                                        <DetailCard label="Type" value={activeEntity.type} />
                                        <DetailCard label="Details" value={activeEntity.details} />
                                    </div>

                                    {activeEntity.type === 'table' && (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                                            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
                                                Columns ({activeEntityColumns.length})
                                            </div>
                                            <div style={{ border: '1px solid var(--border-color)', borderRadius: 8, overflow: 'hidden' }}>
                                                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                                    <thead>
                                                        <tr style={{ background: 'var(--bg-surface)' }}>
                                                            <th style={thStyle}>Column Name</th>
                                                            <th style={thStyle}>Data Type</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {activeEntityColumns.map((c, idx) => (
                                                            <tr key={`ac-${idx}`} style={{ borderBottom: '1px solid var(--border-color)' }}>
                                                                <td style={tdStyle}>{c?.name || c?.unique_name || 'column'}</td>
                                                                <td style={tdStyle}>
                                                                    <code style={{ fontSize: 11, background: 'var(--bg-app)', padding: '2px 4px', borderRadius: 4, color: 'var(--text-secondary)' }}>
                                                                        {c?.data_type || c?.type || 'unknown'}
                                                                    </code>
                                                                </td>
                                                            </tr>
                                                        ))}
                                                        {!activeEntityColumns.length && (
                                                            <tr>
                                                                <td colSpan={2} style={{ ...tdStyle, textAlign: 'center', color: 'var(--text-tertiary)', padding: 30 }}>
                                                                    No columns found for this table.
                                                                </td>
                                                            </tr>
                                                        )}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div style={{ 
                                    flex: 1, 
                                    display: 'flex', 
                                    alignItems: 'center', 
                                    justifyContent: 'center', 
                                    color: 'var(--text-tertiary)',
                                    fontSize: 14,
                                    flexDirection: 'column',
                                    gap: 12
                                }}>
                                    <Search size={32} style={{ opacity: 0.2 }} />
                                    Select an entity from the sidebar to view details.
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function DetailCard({ label, value }) {
    return (
        <div style={{ 
            padding: '12px 16px', 
            borderRadius: 8, 
            background: 'var(--bg-surface)', 
            border: '1px solid var(--border-color)' 
        }}>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 4 }}>
                {label}
            </div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', textTransform: 'capitalize' }}>
                {value}
            </div>
        </div>
    );
}

const selectStyle = {
    border: '1px solid var(--border-color)',
    borderRadius: 8,
    padding: '8px 12px',
    fontSize: 13,
    background: 'var(--bg-app)',
    color: 'var(--text-secondary)',
    cursor: 'pointer',
    outline: 'none',
    minWidth: 140
};

const btnPrimary = {
    border: '1px solid var(--border-color)',
    borderRadius: 8,
    background: '#2563EB',
    color: '#fff',
    padding: '8px 16px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 8,
    transition: 'all 0.2s',
    whiteSpace: 'nowrap'
};

const thStyle = {
    textAlign: 'left',
    padding: '12px 16px',
    borderBottom: '1px solid var(--border-color)',
    color: 'var(--text-tertiary)',
    textTransform: 'uppercase',
    letterSpacing: '.06em',
    fontSize: 11,
    fontWeight: 700
};

const tdStyle = {
    textAlign: 'left',
    padding: '12px 16px',
    color: 'var(--text-primary)',
    fontSize: 13
};
