import { useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

/**
 * DataTable — generic sortable, paginated table.
 * Props:
 *   columns  : [{ key, label, render?, align? }]
 *   data     : array of objects
 *   pageSize : number (default 10)
 *   loading  : boolean
 *   emptyText: string
 *   getRowStyle: (row, index) => style object (optional)
 */
export default function DataTable({
  columns = [],
  data = [],
  pageSize = 10,
  loading = false,
  emptyText = 'No data found.',
  getRowStyle = null,
}) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(data.length / pageSize));
  const slice = data.slice((page - 1) * pageSize, page * pageSize);

  const thStyle = {
    padding: '10px 14px',
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    color: 'var(--text-tertiary)',
    borderBottom: '1px solid var(--border-main)',
    background: 'var(--bg-surface-raised)',
    whiteSpace: 'nowrap',
  };
  const tdStyle = {
    padding: '11px 14px',
    fontSize: 13,
    color: 'var(--text-primary)',
    borderBottom: '1px solid var(--border-main)',
    verticalAlign: 'middle',
  };

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ border: '1px solid var(--border-main)' }}
    >
      <div className="overflow-x-auto">
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.key}
                  style={{ ...thStyle, textAlign: col.align === 'right' ? 'right' : col.align === 'center' ? 'center' : 'left' }}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={columns.length} style={{ ...tdStyle, textAlign: 'center', padding: '32px' }}>
                  <span className="text-tertiary text-sm">Loading…</span>
                </td>
              </tr>
            ) : slice.length === 0 ? (
              <tr>
                <td colSpan={columns.length} style={{ ...tdStyle, textAlign: 'center', padding: '32px' }}>
                  <span className="text-tertiary text-sm">{emptyText}</span>
                </td>
              </tr>
            ) : (
              slice.map((row, i) => {
                const baseStyle = { background: i % 2 === 0 ? 'transparent' : 'var(--bg-surface-raised)' };
                const customStyle = getRowStyle ? getRowStyle(row, i) : {};
                const mergedStyle = { ...baseStyle, ...customStyle };
                
                return (
                <tr
                  key={row.id ?? i}
                  style={mergedStyle}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = mergedStyle.background; }}
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      style={{ ...tdStyle, textAlign: col.align === 'right' ? 'right' : col.align === 'center' ? 'center' : 'left' }}
                    >
                      {col.render ? col.render(row[col.key], row) : String(row[col.key] ?? '—')}
                    </td>
                  ))}
                </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div
          className="flex items-center justify-between px-4 py-3"
          style={{ borderTop: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)' }}
        >
          <span className="text-tertiary text-xs">
            {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, data.length)} of {data.length}
          </span>
          <div className="flex items-center gap-1">
            <PaginationBtn onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>
              <ChevronLeft size={14} />
            </PaginationBtn>
            {Array.from({ length: totalPages }, (_, i) => i + 1)
              .filter(p => p === 1 || p === totalPages || Math.abs(p - page) <= 1)
              .reduce((acc, p, i, arr) => {
                if (i > 0 && p - arr[i - 1] > 1) acc.push('…');
                acc.push(p);
                return acc;
              }, [])
              .map((p, i) =>
                p === '…' ? (
                  <span key={`ellipsis-${i}`} className="text-tertiary text-xs px-1">…</span>
                ) : (
                  <PaginationBtn key={p} active={p === page} onClick={() => setPage(p)}>
                    {p}
                  </PaginationBtn>
                )
              )}
            <PaginationBtn onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}>
              <ChevronRight size={14} />
            </PaginationBtn>
          </div>
        </div>
      )}
    </div>
  );
}

function PaginationBtn({ children, onClick, disabled, active }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="flex items-center justify-center rounded-md text-xs font-medium"
      style={{
        minWidth: 28,
        height: 28,
        padding: '0 6px',
        border: active ? `1px solid var(--accent-blue)` : '1px solid var(--border-main)',
        background: active ? 'var(--accent-blue)' : 'transparent',
        color: active ? '#fff' : disabled ? 'var(--text-tertiary)' : 'var(--text-primary)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.4 : 1,
      }}
    >
      {children}
    </button>
  );
}
