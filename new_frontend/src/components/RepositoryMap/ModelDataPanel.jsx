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
      const measures = useMemo(() => nodes.filter(n => n?.data?.nodeType === 'measure'), [nodes]);
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
              <div style={{ display: 'flex', gap: 6, padding: '8px 12px', borderBottom: '1px solid var(--border-color)' }}>
                  {[{ id: 'model-data', label: 'Schema Explorer' }].map(t => (
                      <button key={t.id} onClick={() => setActiveTab(t.id)} style={{ border: '1px solid var(--border-color)', background: activeTab === t.id ? 'rgba(129,140,248,.14)' : 'var(--bg-app)', color: activeTab === t.id ? '#818CF8' : 'var(--text-secondary)', borderRadius: 6, fontSize: 11, fontWeight: 600, padding: '4px 9px', cursor: 'pointer' }}>
                          {t.label}
                      </button>
                  ))}
              </div>
              )}

              {activeTab === 'model-data' && (
                  <div style={{ padding: '8px 12px', overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: 8, flex: 1, minHeight: 0 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0, flexWrap: 'wrap' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, border: '1px solid var(--border-color)', borderRadius: 6, padding: '2px 8px', background: 'var(--bg-app)', flex: 1 }}>
                              <Search size={12} style={{ color: 'var(--text-tertiary)' }} />
                              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search tables/measures/relationships" style={{ border: 'none', outline: 'none', background: 'transparent', color: 'var(--text-primary)', width: '100%', fontSize: 11 }} />
                          </div>
                          <select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ border: '1px solid var(--border-color)', borderRadius: 6, padding: '4px 6px', fontSize: 11, background: 'var(--bg-app)', color: 'var(--text-secondary)' }}>
                              <option value="all">All</option>
                              <option value="table">Tables</option>
                              <option value="measure">Measures</option>
                              <option value="relationship">Relationships</option>
                          </select>
                          <select value={selectedSchema} onChange={(e) => setSelectedSchema(e.target.value)} style={{ border: '1px solid var(--border-color)', borderRadius: 6, padding: '4px 6px', fontSize: 11, background: 'var(--bg-app)', color: 'var(--text-secondary)' }}>
                              <option value="all">All Schemas</option>
                              {schemaStats.map(([schema, count]) => (
                                  <option key={schema} value={schema}>{schema} ({count})</option>
                              ))}
                          </select>
                          <button onClick={copySelected} style={btnMini}><Copy size={12} /> Copy Selected</button>
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 340px) minmax(0, 1fr)', gap: 10, flex: 1, minHeight: 0 }}>
                          <div style={{ border: '1px solid var(--border-color)', borderRadius: 8, overflowY: 'auto', overflowX: 'hidden', background: 'var(--bg-app)' }}>
                              <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border-color)', fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.05em', fontWeight: 700 }}>
                                  Schema Explorer
                              </div>
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
                                              padding: '8px 10px',
                                              borderBottom: '1px solid var(--border-color)',
                                              cursor: 'pointer',
                                              background: active ? 'rgba(129,140,248,.12)' : 'transparent',
                                          }}
                                      >
                                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                                              <span style={{ fontSize: 11, fontWeight: 700, color: active ? '#818CF8' : 'var(--text-secondary)' }}>{r.name}</span>
                                              <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>{r.type}</span>
                                          </div>
                                          <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.schema ? `${r.schema} • ` : ''}{r.details}</div>
                                          <div style={{ marginTop: 4 }}>
                                              <input type="checkbox" checked={selectedKeys.has(r.key)} onChange={(e) => { const next = new Set(selectedKeys); if (e.target.checked) next.add(r.key); else next.delete(r.key); setSelectedKeys(next); }} />
                                          </div>
                                      </div>
                                  );
                              })}
                              {!modelRows.length && <div style={{ padding: 14, fontSize: 11, color: 'var(--text-tertiary)' }}>No rows found.</div>}
                          </div>

                          <div style={{ border: '1px solid var(--border-color)', borderRadius: 8, overflow: 'auto', background: 'var(--bg-app)', minWidth: 0 }}>
                              <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                  <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)' }}>
                                      {activeEntity ? activeEntity.name : 'Select entity'}
                                  </span>
                                  {activeEntity?.schema && <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>{activeEntity.schema}</span>}
                              </div>

                              {activeEntity ? (
                                  <div style={{ padding: 10 }}>
                                      {activeEntity.type === 'table' && (
                                          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                                              <button
                                                  onClick={() => activeTableId && onSelectTable?.(activeTableId)}
                                                  style={btnMini}
                                              >
                                                  Focus Table
                                              </button>
                                              <button
                                                  onClick={() => activeTableId && onOpenTableER?.(activeTableId)}
                                                  style={{ ...btnMini, border: '1px solid rgba(129,140,248,.55)', color: '#A5B4FC' }}
                                              >
                                                  Open Table ER
                                              </button>
                                              {selectedTableId !== '__all__' && activeTableId === String(selectedTableId) && (
                                                  <span style={{ fontSize: 11, color: '#818CF8', display: 'inline-flex', alignItems: 'center' }}>Focused</span>
                                              )}
                                          </div>
                                      )}
                                      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8 }}>
                                          {activeEntity.details}
                                      </div>
                                      {activeEntity.type === 'table' && (
                                          <div style={{ border: '1px solid var(--border-color)', borderRadius: 6, overflow: 'hidden' }}>
                                              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                                                  <thead>
                                                      <tr>
                                                          <th style={th}>Column</th>
                                                          <th style={th}>Type</th>
                                                      </tr>
                                                  </thead>
                                                  <tbody>
                                                      {activeEntityColumns.map((c, idx) => (
                                                          <tr key={`ac-${idx}`}>
                                                              <td style={td}>{c?.name || c?.unique_name || 'column'}</td>
                                                              <td style={td}>{c?.data_type || c?.type || ''}</td>
                                                          </tr>
                                                      ))}
                                                      {!activeEntityColumns.length && (
                                                          <tr><td colSpan={2} style={{ ...td, color: 'var(--text-tertiary)' }}>No columns available.</td></tr>
                                                      )}
                                                  </tbody>
                                              </table>
                                          </div>
                                      )}
                                  </div>
                              ) : (
                                  <div style={{ padding: 14, fontSize: 11, color: 'var(--text-tertiary)' }}>Select any entity from left list.</div>
                              )}
                          </div>
                      </div>
                  </div>
              )}
          </div>
      );
  }

  const btnMini = {
      border: '1px solid var(--border-color)',
      borderRadius: 6,
      background: 'var(--bg-app)',
      color: 'var(--text-secondary)',
      padding: '4px 8px',
      fontSize: 11,
      cursor: 'pointer',
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6,
  };

  const th = {
      textAlign: 'left',
      padding: '6px 8px',
      borderBottom: '1px solid var(--border-color)',
      color: 'var(--text-tertiary)',
      textTransform: 'uppercase',
      letterSpacing: '.04em',
      fontSize: 10,
  };

  const td = {
      textAlign: 'left',
      padding: '6px 8px',
      borderBottom: '1px solid var(--border-color)',
      color: 'var(--text-secondary)',
  };
