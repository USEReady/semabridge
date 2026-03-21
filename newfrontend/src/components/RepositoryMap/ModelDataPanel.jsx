import { useMemo, useState } from 'react';
import { Copy, Search, ArrowLeft, Info } from 'lucide-react';

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
      const [inspectorModelId, setInspectorModelId] = useState(selectedModelId || '__all__');

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

      const nodeLabelById = useMemo(() => {
          const map = new Map();
          nodes.forEach((n) => {
              map.set(String(n?.id || ''), String(n?.data?.label || n?.id || ''));
          });
          return map;
      }, [nodes]);

      const tableModelById = useMemo(() => {
          const map = new Map();
          tables.forEach((t) => {
              map.set(String(t?.id || ''), String(t?.data?.model_id || '').trim());
          });
          return map;
      }, [tables]);

      const modelRows = useMemo(() => {
          const rows = [];
          const erScope = erMode && selectedModelId !== '__all__' ? selectedModelId : null;
          const inspectorScope = inspectorModelId !== '__all__' ? inspectorModelId : null;

          const norm = (v) => String(v || '').trim().toLowerCase();
          const matchesScope = (rowModelId, scope) => {
              if (!scope) return true;
              const r = norm(rowModelId);
              const s = norm(scope);
              if (!r) return true; // Keep rows without model id visible instead of empty screen.
              return r === s || r.includes(s) || s.includes(r);
          };

          tables.forEach((t) => {
              const tableModelId = String(t?.data?.model_id || '').trim();
              if (!matchesScope(tableModelId, erScope)) return;
              if (!matchesScope(tableModelId, inspectorScope)) return;
              rows.push({
              key: `table:${t.id}`,
              type: 'table',
              name: t?.data?.label || t.id,
              schema: String(t?.data?.schema || 'PUBLIC'),
              details: `${(t?.data?.columns || []).length} columns`,
              modelId: tableModelId || '__unknown__',
              raw: t,
          });
          });
           measures.forEach((m) => {
              const measureModelId = String(m?.data?.model_id || '').trim();
                  if (!matchesScope(measureModelId, erScope)) return;
                  if (!matchesScope(measureModelId, inspectorScope)) return;
              rows.push({ key: `measure:${m.id}`, type: 'measure', name: m?.data?.label || m.id, details: m?.data?.data_type || 'metric', modelId: measureModelId || '__unknown__' });
          });
          relationships.forEach((r) => {
              const sourceId = String(r?.source || '');
              const targetId = String(r?.target || '');
              const sourceModelId = tableModelById.get(sourceId) || '';
              const targetModelId = tableModelById.get(targetId) || '';
              const relationshipModelId = sourceModelId || targetModelId || '__unknown__';

              if (erScope && !matchesScope(sourceModelId, erScope) && !matchesScope(targetModelId, erScope)) return;
              if (inspectorScope && !matchesScope(sourceModelId, inspectorScope) && !matchesScope(targetModelId, inspectorScope)) return;

              const card = r?.data?.cardinality ? ` [${String(r.data.cardinality)}]` : '';
              const fromCol = r?.data?.from_column || '';
              const toCol = r?.data?.to_column || '';
              const joinCols = fromCol && toCol ? `${fromCol} → ${toCol}` : fromCol;
              const detail = `${r.label || 'relationship'}${card}${joinCols ? ` • ${joinCols}` : ''}`;
              rows.push({
                  key: `relationship:${r.id}`,
                  type: 'relationship',
                  name: `${nodeLabelById.get(sourceId) || sourceId} -> ${nodeLabelById.get(targetId) || targetId}`,
                  details: detail,
                  modelId: relationshipModelId,
              });
          });
          const q = search.trim().toLowerCase();
          return rows.filter((r) => {
              if (filter !== 'all' && r.type !== filter) return false;
              if (selectedSchema !== 'all' && r.type === 'table' && String(r.schema || '').toLowerCase() !== String(selectedSchema).toLowerCase()) return false;
              if (!q) return true;
              return r.name.toLowerCase().includes(q) || String(r.details).toLowerCase().includes(q);
          });
    }, [tables, measures, relationships, search, filter, selectedSchema, selectedModelId, erMode, inspectorModelId, tableModelById, nodeLabelById]);

      const hasActiveFilters = search.trim() || filter !== 'all' || selectedSchema !== 'all' || inspectorModelId !== '__all__';

      const resetFilters = () => {
          setSearch('');
          setFilter('all');
          setSelectedSchema('all');
          setInspectorModelId('__all__');
      };

      const inspectorModelOptions = useMemo(() => {
          const byId = new Map();
          nodes
              .filter(n => n?.data?.nodeType === 'model')
              .forEach((n) => {
                  const id = String(n?.data?.model_id || n?.id || '').trim();
                  if (!id) return;
                  const label = String(n?.data?.label || n?.data?.model_name || id);
                  if (!byId.has(id)) byId.set(id, { id, label });
              });

          nodes
              .filter(n => n?.data?.nodeType === 'table' || n?.data?.nodeType === 'measure')
              .forEach((n) => {
                  const id = String(n?.data?.model_id || '').trim();
                  if (!id || byId.has(id)) return;
                  byId.set(id, { id, label: id });
              });

          return [{ id: '__all__', label: 'All Models' }, ...Array.from(byId.values())];
      }, [nodes]);

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

      const activePreviewRows = useMemo(() => {
          if (!activeEntity || activeEntity.type !== 'table') return [];
          const rows = activeEntity?.raw?.data?.preview_rows;
          return Array.isArray(rows) ? rows.slice(0, 25) : [];
      }, [activeEntity]);

      const activePreviewColumns = useMemo(() => {
          if (!activePreviewRows.length) return [];
          const first = activePreviewRows[0];
          if (!first || typeof first !== 'object') return [];
          return Object.keys(first);
      }, [activePreviewRows]);

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
                          <select value={inspectorModelId} onChange={(e) => setInspectorModelId(e.target.value)} style={{ border: '1px solid var(--border-color)', borderRadius: 6, padding: '4px 6px', fontSize: 11, background: 'var(--bg-app)', color: 'var(--text-secondary)', minWidth: 170 }}>
                              {inspectorModelOptions.map((m) => (
                                  <option key={m.id} value={m.id}>{m.label}</option>
                              ))}
                          </select>
                          <button onClick={resetFilters} style={btnMini} title="Reset all inspector filters">Reset</button>
                          <button onClick={copySelected} style={btnMini}><Copy size={12} /> Copy Selected</button>
                      </div>

                      <div style={{
                          display: 'grid',
                          gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                          gap: 8,
                          flexShrink: 0,
                      }}>
                          <InfoPill label="Visible" value={String(modelRows.length)} />
                          <InfoPill label="Checked" value={String(selectedRows.length)} />
                          <InfoPill label="Active" value={activeEntity ? activeEntity.type : 'none'} />
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
                                              background: active ? 'rgba(129,140,248,.18)' : 'transparent',
                                              borderLeft: active ? '3px solid #818CF8' : '3px solid transparent',
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
                              {!modelRows.length && (
                                  <div style={{ padding: 14, fontSize: 11, color: 'var(--text-tertiary)' }}>
                                      No rows found for current filters.
                                      {hasActiveFilters ? (
                                          <button onClick={resetFilters} style={{ ...btnMini, marginLeft: 8, padding: '3px 7px' }}>Reset Filters</button>
                                      ) : null}
                                  </div>
                              )}
                          </div>

                          <div style={{ border: '1px solid var(--border-color)', borderRadius: 8, overflow: 'auto', background: 'var(--bg-app)', minWidth: 0 }}>
                              <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                                      {!!activeEntity && (
                                          <button
                                              onClick={() => setSelectedEntityKey('')}
                                              style={{ ...btnMini, padding: '3px 7px' }}
                                              title="Back to list"
                                          >
                                              <ArrowLeft size={12} /> Back
                                          </button>
                                      )}
                                      <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                          {activeEntity ? activeEntity.name : 'Select an item from left panel'}
                                      </span>
                                  </div>
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
                                      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8, background: 'var(--bg-surface)', border: '1px solid var(--border-color)', borderRadius: 6, padding: '8px 10px' }}>
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

                                      {activeEntity.type === 'table' && (
                                          <div style={{ marginTop: 10, border: '1px solid var(--border-color)', borderRadius: 6, overflow: 'hidden' }}>
                                              <div style={{ ...th, borderBottom: '1px solid var(--border-color)', background: 'var(--bg-surface)' }}>
                                                  Table Content Preview (Top 25 Rows)
                                              </div>
                                              {activePreviewRows.length > 0 ? (
                                                  <div style={{ overflow: 'auto' }}>
                                                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                                                          <thead>
                                                              <tr>
                                                                  {activePreviewColumns.map((col) => (
                                                                      <th key={`pv-h-${col}`} style={th}>{col}</th>
                                                                  ))}
                                                              </tr>
                                                          </thead>
                                                          <tbody>
                                                              {activePreviewRows.map((row, idx) => (
                                                                  <tr key={`pv-r-${idx}`}>
                                                                      {activePreviewColumns.map((col) => (
                                                                          <td key={`pv-c-${idx}-${col}`} style={td}>{String(row?.[col] ?? '')}</td>
                                                                      ))}
                                                                  </tr>
                                                              ))}
                                                          </tbody>
                                                      </table>
                                                  </div>
                                              ) : (
                                                  <div style={{ padding: 10, fontSize: 11, color: 'var(--text-tertiary)' }}>
                                                      No table content available in this snapshot for the selected table.
                                                  </div>
                                              )}
                                          </div>
                                      )}
                                  </div>
                              ) : (
                                  <div style={{ padding: 14, fontSize: 11, color: 'var(--text-tertiary)' }}>Select any table, measure, or relationship from the left panel to see details.</div>
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

function InfoPill({ label, value }) {
    return (
        <div style={{
            border: '1px solid var(--border-color)',
            borderRadius: 8,
            background: 'var(--bg-app)',
            padding: '7px 9px',
        }}>
            <div style={{ fontSize: 9, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.06em', fontWeight: 700 }}>{label}</div>
            <div style={{ fontSize: 12, fontWeight: 800, color: '#A5B4FC', marginTop: 3 }}>{value}</div>
        </div>
    );
}
