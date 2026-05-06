import { useState, useMemo } from 'react';
import usePageCache from '../hooks/usePageCache';
import {
  UploadCloud, FileText, ArrowRightLeft, Sparkles, Split, X,
  CheckCircle2, AlertCircle, ChevronDown, ChevronRight,
  GitCompare, Layers, Hash, Link2, BarChart3, Search,
} from 'lucide-react';
import PageHeader from '../components/common/PageHeader';

// ─── Format badge colours (one per dialect) ─────────────────────────────────
const FORMAT_META = {
  OSI:            { label: 'OSI',       bg: 'var(--color-accent-faint)',   color: 'var(--accent-blue)' },
  FABRIC_OSI:     { label: 'Fabric',         bg: 'rgba(59,130,246,0.12)',       color: '#3b82f6' },
  SML:            { label: 'SML',            bg: 'rgba(139,92,246,0.12)',        color: 'var(--accent-purple)' },
  TSML:           { label: 'TSML',           bg: 'rgba(249,115,22,0.10)',        color: 'var(--accent-orange)' },
  SNOWFLAKE:      { label: 'Snowflake',      bg: 'rgba(56,189,248,0.10)',        color: 'var(--accent-cyan)' },
  ATSCALE_SML:    { label: 'AtScale SML',    bg: 'rgba(236,72,153,0.10)',        color: '#ec4899' },
  SEMABRIDGE_SML: { label: 'Semabridge SML', bg: 'rgba(16,185,129,0.10)',        color: '#10b981' },
  DBT:            { label: 'dbt MetricFlow', bg: 'rgba(244,63,94,0.10)',         color: '#f43f5e' },
  CUBE:           { label: 'Cube.js',        bg: 'rgba(139,92,246,0.12)',        color: '#8b5cf6' },
  GENERIC:        { label: 'Generic',        bg: 'var(--color-accent-faint)',    color: 'var(--text-secondary)' },
};

// ─── Diff status display config ───────────────────────────────────────────────
const DIFF_STATUS = {
  identical:  { label: 'Identical',   color: 'var(--color-success)',  bg: 'var(--color-success-bg)',   border: 'var(--color-success)' },
  only_in_1:  { label: 'Only in File 1',  color: 'var(--accent-blue)',    bg: 'var(--color-accent-faint)', border: 'var(--accent-blue)' },
  only_in_2:  { label: 'Only in File 2',  color: 'var(--accent-purple)',  bg: 'rgba(139,92,246,0.10)',     border: 'var(--accent-purple)' },
  modified:   { label: 'Modified',    color: 'var(--color-warning)',  bg: 'var(--color-warning-bg)',   border: 'var(--color-warning)' },
};

const FILTER_OPTIONS = [
  { id: 'all',       label: 'All' },
  { id: 'identical', label: 'Identical' },
  { id: 'only_in_1', label: 'Only in File 1' },
  { id: 'only_in_2', label: 'Only in File 2' },
  { id: 'modified',  label: 'Modified' },
];

