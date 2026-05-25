import React from 'react';
import { X, Check } from 'lucide-react';
import SourceIcon from '../common/SourceIcon';
import { ConnectorChip } from '../WizardUIComponents';
import { CONNECTOR_TYPES, TARGET_CONNECTOR_TYPES, INTERMEDIATE_FORMAT_TYPES } from '../../utils/constants';

const SECTION_CARD = {
  border: '1px solid var(--border-main)',
  borderRadius: 16,
  padding: '32px 36px',
  background: 'var(--bg-surface)',
  boxShadow: '0 4px 20px rgba(0, 0, 0, 0.08)',
  position: 'relative',
  overflow: 'hidden'
};

export function StepBasicInfo({
  name, setName, description, setDescription,
  sourceConnector, setSourceConnector,
  targetConnectors, setTargetConnectors,
  intermediateFormat, setIntermediateFormat,
  tags, setTags, tagInput, setTagInput,
  showValidation, editMode
}) {
  const isSourceMissing = !sourceConnector;
  const isTargetsMissing = targetConnectors.size === 0;
  const isFormatMissing = !intermediateFormat;

  const LABEL = {
    display: 'block', fontSize: 11, fontWeight: 700,
    color: 'var(--text-tertiary)', marginBottom: 8,
    textTransform: 'uppercase', letterSpacing: '0.05em'
  };
  const INPUT_STYLE = {
    width: '100%', padding: '10px 14px', borderRadius: 8,
    background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
    color: 'var(--text-primary)', fontSize: 13, outline: 'none',
    transition: 'all 0.2s ease',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 32, paddingBottom: 40 }}>
      {/* 1. Project Identity */}
      <div style={SECTION_CARD}>
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>Project Identity</h3>
          <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>Basic details to identify this synchronization pipeline.</p>
        </div>
        
        <div style={{ display: 'grid', gap: 24 }}>
          <div>
            <label style={LABEL}>Project Name</label>
            <input
              type="text" value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. Sales Analytics Sync"
              style={{ ...INPUT_STYLE, maxWidth: 500 }}
              disabled={editMode}
            />
            {showValidation && !name && (
              <p style={{ color: 'var(--color-error)', fontSize: 11, marginTop: 4 }}>Name is required</p>
            )}
          </div>
          <div>
            <label style={LABEL}>Description (optional)</label>
            <textarea
              value={description} onChange={e => setDescription(e.target.value)}
              placeholder="Describe the purpose of this data pipeline..."
              style={{ ...INPUT_STYLE, minHeight: 80, resize: 'vertical' }}
            />
          </div>

          <div>
            <label style={LABEL}>Project Tags</label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
              {[...tags].map(tag => (
                <div
                  key={tag}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '4px 10px', borderRadius: 6,
                    background: 'rgba(59, 130, 246, 0.1)', color: 'var(--accent-blue)',
                    fontSize: 12, fontWeight: 600,
                  }}
                >
                  {tag}
                  <button
                    type="button"
                    onClick={() => { const next = new Set(tags); next.delete(tag); setTags(next); }}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', padding: 0, display: 'flex' }}
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
            </div>
            <input
              type="text" value={tagInput} onChange={e => setTagInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && tagInput.trim()) {
                  const next = new Set(tags);
                  next.add(tagInput.trim());
                  setTags(next);
                  setTagInput('');
                }
              }}
              placeholder="Add tag and press Enter..."
              style={INPUT_STYLE}
            />
          </div>
        </div>
      </div>

      {/* 2. Unified Pipeline Configuration Island */}
      <div style={SECTION_CARD}>
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>Pipeline Configuration</h3>
          <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>Define how data flows from your source to the target destinations.</p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
          {/* Source & Target Row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 32, alignItems: 'stretch' }}>
            {/* Source Column */}
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyBetween: 'space-between', marginBottom: 16 }}>
                <div style={LABEL}>Source Connector</div>
                <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: 'rgba(59, 130, 246, 0.1)', color: 'var(--accent-blue)' }}>SINGLE</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
                {CONNECTOR_TYPES.map(c => {
                  const isSelected = sourceConnector === c.value;
                  const shouldDim = Boolean(sourceConnector) && !isSelected;
                  return (
                    <button
                      key={c.value} type="button" onClick={() => setSourceConnector(c.value)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', borderRadius: 10, cursor: 'pointer',
                        background: isSelected ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg-surface-raised)',
                        border: `1.5px solid ${isSelected ? 'var(--accent-blue)' : 'transparent'}`,
                        color: isSelected ? 'var(--accent-blue)' : shouldDim ? 'var(--text-tertiary)' : 'var(--text-secondary)',
                        fontSize: 13, fontWeight: isSelected ? 600 : 400, opacity: shouldDim ? 0.4 : 1, transition: 'all 0.2s ease',
                        boxShadow: isSelected ? '0 0 0 1px rgba(59, 130, 246, 0.2)' : 'none',
                      }}
                    >
                      <div style={{ 
                        width: 16, height: 16, borderRadius: '50%', 
                        border: isSelected ? '5px solid var(--accent-blue)' : '2px solid var(--border-main)',
                        background: 'transparent',
                        transition: 'all 0.2s ease'
                      }} />
                      <SourceIcon source={c.value} size={18} />
                      <span style={{ flex: 1, textAlign: 'left' }}>{c.label}</span>
                    </button>
                  );
                })}
              </div>
              {showValidation && isSourceMissing && (
                <p style={{ color: 'var(--color-error)', fontSize: 11, marginTop: 12 }}>Please select a source</p>
              )}
            </div>

            {/* Target Column */}
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyBetween: 'space-between', marginBottom: 16 }}>
                <div style={LABEL}>Target Connector(s)</div>
                <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: 'rgba(34, 197, 94, 0.1)', color: 'var(--color-success)' }}>MULTIPLE</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
                {TARGET_CONNECTOR_TYPES.map(t => {
                  const isSelected = targetConnectors.has(t.value);
                  const isDisabled = sourceConnector === t.value;
                  return (
                    <button
                      key={t.value} type="button" disabled={isDisabled}
                      onClick={() => {
                        const next = new Set(targetConnectors);
                        if (next.has(t.value)) next.delete(t.value);
                        else next.add(t.value);
                        setTargetConnectors(next);
                      }}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', borderRadius: 10,
                        cursor: isDisabled ? 'not-allowed' : 'pointer',
                        background: isSelected ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg-surface-raised)',
                        border: `1.5px solid ${isSelected ? 'var(--accent-blue)' : 'transparent'}`,
                        color: isSelected ? 'var(--accent-blue)' : isDisabled ? 'var(--text-tertiary)' : 'var(--text-secondary)',
                        fontSize: 13, fontWeight: isSelected ? 600 : 400, opacity: isDisabled ? 0.2 : 1, transition: 'all 0.2s ease',
                        boxShadow: isSelected ? '0 0 0 1px rgba(59, 130, 246, 0.2)' : 'none',
                      }}
                    >
                      <div style={{ 
                        width: 16, height: 16, borderRadius: 4, 
                        background: isSelected ? 'var(--accent-blue)' : 'transparent', 
                        border: isSelected ? 'none' : '2px solid var(--border-main)', 
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        transition: 'all 0.2s ease'
                      }}>
                        {isSelected && <Check size={12} color="white" strokeWidth={3} />}
                      </div>
                      <SourceIcon source={t.value} size={18} />
                      <span style={{ flex: 1, textAlign: 'left' }}>{t.label}</span>
                    </button>
                  );
                })}
              </div>
              {showValidation && isTargetsMissing && (
                <p style={{ color: 'var(--color-error)', fontSize: 11, marginTop: 12 }}>Select at least one target</p>
              )}
            </div>
          </div>

          {/* Format Selection (Bottom Row) */}
          <div style={{ borderTop: '1px solid rgba(255, 255, 255, 0.05)', paddingTop: 24 }}>
            <label style={LABEL}>Intermediate Format</label>
            <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
              {INTERMEDIATE_FORMAT_TYPES.map(t => (
                <ConnectorChip
                  key={t.value} label={t.label}
                  selected={intermediateFormat === t.value}
                  onClick={() => setIntermediateFormat(t.value)}
                />
              ))}
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 16, lineHeight: 1.5 }}>
              The intermediate format determines the schema representation used during the synchronization process. 
              This is critical for cross-platform semantic translation.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
