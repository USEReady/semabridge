import React, { useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import {
  AlertTriangle,
  Layers,
  Database,
  BarChart3,
  Columns,
  Table as TableIcon,
  ShieldAlert,
  Shield,
  Info,
  List,
  GitFork,
  Sliders,
  Map as MapIcon,
} from 'lucide-react';

const NODE_W = 240;
const NODE_H = 82;

const RISK_CONFIG = {
  BREAKING: {
    color: '#ef4444',
    bg: 'rgba(239, 68, 68, 0.12)',
    border: '#ef4444',
    badgeBg: 'rgba(239, 68, 68, 0.2)',
    badgeText: '#ef4444',
    icon: ShieldAlert,
    label: 'Breaking',
  },
  ADVISORY: {
    color: '#f59e0b',
    bg: 'rgba(245, 158, 11, 0.12)',
    border: '#f59e0b',
    badgeBg: 'rgba(245, 158, 11, 0.2)',
    badgeText: '#f59e0b',
    icon: AlertTriangle,
    label: 'Advisory',
  },
  INFO: {
    color: '#3b82f6',
    bg: 'rgba(59, 130, 246, 0.08)',
    border: '#3b82f6',
    badgeBg: 'rgba(59, 130, 246, 0.2)',
    badgeText: '#3b82f6',
    icon: Info,
    label: 'Info',
  },
  TARGET: {
    color: '#a855f7',
    bg: 'rgba(168, 85, 247, 0.12)',
    border: '#a855f7',
    badgeBg: 'rgba(168, 85, 247, 0.25)',
    badgeText: '#c084fc',
    icon: Shield,
    label: 'Target',
  },
};

const TYPE_CONFIG = {
  column: { icon: Columns, label: 'Column' },
  table: { icon: TableIcon, label: 'Table' },
  metric: { icon: BarChart3, label: 'Metric' },
  consumer_asset: { icon: Database, label: 'Consumer Asset' },
  default: { icon: Layers, label: 'Object' },
};

function ImpactNodeComponent({ data }) {
  const isTarget = Boolean(data.is_target);
  const riskKey = isTarget ? 'TARGET' : (data.risk || 'INFO').toUpperCase();
  const risk = RISK_CONFIG[riskKey] || RISK_CONFIG.INFO;
  const typeCfg = TYPE_CONFIG[String(data.type || '').toLowerCase()] || TYPE_CONFIG.default;
  const TypeIcon = typeCfg.icon;

  return (
    <div
      style={{
        width: NODE_W,
        borderRadius: 8,
        border: `1.5px solid ${risk.border}`,
        background: 'var(--bg-surface, #1e293b)',
        boxShadow: isTarget
          ? '0 0 16px rgba(168, 85, 247, 0.35)'
          : '0 2px 8px rgba(0, 0, 0, 0.2)',
        overflow: 'hidden',
        color: 'var(--text-primary, #f8fafc)',
        textAlign: 'left',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: risk.color }} />
      <div style={{ padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10.5, color: 'var(--text-tertiary, #94a3b8)', textTransform: 'uppercase', fontWeight: 600 }}>
            <TypeIcon size={12} style={{ color: risk.color }} />
            <span>{typeCfg.label}</span>
          </div>
          <span
            style={{
              fontSize: 9.5,
              fontWeight: 700,
              padding: '2px 6px',
              borderRadius: 4,
              background: risk.badgeBg,
              color: risk.badgeText,
              letterSpacing: '0.04em',
            }}
          >
            {risk.label}
          </span>
        </div>
        <div
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: 'var(--text-primary, #f8fafc)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
          title={data.label}
        >
          {data.label}
        </div>
        {data.impact_level && (
          <div style={{ fontSize: 10, color: 'var(--text-secondary, #cbd5e1)', display: 'flex', justifyContent: 'space-between' }}>
            <span>{data.impact_level}</span>
            {data.source && <span>[{data.source}]</span>}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: risk.color }} />
    </div>
  );
}

const nodeTypes = {
  impact: ImpactNodeComponent,
};

function computeLayout(rawNodes, rawEdges, maxDepth = 5) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 24, ranksep: 80 });

  // Filter edges and calculate depths via BFS from target
  const targetNode = rawNodes.find((n) => n.is_target) || rawNodes[0];
  const depthMap = {};
  if (targetNode) {
    depthMap[targetNode.id] = 0;
    const queue = [targetNode.id];
    while (queue.length > 0) {
      const curr = queue.shift();
      const currDepth = depthMap[curr];
      for (const e of rawEdges) {
        if (e.from === curr && depthMap[e.to] === undefined) {
          depthMap[e.to] = currDepth + 1;
          queue.push(e.to);
        }
      }
    }
  }

  const visibleNodes = rawNodes.filter((n) => {
    const d = depthMap[n.id];
    return d === undefined || d <= maxDepth;
  });

  const visibleNodeIds = new Set(visibleNodes.map((n) => n.id));

  const visibleEdges = rawEdges.filter(
    (e) => visibleNodeIds.has(e.from) && visibleNodeIds.has(e.to)
  );

  visibleNodes.forEach((n) => {
    g.setNode(n.id, { width: NODE_W, height: NODE_H });
  });

  visibleEdges.forEach((e) => {
    g.setEdge(e.from, e.to);
  });

  dagre.layout(g);

  const flowNodes = visibleNodes.map((n) => {
    const pos = g.node(n.id) || { x: 0, y: 0 };
    return {
      id: n.id,
      type: 'impact',
      position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 },
      data: {
        label: n.id,
        type: n.type,
        risk: n.risk,
        is_target: n.is_target,
        impact_level: n.impact_level,
        source: n.source,
      },
    };
  });

  const flowEdges = visibleEdges.map((e, idx) => ({
    id: `e-${idx}-${e.from}->${e.to}`,
    source: e.from,
    target: e.to,
    label: e.relationship || '',
    animated: e.risk === 'BREAKING',
    style: {
      stroke: e.risk === 'BREAKING' ? '#ef4444' : e.risk === 'ADVISORY' ? '#f59e0b' : '#64748b',
      strokeWidth: e.risk === 'BREAKING' ? 2 : 1.5,
      strokeDasharray: e.relationship === 'warehouse_addition' ? '4 3' : undefined,
    },
    labelStyle: {
      fill: 'var(--text-tertiary, #94a3b8)',
      fontSize: 9.5,
      fontFamily: 'monospace',
    },
    labelBgStyle: {
      fill: 'var(--bg-surface, #1e293b)',
      fillOpacity: 0.85,
    },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: e.risk === 'BREAKING' ? '#ef4444' : e.risk === 'ADVISORY' ? '#f59e0b' : '#64748b',
      width: 14,
      height: 14,
    },
  }));

  return { flowNodes, flowEdges };
}

