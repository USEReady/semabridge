import { useState, useMemo, useCallback } from 'react';
import {
  UploadCloud, FileText, Split, ArrowRightLeft, Sparkles,
  CheckCircle2, AlertCircle, X, ChevronDown, ChevronRight,
  GitCompare, Layers, Hash, Link2, BarChart3,
} from 'lucide-react';
import PageHeader from '../components/common/PageHeader';

// ─── Format badge colours (one per dialect) ─────────────────────────────────
const FORMAT_META = {
  OSI:            { label: 'OSI v1.0',       bg: 'var(--color-accent-faint)',   color: 'var(--accent-blue)' },
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
  identical:     { label: 'Identical',           color: 'var(--color-success)',   bg: 'var(--color-success-bg)',   border: 'var(--color-success)' },
  only_in_1:     { label: 'Only in F1',           color: 'var(--accent-blue)',     bg: 'var(--color-accent-faint)', border: 'var(--accent-blue)' },
  only_in_2:     { label: 'Only in F2',           color: 'var(--accent-purple)',   bg: 'rgba(139,92,246,0.10)',     border: 'var(--accent-purple)' },
  modified_in_1: { label: 'Modified in F1',       color: 'var(--color-warning)',   bg: 'var(--color-warning-bg)',   border: 'var(--color-warning)' },
  modified_in_2: { label: 'Modified in F2',       color: 'var(--accent-orange)',   bg: 'rgba(249,115,22,0.08)',     border: 'var(--accent-orange)' },
};

