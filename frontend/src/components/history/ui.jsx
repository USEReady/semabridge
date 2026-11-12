import { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

// Small, theme-aware building blocks shared by the History screen.
// Inline styles + CSS variables only, matching the redesign spec.

const BTN_VARIANTS = {
    default: {
        background: 'var(--bg-surface)',
        color: 'var(--text-primary)',
        border: '1px solid var(--border-main)',
    },
    primary: {
        background: 'var(--accent-blue)',
        color: '#fff',
        border: '1px solid var(--accent-blue)',
    },
    danger: {
        background: 'transparent',
        color: 'var(--color-error)',
        border: '1px solid var(--color-error)',
    },
    dangerSolid: {
        background: 'var(--color-error)',
        color: '#fff',
        border: '1px solid var(--color-error)',
    },
    ghost: {
        background: 'transparent',
        color: 'var(--text-secondary)',
        border: '1px solid transparent',
    },
};

export function Btn({ variant = 'default', children, style, disabled, ...rest }) {
    return (
        <button
            type="button"
            disabled={disabled}
            style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '6px 12px', borderRadius: 6,
                fontSize: 13, fontWeight: 500, lineHeight: 1.4,
                cursor: disabled ? 'not-allowed' : 'pointer',
                opacity: disabled ? 0.55 : 1,
                whiteSpace: 'nowrap',
                ...BTN_VARIANTS[variant],
                ...style,
            }}
            {...rest}
        >
            {children}
        </button>
    );
}

export function Card({ children, style, ...rest }) {
    return (
        <div
            style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-main)',
                borderRadius: 10,
                ...style,
            }}
            {...rest}
        >
            {children}
        </div>
    );
}

const TONES = {
    success: { color: 'var(--color-success)', bg: 'var(--color-success-bg)' },
    warning: { color: 'var(--color-warning)', bg: 'var(--color-warning-bg)' },
    error: { color: 'var(--color-error)', bg: 'var(--color-error-bg)' },
    info: { color: 'var(--accent-blue)', bg: 'var(--color-accent-faint)' },
    default: { color: 'var(--text-secondary)', bg: 'var(--bg-surface-raised)' },
};

export function Pill({ tone = 'default', children, style }) {
    const t = TONES[tone] || TONES.default;
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '1px 8px', borderRadius: 999,
            fontSize: 11, fontWeight: 600,
            color: t.color, background: t.bg,
            ...style,
        }}>
            {children}
        </span>
    );
}

/**
 * Button + popover menu. `items` = [{ key, label, hint, onClick, disabled, danger }]
 * or a `{ key, divider: true }` / `{ key, heading }` entry.
 */
export function MenuButton({ label, items, variant = 'default', align = 'right', icon, buttonStyle }) {
    const [open, setOpen] = useState(false);
    const ref = useRef(null);

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    return (
        <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
            <Btn variant={variant} onClick={() => setOpen((v) => !v)} aria-haspopup="menu" aria-expanded={open} style={buttonStyle}>
                {icon}{label}<ChevronDown size={14} style={{ opacity: 0.7 }} />
            </Btn>
            {open && (
                <div
                    role="menu"
                    style={{
                        position: 'absolute', top: 'calc(100% + 4px)', [align]: 0,
                        minWidth: 220, maxHeight: 320, overflowY: 'auto',
                        background: 'var(--bg-surface)', border: '1px solid var(--border-main)',
                        borderRadius: 8, boxShadow: 'var(--shadow-lg)', padding: 4, zIndex: 60,
                    }}
                >
                    {items.length === 0 && (
                        <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--text-tertiary)' }}>Nothing to show</div>
                    )}
                    {items.map((it) => {
                        if (it.divider) return <div key={it.key} style={{ height: 1, background: 'var(--border-light)', margin: '4px 0' }} />;
                        if (it.heading) {
                            return (
                                <div key={it.key} style={{ padding: '6px 10px 2px', fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)' }}>
                                    {it.heading}
                                </div>
                            );
                        }
                        return (
                            <button
                                key={it.key}
                                type="button"
                                role="menuitem"
                                disabled={it.disabled}
                                onClick={() => { setOpen(false); it.onClick?.(); }}
                                style={{
                                    display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 1,
                                    width: '100%', textAlign: 'left', padding: '7px 10px', borderRadius: 6,
                                    background: it.active ? 'var(--color-accent-faint)' : 'transparent',
                                    border: 'none', cursor: it.disabled ? 'not-allowed' : 'pointer',
                                    opacity: it.disabled ? 0.5 : 1,
                                    color: it.danger ? 'var(--color-error)' : 'var(--text-primary)', fontSize: 13,
                                }}
                                onMouseEnter={(e) => { if (!it.active) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                                onMouseLeave={(e) => { e.currentTarget.style.background = it.active ? 'var(--color-accent-faint)' : 'transparent'; }}
                            >
                                <span>{it.label}</span>
                                {it.hint && <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{it.hint}</span>}
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

/** Pill-shaped segmented filter: options = [{ id, label, count? }] */
export function Segmented({ options, value, onChange, size = 'sm' }) {
    return (
        <div style={{
            display: 'inline-flex', padding: 2, gap: 2, borderRadius: 8,
            background: 'var(--bg-surface-raised)', border: '1px solid var(--border-light)',
        }}>
            {options.map((o) => {
                const active = o.id === value;
                return (
                    <button
                        key={o.id}
                        type="button"
                        aria-pressed={active}
                        onClick={() => onChange(o.id)}
                        style={{
                            padding: size === 'sm' ? '3px 10px' : '5px 12px', borderRadius: 6, border: 'none',
                            fontSize: 12, fontWeight: active ? 600 : 500, cursor: 'pointer',
                            background: active ? 'var(--bg-surface)' : 'transparent',
                            color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                            boxShadow: active ? 'var(--shadow-sm)' : 'none',
                        }}
                    >
                        {o.label}{o.count != null && <span style={{ marginLeft: 5, color: 'var(--text-tertiary)' }}>{o.count}</span>}
                    </button>
                );
            })}
        </div>
    );
}
