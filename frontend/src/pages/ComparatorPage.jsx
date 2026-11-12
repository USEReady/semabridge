import { useRef, useState, useMemo } from 'react';
import usePageCache from '../hooks/usePageCache';
import {
  FileText, ArrowRightLeft, X, AlertCircle, ChevronRight, Layers, Hash, Link2,
  BarChart3, Search, Loader2, RefreshCw,
} from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import { api } from '../utils/api';
import DropZone from '../components/compare/DropZone';
import CompareResults from '../components/compare/CompareResults';
import { LLM_PROVIDERS, formatLabel } from '../components/compare/compareUtils';

// Compare (was "Semantic Comparator"): two drop zones, File 1 / File 2.
// Format detection is automatic; the manual format check and the AI model
// picker sit behind "Advanced". Single-file mode shows the inspect stats.

// ─── Inspect (single file) ────────────────────────────────────────────────────

function StatCard({ icon, label, value, accent, breakdown }) {
  const IconCmp = icon;
  return (
    <div style={{
      flex: '1 1 140px', minWidth: 0,
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-main)',
      borderRadius: 10, padding: '14px 16px',
      display: 'flex', flexDirection: 'column', gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{
          width: 34, height: 34, borderRadius: 8, flexShrink: 0,
          background: accent ? `${accent}18` : 'var(--color-accent-faint)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <IconCmp size={16} color={accent ?? 'var(--accent-blue)'} />
        </div>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.1 }}>{value}</div>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>{label}</div>
        </div>
      </div>
      
      {breakdown && breakdown.some(b => b.value > 0) && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
          {breakdown.map((b, i) => b.value > 0 && (
            <div key={i} style={{ 
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '3px 6px 3px 10px', borderRadius: 20, 
              background: b.bg,
              border: `1px solid ${b.color}30`,
              color: b.color, fontSize: 11, fontWeight: 600 
            }}>
              {b.label}
              <span style={{
                background: b.color, color: '#fff',
                padding: '1px 6px', borderRadius: 10, fontSize: 10, fontWeight: 700,
                minWidth: 18, textAlign: 'center'
              }}>
                {b.value}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SectionHeader({ title, count, icon: Icon }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '12px 16px',
      background: 'var(--bg-surface-raised)',
      borderBottom: '1px solid var(--border-main)',
    }}>
      {Icon && <Icon size={15} color="var(--accent-blue)" />}
      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{title}</span>
      <span style={{
        marginLeft: 'auto',
        background: 'var(--color-accent-faint)',
        color: 'var(--accent-blue)',
        border: '1px solid var(--accent-blue)30',
        padding: '1px 8px', borderRadius: 12,
        fontSize: 11, fontWeight: 700,
      }}>{count}</span>
    </div>
  );
}


function StatsPanel({ data, fileName }) {
  const [colSearch, setColSearch] = useState('');
  const [metSearch, setMetSearch] = useState('');
  const [colTypeFilter, setColTypeFilter] = useState('All Types');

  const availableTypes = useMemo(() => {
    if (!data?.columns) return [];
    const types = new Set(data.columns.map(c => c.type || 'unknown'));
    return Array.from(types).sort();
  }, [data]);

  if (!data) return null;

  const filteredCols = data.columns.filter(col => {
    const colType = col.type || 'unknown';
    if (colTypeFilter !== 'All Types' && colType !== colTypeFilter) return false;
    
    if (!colSearch) return true;
    const q = colSearch.toLowerCase();
    return col.name.toLowerCase().includes(q) || col.table.toLowerCase().includes(q);
  });

  const filteredMetrics = data.metrics.filter(m => {
    if (!metSearch) return true;
    const q = metSearch.toLowerCase();
    return m.name.toLowerCase().includes(q) || m.table.toLowerCase().includes(q) || (m.definition || '').toLowerCase().includes(q);
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 24 }}>
      {/* Header and Summary Cards */}
      <div style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        borderRadius: 12, overflow: 'hidden',
      }}>
        <div style={{
          padding: '14px 20px',
          borderBottom: '1px solid var(--border-main)',
          background: 'var(--bg-surface-raised)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <FileText size={16} color="var(--accent-blue)" />
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
            {fileName}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{formatLabel(data.format)}</span>
        </div>
        <div style={{ padding: '16px 20px', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <StatCard icon={Layers}   label="Tables"        value={data.summary.total_tables}         accent="var(--accent-blue)" />
          <StatCard icon={Hash}     label="Columns"       value={data.summary.total_columns}        accent="var(--accent-purple)" />
          <StatCard icon={BarChart3} label="Measures"       value={data.summary.total_metrics}        accent="var(--accent-orange)" />
          <StatCard icon={Link2}    label="Relationships" value={data.summary.total_relationships}   accent="var(--accent-cyan)" />
        </div>
      </div>

      {/* Tables Section */}
      <div style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        borderRadius: 12, overflow: 'hidden',
      }}>
        <SectionHeader title="Tables" count={data.tables.length} icon={Layers} />
        <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {data.tables.length === 0 ? (
            <div style={{ padding: '10px 14px', fontSize: 13, color: 'var(--text-tertiary)', fontStyle: 'italic', textAlign: 'center' }}>No tables detected.</div>
          ) : data.tables.map(t => (
            <div key={t.name} style={{ padding: '10px 14px', borderRadius: 8, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-light)', borderLeft: '3px solid var(--accent-blue)' }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>{t.name}</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t.column_count} col &middot; {t.metric_count} metric &middot; {t.relationship_count} rel</div>
            </div>
          ))}
        </div>
      </div>

      {/* Columns Section */}
      <div style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        borderRadius: 12, overflow: 'hidden',
      }}>
        <SectionHeader title="Columns" count={filteredCols.length} icon={Hash} />
        <div style={{ padding: '10px 14px 0', display: 'flex', gap: 10 }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)' }}>
            <Search size={14} color="var(--text-tertiary)" />
            <input
              type="text" placeholder="Search columns…" value={colSearch}
              onChange={e => setColSearch(e.target.value)}
              style={{ border: 'none', background: 'transparent', outline: 'none', fontSize: 13, color: 'var(--text-primary)', width: '100%' }}
            />
          </div>
          {availableTypes.length > 0 && (
            <select
              value={colTypeFilter}
              onChange={(e) => setColTypeFilter(e.target.value)}
              style={{
                padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)', color: 'var(--text-primary)',
                fontSize: 12, outline: 'none', cursor: 'pointer', maxWidth: '140px'
              }}
            >
              <option value="All Types">All Types</option>
              {availableTypes.map(type => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          )}
        </div>
        <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {filteredCols.length === 0 ? (
            <div style={{ padding: '10px 14px', fontSize: 13, color: 'var(--text-tertiary)', fontStyle: 'italic', textAlign: 'center' }}>{colSearch ? 'No matches' : 'None'}</div>
          ) : filteredCols.map(col => (
            <div key={`${col.table}.${col.name}`} style={{ padding: '10px 14px', borderRadius: 8, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-light)', borderLeft: '3px solid var(--accent-purple)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>
                <span style={{ color: 'var(--text-tertiary)' }}>{col.table}.</span>{col.name}
              </span>
              <span style={{
                fontSize: 10, fontWeight: 600, fontFamily: 'monospace',
                color: 'var(--accent-orange)', background: 'rgba(249,115,22,0.08)',
                padding: '2px 6px', borderRadius: 4,
              }}>{col.type || '—'}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Metrics Section */}
      <div style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        borderRadius: 12, overflow: 'hidden',
      }}>
        <SectionHeader title="Measures" count={filteredMetrics.length} icon={BarChart3} />
        <div style={{ padding: '10px 14px 0', display: 'flex', gap: 10 }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)' }}>
            <Search size={14} color="var(--text-tertiary)" />
            <input
              type="text" placeholder="Search measures…" value={metSearch}
              onChange={e => setMetSearch(e.target.value)}
              style={{ border: 'none', background: 'transparent', outline: 'none', fontSize: 13, color: 'var(--text-primary)', width: '100%' }}
            />
          </div>
        </div>
        <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {filteredMetrics.length === 0 ? (
            <div style={{ padding: '10px 14px', fontSize: 13, color: 'var(--text-tertiary)', fontStyle: 'italic', textAlign: 'center' }}>{metSearch ? 'No matches' : 'None'}</div>
          ) : filteredMetrics.map(m => (
            <div key={`${m.table}.${m.name}`} style={{ padding: '10px 14px', borderRadius: 8, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-light)', borderLeft: '3px solid var(--accent-orange)' }}>
              <div style={{ fontSize: 12, color: 'var(--text-primary)', fontWeight: 600, marginBottom: 6 }}>
                <span style={{ color: 'var(--text-tertiary)' }}>{m.table}.</span>{m.name}
              </div>
              <div style={{ padding: '10px 14px', background: 'var(--bg-surface)', borderRadius: 6, fontSize: 11, fontFamily: 'monospace', color: 'var(--accent-orange)', wordBreak: 'break-all' }}>
                {m.definition}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Relationships Section */}
      {data.relationships.length > 0 && (
        <div style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          borderRadius: 12, overflow: 'hidden',
        }}>
          <SectionHeader title="Relationships" count={data.relationships.length} icon={Link2} />
          <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.relationships.map(r => (
              <div key={r.name} style={{
                padding: '12px 16px', borderRadius: 8,
                background: 'var(--bg-surface-raised)',
                border: '1px solid var(--border-light)',
                borderLeft: '3px solid var(--accent-cyan)'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <span style={{ padding: '3px 8px', borderRadius: 6, fontSize: 12, fontWeight: 600, background: 'var(--color-accent-faint)', color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)25' }}>{r.left_table}</span>
                  <ArrowRightLeft size={14} color="var(--text-tertiary)" />
                  <span style={{ padding: '3px 8px', borderRadius: 6, fontSize: 12, fontWeight: 600, background: 'rgba(139,92,246,0.10)', color: 'var(--accent-purple)', border: '1px solid var(--accent-purple)25' }}>{r.right_table}</span>
                  <span style={{ marginLeft: 'auto', fontSize: 10, fontWeight: 700, color: 'var(--accent-cyan)', background: 'rgba(56,189,248,0.10)', padding: '2px 10px', borderRadius: 12, border: '1px solid var(--accent-cyan)30' }}>{r.cardinality}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontFamily: 'monospace', color: 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--accent-blue)' }}>{r.left_column}</span>
                  <span style={{ color: 'var(--text-tertiary)' }}>→</span>
                  <span style={{ color: 'var(--accent-purple)' }}>{r.right_column}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// ─── Main Page ────────────────────────────────────────────────────────────────

const EXPECTED_FORMATS = ['OSI', 'SML', 'TSML', 'SNOWFLAKE', 'SNOWFLAKE_TSML', 'ATSCALE_SML', 'SEMABRIDGE_SML', 'DBT', 'CUBE', 'GENERIC'];
const AI_OFF = -1;

export default function ComparatorPage() {
  const [file1, setFile1] = useState(null);
  const [file2, setFile2] = useState(null);
  const [file1Data, setFile1Data] = useState(null);
  const [file2Data, setFile2Data] = useState(null);
  const [parsing, setParsing] = useState({ 1: false, 2: false });
  const [parseErrors, setParseErrors] = useState({ 1: '', 2: '' });
  const [compareResults, setCompareResults] = useState(null);
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Cached UI state — survives SPA navigation within the same tab.
  const [compCache, setCompCache] = usePageCache('comparator-page', {
    selectedProviderIdx: 0,
    expectedFormat: 'auto',
  });
  const { selectedProviderIdx, expectedFormat } = compCache;
  const selectedProvider = selectedProviderIdx === AI_OFF ? null : (LLM_PROVIDERS[selectedProviderIdx] || LLM_PROVIDERS[0]);

  // Guards against a slow parse/compare overwriting a newer one.
  const parseSeq = useRef({ 1: 0, 2: 0 });
  const compareSeq = useRef(0);

  const readText = (f) =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (ev) => resolve(ev.target.result);
      reader.onerror = reject;
      reader.readAsText(f);
    });

  const parseFile = async (file, index) => {
    const seq = ++parseSeq.current[index];
    const setData = index === 1 ? setFile1Data : setFile2Data;
    setData(null);
    setParsing((p) => ({ ...p, [index]: true }));
    setParseErrors((e) => ({ ...e, [index]: '' }));
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await api.apiFetch('/comparator/parse', { method: 'POST', body: form, timeoutMs: 120000 });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not read this file');
      if (seq === parseSeq.current[index]) setData(data);
    } catch (err) {
      if (seq === parseSeq.current[index]) setParseErrors((e) => ({ ...e, [index]: err.message }));
    } finally {
      if (seq === parseSeq.current[index]) setParsing((p) => ({ ...p, [index]: false }));
    }
  };

  const runCompare = async (f1 = file1, f2 = file2, provider = selectedProvider) => {
    if (!f1 || !f2) return;
    const seq = ++compareSeq.current;
    setComparing(true);
    setError('');
    try {
      const [f1c, f2c] = await Promise.all([readText(f1), readText(f2)]);
      const res = await api.apiFetch('/comparator/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        timeoutMs: 300000, // the optional AI check can take a while
        body: JSON.stringify({
          file1_name: f1.name,
          file1_content: f1c,
          file2_name: f2.name,
          file2_content: f2c,
          // Optional AI check of changed measures; empty = structural only.
          provider: provider?.provider || '',
          model: provider?.model || '',
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Comparison failed');
      if (seq === compareSeq.current) setCompareResults(data);
    } catch (err) {
      if (seq === compareSeq.current) setError(err.message);
    } finally {
      if (seq === compareSeq.current) setComparing(false);
    }
  };

  const handleFile = (f, index) => {
    if (!/\.ya?ml$/i.test(f.name)) {
      setError('Only .yaml / .yml files can be compared.');
      return;
    }
    setError('');
    setCompareResults(null);
    compareSeq.current += 1;
    if (index === 1) setFile1(f); else setFile2(f);
    parseFile(f, index);
    const other = index === 1 ? file2 : file1;
    if (other) runCompare(index === 1 ? f : file1, index === 1 ? file2 : f);
  };

  const clearFile2 = () => {
    parseSeq.current[2] += 1;
    compareSeq.current += 1;
    setFile2(null);
    setFile2Data(null);
    setParseErrors((e) => ({ ...e, 2: '' }));
    setParsing((p) => ({ ...p, 2: false }));
    setCompareResults(null);
    setComparing(false);
  };

  const swapFiles = () => {
    if (!file1 || !file2) return;
    const [a, b, da, db] = [file2, file1, file2Data, file1Data];
    setFile1(a); setFile2(b); setFile1Data(da); setFile2Data(db);
    setParseErrors((e) => ({ 1: e[2], 2: e[1] }));
    setCompareResults(null);
    runCompare(a, b);
  };

  const singleFile = file1 && !file2 ? { file: file1, data: file1Data } : (!file1 && file2 ? { file: file2, data: file2Data } : null);

  const selectStyle = {
    padding: '6px 10px', borderRadius: 6, fontSize: 13, maxWidth: '100%',
    background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-main)',
  };

  return (
    <div style={{ padding: '28px 32px', minHeight: '100%', maxWidth: 1400, margin: '0 auto' }}>
      <PageHeader
        title="Compare"
        description="Check two semantic model files side by side, or inspect one"
      />

      {error && (
        <div role="alert" style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '10px 14px', borderRadius: 8, marginBottom: 16,
          background: 'var(--color-error-bg)', color: 'var(--color-error)', fontSize: 13,
        }}>
          <AlertCircle size={15} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{error}</span>
          <button onClick={() => setError('')} aria-label="Dismiss" style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--color-error)', padding: 0 }}>
            <X size={14} />
          </button>
        </div>
      )}

      {/* ── Two drop zones ── */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'stretch', flexWrap: 'wrap' }}>
        <DropZone
          label="File 1"
          file={file1}
          parsed={file1Data}
          parsing={parsing[1]}
          error={parseErrors[1]}
          expectedFormat={expectedFormat}
          onFile={(f) => handleFile(f, 1)}
        />
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <button
            type="button"
            onClick={swapFiles}
            disabled={!file1 || !file2}
            title="Swap File 1 and File 2"
            aria-label="Swap File 1 and File 2"
            style={{
              width: 32, height: 32, borderRadius: 999, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-secondary)',
              cursor: file1 && file2 ? 'pointer' : 'default', opacity: file1 && file2 ? 1 : 0.5,
            }}
          >
            <ArrowRightLeft size={14} />
          </button>
        </div>
        <DropZone
          label="File 2"
          optional
          file={file2}
          parsed={file2Data}
          parsing={parsing[2]}
          error={parseErrors[2]}
          expectedFormat={expectedFormat}
          onFile={(f) => handleFile(f, 2)}
          onClear={file2 ? clearFile2 : undefined}
        />
      </div>

      {/* ── Advanced: format check + AI model ── */}
      <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          aria-expanded={advancedOpen}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: 0, border: 'none', background: 'none', cursor: 'pointer', fontSize: 13, color: 'var(--text-secondary)' }}
        >
          <ChevronRight size={14} style={{ transform: advancedOpen ? 'rotate(90deg)' : 'none', transition: 'transform 120ms' }} />
          Advanced
        </button>
        {file1 && file2 && (
          <button
            type="button"
            onClick={() => runCompare()}
            disabled={comparing}
            style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 12px', borderRadius: 6, fontSize: 13, border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-primary)', cursor: comparing ? 'default' : 'pointer', opacity: comparing ? 0.6 : 1 }}
          >
            <RefreshCw size={13} className={comparing ? 'animate-spin' : undefined} /> {comparing ? 'Comparing…' : 'Compare again'}
          </button>
        )}
      </div>
      {advancedOpen && (
        <div style={{
          marginTop: 8, padding: 14, borderRadius: 10, border: '1px solid var(--border-main)', background: 'var(--bg-surface)',
          display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16,
        }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13, color: 'var(--text-primary)' }}>
            File format
            <select value={expectedFormat} onChange={(e) => setCompCache({ expectedFormat: e.target.value })} style={selectStyle}>
              <option value="auto">Auto-detect (recommended)</option>
              {EXPECTED_FORMATS.map((f) => <option key={f} value={f}>{formatLabel(f)}</option>)}
            </select>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
              The format is always detected from the file. Pick one here to be warned if a file is detected as something else.
            </span>
          </label>
          {/* TODO(settings): move this picker to Settings › AI providers (one
              workspace default) once that page exists; keep only an override here. */}
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13, color: 'var(--text-primary)' }}>
            AI check for changed measures
            <select
              value={selectedProviderIdx}
              onChange={(e) => setCompCache({ selectedProviderIdx: Number(e.target.value) })}
              style={selectStyle}
            >
              <option value={AI_OFF}>Off — compare structure only</option>
              {LLM_PROVIDERS.map((p, i) => <option key={p.model} value={i}>{p.label}{i === 0 ? ' (default)' : ''}</option>)}
            </select>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
              When on, changed measure expressions are checked for equivalent meaning; equivalent ones count as identical. Needs that provider's API key on the server.
            </span>
          </label>
        </div>
      )}

      {/* ── Results ── */}
      <div style={{ marginTop: 20 }}>
        {comparing && !compareResults && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-tertiary)' }}>
            <Loader2 size={14} className="animate-spin" /> Comparing {file1?.name} with {file2?.name}…
          </div>
        )}
        {compareResults && (
          <div style={{ opacity: comparing ? 0.6 : 1, transition: 'opacity 120ms' }}>
            <CompareResults
              results={compareResults}
              file1Name={compareResults.file1_name || file1?.name}
              file2Name={compareResults.file2_name || file2?.name}
            />
          </div>
        )}
        {singleFile?.data && (
          <StatsPanel data={singleFile.data} fileName={singleFile.file.name} />
        )}
        {!file1 && !file2 && (
          <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>
            Add one file to see what's in it, or two files to see what differs.
          </div>
        )}
      </div>
    </div>
  );
}
