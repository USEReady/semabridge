import { useState } from 'react';
import { UploadCloud, CheckCircle2, AlertCircle, FileText, Split, ArrowRightLeft, Sparkles, Filter } from 'lucide-react';
import PageHeader from '../components/common/PageHeader';

export default function ComparatorPage() {
  const [file1, setFile1] = useState(null);
  const [file2, setFile2] = useState(null);
  const [file1Data, setFile1Data] = useState(null);
  const [file2Data, setFile2Data] = useState(null);

  const [compareResults, setCompareResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [filterType, setFilterType] = useState('all'); // 'all', 'identical', 'only_in_1', 'only_in_2', 'modified'

  const [semanticLoading, setSemanticLoading] = useState({});
  const [semanticResults, setSemanticResults] = useState({});

  const resetState = () => {
    setFile1Data(null);
    setFile2Data(null);
    setCompareResults(null);
    setError('');
    setFilterType('all');
    setSemanticResults({});
  };

  const handleFileUpload = (e, index) => {
    const file = e.target.files[0];
    if (!file) return;

    // reset visual results when new files are uploaded
    if (index === 1) {
      setFile1(file);
    } else {
      setFile2(file);
    }
    resetState();
  };

  const readFileContent = (file) => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (e) => resolve(e.target.result);
      reader.onerror = (e) => reject(e);
      reader.readAsText(file);
    });
  };

  const processSingleFile = async (file, index) => {
    try {
      const formData = new FormData();
      formData.append('file', file);

      const token = localStorage.getItem('semabridge-token');
      const res = await fetch('/api/comparator/parse', {
        method: 'POST',
        headers: { ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
        body: formData
      });

      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || 'Failed to parse file');
      }
      const data = await res.json();
      if (index === 1) setFile1Data(data);
      if (index === 2) setFile2Data(data);

      return data;
    } catch (err) {
      setError(`Error parsing File ${index}: ${err.message}`);
      return null;
    }
  };

  const handleAnalyze = async () => {
    setLoading(true);
    setError('');

    try {
      if (file1 && !file2) {
        // Just analyze one
        await processSingleFile(file1, 1);
      } else if (file2 && !file1) {
        await processSingleFile(file2, 2);
      } else if (file1 && file2) {
        // Semantic Diff Comparison
        const f1Content = await readFileContent(file1);
        const f2Content = await readFileContent(file2);

        const token = localStorage.getItem('semabridge-token');
        const res = await fetch('/api/comparator/compare', {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {})
          },
          body: JSON.stringify({
            file1_name: file1.name,
            file1_content: f1Content,
            file2_name: file2.name,
            file2_content: f2Content
          })
        });

        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || 'Failed to compare files');
        }

        const data = await res.json();
        setCompareResults(data);
      } else {
        setError('Please upload at least one file to analyze.');
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const checkSemanticIdentity = async (metricResult) => {
    if (metricResult._diff_status !== 'modified') return;
    const metricId = metricResult._id;

    setSemanticLoading(prev => ({ ...prev, [metricId]: true }));

    try {
      const token = localStorage.getItem('semabridge-token');
      const res = await fetch('/api/comparator/compare-semantic', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {})
        },
        body: JSON.stringify({
          metric1_definition: metricResult._old_value,
          metric2_definition: metricResult.definition
        })
      });

      if (!res.ok) throw new Error('API Error');
      const data = await res.json();

      setSemanticResults(prev => ({ ...prev, [metricId]: data }));
    } catch (err) {
      setSemanticResults(prev => ({ ...prev, [metricId]: { error: err.message } }));
    } finally {
      setSemanticLoading(prev => ({ ...prev, [metricId]: false }));
    }
  };

  const renderStats = (data, title) => {
    if (!data) return null;
    return (
      <div style={{ background: 'var(--bg-surface)', padding: 24, borderRadius: 12, border: '1px solid var(--border-main)', marginTop: 24 }}>
        <h3 style={{ marginTop: 0, marginBottom: 16, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <FileText size={18} color="var(--accent-blue)" /> {title} Statistics
        </h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 24 }}>
          <div style={{ padding: 16, background: 'var(--bg-subtle)', borderRadius: 8, border: '1px solid var(--border-main)' }}>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>Total Tables</p>
            <p style={{ margin: '4px 0 0', fontSize: 24, fontWeight: 'bold', color: 'var(--text-primary)' }}>{data.summary.total_tables}</p>
          </div>
          <div style={{ padding: 16, background: 'var(--bg-subtle)', borderRadius: 8, border: '1px solid var(--border-main)' }}>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>Total Columns</p>
            <p style={{ margin: '4px 0 0', fontSize: 24, fontWeight: 'bold', color: 'var(--text-primary)' }}>{data.summary.total_columns}</p>
          </div>
          <div style={{ padding: 16, background: 'var(--bg-subtle)', borderRadius: 8, border: '1px solid var(--border-main)' }}>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>Total Metrics</p>
            <p style={{ margin: '4px 0 0', fontSize: 24, fontWeight: 'bold', color: 'var(--text-primary)' }}>{data.summary.total_metrics}</p>
          </div>
          <div style={{ padding: 16, background: 'var(--bg-subtle)', borderRadius: 8, border: '1px solid var(--border-main)' }}>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>Total Relationships</p>
            <p style={{ margin: '4px 0 0', fontSize: 24, fontWeight: 'bold', color: 'var(--text-primary)' }}>{data.summary.total_relationships}</p>
          </div>
        </div>

        {/* Table Details */}
        <h4 style={{ color: 'var(--text-primary)', marginTop: 32, marginBottom: 12 }}>Table Breakdown</h4>
        <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden', marginBottom: 24 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-subtle)', textAlign: 'left' }}>
                <th style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-main)' }}>Table Name</th>
                <th style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-main)' }}>Columns</th>
                <th style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-main)' }}>Metrics</th>
                <th style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-main)' }}>Relationships</th>
              </tr>
            </thead>
            <tbody>
              {data.tables.map(t => (
                <tr key={t.name}>
                  <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-primary)', borderBottom: '1px solid var(--border-subtle)' }}>{t.name}</td>
                  <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-primary)', borderBottom: '1px solid var(--border-subtle)' }}>{t.column_count}</td>
                  <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-primary)', borderBottom: '1px solid var(--border-subtle)' }}>{t.metric_count}</td>
                  <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-primary)', borderBottom: '1px solid var(--border-subtle)' }}>{t.relationship_count}</td>
                </tr>
              ))}
              {data.tables.length === 0 && (
                <tr>
                  <td colSpan={4} style={{ padding: 16, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>No tables found.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Detailed Lists */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
          {/* Columns */}
          <div>
            <h4 style={{ color: 'var(--text-primary)', marginTop: 0, marginBottom: 12 }}>Columns & Datatypes</h4>
            <div style={{ maxHeight: 300, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8, background: 'var(--bg-subtle)' }}>
              {data.columns.map(c => (
                <div key={`${c.table}.${c.name}`} style={{ padding: '8px 12px', borderBottom: '1px solid var(--border-main)', display: 'flex', justifyContent: 'space-between' }}>
                   <span style={{ fontSize: 13, color: 'var(--text-primary)' }}><strong>{c.table}</strong>.{c.name}</span>
                   <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'monospace', background: 'var(--bg-active)', padding: '2px 6px', borderRadius: 4 }}>{c.type}</span>
                </div>
              ))}
              {data.columns.length === 0 && <p style={{ padding: 12, margin: 0, color: 'var(--text-tertiary)', fontSize: 12 }}>No columns found.</p>}
            </div>
          </div>

          {/* Metrics */}
          <div>
            <h4 style={{ color: 'var(--text-primary)', marginTop: 0, marginBottom: 12 }}>Metrics Definitions</h4>
            <div style={{ maxHeight: 300, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8, background: 'var(--bg-subtle)' }}>
              {data.metrics.map(m => (
                <div key={`${m.table}.${m.name}`} style={{ padding: '8px 12px', borderBottom: '1px solid var(--border-main)' }}>
                   <div style={{ fontSize: 13, color: 'var(--text-primary)', marginBottom: 4 }}><strong>{m.table}</strong>.{m.name}</div>
                   <div style={{ fontSize: 11, color: '#f97316', fontFamily: 'monospace', wordBreak: 'break-all', background: 'rgba(249, 115, 22, 0.05)', padding: 6, borderRadius: 4 }}>{m.definition}</div>
                </div>
              ))}
              {data.metrics.length === 0 && <p style={{ padding: 12, margin: 0, color: 'var(--text-tertiary)', fontSize: 12 }}>No metrics found.</p>}
            </div>
          </div>
        </div>

        {/* Relationships */}
        <h4 style={{ color: 'var(--text-primary)', marginTop: 24, marginBottom: 12 }}>Relationships</h4>
        <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-subtle)', textAlign: 'left' }}>
                <th style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-main)' }}>Relationship Name</th>
                <th style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-main)' }}>Left Mapping</th>
                <th style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-main)' }}>Right Mapping</th>
                <th style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-main)' }}>Cardinality</th>
              </tr>
            </thead>
            <tbody>
              {data.relationships.map(r => (
                <tr key={r.name}>
                  <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-primary)', borderBottom: '1px solid var(--border-subtle)' }}>{r.name}</td>
                  <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-subtle)' }}>{r.left_table}.{r.left_column}</td>
                  <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-subtle)' }}>{r.right_table}.{r.right_column}</td>
                  <td style={{ padding: '12px 16px', fontSize: 12, color: 'var(--accent-blue)', borderBottom: '1px solid var(--border-subtle)', fontWeight: 600 }}>{r.cardinality}</td>
                </tr>
              ))}
              {data.relationships.length === 0 && (
                <tr>
                  <td colSpan={4} style={{ padding: 16, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>No relationships found.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  const applyFilter = (items) => {
    if (filterType === 'all') return items;
    return items.filter(item => item._diff_status === filterType);
  };

  const getDiffStatusBadge = (status) => {
    if (status === 'identical') return <span style={{ padding: '2px 8px', borderRadius: 12, background: 'var(--color-success-bg)', color: 'var(--color-success)', fontSize: 11 }}>Identical</span>;
    if (status === 'only_in_1') return <span style={{ padding: '2px 8px', borderRadius: 12, background: 'rgba(249, 115, 22, 0.1)', color: '#f97316', fontSize: 11 }}>File 1 Only</span>;
    if (status === 'only_in_2') return <span style={{ padding: '2px 8px', borderRadius: 12, background: 'rgba(56, 189, 248, 0.1)', color: '#38bdf8', fontSize: 11 }}>File 2 Only</span>;
    if (status === 'modified') return <span style={{ padding: '2px 8px', borderRadius: 12, background: 'rgba(234, 186, 62, 0.1)', color: '#eaba3e', fontSize: 11 }}>Modified</span>;
    return null;
  };

  const renderComparison = () => {
    if (!compareResults) return null;

    return (
      <div style={{ marginTop: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <h2 style={{ margin: 0, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <Split size={20} color="var(--accent-blue)" /> Semantic Comparison
          </h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Filter size={14} color="var(--text-secondary)" />
            <select
              value={filterType}
              onChange={e => setFilterType(e.target.value)}
              style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-main)', padding: '6px 12px', borderRadius: 6, fontSize: 13 }}
            >
              <option value="all">View All Variations</option>
              <option value="identical">Identical in Both</option>
              <option value="only_in_1">Only in File 1 ({compareResults.file1_name})</option>
              <option value="only_in_2">Only in File 2 ({compareResults.file2_name})</option>
              <option value="modified">Modified (Conflict)</option>
            </select>
          </div>
        </div>

        {/* Helper function to generate sections */}
        {renderCompareSection("Tables", applyFilter(compareResults.tables), (t) => (
          <span>Columns: {t.column_count} | Metrics: {t.metric_count}</span>
        ))}
        {renderCompareSection("Columns", applyFilter(compareResults.columns), (c) => (
          <span>Type: {c.type}</span>
        ))}
        {renderCompareSection("Relationships", applyFilter(compareResults.relationships), (r) => (
          <span>{r.left_table}.{r.left_column} <ArrowRightLeft size={10} style={{ display: 'inline', margin: '0 4px' }} /> {r.right_table}.{r.right_column} ({r.cardinality})</span>
        ))}

        {/* Metrics Section gets special LLM Logic */}
        <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 12, marginBottom: 24, overflow: 'hidden' }}>
          <div style={{ background: 'var(--bg-surface-raised)', padding: '12px 16px', borderBottom: '1px solid var(--border-main)' }}>
            <h4 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 15 }}>Metrics ({applyFilter(compareResults.metrics).length})</h4>
          </div>
          <div style={{ padding: 16 }}>
            {applyFilter(compareResults.metrics).length === 0 ? (
              <p style={{ margin: 0, color: 'var(--text-tertiary)', fontSize: 13, fontStyle: 'italic' }}>No metrics found matching the current filter.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {applyFilter(compareResults.metrics).map(m => {
                  const isSemanticChecked = !!semanticResults[m._id];
                  const semRes = semanticResults[m._id];
                  return (
                    <div key={m._id} style={{ display: 'flex', flexDirection: 'column', padding: 12, background: 'var(--bg-subtle)', borderRadius: 8, border: '1px solid var(--border-main)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                        <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: 14 }}>{m._id}</span>
                        {getDiffStatusBadge(m._diff_status)}
                      </div>
                      <div style={{ color: 'var(--text-secondary)', fontSize: 13, fontFamily: 'monospace', wordBreak: 'break-all' }}>
                        {m._diff_status === 'modified' ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                            <div style={{ background: 'rgba(249, 115, 22, 0.05)', padding: 8, borderRadius: 6, borderLeft: '3px solid #f97316' }}>
                              <strong style={{ display: 'block', fontSize: 10, color: '#f97316', marginBottom: 4 }}>FILE 1:</strong>
                              {m._old_value}
                            </div>
                            <div style={{ background: 'rgba(56, 189, 248, 0.05)', padding: 8, borderRadius: 6, borderLeft: '3px solid #38bdf8' }}>
                              <strong style={{ display: 'block', fontSize: 10, color: '#38bdf8', marginBottom: 4 }}>FILE 2:</strong>
                              {m.definition}
                            </div>

                            {/* 2nd Level Semantic Diff Action */}
                            <div style={{ marginTop: 8 }}>
                              {semanticLoading[m._id] ? (
                                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                                  <div className="spinner" style={{ width: 12, height: 12 }} /> Analyzing semantics...
                                </div>
                              ) : !isSemanticChecked ? (
                                <button
                                  onClick={() => checkSemanticIdentity(m)}
                                  style={{ background: 'var(--bg-input)', border: '1px solid var(--border-main)', color: 'var(--text-primary)', padding: '6px 12px', borderRadius: 6, fontSize: 12, top: 4, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
                                >
                                  <Sparkles size={14} color="#a855f7" /> Evaluate Logical Identity (AI)
                                </button>
                              ) : semRes.error ? (
                                <p style={{ margin: 0, color: 'var(--color-error)', fontSize: 12 }}>{semRes.error}</p>
                              ) : (
                                <div style={{ background: semRes.is_semantically_identical ? 'var(--color-success-bg)' : 'var(--color-error-bg)', padding: '12px', borderRadius: 8, border: `1px solid ${semRes.is_semantically_identical ? 'var(--color-success)' : 'var(--color-error)'}30` }}>
                                  <p style={{ margin: 0, fontWeight: 600, fontSize: 13, color: semRes.is_semantically_identical ? 'var(--color-success)' : 'var(--color-error)', display: 'flex', alignItems: 'center', gap: 6 }}>
                                    {semRes.is_semantically_identical ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                                    {semRes.is_semantically_identical ? 'Semantically Identical' : 'Logically Different'}
                                  </p>
                                  <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-primary)' }}>{semRes.explanation}</p>
                                </div>
                              )}
                            </div>
                          </div>
                        ) : (
                          <span>{m.definition || "No definition provided"}</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

      </div>
    );
  };

  const renderCompareSection = (title, items, detailRenderer) => {
    return (
      <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 12, marginBottom: 24, overflow: 'hidden' }}>
        <div style={{ background: 'var(--bg-surface-raised)', padding: '12px 16px', borderBottom: '1px solid var(--border-main)' }}>
          <h4 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 15 }}>{title} ({items.length})</h4>
        </div>
        <div style={{ padding: 16 }}>
          {items.length === 0 ? (
            <p style={{ margin: 0, color: 'var(--text-tertiary)', fontSize: 13, fontStyle: 'italic' }}>No {title.toLowerCase()} found matching the current filter.</p>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
              {items.map(item => (
                <div key={item._id} style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 12, background: 'var(--bg-subtle)', borderRadius: 8, border: '1px solid var(--border-main)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: 13, wordBreak: 'break-word', lineHeight: 1.4 }}>{item._id}</span>
                    <div style={{ flexShrink: 0 }}>
                       {getDiffStatusBadge(item._diff_status)}
                    </div>
                  </div>
                  <div style={{ color: 'var(--text-secondary)', fontSize: 12, wordBreak: 'break-word', lineHeight: 1.4 }}>{detailRenderer(item)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: '28px 32px' }}>
      <PageHeader
        title="Semantic YAML Comparator"
        description="Advanced tool to analyze and diff OSV, SML, and Snowflake semantic definitions."
      />

      {error && (
        <div style={{ background: 'var(--color-error-bg)', color: 'var(--color-error)', border: '1px solid var(--color-error)40', padding: '12px 16px', borderRadius: 8, marginBottom: 24, display: 'flex', alignItems: 'center', gap: 12 }}>
          <AlertCircle size={18} /> <span>{error}</span>
        </div>
      )}

      {/* File Upload Area */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
        <div style={{ border: '2px dashed var(--border-main)', padding: 32, borderRadius: 12, textAlign: 'center', background: 'var(--bg-surface)' }}>
          <UploadCloud size={32} color="var(--accent-blue)" style={{ margin: '0 auto 16px' }} />
          <h3 style={{ margin: '0 0 8px', color: 'var(--text-primary)' }}>{file1 ? file1.name : "Upload Primary YAML"}</h3>
          <p style={{ margin: '0 0 16px', color: 'var(--text-secondary)', fontSize: 13 }}>Click to browse or drag 'n drop.</p>
          <label style={{ cursor: 'pointer', background: 'var(--bg-input)', padding: '8px 16px', borderRadius: 8, border: '1px solid var(--border-main)', color: 'var(--text-primary)', fontSize: 13, fontWeight: 500 }}>
            Browse File
            <input type="file" style={{ display: 'none' }} onChange={(e) => handleFileUpload(e, 1)} />
          </label>
        </div>

        <div style={{ border: '2px dashed var(--border-main)', padding: 32, borderRadius: 12, textAlign: 'center', background: 'var(--bg-surface)' }}>
          <Split size={32} color="var(--color-warning)" style={{ margin: '0 auto 16px' }} />
          <h3 style={{ margin: '0 0 8px', color: 'var(--text-primary)' }}>{file2 ? file2.name : "Upload Secondary YAML (Optional)"}</h3>
          <p style={{ margin: '0 0 16px', color: 'var(--text-secondary)', fontSize: 13 }}>For performing semantic diffs.</p>
          <label style={{ cursor: 'pointer', background: 'var(--bg-input)', padding: '8px 16px', borderRadius: 8, border: '1px solid var(--border-main)', color: 'var(--text-primary)', fontSize: 13, fontWeight: 500 }}>
            Browse File
            <input type="file" style={{ display: 'none' }} onChange={(e) => handleFileUpload(e, 2)} />
          </label>
        </div>
      </div>

      <div style={{ marginTop: 24, display: 'flex', justifyContent: 'flex-end', gap: 12 }}>
        <button
          disabled={loading || (!file1 && !file2)}
          onClick={handleAnalyze}
          style={{
            background: 'var(--accent-blue)',
            color: '#fff',
            border: 'none',
            padding: '10px 24px',
            borderRadius: 8,
            cursor: (loading || (!file1 && !file2)) ? 'not-allowed' : 'pointer',
            opacity: (loading || (!file1 && !file2)) ? 0.7 : 1,
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            gap: 8
          }}
        >
          {loading ? 'Processing...' : (file1 && file2 ? 'Run Comparison' : 'Analyze File')}
        </button>
      </div>

      {(!compareResults && file1Data) && renderStats(file1Data, `Primary File (${file1.name})`)}
      {(!compareResults && file2Data && !file1) && renderStats(file2Data, `Secondary File (${file2.name})`)}

      {renderComparison()}

    </div>
  );
}
