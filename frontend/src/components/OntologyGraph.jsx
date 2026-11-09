import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import { AlertTriangle, FolderOpen, Layers, Box, BarChart3, Database, RefreshCw, X } from 'lucide-react';
import { authFetch } from '../utils/api';

// Connection Map: projects -> domains -> concepts / metrics -> bound BI objects.
// Data comes from GET /api/ontology/domains/graph (see semabridge/ontology/graph.py).

const KIND = {
  project: { label: 'Project', icon: FolderOpen, color: '#64748b' },
  domain: { label: 'Domain', icon: Layers, color: '#2563eb' },
  concept: { label: 'Concept', icon: Box, color: '#0d9488' },
  metric: { label: 'Metric', icon: BarChart3, color: '#059669' },
  bi: { label: 'BI object', icon: Database, color: '#94a3b8' },
};

const EDGE_STYLE = {
  uses: { stroke: '#64748b', strokeDasharray: '6 4' },
  defines: { stroke: '#94a3b8' },
  measures: { stroke: '#059669' },
  relates: { stroke: '#0d9488', strokeWidth: 2 },
  binds: { stroke: '#94a3b8', strokeDasharray: '3 3' },
};

const NODE_W = 230;
const NODE_H = 74;

function issuesOf(n) {
  const out = [];
  if (n.collision) out.push({ level: 'error', text: `Defined differently in: ${n.collision.join(', ')}` });
  if (n.unresolved) out.push({ level: 'error', text: n.unresolved });
  (n.unresolved_relationships || []).forEach((t) => out.push({ level: 'error', text: `Relationship ${t}` }));
  if (n.stale) out.push({ level: 'warn', text: 'No longer in the BI model (stale since last re-sync)' });
  if (n.unbound) out.push({ level: 'warn', text: 'No maps_to binding: matched only by exact name or synonym at run time' });
  return out;
}

function OntoNode({ data }) {
  const kind = KIND[data.type] || KIND.bi;
  const Icon = kind.icon;
  const issues = issuesOf(data);
  const hasError = issues.some((i) => i.level === 'error');
  const hasWarn = !hasError && issues.length > 0;
  const border = hasError ? '#dc2626' : hasWarn ? '#d97706' : kind.color;
  return (
    <div
      className="rounded-lg bg-surface text-left shadow-sm"
      style={{
        width: NODE_W,
        border: `1.5px ${data.type === 'bi' ? 'dashed' : 'solid'} ${border}`,
        opacity: data.dimmed ? 0.25 : 1,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: border }} />
      <div className="px-3 py-2 space-y-1">
        <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider" style={{ color: kind.color }}>
          <Icon size={12} />
          <span>{data.type === 'bi' ? (data.bi_kind === 'column' ? 'BI column' : 'BI table / measure') : kind.label}</span>
          {issues.length > 0 && (
            <AlertTriangle size={12} className="ml-auto" style={{ color: border }} aria-label={`${issues.length} issue(s)`} />
          )}
        </div>
        <div className="text-[13px] font-semibold text-primary truncate" title={data.label}>{data.label}</div>
        <div className="text-[10px] text-tertiary truncate">
          {data.type === 'domain' && `${data.concept_count} concepts · ${data.metric_count} metrics`}
          {data.type === 'concept' && `${(data.attributes || []).length} attributes · ${(data.synonyms || []).length} synonyms`}
          {data.type === 'metric' && `${(data.synonyms || []).length} synonyms${data.data_steward ? ` · ${data.data_steward}` : ''}`}
          {data.type === 'project' && 'attaches domains'}
          {data.type === 'bi' && 'from the Power BI model'}
        </div>
      </div>
      <Handle type="source" position={Position.Right} style={{ background: border }} />
    </div>
  );
}

const nodeTypes = { onto: OntoNode };

function layout(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 28, ranksep: 110 });
  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  // "relates" edges are concept-to-concept; leaving them out of the layout keeps
  // concepts in one column instead of pushing related ones a rank further right.
  // Metrics sit in the same column as concepts, so their BI bindings span two
  // ranks; that keeps every BI object in the right-most column.
  edges.filter((e) => e.kind !== 'relates').forEach((e) => g.setEdge(e.source, e.target, {
    minlen: e.kind === 'binds' && e.source.startsWith('metric:') ? 2 : 1,
  }));
  dagre.layout(g);
  return nodes.map((n) => {
    const p = g.node(n.id);
    return { ...n, position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 } };
  });
}

function DetailList({ title, items }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="space-y-1">
      <div className="text-[10px] uppercase tracking-wider font-semibold text-tertiary">{title}</div>
      <div className="flex flex-wrap gap-1">
        {items.map((s, i) => (
          <span key={i} className="px-1.5 py-0.5 rounded border border-main bg-surface-raised text-[11px] text-secondary">{s}</span>
        ))}
      </div>
    </div>
  );
}

