import React from 'react';
import { ArrowRight, Sparkles, Upload } from 'lucide-react';
import { BTN, BTN_PRIMARY, CARD, plural } from './styles';

function Kpi({ label, value, sub, tone }) {
  return (
    <div style={{ ...CARD, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 600 }}>{label}</span>
      <span
        style={{
          fontSize: 28,
          fontWeight: 800,
          lineHeight: 1.1,
          letterSpacing: '-0.02em',
          color: tone || 'var(--text-primary)',
        }}
      >
        {value}
      </span>
      <span
        style={{
          fontSize: 12,
          color: 'var(--text-tertiary)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
        title={typeof sub === 'string' ? sub : undefined}
      >
        {sub}
      </span>
    </div>
  );
}

function Feature({ letter, title, why, link, onClick }) {
  return (
    <div style={{ ...CARD, padding: 18, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <span
        style={{
          width: 28,
          height: 28,
          borderRadius: 8,
          display: 'grid',
          placeItems: 'center',
          fontSize: 12,
          fontWeight: 800,
          color: 'var(--accent-blue)',
          background: 'color-mix(in srgb, var(--accent-blue) 12%, transparent)',
        }}
      >
        {letter}
      </span>
      <b style={{ fontSize: 14 }}>{title}</b>
      <span style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.55, flex: 1 }}>{why}</span>
      <button
        type="button"
        onClick={onClick}
        style={{
          alignSelf: 'flex-start',
          background: 'transparent',
          border: 'none',
          padding: 0,
          color: 'var(--accent-blue)',
          fontSize: 12.5,
          fontWeight: 600,
          cursor: 'pointer',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
        }}
      >
        {link} <ArrowRight size={13} />
      </button>
    </div>
  );
}

function SetupSteps({ onGenerate, onImport, onGo }) {
  const steps = [
    {
      title: 'Generate from OSI',
      text: 'Build a first model from a project you already translated: its tables become concepts and its measures become governed metrics.',
      action: (
        <button type="button" onClick={onGenerate} style={BTN_PRIMARY}>
          <Sparkles size={13} /> Generate from OSI
        </button>
      ),
    },
    {
      title: 'Review',
      text: 'Check names, descriptions and owners in Domains. Health flags anything missing or defined twice.',
      action: (
        <button type="button" onClick={() => onGo('mesh')} style={BTN}>
          Open Domains
        </button>
      ),
    },
    {
      title: 'Publish',
      text: 'Save the model. Every BI tool and AI agent then uses the same definitions. Have YAML already? Import it instead.',
      action: (
        <button type="button" onClick={onImport} style={BTN}>
          <Upload size={13} /> Import YAML
        </button>
      ),
    },
  ];
  return (
    <div style={{ ...CARD, padding: '20px 22px' }}>
      <div style={{ fontSize: 15, fontWeight: 700 }}>Set up your ontology</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 4 }}>
        No models yet. Three steps to one shared definition per metric.
      </div>
      <ol style={{ listStyle: 'none', margin: '14px 0 0', padding: 0 }}>
        {steps.map((s, i) => (
          <li
            key={s.title}
            style={{
              display: 'grid',
              gridTemplateColumns: '34px 1fr auto',
              gap: 12,
              alignItems: 'center',
              padding: '14px 0',
              borderTop: '1px solid var(--border-main)',
            }}
          >
            <span style={{ fontFamily: 'monospace', color: 'var(--accent-blue)', fontWeight: 700 }}>{i + 1}</span>
            <div>
              <b style={{ fontSize: 13.5 }}>{s.title}</b>
              <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 2 }}>{s.text}</div>
            </div>
            {s.action}
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function OverviewView({ domains, stats, attention, healthIssues, onGo, onAsk, onGenerate, onImport }) {
  const names = domains.map((d) => d.domain);
  const attentionParts = [];
  if (attention.collisions) attentionParts.push(plural(attention.collisions, 'collision'));
  if (attention.unresolved) attentionParts.push(`${attention.unresolved} unresolved`);
  if (attention.parityDrift) attentionParts.push(`${attention.parityDrift} parity drift`);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
        <Kpi
          label="Models"
          value={domains.length}
          sub={names.length ? names.slice(0, 3).join(', ') + (names.length > 3 ? '…' : '') : 'None yet'}
        />
        <Kpi
          label="Governed metrics"
          value={stats.metrics}
          sub={stats.detailsLoaded ? `${stats.boundMetrics} bound to BI measures` : ' '}
        />
        <Kpi
          label="Business concepts"
          value={stats.concepts}
          sub={
            stats.detailsLoaded && stats.tables
              ? `mapped to ${plural(stats.tables, 'table')}`
              : plural(stats.relationships, 'relationship')
          }
        />
        <Kpi
          label="Needs attention"
          value={attention.total}
          tone={attention.total ? 'var(--color-warning)' : 'var(--color-success)'}
          sub={attentionParts.length ? attentionParts.join(' · ') : 'Nothing right now'}
        />
      </div>

      {domains.length === 0 ? (
        <SetupSteps onGenerate={onGenerate} onImport={onImport} onGo={onGo} />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
          <Feature
            letter="D"
            title="One definition per metric"
            why="Each model owns its glossary. “Revenue” means the same thing in Power BI, Tableau and Snowflake."
            link="Browse models"
            onClick={() => onGo('mesh')}
          />
          <Feature
            letter="M"
            title="See what depends on what"
            why="Trace a metric from source tables through models to every dashboard that uses it."
            link="Open map"
            onClick={() => onGo('graph')}
          />
          <Feature
            letter="H"
            title="Catch breaks before they ship"
            why="Impact analysis, DAX-vs-SQL parity, quality lint and row-level security in one checklist."
            link={healthIssues ? `Review ${plural(healthIssues, 'issue')}` : 'Run checks'}
            onClick={() => onGo('governance')}
          />
        </div>
      )}

      <div
        style={{
          ...CARD,
          padding: '14px 18px',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 12,
          background: 'color-mix(in srgb, var(--accent-blue) 10%, var(--bg-surface))',
          borderColor: 'transparent',
        }}
      >
        <b style={{ color: 'var(--accent-blue)', fontSize: 13 }}>Ask AI uses this ontology.</b>
        <span style={{ fontSize: 12.5, color: 'var(--text-secondary)', flex: 1, minWidth: 240 }}>
          Questions are answered with these governed metrics and policies, so AI answers match your dashboards.
        </span>
        <button type="button" onClick={onAsk} style={BTN}>
          Try Ask AI
        </button>
      </div>
    </div>
  );
}
