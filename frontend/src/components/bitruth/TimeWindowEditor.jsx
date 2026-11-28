import React, { useState, useEffect } from 'react';
import { Plus, Trash2, Calendar, AlertTriangle, HelpCircle } from 'lucide-react';
import { authFetch } from '../../utils/api';

const INPUT = {
  padding: '6px 8px',
  borderRadius: 6,
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
};

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  padding: '4px 8px',
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 11.5,
  cursor: 'pointer',
};

export default function TimeWindowEditor({
  caseId,
  projectId,
  windows = [],
  onChange,
  readOnly = false,
}) {
  const [availableDims, setAvailableDims] = useState([]);
  const [selectedDim, setSelectedDim] = useState('');
  const [newOp, setNewOp] = useState('=');
  const [newVal, setNewVal] = useState('');
  const [newExpVal, setNewExpVal] = useState('');
  const [newExpRows, setNewExpRows] = useState('');
  const [loadingDims, setLoadingDims] = useState(false);

  useEffect(() => {
    if (!caseId) return;
    let active = true;
    const timer = setTimeout(() => {
      if (active) setLoadingDims(true);
    }, 0);
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    authFetch(`/api/bi-truth/cases/${caseId}/dimensions${qs}`)
      .then((r) => (r.ok ? r.json() : { dimensions: [] }))
      .then((data) => {
        if (!active) return;
        const dims = Array.isArray(data.dimensions) ? data.dimensions : [];
        setAvailableDims(dims);
        if (dims.length > 0) {
          setSelectedDim((prev) => (dims.includes(prev) ? prev : dims[0]));
        }
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoadingDims(false);
      });
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [caseId, projectId]);

  const handleAdd = () => {
    const dim = selectedDim.trim();
    const val = newVal.trim();
    if (!dim || !val) return;

    const isNumeric = !isNaN(Number(val)) && val !== '';
    const formattedVal = isNumeric ? val : `'${val.replace(/'/g, "''")}'`;
    const filterClause = `${dim} ${newOp} ${formattedVal}`;
    const windowKey = `${dim}_${val}`.replace(/[^a-zA-Z0-9_]/g, '_').toLowerCase();
    const windowLabel = `${dim} ${newOp} ${val}`;

    const expVal = newExpVal !== ''
      ? (!isNaN(Number(newExpVal)) ? Number(newExpVal) : newExpVal)
      : null;
    const expRows = newExpRows !== '' ? parseInt(newExpRows, 10) : null;

    const item = {
      window_key: windowKey,
      window_label: windowLabel,
      filter_clause: filterClause,
      expected_value: expVal,
      expected_row_count: expRows,
    };

    onChange([...windows, item]);
    setNewVal('');
    setNewExpVal('');
    setNewExpRows('');
  };

  const handleRemove = (index) => {
    const next = windows.filter((_, i) => i !== index);
    onChange(next);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)', fontWeight: 600 }}>
        <Calendar size={13} style={{ color: 'var(--accent-blue)' }} />
        Time &amp; Slice Windows ({windows.length})
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 6,
          background: 'rgba(59,130,246,0.06)',
          border: '1px solid rgba(59,130,246,0.2)',
          borderRadius: 6,
          padding: '6px 10px',
          fontSize: 11,
          color: 'var(--text-secondary)',
          lineHeight: 1.4,
        }}
      >
        <HelpCircle size={14} style={{ color: 'var(--accent-blue)', flexShrink: 0, marginTop: 1 }} />
        <span>
          <strong>Slice &amp; Time Filters:</strong> If your semantic model contains date, period, or categorical slice dimensions (e.g. <code>YEAR</code>, <code>REGION</code>, <code>SEGMENT</code>), you can select them here to verify warehouse measure accuracy across specific subsets and time windows.
        </span>
      </div>

      {windows.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {windows.map((w, idx) => {
            const hasExp = w.expected_value !== null && w.expected_value !== undefined;
            const filterDisplay = w.filter_clause || (w.dimension ? `${w.dimension} ${w.operator || '='} '${w.value}'` : w.window_label);
            return (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '6px 10px',
                  borderRadius: 6,
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--border-main)',
                  gap: 8,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontFamily: 'monospace', fontWeight: 600, color: 'var(--accent-blue)' }}>
                    {filterDisplay}
                  </span>
                  {hasExp ? (
                    <span style={{ fontSize: 11, color: 'var(--color-success)', background: 'rgba(34,197,94,0.1)', padding: '1px 6px', borderRadius: 4 }}>
                      Exp: {String(w.expected_value)}
                    </span>
                  ) : (
                    <span
                      style={{
                        fontSize: 10.5,
                        color: 'var(--color-warning)',
                        background: 'rgba(234,179,8,0.12)',
                        padding: '1px 6px',
                        borderRadius: 4,
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 3,
                      }}
                      title="This window has no benchmark value. It will report NOT_COMPARABLE on runs."
                    >
                      <AlertTriangle size={11} /> Will not be compared (no benchmark)
                    </span>
                  )}
                  {w.expected_row_count !== null && w.expected_row_count !== undefined && (
                    <span style={{ fontSize: 11, color: 'var(--text-secondary)', background: 'rgba(255,255,255,0.06)', padding: '1px 6px', borderRadius: 4 }}>
                      Rows: {String(w.expected_row_count)}
                    </span>
                  )}
                </div>
                {!readOnly && (
                  <button
                    type="button"
                    style={{ ...BTN, border: 'none', background: 'transparent', color: 'var(--color-error)', padding: 2 }}
                    onClick={() => handleRemove(idx)}
                    title="Remove window"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {!readOnly && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span>Filter by Model Dimensions: If your model has dimensional attributes (e.g. Accounts, Year, Region), select one below to evaluate this metric on that specific slice.</span>
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            {availableDims.length > 0 ? (
              <select
                value={selectedDim}
                onChange={(e) => setSelectedDim(e.target.value)}
                style={{ ...INPUT, flex: '1 1 140px' }}
                title="Select a dimension from this model view"
              >
                {availableDims.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
              placeholder={loadingDims ? "Loading dimensions..." : "Dimension (e.g. COL_DATE.YEAR)"}
              value={selectedDim}
              onChange={(e) => setSelectedDim(e.target.value)}
              style={{ ...INPUT, flex: '1 1 140px' }}
            />
          )}

          <select
            value={newOp}
            onChange={(e) => setNewOp(e.target.value)}
            style={{ ...INPUT, width: 65, padding: '6px 4px' }}
          >
            <option value="=">=</option>
            <option value="!=">!=</option>
            <option value=">">&gt;</option>
            <option value="<">&lt;</option>
            <option value=">=">&gt;=</option>
            <option value="<=">&lt;=</option>
            <option value="LIKE">LIKE</option>
            <option value="IN">IN</option>
          </select>
          <input
            type="text"
            placeholder="Value (e.g. 2024)"
            value={newVal}
            onChange={(e) => setNewVal(e.target.value)}
            style={{ ...INPUT, flex: '1 1 90px' }}
          />
          <input
            type="number"
            step="any"
            placeholder="Exp Val (optional)"
            value={newExpVal}
            onChange={(e) => setNewExpVal(e.target.value)}
            style={{ ...INPUT, width: 110 }}
          />
          <input
            type="number"
            step="1"
            placeholder="Exp Rows"
            value={newExpRows}
            onChange={(e) => setNewExpRows(e.target.value)}
            style={{ ...INPUT, width: 75 }}
          />
          <button
            type="button"
            style={{ ...BTN, background: 'var(--accent-blue)', color: '#fff', borderColor: 'var(--accent-blue)' }}
            onClick={handleAdd}
            disabled={!selectedDim.trim() || !newVal.trim()}
          >
            <Plus size={12} /> Add
          </button>
        </div>
        </div>
      )}
    </div>
  );
}
