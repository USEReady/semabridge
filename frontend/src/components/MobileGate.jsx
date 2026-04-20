import React from 'react';
import { Monitor, Smartphone, AlertCircle } from 'lucide-react';

const MobileGate = () => {
  return (
    <div className="fixed inset-0 z-[9999] flex flex-col items-center justify-center p-6 text-center bg-[var(--bg-app)] overflow-hidden">
      {/* Background Decorative Elements */}
      <div 
        className="absolute top-[-10%] left-[-10%] w-[60%] h-[60%] rounded-full blur-[120px] opacity-40"
        style={{ background: 'radial-gradient(circle, var(--accent-blue) 0%, transparent 70%)' }}
      />
      <div 
        className="absolute bottom-[-10%] right-[-10%] w-[60%] h-[60%] rounded-full blur-[120px] opacity-20"
        style={{ background: 'radial-gradient(circle, var(--color-error) 0%, transparent 70%)' }}
      />

      <div className="relative max-w-md w-full p-10 rounded-3xl border border-[var(--border-main)] bg-[var(--bg-surface)] shadow-[var(--shadow-lg)] backdrop-blur-md">
        <div className="flex justify-center items-center gap-6 mb-10">
          <div className="relative group">
            <div className="absolute inset-0 bg-[var(--accent-blue)] blur-xl opacity-20 group-hover:opacity-40 transition-opacity" />
            <Monitor className="relative w-20 h-20 text-[var(--accent-blue)]" />
            <div className="absolute -bottom-2 -right-2 bg-[var(--color-success)] rounded-full p-1.5 border-4 border-[var(--bg-surface)]">
              <div className="w-2.5 h-2.5 rounded-full bg-white shadow-sm" />
            </div>
          </div>
          
          <div className="flex flex-col items-center gap-1">
            <div className="w-12 h-[2px] bg-gradient-to-r from-transparent via-[var(--border-main)] to-transparent" />
            <span className="text-[10px] font-bold text-[var(--text-quaternary)] tracking-tighter uppercase">VS</span>
            <div className="w-12 h-[2px] bg-gradient-to-r from-transparent via-[var(--border-main)] to-transparent" />
          </div>

          <div className="relative opacity-30 grayscale">
            <Smartphone className="w-20 h-20 text-[var(--text-tertiary)]" />
            <div className="absolute -top-2 -right-2 bg-[var(--color-error)] rounded-full p-1 border-4 border-[var(--bg-surface)]">
               <AlertCircle className="w-4 h-4 text-white" />
            </div>
          </div>
        </div>

        <h1 className="text-3xl font-extrabold mb-4 text-[var(--text-primary)] tracking-tight">
          Desktop Only
        </h1>
        
        <p className="text-[var(--text-secondary)] mb-10 leading-relaxed text-lg">
          SemaBridge is optimized for computers. Please connect from a <span className="text-[var(--text-primary)] font-semibold">desktop browser</span> to access the full experience.
        </p>

        <div className="p-5 rounded-2xl bg-[var(--color-warning-bg)] border border-[var(--color-warning-muted)] flex items-start gap-4 text-left shadow-sm">
          <AlertCircle className="w-6 h-6 text-[var(--color-warning)] shrink-0 mt-0.5" />
          <div>
            <p className="text-sm text-[var(--color-warning)] font-bold mb-1">
              Compatibility Notice
            </p>
            <p className="text-sm text-[var(--color-warning)] opacity-90 leading-tight">
              Mobile environments do not yet support the advanced semantic modeling engine.
            </p>
          </div>
        </div>
        
        <div className="mt-10 pt-8 border-t border-[var(--border-light)]">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[var(--bg-app)] border border-[var(--border-main)]">
            <div className="w-1.5 h-1.5 rounded-full bg-[var(--color-grey)] animate-pulse" />
            <span className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-widest font-black">
              SemaBridge v1.0
            </span>
          </div>
        </div>
      </div>
      
      <p className="absolute bottom-8 text-[var(--text-quaternary)] text-xs font-medium">
        &copy; {new Date().getFullYear()} SemaBridge. All rights reserved.
      </p>
    </div>
  );
};

export default MobileGate;