// ─── LLM provider options ─────────────────────────────────────────────────────
// All models listed here have a developer/free tier. Model IDs are verified
// against official provider API documentation (April 2025).
const LLM_PROVIDERS = [
  // ── Google AI Studio — free developer tier ─────────────────────────────
  { provider: 'google',     model: 'gemini-2.0-flash',                           label: 'Google — Gemini 2.0 Flash' },

  // ── OpenAI ─────────────────────────────────────────────────────────────
  { provider: 'openai',     model: 'gpt-4.1',                                    label: 'OpenAI — GPT-4.1' },
  { provider: 'openai',     model: 'gpt-4.1-mini',                               label: 'OpenAI — GPT-4.1 Mini' },

  // ── Anthropic ──────────────────────────────────────────────────────────
  { provider: 'anthropic',  model: 'claude-3-5-sonnet-20241022',                 label: 'Anthropic — Claude 3.5 Sonnet' },
  { provider: 'anthropic',  model: 'claude-3-5-haiku-20241022',                  label: 'Anthropic — Claude 3.5 Haiku' },

  // ── Groq — free developer tier ─────────────────────────────────────────
  { provider: 'groq',       model: 'meta-llama/llama-4-scout-17b-16e-instruct',  label: 'Groq — Llama 4 Scout 17B' },
  { provider: 'groq',       model: 'llama-3.3-70b-versatile',                    label: 'Groq — Llama 3.3 70B' },
  { provider: 'groq',       model: 'qwen/qwen3-32b',                             label: 'Groq — Qwen3 32B' },
  { provider: 'groq',       model: 'openai/gpt-oss-safeguard-20b',               label: 'Groq — GPT-OSS 20B (Safety)' },

  // ── Cerebras — free developer tier ─────────────────────────────────────
  // Confirmed model IDs from inference-docs.cerebras.ai (April 2025)
  { provider: 'cerebras',   model: 'llama3.1-8b',                                label: 'Cerebras — Llama 3.1 8B' },
  { provider: 'cerebras',   model: 'qwen-3-235b-a22b-instruct-2507',             label: 'Cerebras — Qwen 3 235B A22B' },

  // ── Mistral — free tier on La Plateforme ───────────────────────────────
  { provider: 'mistral',    model: 'mistral-small-latest',                        label: 'Mistral — Small 3.1' },
  { provider: 'mistral',    model: 'mistral-large-latest',                        label: 'Mistral — Large' },

  // ── SambaNova — free developer tier ($5 trial credits) ─────────────────
  { provider: 'sambanova',  model: 'Meta-Llama-3.3-70B-Instruct',                label: 'SambaNova — Llama 3.3 70B' },
  { provider: 'sambanova',  model: 'DeepSeek-V3.1',                              label: 'SambaNova — DeepSeek V3.1' },
  { provider: 'sambanova',  model: 'Llama-4-Maverick-17B-128E-Instruct',         label: 'SambaNova — Llama 4 Maverick 17B' },

  // ── OpenRouter — free tier via :free suffix ────────────────────────────
  { provider: 'openrouter', model: 'google/gemini-2.0-flash-exp:free',           label: 'OpenRouter — Gemini 2.0 Flash (Free)' },
  { provider: 'openrouter', model: 'meta-llama/llama-3.3-70b-instruct:free',     label: 'OpenRouter — Llama 3.3 70B (Free)' },
];

// ─── Small reusable atoms ─────────────────────────────────────────────────────

function FormatBadge({ format }) {
  const meta = FORMAT_META[format] ?? FORMAT_META.GENERIC;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 9px', borderRadius: 20,
      background: meta.bg, color: meta.color,
      fontSize: 11, fontWeight: 700, letterSpacing: '0.04em',
      border: `1px solid ${meta.color}40`,
    }}>
      {meta.label}
    </span>
  );
}