const FILTER_OPTIONS = [
  { id: 'all',           label: 'All' },
  { id: 'identical',     label: 'Identical' },
  { id: 'only_in_1',     label: 'Only in F1' },
  { id: 'only_in_2',     label: 'Only in F2' },
  { id: 'modified_in_1', label: 'Modified in F1' },
  { id: 'modified_in_2', label: 'Modified in F2' },
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

function StatCard({ icon: Icon, label, value, accent }) {
  return (
    <div style={{
      flex: '1 1 140px', minWidth: 0,
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-main)',
      borderRadius: 10, padding: '14px 16px',
      display: 'flex', alignItems: 'center', gap: 12,
    }}>
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
  if (!data) return null;
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-main)',
      borderRadius: 12, marginTop: 24, overflow: 'hidden',
    }}>
      {/* Header */}
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

      {/* Summary stat cards */}
      <div style={{ padding: '16px 20px', display: 'flex', gap: 12, flexWrap: 'wrap', borderBottom: '1px solid var(--border-main)' }}>
        <StatCard icon={Layers}   label="Tables"        value={data.summary.total_tables}         accent="var(--accent-blue)" />
        <StatCard icon={Hash}     label="Columns"       value={data.summary.total_columns}        accent="var(--accent-purple)" />
        <StatCard icon={BarChart3} label="Metrics"       value={data.summary.total_metrics}        accent="var(--accent-orange)" />
        <StatCard icon={Link2}    label="Relationships" value={data.summary.total_relationships}   accent="var(--accent-cyan)" />
      </div>

      {/* Table breakdown */}
      <div style={{ borderBottom: '1px solid var(--border-main)' }}>
        <div style={{ padding: '10px 16px 6px', fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Tables
        </div>
        {data.tables.length === 0 ? (
          <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-tertiary)', fontStyle: 'italic' }}>No tables detected.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-surface-raised)' }}>
                {['Table', 'Columns', 'Metrics', 'Relationships'].map(h => (
                  <th key={h} style={{ padding: '8px 16px', textAlign: 'left', fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid var(--border-main)' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.tables.map((t, i) => (
                <tr key={t.name} style={{ borderTop: i > 0 ? '1px solid var(--border-light)' : 'none' }}>
                  <td style={{ padding: '10px 16px', fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{t.name}</td>
                  <td style={{ padding: '10px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>{t.column_count}</td>
                  <td style={{ padding: '10px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>{t.metric_count}</td>
                  <td style={{ padding: '10px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>{t.relationship_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Columns & Metrics side-by-side */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid var(--border-main)' }}>
        {/* Columns */}
        <div style={{ borderRight: '1px solid var(--border-main)' }}>
          <div style={{ padding: '10px 16px 6px', fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Columns</div>
          <div style={{ maxHeight: 240, overflowY: 'auto' }} className="custom-scrollbar">
            {data.columns.length === 0 ? (
              <div style={{ padding: '10px 16px', fontSize: 12, color: 'var(--text-tertiary)', fontStyle: 'italic' }}>None</div>
            ) : data.columns.map(col => (
              <div key={`${col.table}.${col.name}`} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '7px 14px', borderBottom: '1px solid var(--border-light)' }}>
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

        {/* Metrics */}
        <div>
          <div style={{ padding: '10px 16px 6px', fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Metrics</div>
          <div style={{ maxHeight: 240, overflowY: 'auto' }} className="custom-scrollbar">
            {data.metrics.length === 0 ? (
              <div style={{ padding: '10px 16px', fontSize: 12, color: 'var(--text-tertiary)', fontStyle: 'italic' }}>None</div>
            ) : data.metrics.map(m => (
              <div key={`${m.table}.${m.name}`} style={{ padding: '7px 14px', borderBottom: '1px solid var(--border-light)' }}>
                <div style={{ fontSize: 12, color: 'var(--text-primary)', fontWeight: 600, marginBottom: 3 }}>
                  <span style={{ color: 'var(--text-tertiary)' }}>{m.table}.</span>{m.name}
                </div>
                <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--accent-orange)', wordBreak: 'break-all' }}>
                  {m.definition}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Relationships */}
      {data.relationships.length > 0 && (
        <div>
          <div style={{ padding: '10px 16px 6px', fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Relationships</div>
          {data.relationships.map(r => (
            <div key={r.name} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 14px', borderBottom: '1px solid var(--border-light)', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', minWidth: 120 }}>{r.name}</span>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                {r.left_table}.{r.left_column}
              </span>
              <ArrowRightLeft size={12} color="var(--text-tertiary)" />
              <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                {r.right_table}.{r.right_column}
              </span>
              <span style={{
                marginLeft: 'auto', fontSize: 10, fontWeight: 700,
                color: 'var(--accent-blue)', background: 'var(--color-accent-faint)',
                padding: '2px 8px', borderRadius: 12, border: '1px solid var(--accent-blue)30',
              }}>{r.cardinality}</span>
            </div>
          ))}
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

// ─── Diff section card ────────────────────────────────────────────────────────

function DiffSection({ title, items, icon: SectionIcon, renderDetail }) {
  if (items.length === 0) return null;
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-main)',
      borderRadius: 12, marginBottom: 16, overflow: 'hidden',
    }}>
      <SectionHeader title={title} count={items.length} icon={SectionIcon} />
      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.map(item => {
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
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', wordBreak: 'break-word' }}>
                  {item._id}
                </span>
                <DiffBadge status={item._diff_status} />
              </div>
              {renderDetail && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {renderDetail(item)}
                </div>
              )}
              {item._diff_status === 'modified' && item._changes && item._changes.length > 0 && (
                <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {item._changes.map((ch, ci) => (
                    <div key={ci} style={{ display: 'flex', gap: 8, fontSize: 11, fontFamily: 'monospace', flexWrap: 'wrap' }}>
                      <span style={{ color: 'var(--text-tertiary)', fontFamily: 'inherit', fontWeight: 600 }}>{ch.field}:</span>
                      <span style={{ color: 'var(--color-error)', textDecoration: 'line-through' }}>{String(ch.old_value ?? '—')}</span>
                      <span style={{ color: 'var(--text-tertiary)' }}>→</span>
                      <span style={{ color: 'var(--color-success)' }}>{String(ch.new_value ?? '—')}</span>
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

// ─── Metrics section — with LLM trigger ───────────────────────────────────────

function MetricsDiffSection({ items, selectedProvider, onLlmResult, llmResults, llmLoading }) {
  if (items.length === 0) return null;

  const triggerLlm = useCallback(async (metric) => {
    const isModified = metric._diff_status === 'modified_in_1' || metric._diff_status === 'modified_in_2';
    if (!isModified) return;

    // Use _base_id (without ::f1/::f2 suffix) as the shared state key so both
    // the f1-row and the f2-row cards share a single LLM response.
    const key = metric._base_id ?? metric._id.split('::')[0];
    onLlmResult(key, null, true); // loading

    try {
      const token = localStorage.getItem('semabridge-token');
      const res = await fetch('/api/comparator/compare-semantic', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          metric1_name: metric.name ?? key,
          metric1_definition: metric._old_definition ?? '',
          metric2_name: metric.name ?? key,
          metric2_definition: metric._new_definition ?? metric.definition ?? '',
          provider: selectedProvider.provider,
          model: selectedProvider.model,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        onLlmResult(key, { error: data.detail ?? 'LLM request failed' }, false);
      } else {
        onLlmResult(key, data, false);
      }
    } catch (err) {
      onLlmResult(key, { error: err.message }, false);
    }
  }, [onLlmResult, selectedProvider]);

  return (
    <div style={{
      background: 'var(--bg-surface)', border: '1px solid var(--border-main)',
      borderRadius: 12, marginBottom: 16, overflow: 'hidden',
    }}>
      <SectionHeader title="Metrics" count={items.length} icon={BarChart3} />
      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {items.map(m => {
          const cfg = DIFF_STATUS[m._diff_status] ?? DIFF_STATUS.identical;
          // Shared LLM state key — strips ::f1/::f2 suffix so both rows share one verdict
          const llmKey = m._base_id ?? m._id.split('::')[0];
          const llmRes = llmResults[llmKey];
          const isLoading = llmLoading[llmKey] === true;
          const isModified = m._diff_status === 'modified_in_1' || m._diff_status === 'modified_in_2';
          // Display name — strips ::f1/::f2 suffix that was added for React key uniqueness
          const displayName = m._base_id ?? m._id.replace(/::f[12]$/, '');

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
                  {displayName}
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

                    {/* LLM comparison area */}
                    {llmRes ? (
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
                            {typeof llmRes.confidence === 'number' && (
                              <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                                {(llmRes.confidence * 100).toFixed(0)}% confidence
                              </span>
                            )}
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
                    ) : (
                      <button
                        onClick={() => triggerLlm(m)}
                        disabled={isLoading}
                        style={{
                          display: 'inline-flex', alignItems: 'center', gap: 7,
                          padding: '7px 14px', borderRadius: 8,
                          background: isLoading ? 'var(--bg-surface-raised)' : 'transparent',
                          border: '1px solid var(--border-main)',
                          color: 'var(--text-secondary)',
                          fontSize: 12, fontWeight: 600,
                          cursor: isLoading ? 'not-allowed' : 'pointer',
                          alignSelf: 'flex-start',
                          transition: 'all var(--transition-normal)',
                        }}
                        onMouseEnter={(e) => { if (!isLoading) { e.currentTarget.style.borderColor = 'var(--accent-purple)'; e.currentTarget.style.color = 'var(--accent-purple)'; } }}
                        onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-main)'; e.currentTarget.style.color = 'var(--text-secondary)'; }}
                      >
                        {isLoading
                          ? <div style={{ width: 12, height: 12, border: '2px solid var(--border-main)', borderTopColor: 'var(--accent-purple)', borderRadius: '50%' }} className="animate-spin" />
                          : <Sparkles size={13} color="var(--accent-purple)" />
                        }
                        {isLoading ? 'Analysing…' : 'Evaluate Semantic Identity (AI)'}
                      </button>
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
  const [filterType, setFilterType] = useState('all');
  const [llmResults, setLlmResults] = useState({});
  const [llmLoading, setLlmLoading] = useState({});
  const [selectedProviderIdx, setSelectedProviderIdx] = useState(0);
  const selectedProvider = LLM_PROVIDERS[selectedProviderIdx];

  const resetResults = () => {
    setFile1Data(null);
    setFile2Data(null);
    setCompareResults(null);
    setError('');
    setFilterType('all');
    setLlmResults({});
    setLlmLoading({});
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

  const handleLlmResult = useCallback((key, result, isLoading) => {
    setLlmLoading(prev => ({ ...prev, [key]: isLoading }));
    if (result !== null) setLlmResults(prev => ({ ...prev, [key]: result }));
  }, []);

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

  const canAnalyze = (file1 || file2) && !loading;

  return (
    <div style={{ padding: '28px 32px', minHeight: '100%' }}>
      <PageHeader
        title="Semantic Comparator"
        description="Analyse and diff OSI, SML, TSML, and Snowflake semantic model YAML definitions."
        breadcrumb={['Semantic Comparator']}
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
                Semantic Diff
              </h2>
              {compareResults.file1_format && <FormatBadge format={compareResults.file1_format} />}
              <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>vs</span>
              {compareResults.file2_format && <FormatBadge format={compareResults.file2_format} />}
            </div>

            {/* Summary stat row */}
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
              {[
                { label: 'Identical',     value: compareResults.summary?.identical ?? 0,     color: 'var(--color-success)' },
                { label: 'Only in F1',    value: compareResults.summary?.only_in_1 ?? 0,      color: 'var(--accent-blue)' },
                { label: 'Only in F2',    value: compareResults.summary?.only_in_2 ?? 0,      color: 'var(--accent-purple)' },
                { label: 'Modified in F1', value: compareResults.summary?.modified_in_1 ?? 0, color: 'var(--color-warning)' },
                { label: 'Modified in F2', value: compareResults.summary?.modified_in_2 ?? 0, color: 'var(--accent-orange)' },
              ].map(s => (
                <div key={s.label} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '6px 14px', borderRadius: 8,
                  background: 'var(--bg-surface)', border: '1px solid var(--border-main)',
                }}>
                  <span style={{ fontSize: 17, fontWeight: 700, color: s.color }}>{s.value}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{s.label}</span>
                </div>
              ))}
            </div>

            {/* Filter pill bar */}
            <FilterPillBar active={filterType} onChange={setFilterType} counts={filterCounts} />
          </div>

          {/* Diff sections */}
          <DiffSection
            title="Tables"
            items={applyFilter(compareResults.tables)}
            icon={Layers}
            renderDetail={(t) => `${t.column_count} col · ${t.metric_count} metric · ${t.relationship_count} rel`}
          />
          <DiffSection
            title="Columns"
            items={applyFilter(compareResults.columns)}
            icon={Hash}
            renderDetail={(c) => c.type ? `Type: ${c.type}` : null}
          />
          <DiffSection
            title="Relationships"
            items={applyFilter(compareResults.relationships)}
            icon={Link2}
            renderDetail={(r) => (
              <span style={{ fontFamily: 'monospace' }}>
                {r.left_table}.{r.left_column} → {r.right_table}.{r.right_column}
                {r.cardinality ? ` (${r.cardinality})` : ''}
              </span>
            )}
          />
          <MetricsDiffSection
            items={applyFilter(compareResults.metrics)}
            selectedProvider={selectedProvider}
            onLlmResult={handleLlmResult}
            llmResults={llmResults}
            llmLoading={llmLoading}
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
