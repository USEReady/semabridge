import React from 'react';
import { X, Check, Layers, Sparkles } from 'lucide-react';
import { STEPS } from '../../utils/constants';
import { useProjectWizardStore } from '../../store/projectWizardStore';

export function WizardHeader({ editMode, hasMeaningfulData, clearWizardState, navigate, name, step, canUpsert = false }) {
  const setWizardState = useProjectWizardStore(state => state.setWizardState);
  
  return (
    <header style={{
      height: 72,
      padding: '0 36px',
      background: 'var(--bg-surface)',
      backdropFilter: 'blur(16px)',
      borderBottom: '1px solid var(--border-main)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      position: 'sticky',
      top: 0,
      zIndex: 100,
      boxShadow: 'var(--shadow-sm)'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
        <button
          onClick={() => {
            if (hasMeaningfulData && !editMode) {
              if (window.confirm('Discard project draft?')) {
                clearWizardState();
                navigate('/projects');
              }
            } else {
              navigate('/projects');
            }
          }}
          className="flex items-center justify-center w-9 h-9 rounded-lg border border-[var(--border-main)] bg-[var(--bg-surface-raised)] hover:bg-[var(--bg-surface-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-all cursor-pointer shadow-xs"
          title="Exit Wizard"
        >
          <X size={16} strokeWidth={2.2} />
        </button>

        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h1 style={{
              fontSize: 17,
              fontWeight: 700,
              color: 'var(--text-primary)',
              margin: 0,
              letterSpacing: '-0.02em',
              lineHeight: 1.2
            }}>
              {editMode ? 'Modernize Project' : 'New Semantic Project'}
            </h1>
            {name.trim() && (
              <span style={{
                background: 'var(--color-accent-faint)',
                color: 'var(--accent-blue)',
                padding: '2px 8px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 600,
                border: '1px solid var(--border-focus)'
              }}>
                {name}
              </span>
            )}
          </div>
          <div style={{
            fontSize: 11,
            color: 'var(--text-tertiary)',
            marginTop: 3,
            display: 'flex',
            alignItems: 'center',
            gap: 6
          }}>
            <span style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>Step {step} of 5</span>
            <span style={{ color: 'var(--border-main)' }}>•</span>
            <span>{STEPS[step - 1]?.label}</span>
          </div>
        </div>

        {/* Sync Strategy Control */}
        {editMode ? (
          <div style={{
            background: 'var(--bg-surface-raised)',
            padding: '6px 12px',
            borderRadius: 8,
            border: '1px solid var(--border-main)',
            display: 'flex',
            alignItems: 'center',
            gap: 10
          }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)' }}>Strategy:</span>
            <div style={{ display: 'flex', gap: 6 }}>
              {['copy', 'upsert'].map((opt) => {
                const isUpsertDisabled = opt === 'upsert' && !canUpsert;
                const active = (useProjectWizardStore.getState().wizard.write_strategy || 'copy') === opt;
                return (
                  <button
                    key={opt}
                    type="button"
                    disabled={isUpsertDisabled}
                    title={isUpsertDisabled ? 'Upsert requires at least one prior deployment to create target views.' : undefined}
                    onClick={() => !isUpsertDisabled && setWizardState({ write_strategy: opt })}
                    style={{
                      padding: '4px 10px',
                      borderRadius: 6,
                      border: '1px solid',
                      borderColor: active ? 'transparent' : 'var(--border-main)',
                      cursor: isUpsertDisabled ? 'not-allowed' : 'pointer',
                      opacity: isUpsertDisabled ? 0.45 : 1,
                      background: active ? (opt === 'upsert' ? 'var(--accent-orange)' : 'var(--accent-blue)') : 'var(--bg-surface)',
                      color: active ? '#ffffff' : 'var(--text-secondary)',
                      fontWeight: 600,
                      fontSize: 11,
                      transition: 'all 0.15s ease'
                    }}
                  >
                    {opt === 'copy' ? 'Copy (Replace)' : 'Upsert (Delta)'}
                  </button>
                );
              })}
            </div>
          </div>
        ) : (
          <div style={{
            background: 'var(--bg-surface-raised)',
            padding: '5px 12px',
            borderRadius: 20,
            border: '1px solid var(--border-main)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 11,
            color: 'var(--text-secondary)'
          }}>
            <span style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: 'var(--accent-cyan)',
              display: 'inline-block'
            }} />
            <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Sync Mode:</span>
            <span style={{
              fontWeight: 700,
              color: 'var(--accent-cyan)',
              fontFamily: 'monospace',
              letterSpacing: '0.05em'
            }}>
              BASELINE COPY
            </span>
            <span style={{ color: 'var(--text-tertiary)' }}>· Establishes initial target schema</span>
          </div>
        )}
      </div>

      {/* Stepper Progress Badges */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {STEPS.map((s, idx) => {
          const isCompleted = step > idx + 1;
          const isActive = step === idx + 1;
          return (
            <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div
                title={`Step ${idx + 1}: ${s.label}`}
                style={{
                  width: isActive ? 28 : 24,
                  height: isActive ? 28 : 24,
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 11,
                  fontWeight: 700,
                  transition: 'all 0.2s ease',
                  background: isActive
                    ? 'var(--accent-blue)'
                    : isCompleted
                    ? 'var(--color-success-bg)'
                    : 'var(--bg-surface-raised)',
                  color: isActive
                    ? '#ffffff'
                    : isCompleted
                    ? 'var(--color-success)'
                    : 'var(--text-quaternary)',
                  border: isActive
                    ? '2px solid var(--border-focus)'
                    : isCompleted
                    ? '1.5px solid var(--color-success)'
                    : '1.5px solid var(--border-main)',
                  boxShadow: isActive ? '0 0 12px var(--color-accent-faint)' : 'none'
                }}
              >
                {isCompleted ? <Check size={13} strokeWidth={2.8} /> : idx + 1}
              </div>
              {idx < STEPS.length - 1 && (
                <div
                  style={{
                    width: 18,
                    height: 2,
                    borderRadius: 1,
                    background: step > idx + 1 ? 'var(--color-success)' : 'var(--border-main)',
                    transition: 'background 0.3s ease'
                  }}
                />
              )}
            </div>
          );
        })}
      </div>
    </header>
  );
}