export default function ImpactDependencyGraph({
  report,
  defaultView = 'graph', // 'graph' | 'list'
}) {
  const [viewMode, setViewMode] = useState(defaultView);
  const [maxDepth, setMaxDepth] = useState(3);
  const [showMinimap, setShowMinimap] = useState(false);

  // Extract raw graph nodes and edges
  const { rawNodes, rawEdges } = useMemo(() => {
    if (!report) return { rawNodes: [], rawEdges: [] };

    const targetObj = report.target_object || report.target || '';
    const targetType = report.target_type || 'METRIC';
    const edges = report.dependency_graph_edges || report.edges || [];

    const nodesMap = new Map();
    nodesMap.set(targetObj, {
      id: targetObj,
      type: targetType.toLowerCase(),
      risk: 'TARGET',
      is_target: true,
    });

    // Populate from affected collections
    (report.affected_metrics || []).forEach((m) => {
      nodesMap.set(m.name, {
        id: m.name,
        type: 'metric',
        risk: m.risk || 'INFO',
        impact_level: m.impact_level,
        source: m.source,
        is_target: false,
      });
    });

    (report.affected_bi_reports || []).forEach((b) => {
      nodesMap.set(b.name, {
        id: b.name,
        type: 'consumer_asset',
        risk: b.risk || 'INFO',
        impact_level: b.impact_level,
        source: b.source,
        is_target: false,
      });
    });

    (report.affected_ai_tools || []).forEach((a) => {
      nodesMap.set(a.name, {
        id: a.name,
        type: 'consumer_asset',
        risk: a.risk || 'INFO',
        impact_level: a.impact_level,
        source: a.source,
        is_target: false,
      });
    });

    // If edges connect nodes not explicitly in affected collections (e.g. warehouse references)
    edges.forEach((e) => {
      if (e.from && !nodesMap.has(e.from)) {
        nodesMap.set(e.from, { id: e.from, type: 'column', risk: 'INFO', is_target: false });
      }
      if (e.to && !nodesMap.has(e.to)) {
        nodesMap.set(e.to, { id: e.to, type: 'metric', risk: 'INFO', is_target: false });
      }
    });

    return {
      rawNodes: Array.from(nodesMap.values()),
      rawEdges: edges,
    };
  }, [report]);

  const { flowNodes, flowEdges } = useMemo(() => {
    return computeLayout(rawNodes, rawEdges, maxDepth);
  }, [rawNodes, rawEdges, maxDepth]);

  if (!report) return null;

  const totalImpact = report.total_impacted_items || (rawNodes.length > 1 ? rawNodes.length - 1 : 0);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        borderRadius: 8,
        border: '1px solid var(--border-main)',
        background: 'var(--bg-surface)',
        overflow: 'hidden',
        marginTop: 10,
        flex: 1,
        minHeight: 0,
      }}
    >
      {/* ── TOOLBAR ──────────────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 12px',
          borderBottom: '1px solid var(--border-main)',
          background: 'var(--bg-app)',
          flexWrap: 'wrap',
          gap: 8,
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <strong style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
            <GitFork size={13} style={{ color: 'var(--accent-purple)' }} />
            Dependency Lineage ({rawNodes.length} nodes, {rawEdges.length} edges)
          </strong>

          {/* Depth Slider */}
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--text-secondary)' }}>
            <Sliders size={11} />
            <span>Depth:</span>
            <input
              type="range"
              min="1"
              max="5"
              value={maxDepth}
              onChange={(e) => setMaxDepth(Number(e.target.value))}
              style={{ width: 64, cursor: 'pointer' }}
              title={`Showing dependencies up to depth ${maxDepth}`}
            />
            <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{maxDepth}</span>
          </div>
        </div>

        {/* View Toggle & Legend */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* Minimap Toggle */}
          {viewMode === 'graph' && (
            <button
              type="button"
              onClick={() => setShowMinimap((prev) => !prev)}
              style={{
                padding: '3px 8px',
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                cursor: 'pointer',
                background: showMinimap ? 'rgba(168, 85, 247, 0.15)' : 'transparent',
                color: showMinimap ? 'var(--accent-purple)' : 'var(--text-secondary)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
              title={showMinimap ? 'Hide overview minimap' : 'Show overview minimap'}
            >
              <MapIcon size={12} /> Map
            </button>
          )}

          {/* Legend */}
          <div style={{ display: 'none', mdDisplay: 'flex', alignItems: 'center', gap: 8, fontSize: 10.5 }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 3, color: '#ef4444' }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#ef4444' }} />
              Breaking
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 3, color: '#f59e0b' }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#f59e0b' }} />
              Advisory
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 3, color: '#3b82f6' }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#3b82f6' }} />
              Info
            </span>
          </div>

          {/* Toggle Button */}
          <div style={{ display: 'inline-flex', borderRadius: 6, border: '1px solid var(--border-main)', overflow: 'hidden' }}>
            <button
              type="button"
              onClick={() => setViewMode('graph')}
              style={{
                padding: '3px 8px',
                fontSize: 11,
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
                background: viewMode === 'graph' ? 'var(--accent-purple)' : 'transparent',
                color: viewMode === 'graph' ? '#fff' : 'var(--text-secondary)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <GitFork size={12} /> Graph
            </button>
            <button
              type="button"
              onClick={() => setViewMode('list')}
              style={{
                padding: '3px 8px',
                fontSize: 11,
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
                background: viewMode === 'list' ? 'var(--accent-purple)' : 'transparent',
                color: viewMode === 'list' ? '#fff' : 'var(--text-secondary)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <List size={12} /> List
            </button>
          </div>
        </div>
      </div>

      {/* ── GRAPH VIEW ───────────────────────────────────────────────────── */}
      {viewMode === 'graph' && (
        <div style={{ flex: 1, minHeight: 460, width: '100%', position: 'relative' }}>
          {flowNodes.length === 0 ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-tertiary)', fontSize: 12 }}>
              No dependency graph nodes to render.
            </div>
          ) : (
            <ReactFlow
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.25 }}
              minZoom={0.2}
              maxZoom={1.5}
            >
              <Background color="#334155" gap={16} size={1} />
              <Controls position="top-right" showInteractive={false} />
              {showMinimap && (
                <MiniMap
                  position="bottom-left"
                  nodeColor={(n) => {
                    if (n.data?.is_target) return '#a855f7';
                    if (n.data?.risk === 'BREAKING') return '#ef4444';
                    if (n.data?.risk === 'ADVISORY') return '#f59e0b';
                    return '#3b82f6';
                  }}
                  style={{ height: 70, width: 100, background: 'rgba(15, 23, 42, 0.75)', borderRadius: 6, border: '1px solid var(--border-main)' }}
                />
              )}
            </ReactFlow>
          )}
        </div>
      )}

      {/* ── ACCESSIBLE LIST VIEW ─────────────────────────────────────────── */}
      {viewMode === 'list' && (
        <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8, flex: 1, minHeight: 460, overflowY: 'auto' }}>
          {totalImpact === 0 ? (
            <div style={{ color: 'var(--text-secondary)', fontSize: 12, padding: '8px 0' }}>
              {report.empty_messages?.summary || `Nothing depends on '${report.target_object || report.target}'.`}
            </div>
          ) : (
            rawNodes
              .filter((n) => !n.is_target)
              .map((n) => {
                const risk = RISK_CONFIG[n.risk] || RISK_CONFIG.INFO;
                return (
                  <div
                    key={n.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '6px 10px',
                      borderRadius: 6,
                      border: '1px solid var(--border-main)',
                      background: 'var(--bg-app)',
                      fontSize: 11.5,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span
                        style={{
                          fontSize: 9,
                          fontWeight: 700,
                          padding: '1px 6px',
                          borderRadius: 4,
                          background: risk.badgeBg,
                          color: risk.badgeText,
                        }}
                      >
                        {risk.label}
                      </span>
                      <strong style={{ fontFamily: 'monospace' }}>{n.id}</strong>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-tertiary)', fontSize: 11 }}>
                      {n.impact_level && <span>[{n.impact_level}]</span>}
                      {n.source && <span>[{n.source}]</span>}
                    </div>
                  </div>
                );
              })
          )}
        </div>
      )}
    </div>
  );
}
