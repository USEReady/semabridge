import React, { useState } from 'react';
import { ChevronRight, ChevronDown, Play, Pause, MoreVertical, Plus } from 'lucide-react';
import { aggregateStatus, getStatusColor, hasFailures } from '../../utils/statusUtils';

/**
 * A tiny 5-bar sparkline showing recent health history.
 */
function HealthSparkline({ history = ['success', 'success', 'failed', 'success', 'success'] }) {
  return (
    <div style={{ display: 'flex', gap: 2, alignItems: 'flex-end', height: 10, padding: '0 4px' }}>
      {history.slice(-5).map((status, i) => (
        <div
          key={i}
          style={{
            width: 2,
            height: status === 'failed' ? 10 : 6,
            background: getStatusColor(status),
            borderRadius: 1,
            opacity: 0.8
          }}
        />
      ))}
    </div>
  );
}

/**
 * Individual project row inside a system group.
 */
function SidebarProjectChild({ project, active, onClick, onMenuToggle }) {
  const status = (project.status || 'idle').toLowerCase();
  
  return (
    <div
      onClick={(e) => { e.stopPropagation(); onClick(project); }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '4px 12px 4px 16px',
        cursor: 'pointer',
        background: active ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
        transition: 'all 0.15s',
        fontSize: '12px',
        color: active ? 'var(--accent-blue)' : '#d1d5db',
        position: 'relative',
        minHeight: '28px'
      }}
      className="hover:bg-white/5 group"
    >
      <div 
        style={{ 
          width: 6, 
          height: 6, 
          borderRadius: '50%', 
          background: getStatusColor(status),
          flexShrink: 0,
          zIndex: 2
        }} 
      />
      <span style={{ 
        flex: 1, 
        overflow: 'hidden', 
        textOverflow: 'ellipsis', 
        whiteSpace: 'nowrap',
        fontWeight: 400,
        letterSpacing: '-0.01em'
      }}>
        {project.name}
      </span>
      <button
        onClick={(e) => { e.stopPropagation(); onMenuToggle(project.id); }}
        style={{ 
          background: 'none', 
          border: 'none', 
          padding: 2, 
          cursor: 'pointer', 
          color: 'var(--text-tertiary)',
          opacity: 0
        }}
        className="group-hover:opacity-100"
      >
        <MoreVertical size={12} />
      </button>
    </div>
  );
}

/**
 * Expandable group for a System (e.g. Fabric, Snowflake).
 */
export function SidebarSystemGroup({ 
  systemName, 
  projects, 
  icon: Icon, 
  expanded, 
  onToggle, 
  onHeaderClick, // New: for filtering
  activeProject, 
  onProjectClick,
  onMenuToggle,
  onRunFailed,
  onForceRunAll
}) {
  const [hover, setHover] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const status = aggregateStatus(projects);
  const statusColor = getStatusColor(status);
  const failureCount = projects.filter(p => (p.status || '').toLowerCase() === 'failed').length;

  return (
    <div 
      onMouseEnter={() => setHover(true)} 
      onMouseLeave={() => { setHover(false); setMenuOpen(false); }}
      style={{ marginBottom: 2, position: 'relative' }}
    >
      {/* Group Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 0,
          padding: 0,
          background: 'transparent',
          borderBottom: '1px solid var(--border-subtle)',
          transition: 'all 0.2s',
          minHeight: 36
        }}
        className="hover:bg-surface-hover"
      >
        {/* Expansion Toggle Zone */}
        <div 
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          style={{ 
            color: 'var(--text-tertiary)', 
            display: 'flex',
            padding: '8px 4px 8px 12px',
            cursor: 'pointer'
          }}
          className="hover:text-primary"
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
        
        {/* Filtering Zone */}
        <div 
          onClick={(e) => { e.stopPropagation(); onHeaderClick(); }}
          style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: 6, 
            flex: 1, 
            minWidth: 0,
            padding: '8px 4px',
            cursor: 'pointer'
          }}
        >
          {Icon}
          <span style={{ 
            fontSize: 12, 
            fontWeight: 600, 
            color: 'var(--text-primary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
          }}>
            {systemName}
          </span>
        </div>

        {/* Right Side: Status/Count OR Batch Actions */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingRight: 12 }}>
          {hover ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }} className="animate-fade-in">
              <button
                title={failureCount > 0 ? `Run ${failureCount} failed` : "Run stale"}
                onClick={(e) => { e.stopPropagation(); onRunFailed(); }}
                style={{
                  background: 'var(--accent-blue)',
                  color: 'white',
                  border: 'none',
                  borderRadius: 4,
                  padding: '2px 6px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  fontSize: 10,
                  fontWeight: 700,
                  cursor: 'pointer'
                }}
              >
                <Play size={10} fill="currentColor" /> Run {failureCount > 0 ? 'Failed' : ''}
              </button>
              <div style={{ position: 'relative' }}>
                <button
                  onClick={(e) => { e.stopPropagation(); setMenuOpen(!menuOpen); }}
                  title="Batch Options"
                  style={{
                    background: menuOpen ? 'var(--bg-surface-raised)' : 'none',
                    border: '1px solid var(--border-main)',
                    color: 'var(--text-secondary)',
                    borderRadius: 4,
                    padding: 2,
                    cursor: 'pointer'
                  }}
                >
                  <MoreVertical size={10} />
                </button>
                {menuOpen && (
                  <div style={{
                    position: 'absolute', top: 20, right: 0, zIndex: 50,
                    background: 'var(--bg-surface)', border: '1px solid var(--border-main)',
                    borderRadius: 4, boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
                    minWidth: 120, padding: 4
                  }}>
                    <button
                      onClick={(e) => { e.stopPropagation(); onForceRunAll(); setMenuOpen(false); }}
                      style={{
                        width: '100%', textAlign: 'left', padding: '6px 8px',
                        fontSize: 11, background: 'none', border: 'none',
                        color: 'var(--text-secondary)', cursor: 'pointer',
                        borderRadius: 3
                      }}
                      className="hover:bg-surface-hover"
                    >
                      Force Run All
                    </button>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <>
              <HealthSparkline />
              <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>({projects.length})</span>
              <div 
                style={{ 
                  width: 8, 
                  height: 8, 
                  borderRadius: '50%', 
                  background: statusColor,
                  boxShadow: status === 'failed' ? '0 0 8px var(--color-error)' : 'none'
                }} 
              />
            </>
          )}
        </div>
      </div>

      {/* Child Projects */}
      {expanded && (
        <div style={{ padding: '2px 0', position: 'relative', marginLeft: '22px' }}>
          <div style={{
            position: 'absolute',
            left: '18px',
            top: 0,
            bottom: 0,
            width: '1px',
            background: 'var(--border-dark, #2d3139)',
            zIndex: 1
          }} />
          {projects.map(p => (
            <SidebarProjectChild
              key={p.id}
              project={p}
              active={activeProject?.id === p.id}
              onClick={onProjectClick}
              onMenuToggle={onMenuToggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Bottom action button for the sidebar.
 */
export function SidebarAddButton({ onClick, label }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '12px',
        width: '100%',
        background: 'transparent',
        border: 'none',
        borderTop: '1px solid var(--border-main)',
        color: 'var(--accent-blue)',
        fontSize: 12,
        fontWeight: 600,
        cursor: 'pointer',
        marginTop: 'auto'
      }}
      className="hover:bg-accent-faint"
    >
      <Plus size={14} />
      {label}
    </button>
  );
}
