import React from 'react';
import { ArrowLeft, ArrowRight, Save, Loader2 } from 'lucide-react';

export function WizardFooter({ createdProject, step, goBack, goNext, canAdvance, saving, mappingLoading, editMode }) {
  if (createdProject) return null;

  return (
    <div style={{
      height: 80,
      position: 'fixed',
      bottom: 0,
      left: 0,
      right: 0,
      background: 'rgba(18, 20, 28, 0.95)',
      backdropFilter: 'blur(12px)',
      borderTop: '1px solid var(--border-main)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000,
      boxShadow: '0 -4px 20px rgba(0, 0, 0, 0.2)'
    }}>
      <div style={{
        width: '100%',
        maxWidth: (step === 3 || step === 4) ? 1400 : 900,
        padding: '0 48px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between'
      }}>
        {step > 1 ? (
          <button
            onClick={goBack}
            className="flex items-center gap-2 px-6 py-2.5 text-xs font-bold transition-all rounded-lg border border-[var(--border-main)] hover:bg-[var(--bg-surface-raised)] text-[var(--text-secondary)]"
          >
            <ArrowLeft size={14} /> BACK
          </button>
        ) : <div />}

        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <button
            onClick={goNext}
            disabled={!canAdvance || saving || mappingLoading}
            className={`flex items-center gap-2 px-8 py-2.5 text-xs font-bold transition-all rounded-lg shadow-lg ${
              !canAdvance || saving || mappingLoading
                ? 'bg-[var(--bg-surface-raised)] text-[var(--text-tertiary)] cursor-not-allowed opacity-50'
                : 'bg-[var(--accent-blue)] text-white hover:bg-blue-600 shadow-blue-900/20'
            }`}
          >
            {(saving || mappingLoading) && <Loader2 size={14} className="animate-spin" />}
            {step === 5 ? (
              <>
                <Save size={14} />
                {saving ? 'SAVING…' : (editMode ? 'SAVE CHANGES' : 'CREATE PROJECT')}
              </>
            ) : (
              <>
                CONTINUE
                <ArrowRight size={14} />
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