export default function OntologyGraph({ projects = [] }) {
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [projectFilter, setProjectFilter] = useState('');
  const [showBindings, setShowBindings] = useState(true);
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [selected, setSelected] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  const projectNames = useMemo(() => {
    const m = {};
    projects.forEach((p) => { m[p.id || p.project_id] = p.name || p.display_name || p.id; });
    return m;
  }, [projects]);

  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => {
    setLoading(true);
    setReloadKey((k) => k + 1);
  }, []);

  // State is only set when the request settles (no synchronous setState in the effect).
  useEffect(() => {
    let cancelled = false;
    const qs = projectFilter ? `?project_id=${encodeURIComponent(projectFilter)}` : '';
    authFetch(`/api/ontology/domains/graph${qs}`)
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || `Request failed (${res.status})`);
        if (!cancelled) { setGraph(data); setSelected(null); setError(null); }
      })
      .catch((e) => { if (!cancelled) setError(String(e.message || e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectFilter, reloadKey]);

  useEffect(() => {
    if (!graph) return;
    const visible = graph.nodes.filter((n) => showBindings || n.type !== 'bi');
    const ids = new Set(visible.map((n) => n.id));
    const visEdges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    let flagged = null;
    if (onlyIssues) {
      flagged = new Set(visible.filter((n) => issuesOf(n).length > 0).map((n) => n.id));
    }
    const rfNodes = visible.map((n) => ({
      id: n.id,
      type: 'onto',
      data: {
        ...n,
        label: n.type === 'project' ? projectNames[n.project_id] || n.label : n.label,
        dimmed: flagged ? !flagged.has(n.id) : false,
      },
      position: { x: 0, y: 0 },
    }));
    const rfEdges = visEdges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      kind: e.kind,
      label: e.kind === 'defines' ? undefined : e.label || undefined,
      type: e.kind === 'relates' ? 'default' : 'smoothstep',
      style: { ...(EDGE_STYLE[e.kind] || {}), opacity: flagged && !(flagged.has(e.source) || flagged.has(e.target)) ? 0.15 : 1 },
      labelStyle: { fontSize: 10, fill: 'var(--text-secondary, #475569)' },
      labelBgStyle: { fill: 'var(--bg-surface, #fff)' },
      markerEnd: { type: MarkerType.ArrowClosed, color: (EDGE_STYLE[e.kind] || {}).stroke },
    }));
    setNodes(layout(rfNodes, rfEdges));
    setEdges(rfEdges);
  }, [graph, showBindings, onlyIssues, projectNames, setNodes, setEdges]);

  const s = graph?.summary;
  const issueCount = s ? s.collisions + s.stale + s.unbound + s.unresolved : 0;
  const selIssues = selected ? issuesOf(selected) : [];

  return (
    <div className="space-y-4">
      <div className="p-4 rounded-xl bg-surface border border-main flex flex-col lg:flex-row lg:items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold text-primary m-0">Connection Map</h2>
          <p className="text-[11px] text-secondary m-0 mt-0.5">
            Which projects use which domains, what each domain defines, and which Power BI objects each concept and metric is bound to. Click a node for details.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <label htmlFor="onto-graph-project" className="text-tertiary">Project</label>
          <select
            id="onto-graph-project"
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            className="px-2 py-1.5 rounded-lg bg-surface-raised border border-main text-primary"
          >
            <option value="">All domains</option>
            {projects.map((p) => (
              <option key={p.id || p.project_id} value={p.id || p.project_id}>{p.name || p.display_name || p.id}</option>
            ))}
          </select>
          <label className="flex items-center gap-1.5 text-secondary cursor-pointer">
            <input id="onto-graph-bindings" type="checkbox" checked={showBindings} onChange={(e) => setShowBindings(e.target.checked)} />
            BI bindings
          </label>
          <label className="flex items-center gap-1.5 text-secondary cursor-pointer">
            <input id="onto-graph-issues" type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} />
            Highlight issues
          </label>
          <button type="button" onClick={reload} className="p-1.5 rounded-lg border border-main text-secondary hover:text-primary" title="Reload">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {s && (
        <div className="flex flex-wrap gap-2 text-[11px]">
          {[
            ['Domains', s.domains], ['Concepts', s.concepts], ['Metrics', s.metrics],
            ['Relationships', s.relationships], ['BI objects bound', s.bi_objects], ['Projects', s.projects],
          ].map(([k, v]) => (
            <span key={k} className="px-2 py-1 rounded-lg border border-main bg-surface text-secondary">
              {k} <b className="text-primary tabular-nums">{v}</b>
            </span>
          ))}
          <span className={`px-2 py-1 rounded-lg border ${issueCount ? 'border-amber-500/50 text-amber-600' : 'border-emerald-500/40 text-emerald-600'} bg-surface`}>
            {issueCount
              ? `Issues: ${s.collisions} collisions · ${s.unresolved} unresolved · ${s.stale} stale · ${s.unbound} unbound`
              : 'No issues'}
          </span>
        </div>
      )}

      {error && (
        <div role="alert" className="p-3 rounded-lg border border-red-500/40 bg-red-500/10 text-xs text-red-500">
          Could not load the connection map: {error}
        </div>
      )}

      <div className="flex gap-4" style={{ height: 'max(520px, calc(100vh - 340px))' }}>
        <div className="flex-1 min-w-0 rounded-xl border border-main bg-surface-raised overflow-hidden relative">
          {graph && graph.nodes.length === 0 ? (
            <div className="h-full flex items-center justify-center text-center p-8">
              <div className="space-y-1">
                <p className="text-sm font-semibold text-primary m-0">No domains to show</p>
                <p className="text-xs text-secondary m-0">
                  {projectFilter ? 'This project has no domains attached. Attach one on the project page.' : 'Add a domain with Ingest YAML or Generate from OSI.'}
                </p>
              </div>
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={(_, n) => setSelected(graph.nodes.find((x) => x.id === n.id) || null)}
              onPaneClick={() => setSelected(null)}
              fitView
              fitViewOptions={{ padding: 0.15 }}
              minZoom={0.2}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={18} size={1} />
              <Controls showInteractive={false} />
              {!selected && (
                <MiniMap position="top-right" style={{ width: 130, height: 80 }} pannable zoomable nodeColor={(n) => (KIND[n.data?.type] || KIND.bi).color} />
              )}
            </ReactFlow>
          )}
          <div className="absolute left-3 top-3 p-2 rounded-lg bg-surface/90 border border-main text-[10px] text-secondary space-y-1 pointer-events-none">
            {Object.entries(KIND).map(([k, v]) => (
              <div key={k} className="flex items-center gap-1.5">
                <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ border: `1.5px ${k === 'bi' ? 'dashed' : 'solid'} ${v.color}` }} />
                {v.label}
              </div>
            ))}
            <div className="flex items-center gap-1.5"><span className="inline-block w-2.5 h-2.5 rounded-sm border-[1.5px] border-amber-600" />Warning</div>
            <div className="flex items-center gap-1.5"><span className="inline-block w-2.5 h-2.5 rounded-sm border-[1.5px] border-red-600" />Conflict</div>
          </div>
        </div>

        {selected && (
          <aside className="w-80 shrink-0 rounded-xl border border-main bg-surface p-4 space-y-3 overflow-y-auto" aria-label="Node details">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: (KIND[selected.type] || KIND.bi).color }}>
                  {(KIND[selected.type] || KIND.bi).label}{selected.domain ? ` · ${selected.domain}` : ''}
                </div>
                <h3 className="text-sm font-bold text-primary m-0 mt-0.5">
                  {selected.type === 'project' ? projectNames[selected.project_id] || selected.label : selected.label}
                </h3>
              </div>
              <button type="button" onClick={() => setSelected(null)} className="p-1 rounded text-tertiary hover:text-primary" aria-label="Close details"><X size={14} /></button>
            </div>
            {selIssues.length > 0 && (
              <div className="space-y-1">
                {selIssues.map((i, k) => (
                  <div key={k} className={`text-[11px] flex gap-1.5 ${i.level === 'error' ? 'text-red-500' : 'text-amber-600'}`}>
                    <AlertTriangle size={12} className="shrink-0 mt-0.5" />{i.text}
                  </div>
                ))}
              </div>
            )}
            {selected.description && <p className="text-xs text-secondary m-0">{selected.description}</p>}
            {selected.type === 'domain' && (
              <p className="text-[11px] text-tertiary m-0">
                Owner {selected.owner || '—'} · v{selected.version || '—'}
                {selected.source_project_id ? ` · built from ${projectNames[selected.source_project_id] || selected.source_project_id}` : ''}
              </p>
            )}
            <DetailList title="Bound to (maps_to)" items={selected.maps_to} />
            <DetailList title="Synonyms" items={selected.synonyms} />
            <DetailList title="Business rules" items={selected.business_rules} />
            {selected.data_steward && <p className="text-[11px] text-secondary m-0">Data steward: <b className="text-primary">{selected.data_steward}</b></p>}
            {selected.sql_expression && (
              <pre className="p-2 rounded bg-surface-raised border border-main text-[11px] text-emerald-500 whitespace-pre-wrap m-0">{selected.sql_expression}</pre>
            )}
            {(selected.attributes || []).length > 0 && (
              <div className="space-y-1">
                <div className="text-[10px] uppercase tracking-wider font-semibold text-tertiary">Attributes</div>
                {selected.attributes.map((a, i) => (
                  <div key={i} className="p-1.5 rounded border border-main text-[11px] flex justify-between gap-2">
                    <span className="text-primary">{a.name}{a.stale ? ' (stale)' : ''}</span>
                    <span className="text-tertiary font-mono truncate">{a.maps_to.length ? `→ ${a.maps_to.join(', ')}` : 'unbound'}</span>
                  </div>
                ))}
              </div>
            )}
            {selected.type === 'bi' && (
              <p className="text-[11px] text-secondary m-0">An object in the Power BI model. The ontology entries pointing here supply its synonyms and description in Snowflake.</p>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
