import { createContext, useContext, useState, useEffect, useCallback } from 'react';

const ThemeContext = createContext(undefined);

function getInitialTheme() {
    const stored = localStorage.getItem('semabridge-theme');
    if (stored === 'dark' || stored === 'light' || stored === 'system') return stored;
    return 'system'; // Default to system
}

export function ThemeProvider({ children }) {
    const [theme, setTheme] = useState(getInitialTheme);
    const [resolvedTheme, setResolvedTheme] = useState(() => {
        const initial = getInitialTheme();
        if (initial === 'system') {
            return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
        }
        return initial;
    });

    useEffect(() => {
        const root = document.documentElement;
        
        const applyTheme = (t) => {
            let resolved = t;
            if (t === 'system') {
                resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
            }
            setResolvedTheme(resolved);
            root.setAttribute('data-theme', resolved);
            document.body.setAttribute('data-theme', resolved);
        };

        applyTheme(theme);
        localStorage.setItem('semabridge-theme', theme);

        if (theme === 'system') {
            const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
            const handleChange = () => applyTheme('system');
            mediaQuery.addEventListener('change', handleChange);
            return () => mediaQuery.removeEventListener('change', handleChange);
        }
    }, [theme]);

    return (
        <ThemeContext.Provider value={{ theme, setTheme, resolvedTheme }}>
            {children}
        </ThemeContext.Provider>
    );
}

export function useTheme() {
    const ctx = useContext(ThemeContext);
    if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
    return ctx;
}