function DiffBadge({ status }) {
  const cfg = DIFF_STATUS[status];
  if (!cfg) return null;
  return (
    <span style={{
      display: 'inline-block', padding: '2px 9px', borderRadius: 20,
      background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}40`,
      fontSize: 11, fontWeight: 700, flexShrink: 0,
      letterSpacing: '0.03em',
    }}>
      {cfg.label}
    </span>
  );
}

function StatCard({ icon: Icon, label, value, accent, breakdown }) {
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
          <Icon size={16} color={accent ?? 'var(--accent-blue)'} />
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

// ─── Upload Card ──────────────────────────────────────────────────────────────

function UploadCard({ index, file, parsedData, onFileChange }) {
  const isDragging = false;
  return (
    <div style={{
      flex: 1, minWidth: 0,
      borderRadius: 12,
      border: file ? '1px solid var(--accent-blue)50' : '2px dashed var(--border-main)',
      background: file ? 'var(--color-accent-faint)' : 'var(--bg-surface)',
      padding: 24,
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      textAlign: 'center', gap: 12,
      transition: 'all var(--transition-normal)',
      position: 'relative',
    }}>
      {file ? (
        <>
          <div style={{
            width: 44, height: 44, borderRadius: 10,
            background: 'var(--color-accent-faint)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <FileText size={22} color="var(--accent-blue)" />
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', wordBreak: 'break-all' }}>
              {file.name}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
              {(file.size / 1024).toFixed(1)} KB
            </div>
          </div>
          {parsedData && <FormatBadge format={parsedData.format} />}
        </>
      ) : (
        <>
          <div style={{
            width: 44, height: 44, borderRadius: 10,
            background: 'var(--bg-surface-raised)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <UploadCloud size={22} color="var(--text-tertiary)" />
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
              {index === 1 ? 'Primary YAML' : 'Secondary YAML'}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>
              {index === 2 ? 'Optional — for comparison diff' : 'OSI · SML · TSML · Snowflake'}
            </div>
          </div>
        </>
      )}

      <label style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '7px 16px', borderRadius: 8,
        background: file ? 'transparent' : 'var(--bg-surface-raised)',
        color: file ? 'var(--accent-blue)' : 'var(--text-secondary)',
        border: file ? '1px solid var(--accent-blue)50' : '1px solid var(--border-main)',
        fontSize: 12, fontWeight: 600, cursor: 'pointer',
        transition: 'all var(--transition-normal)',
      }}>
        {file ? 'Change File' : 'Browse File'}
        <input
          type="file"
          accept=".yaml,.yml"
          style={{ display: 'none' }}
          onChange={(e) => onFileChange(e, index)}
        />
      </label>
    </div>
  );
}

// ─── Single-file Stats Panel ──────────────────────────────────────────────────

function CollapsibleRow({ children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ borderBottom: '1px solid var(--border-main)' }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 14px', background: 'transparent',
          border: 'none', cursor: 'pointer', textAlign: 'left',
        }}
      >
        {open ? <ChevronDown size={14} color="var(--text-tertiary)" /> : <ChevronRight size={14} color="var(--text-tertiary)" />}
        {children[0]}
      </button>
      {open && <div style={{ padding: '0 14px 10px 32px' }}>{children[1]}</div>}
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
          <FormatBadge format={data.format} />
        </div>
        <div style={{ padding: '16px 20px', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <StatCard icon={Layers}   label="Tables"        value={data.summary.total_tables}         accent="var(--accent-blue)" />
          <StatCard icon={Hash}     label="Columns"       value={data.summary.total_columns}        accent="var(--accent-purple)" />
          <StatCard icon={BarChart3} label="Metrics"       value={data.summary.total_metrics}        accent="var(--accent-orange)" />
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
        <SectionHeader title="Metrics" count={filteredMetrics.length} icon={BarChart3} />
        <div style={{ padding: '10px 14px 0', display: 'flex', gap: 10 }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)' }}>
            <Search size={14} color="var(--text-tertiary)" />
            <input
              type="text" placeholder="Search metrics…" value={metSearch}
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

// ─── Diff filter pill bar ─────────────────────────────────────────────────────

function FilterPillBar({ active, onChange, counts }) {
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      {FILTER_OPTIONS.map(opt => {
        const isActive = active === opt.id;
        const cfg = DIFF_STATUS[opt.id] ?? { color: 'var(--text-secondary)', bg: 'var(--bg-surface)', border: 'var(--border-main)' };
        const count = counts?.[opt.id] ?? 0;
        const fillColor = opt.id === 'all' ? 'var(--accent-blue)' : cfg.color;
        const fillBg = opt.id === 'all' ? 'var(--color-accent-faint)' : cfg.bg;
        const fillBorder = opt.id === 'all' ? 'var(--accent-blue)' : cfg.border;

        return (
          <button
            key={opt.id}
            onClick={() => onChange(opt.id)}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '5px 12px', borderRadius: 20,
              background: isActive ? fillBg : 'var(--bg-surface)',
              color: isActive ? fillColor : 'var(--text-tertiary)',
              border: `1px solid ${isActive ? `${fillBorder}60` : 'var(--border-main)'}`,
              fontSize: 12, fontWeight: isActive ? 700 : 500,
              cursor: 'pointer', transition: 'all var(--transition-normal)',
              outline: 'none',
            }}
          >
            {opt.label}
            <span style={{
              background: isActive ? fillColor : 'var(--bg-surface-raised)',
              color: isActive ? '#fff' : 'var(--text-tertiary)',
              padding: '0px 6px', borderRadius: 10, fontSize: 10, fontWeight: 700,
              minWidth: 18, textAlign: 'center',
            }}>
              {opt.id === 'all' ? (counts?.total ?? 0) : count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function UserFriendlyDiffValue({ field, val }) {
  if (val === null || val === undefined || val === '') {
    return <span style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>—</span>;
  }

  const isTrue = val === true || val === 'true' || val === 'True';
  const isFalse = val === false || val === 'false' || val === 'False';

  if (isTrue || isFalse) {
    return (
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700,
        background: isTrue ? 'rgba(16,185,129,0.1)' : 'rgba(107,114,128,0.1)',
        color: isTrue ? 'var(--color-success)' : 'var(--text-secondary)',
        border: `1px solid ${isTrue ? 'rgba(16,185,129,0.2)' : 'rgba(107,114,128,0.2)'}`,
      }}>
        {isTrue ? 'Yes' : 'No'}
      </span>
    );
  }

  if (field === 'type' || field === 'data_type') {
    return (
      <span style={{
        padding: '2px 6px', borderRadius: 4, fontSize: 11, fontWeight: 600,
        background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
        color: 'var(--text-primary)', fontFamily: 'monospace'
      }}>
        {String(val)}
      </span>
    );
  }

  return <span style={{ wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>{String(val)}</span>;
}

// ─── Diff section card ────────────────────────────────────────────────────────

function DiffSection({ title, items, icon: SectionIcon, renderTitle, renderDetail, file1Name, file2Name, enableSearch, enableTypeFilter }) {
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('All Types');

  const availableTypes = useMemo(() => {
    if (!enableTypeFilter) return [];
    const types = new Set();
    items.forEach(item => {
      // In diff mode, type might be at the top level or inside _changes if it was modified
      let typeVal = item.type;
      if (!typeVal && item._changes) {
        const typeChange = item._changes.find(c => c.field === 'type' || c.field === 'data_type');
        if (typeChange) typeVal = typeChange.new_value || typeChange.old_value;
      }
      if (typeVal) types.add(typeVal);
    });
    return Array.from(types).sort();
  }, [items, enableTypeFilter]);

  const filteredItems = useMemo(() => {
    let result = items;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter(item => String(item._id).toLowerCase().includes(q));
    }
    if (enableTypeFilter && typeFilter !== 'All Types') {
      result = result.filter(item => {
        let typeVal = item.type;
        if (!typeVal && item._changes) {
          const typeChange = item._changes.find(c => c.field === 'type' || c.field === 'data_type');
          if (typeChange) typeVal = typeChange.new_value || typeChange.old_value;
        }
        return typeVal === typeFilter;
      });
    }
    return result;
  }, [items, searchQuery, typeFilter, enableTypeFilter]);

  if (items.length === 0) return null;
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-main)',
      borderRadius: 12, marginBottom: 16, overflow: 'hidden',
    }}>
      <SectionHeader title={title} count={filteredItems.length} icon={SectionIcon} />
      
      {(enableSearch || enableTypeFilter) && (
        <div style={{ padding: '10px 14px 0', display: 'flex', gap: 10 }}>
          {enableSearch && (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)' }}>
              <Search size={14} color="var(--text-tertiary)" />
              <input
                type="text" placeholder={`Search ${title.toLowerCase()}...`} value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                style={{ border: 'none', background: 'transparent', outline: 'none', fontSize: 13, color: 'var(--text-primary)', width: '100%' }}
              />
            </div>
          )}
          {enableTypeFilter && availableTypes.length > 0 && (
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
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
      )}

      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {filteredItems.length === 0 ? (
          <div style={{ padding: '10px 14px', fontSize: 13, color: 'var(--text-tertiary)', fontStyle: 'italic', textAlign: 'center' }}>
            No matches found
          </div>
        ) : filteredItems.map(item => {
          const cfg = DIFF_STATUS[item._diff_status] ?? DIFF_STATUS.identical;
          return (
            <div
              key={item._id}
              style={{
                padding: '10px 14px',
                borderRadius: 8,
                background: 'var(--bg-surface-raised)',
                border: `1px solid var(--border-light)`,
                borderLeft: `3px solid ${cfg.color}`,
                display: 'flex', flexDirection: 'column', gap: 6,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
                {renderTitle ? renderTitle(item) : (
                  <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', wordBreak: 'break-word' }}>
                    {item._id}
                  </span>
                )}
                <DiffBadge status={item._diff_status} />
              </div>
              {renderDetail && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {renderDetail(item)}
                </div>
              )}
              {item._diff_status === 'modified' && item._changes && item._changes.length > 0 && (
                <div style={{ marginTop: 8, background: 'var(--bg-surface)', borderRadius: 6, border: '1px solid var(--border-main)', overflow: 'hidden' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 1fr) 1.5fr 1.5fr', gap: 12, background: 'var(--bg-surface-raised)', borderBottom: '1px solid var(--border-main)', padding: '8px 12px', fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)' }}>
                    <div>Changed Property</div>
                    <div>{file1Name || 'Primary File'}</div>
                    <div style={{ color: 'var(--accent-blue)' }}>{file2Name || 'Secondary File'}</div>
                  </div>
                  {item._changes.map((ch, ci) => (
                    <div key={ci} style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 1fr) 1.5fr 1.5fr', gap: 12, padding: '10px 12px', fontSize: 12, borderBottom: ci < item._changes.length - 1 ? '1px solid var(--border-light)' : 'none', alignItems: 'center' }}>
                      <div style={{ fontWeight: 600, color: 'var(--text-primary)', textTransform: 'capitalize' }}>
                        {ch.field.replace(/_/g, ' ')}
                      </div>
                      <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                        <UserFriendlyDiffValue field={ch.field} val={ch.old_value} />
                      </div>
                      <div style={{ color: 'var(--text-primary)', fontSize: 12 }}>
                        <UserFriendlyDiffValue field={ch.field} val={ch.new_value} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Metrics section — LLM results shown inline from backend ──────────────────

function MetricsDiffSection({ items }) {
  const [searchQuery, setSearchQuery] = useState('');

  const filteredItems = useMemo(() => {
    if (!searchQuery) return items;
    const q = searchQuery.toLowerCase();
    return items.filter(item => String(item._id).toLowerCase().includes(q));
  }, [items, searchQuery]);

  if (items.length === 0) return null;

  return (
    <div style={{
      background: 'var(--bg-surface)', border: '1px solid var(--border-main)',
      borderRadius: 12, marginBottom: 16, overflow: 'hidden',
    }}>
      <SectionHeader title="Metrics" count={filteredItems.length} icon={BarChart3} />
      
      <div style={{ padding: '10px 14px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)' }}>
          <Search size={14} color="var(--text-tertiary)" />
          <input
            type="text" placeholder="Search metrics..." value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            style={{ border: 'none', background: 'transparent', outline: 'none', fontSize: 13, color: 'var(--text-primary)', width: '100%' }}
          />
        </div>
      </div>

      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {filteredItems.length === 0 ? (
          <div style={{ padding: '10px 14px', fontSize: 13, color: 'var(--text-tertiary)', fontStyle: 'italic', textAlign: 'center' }}>
            No matches found
          </div>
        ) : filteredItems.map(m => {
          const cfg = DIFF_STATUS[m._diff_status] ?? DIFF_STATUS.identical;
          const isModified = m._diff_status === 'modified' || (m._diff_status === 'identical' && m._llm_verdict != null);
          const llmRes = m._llm_verdict;

          return (
            <div key={m._id} style={{
              borderRadius: 8, overflow: 'hidden',
              border: `1px solid var(--border-light)`,
              borderLeft: `3px solid ${cfg.color}`,
            }}>
              {/* Metric header */}
              <div style={{
                padding: '10px 14px',
                background: 'var(--bg-surface-raised)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10,
              }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', wordBreak: 'break-word' }}>
                  {m._id}
                </span>
                <DiffBadge status={m._diff_status} />
              </div>

              <div style={{ padding: '10px 14px', background: 'var(--bg-surface)' }}>
                {isModified ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {/* Side-by-side definitions */}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                      <div style={{ padding: 10, background: 'var(--bg-surface-raised)', borderRadius: 6, borderLeft: '3px solid var(--accent-blue)' }}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--accent-blue)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>File 1</div>
                        <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>
                          {m._old_definition || '(no definition)'}
                        </div>
                      </div>
                      <div style={{ padding: 10, background: 'var(--bg-surface-raised)', borderRadius: 6, borderLeft: '3px solid var(--accent-purple)' }}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--accent-purple)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>File 2</div>
                        <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>
                          {m._new_definition || m.definition || '(no definition)'}
                        </div>
                      </div>
                    </div>

                    {/* LLM verdict — shown inline (no button, no confidence) */}
                    {llmRes && (
                      llmRes.error ? (
                        <div style={{ padding: '10px 12px', borderRadius: 8, background: 'var(--color-error-bg)', border: '1px solid var(--color-error)30', display: 'flex', alignItems: 'center', gap: 8 }}>
                          <AlertCircle size={14} color="var(--color-error)" />
                          <span style={{ fontSize: 12, color: 'var(--color-error)' }}>{llmRes.error}</span>
                        </div>
                      ) : (
                        <div style={{
                          padding: '12px 14px', borderRadius: 8,
                          background: llmRes.verdict === 'EQUIVALENT' ? 'var(--color-success-bg)' : llmRes.verdict === 'PARTIAL' ? 'var(--color-warning-bg)' : 'var(--color-error-bg)',
                          border: `1px solid ${llmRes.verdict === 'EQUIVALENT' ? 'var(--color-success)' : llmRes.verdict === 'PARTIAL' ? 'var(--color-warning)' : 'var(--color-error)'}30`,
                        }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                            {llmRes.verdict === 'EQUIVALENT'
                              ? <CheckCircle2 size={15} color="var(--color-success)" />
                              : <AlertCircle size={15} color={llmRes.verdict === 'PARTIAL' ? 'var(--color-warning)' : 'var(--color-error)'} />
                            }
                            <span style={{ fontSize: 13, fontWeight: 700, color: llmRes.verdict === 'EQUIVALENT' ? 'var(--color-success)' : llmRes.verdict === 'PARTIAL' ? 'var(--color-warning)' : 'var(--color-error)' }}>
                              {llmRes.verdict}
                            </span>
                          </div>
                          <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>{llmRes.reasoning}</div>
                          {llmRes.key_differences && llmRes.key_differences.length > 0 && (
                            <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                              {llmRes.key_differences.map((d, i) => (
                                <li key={i} style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 3 }}>{d}</li>
                              ))}
                            </ul>
                          )}
                          <div style={{ marginTop: 8, fontSize: 10, color: 'var(--text-tertiary)' }}>
                            via {llmRes.model_used}
                          </div>
                        </div>
                      )
                    )}
                  </div>
                ) : (
                  <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>
                    {m.definition || '(no definition)'}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function ComparatorPage() {
  const [file1, setFile1] = useState(null);
  const [file2, setFile2] = useState(null);
  const [file1Data, setFile1Data] = useState(null);
  const [file2Data, setFile2Data] = useState(null);
  const [compareResults, setCompareResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Cached UI state — survives SPA navigation within the same tab.
  const [compCache, setCompCache] = usePageCache('comparator-page', {
    filterType: 'all',
    selectedProviderIdx: 0,
  });
  const { filterType, selectedProviderIdx } = compCache;
  const setFilterType = (v) => setCompCache({ filterType: typeof v === 'function' ? v(filterType) : v });
  const setSelectedProviderIdx = (v) => setCompCache({ selectedProviderIdx: typeof v === 'function' ? v(selectedProviderIdx) : v });
  const selectedProvider = LLM_PROVIDERS[selectedProviderIdx];

  const resetResults = () => {
    setFile1Data(null);
    setFile2Data(null);
    setCompareResults(null);
    setError('');
    setFilterType('all');
  };

  const handleFileChange = (e, index) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (index === 1) setFile1(f);
    else setFile2(f);
    resetResults();
  };

  const readText = (f) =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (ev) => resolve(ev.target.result);
      reader.onerror = reject;
      reader.readAsText(f);
    });

  const getToken = () => localStorage.getItem('semabridge-token');

  const parseSingleFile = async (file, index) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch('/api/comparator/parse', {
      method: 'POST',
      headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
      body: form,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ?? 'Parse failed');
    if (index === 1) setFile1Data(data);
    else setFile2Data(data);
    return data;
  };

  const handleAnalyze = async () => {
    if (!file1 && !file2) {
      setError('Please upload at least one YAML file.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      if (file1 && !file2) {
        await parseSingleFile(file1, 1);
      } else if (file2 && !file1) {
        await parseSingleFile(file2, 2);
      } else {
        const [f1c, f2c] = await Promise.all([readText(file1), readText(file2)]);
        const res = await fetch('/api/comparator/compare', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
          },
          body: JSON.stringify({
            file1_name: file1.name,
            file1_content: f1c,
            file2_name: file2.name,
            file2_content: f2c,
            // Pass LLM config for auto-evaluation of modified metrics
            provider: selectedProvider.provider,
            model: selectedProvider.model,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail ?? 'Comparison failed');
        setCompareResults(data);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Build per-status counts from compareResults for the filter pill bar
  const filterCounts = useMemo(() => {
    if (!compareResults) return {};
    const all = [
      ...compareResults.tables,
      ...compareResults.columns,
      ...compareResults.metrics,
      ...compareResults.relationships,
    ];
    const counts = {};
    for (const opt of FILTER_OPTIONS) {
      if (opt.id === 'all') counts.all = all.length;
      else counts[opt.id] = all.filter(i => i._diff_status === opt.id).length;
    }
    counts.total = all.length;
    return counts;
  }, [compareResults]);

  const applyFilter = (items) =>
    filterType === 'all' ? items : items.filter(i => i._diff_status === filterType);

  const entityDiffs = useMemo(() => {
    if (!compareResults) return null;
    const getDiffs = (items) => {
      if (!items) return { identical: 0, only_in_1: 0, only_in_2: 0, modified: 0 };
      return {
        identical: items.filter(i => i._diff_status === 'identical').length,
        only_in_1: items.filter(i => i._diff_status === 'only_in_1').length,
        only_in_2: items.filter(i => i._diff_status === 'only_in_2').length,
        modified: items.filter(i => i._diff_status === 'modified').length,
      };
    };
    return {
      tables: getDiffs(compareResults.tables),
      columns: getDiffs(compareResults.columns),
      metrics: getDiffs(compareResults.metrics),
      relationships: getDiffs(compareResults.relationships),
    };
  }, [compareResults]);

  const canAnalyze = (file1 || file2) && !loading;

  return (
    <div style={{ padding: '28px 32px', minHeight: '100%' }}>
      <PageHeader
        title="Semantic Comparator"
        description="Analyse and difference OSI, SML, TSML, and Snowflake semantic model YAML definitions."
      />

      {/* Error banner — matches pattern used across all Semabridge pages */}
      {error && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '10px 14px', borderRadius: 8, marginBottom: 20,
          background: 'var(--color-error-bg)',
          border: '1px solid var(--color-error)30',
          color: 'var(--color-error)', fontSize: 13,
        }}>
          <AlertCircle size={15} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{error}</span>
          <button
            onClick={() => setError('')}
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--color-error)', padding: 0 }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* ── Upload zone ── */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
        <UploadCard index={1} file={file1} parsedData={file1Data} onFileChange={handleFileChange} />
        <div style={{ display: 'flex', alignItems: 'center', color: 'var(--text-tertiary)' }}>
          <GitCompare size={20} />
        </div>
        <UploadCard index={2} file={file2} parsedData={file2Data} onFileChange={handleFileChange} />
      </div>

      {/* ── Action row ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, justifyContent: 'flex-end', marginBottom: 8 }}>
        {/* LLM provider selector — only shown when two files are loaded */}
        {file1 && file2 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Sparkles size={13} color="var(--accent-purple)" />
            <label style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 600 }}>AI Model</label>
            <select
              value={selectedProviderIdx}
              onChange={e => setSelectedProviderIdx(Number(e.target.value))}
              style={{
                padding: '6px 10px', borderRadius: 6, fontSize: 12,
                background: 'var(--bg-input)', color: 'var(--text-primary)',
                border: '1px solid var(--border-main)', outline: 'none',
              }}
            >
              {LLM_PROVIDERS.map((p, i) => (
                <option key={i} value={i}>{p.label}</option>
              ))}
            </select>
          </div>
        )}

        <button
          id="comparator-analyze-btn"
          disabled={!canAnalyze}
          onClick={handleAnalyze}
          className="btn-primary"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 8,
            padding: '9px 22px', borderRadius: 8, fontSize: 13, fontWeight: 700,
            cursor: canAnalyze ? 'pointer' : 'not-allowed',
            opacity: canAnalyze ? 1 : 0.5,
            background: 'var(--accent-blue)', color: '#fff', border: 'none',
            boxShadow: canAnalyze ? '0 2px 8px rgba(88,166,255,0.25)' : 'none',
            transition: 'all var(--transition-normal)',
          }}
          onMouseEnter={(e) => { if (canAnalyze) e.currentTarget.style.opacity = '0.9'; }}
          onMouseLeave={(e) => { e.currentTarget.style.opacity = canAnalyze ? '1' : '0.5'; }}
        >
          {loading ? (
            <>
              <div style={{ width: 14, height: 14, border: '2px solid rgba(255,255,255,0.3)', borderTopColor: '#fff', borderRadius: '50%' }} className="animate-spin" />
              Processing…
            </>
          ) : (
            <>
              {file1 && file2 ? <Split size={15} /> : <FileText size={15} />}
              {file1 && file2 ? 'Compare Files' : 'Analyse File'}
            </>
          )}
        </button>
      </div>

      {/* ── Single-file stats ── */}
      {!compareResults && file1Data && (
        <StatsPanel data={file1Data} fileName={file1?.name ?? 'Primary File'} />
      )}
      {!compareResults && file2Data && !file1 && (
        <StatsPanel data={file2Data} fileName={file2?.name ?? 'Secondary File'} />
      )}

      {/* ── Comparison results ── */}
      {compareResults && (
        <div style={{ marginTop: 24 }}>
          {/* Comparison header */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <GitCompare size={18} color="var(--accent-blue)" />
              <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: 'var(--text-primary)' }}>
                Semantic Difference
              </h2>
              {compareResults.file1_name && <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{compareResults.file1_name}</span>}
              {compareResults.file1_format && <FormatBadge format={compareResults.file1_format} />}
              <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>vs</span>
              {compareResults.file2_name && <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{compareResults.file2_name}</span>}
              {compareResults.file2_format && <FormatBadge format={compareResults.file2_format} />}
            </div>

            {/* Per-file entity counts and Differences summary */}
            {compareResults.file1_summary && compareResults.file2_summary && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginBottom: 14 }}>
                {[
                  { name: compareResults.file1_name, format: compareResults.file1_format, summary: compareResults.file1_summary, accent: 'var(--accent-blue)' },
                  { name: compareResults.file2_name, format: compareResults.file2_format, summary: compareResults.file2_summary, accent: 'var(--accent-purple)' },
                ].map((f, i) => {
                  const makeBreakdown = (diffs) => [
                    { label: 'Identical', value: diffs.identical, bg: 'var(--color-success-bg)', color: 'var(--color-success)' },
                    i === 0 
                      ? { label: 'Only in File 1', value: diffs.only_in_1, bg: 'var(--color-accent-faint)', color: 'var(--accent-blue)' }
                      : { label: 'Only in File 2', value: diffs.only_in_2, bg: 'rgba(139,92,246,0.12)', color: 'var(--accent-purple)' },
                    { label: 'Modified', value: diffs.modified, bg: 'rgba(245,158,11,0.1)', color: 'var(--color-warning)' },
                  ];
                  return (
                    <div key={i} style={{
                      padding: '16px 20px', borderRadius: 12,
                      background: 'var(--bg-surface-raised)',
                      border: `1px solid var(--border-main)`,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                        <FileText size={16} color={f.accent} />
                        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{f.name}</span>
                        {f.format && <FormatBadge format={f.format} />}
                      </div>
                      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                        <StatCard icon={Layers}   label="Tables"        value={f.summary.total_tables}         accent="var(--accent-blue)" breakdown={makeBreakdown(entityDiffs.tables)} />
                        <StatCard icon={Hash}     label="Columns"       value={f.summary.total_columns}        accent="var(--accent-purple)" breakdown={makeBreakdown(entityDiffs.columns)} />
                        <StatCard icon={BarChart3} label="Metrics"       value={f.summary.total_metrics}        accent="var(--accent-orange)" breakdown={makeBreakdown(entityDiffs.metrics)} />
                        <StatCard icon={Link2}    label="Relationships" value={f.summary.total_relationships}   accent="var(--accent-cyan)" breakdown={makeBreakdown(entityDiffs.relationships)} />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Filter pill bar */}
            <FilterPillBar active={filterType} onChange={setFilterType} counts={filterCounts} />
          </div>

          {/* Diff sections */}
          <DiffSection
            title="Tables"
            items={applyFilter(compareResults.tables)}
            icon={Layers}
            renderDetail={(t) => `${t.column_count} col · ${t.metric_count} metric · ${t.relationship_count} rel`}
            file1Name={compareResults.file1_name}
            file2Name={compareResults.file2_name}
          />
          <DiffSection
            title="Columns"
            items={applyFilter(compareResults.columns)}
            icon={Hash}
            renderDetail={(c) => {
              let typeVal = c.type;
              if (!typeVal && c._changes) {
                const typeChange = c._changes.find(ch => ch.field === 'type' || ch.field === 'data_type');
                if (typeChange) typeVal = typeChange.new_value || typeChange.old_value;
              }
              return typeVal ? `Type: ${typeVal}` : null;
            }}
            file1Name={compareResults.file1_name}
            file2Name={compareResults.file2_name}
            enableSearch={true}
            enableTypeFilter={true}
          />
          <DiffSection
            title="Relationships"
            items={applyFilter(compareResults.relationships)}
            icon={Link2}
            file1Name={compareResults.file1_name}
            file2Name={compareResults.file2_name}
            renderTitle={(r) => (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{
                  padding: '3px 8px', borderRadius: 6, fontSize: 12, fontWeight: 600,
                  background: 'var(--color-accent-faint)', color: 'var(--accent-blue)',
                  border: '1px solid var(--accent-blue)25',
                }}>{r.left_table || 'unknown'}</span>
                <ArrowRightLeft size={14} color="var(--text-tertiary)" />
                <span style={{
                  padding: '3px 8px', borderRadius: 6, fontSize: 12, fontWeight: 600,
                  background: 'rgba(139,92,246,0.10)', color: 'var(--accent-purple)',
                  border: '1px solid var(--accent-purple)25',
                }}>{r.right_table || 'unknown'}</span>
                <span style={{
                  fontSize: 10, fontWeight: 700,
                  color: 'var(--accent-cyan)', background: 'rgba(56,189,248,0.10)',
                  padding: '2px 10px', borderRadius: 12, border: '1px solid var(--accent-cyan)30',
                }}>{r.cardinality || 'many-to-one'}</span>
              </div>
            )}
            renderDetail={(r) => (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontFamily: 'monospace', color: 'var(--text-secondary)' }}>
                <span style={{ color: 'var(--accent-blue)' }}>{r.left_column || 'unknown'}</span>
                <span style={{ color: 'var(--text-tertiary)' }}>→</span>
                <span style={{ color: 'var(--accent-purple)' }}>{r.right_column || 'unknown'}</span>
              </div>
            )}
          />
          <MetricsDiffSection
            items={applyFilter(compareResults.metrics)}
          />

          {applyFilter([
            ...compareResults.tables,
            ...compareResults.columns,
            ...compareResults.metrics,
            ...compareResults.relationships,
          ]).length === 0 && (
            <div style={{
              textAlign: 'center', padding: '40px 24px',
              background: 'var(--bg-surface)', border: '1px solid var(--border-main)',
              borderRadius: 12, color: 'var(--text-tertiary)', fontSize: 13,
            }}>
              No items match the current filter.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
