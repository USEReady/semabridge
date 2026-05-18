import React from 'react';
import { X, Settings } from 'lucide-react';
import { STEPS } from '../../utils/constants';
import { useProjectWizardStore } from '../../store/projectWizardStore';

export function WizardHeader({ editMode, hasMeaningfulData, clearWizardState, navigate, name, step }) {
  const setWizardState = useProjectWizardStore(state => state.setWizardState);
  
  return (
    <div style={{
      height: 72,
      padding: '0 48px',
      background: 'rgba(18, 20, 28, 0.95)',
      backdropFilter: 'blur(12px)',
      borderBottom: '1px solid var(--border-main)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      position: 'sticky',
      top: 0,
      zIndex: 100,
      boxShadow: '0 4px 20px rgba(0, 0, 0, 0.15)'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
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
          style={{
            background: 'var(--bg-surface-raised)',
            border: '1px solid var(--border-main)',
            borderRadius: 10,
            width: 38,
            height: 38,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            color: 'var(--text-secondary)',
            transition: 'all 0.2s'
          }}
          title="Exit Wizard"
        >
          <X size={18} />
        </button>

        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h1 style={{ fontSize: 18, fontWeight: 800, color: 'var(--text-primary)', margin: 0, letterSpacing: '-0.01em' }}>
              {editMode ? 'Modernize Project' : 'New Semantic Project'}
            </h1>
            {name.trim() && (
              <div style={{
                background: 'var(--accent-blue)15',
                color: 'var(--accent-blue)',
                padding: '2px 10px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 700,
                border: '1px solid var(--accent-blue)30'
              }}>
                {name}
              </div>
            )}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Settings size={10} />
            Step {step} of 5 — {STEPS[step - 1].label}
          </div>
        </div>

        {/* Sync Strategy control (copy | upsert) — only in edit mode */}
        {editMode && (
          <div style={{
            background: 'var(--bg-surface)', padding: 16, borderRadius: 12, border: '1px solid var(--border-main)'
          }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>Sync Strategy</div>
            <div style={{ display: 'flex', gap: 8 }}>
              {['copy', 'upsert'].map((opt) => {
                const active = (useProjectWizardStore.getState().wizard.write_strategy || 'copy') === opt;
                return (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => setWizardState({ write_strategy: opt })}
                    style={{
                      padding: '8px 14px', borderRadius: 8, border: 'none', cursor: 'pointer',
                      background: active ? (opt === 'upsert' ? 'var(--accent-orange)' : 'var(--accent-blue)') : 'transparent',
                      color: active ? '#fff' : 'var(--text-secondary)',
                      fontWeight: 700,
                    }}
                  >
                    {opt === 'copy' ? 'Copy (replace target)' : 'Upsert (preserve target)'}
                  </button>
                );
              })}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 8 }}>
              Choose how the sync applies changes to the target: <strong>Copy</strong> fully replaces the target; <strong>Upsert</strong> preserves target-only models and overwrites conflicts.
            </div>
          </div>
        )}
      </div>

      {/* Wizard Progress Stepper (Compact) */}
      <div style={{ display: 'flex', gap: 4 }}>
        {STEPS.map((s, idx) => {
          const isCompleted = step > idx + 1;
          const isActive = step === idx + 1;
          return (
            <div
              key={s.id}
              style={{
                width: 40,
                height: 4,
                borderRadius: 2,
                background: isActive ? 'var(--accent-blue)' : (isCompleted ? 'var(--color-success)' : 'var(--border-main)'),
                transition: 'all 0.3s'
              }}
              title={s.label}
            />
          );
        })}
      </div>
    </div>
  );
}
