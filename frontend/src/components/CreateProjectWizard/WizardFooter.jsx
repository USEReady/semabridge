import React from 'react';
import { ArrowLeft, ArrowRight, Save, Loader2 } from 'lucide-react';

export function WizardFooter({ createdProject, step, goBack, goNext, canAdvance, saving, mappingLoading, editMode }) {
  if (createdProject) return null;

  return (
    <footer style={{
      height: 72,
      position: 'fixed',
      bottom: 0,
      left: 0,
      right: 0,
      background: 'var(--bg-surface)',
      backdropFilter: 'blur(16px)',
      borderTop: '1px solid var(--border-main)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000,
      boxShadow: 'var(--shadow-md)'
    }}>
      <div style={{
        width: '100%',
        maxWidth: (step === 3 || step === 4) ? 1400 : 900,
        padding: '0 36px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between'
      }}>
        {step > 1 ? (
          <button
            type="button"
            onClick={goBack}
            className="flex items-center gap-2 px-5 py-2.5 text-xs font-semibold transition-all rounded-lg border border-[var(--border-main)] bg-[var(--bg-surface-raised)] hover:bg-[var(--bg-surface-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
          >
            <ArrowLeft size={14} /> BACK
          </button>
        ) : <div />}

        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <button
            type="button"
            onClick={goNext}
            disabled={!canAdvance || saving || mappingLoading}
            className={`flex items-center gap-2 px-7 py-2.5 text-xs font-bold transition-all rounded-lg cursor-pointer ${
              !canAdvance || saving || mappingLoading
                ? 'bg-[var(--bg-surface-raised)] text-[var(--text-tertiary)] border border-[var(--border-main)] cursor-not-allowed opacity-50'
                : 'bg-[var(--accent-blue)] text-white hover:bg-[var(--accent-blue-hover)] shadow-sm'
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
    </footer>
  );
}
