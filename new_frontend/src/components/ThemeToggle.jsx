import { useTheme } from '../context/ThemeProvider';
import { Sun, Moon } from 'lucide-react';

export default function ThemeToggle() {
    const { theme, toggleTheme } = useTheme();
    const isDark = theme === 'dark';

    return (
        <button
            id="theme-toggle"
            onClick={toggleTheme}
            aria-label={`Switch to ${isDark ? 'light' : 'dark'} mode`}
            className="relative w-10 h-10 rounded-xl flex items-center justify-center cursor-pointer border-none outline-none"
            style={{
                background: isDark ? 'var(--bg-surface-hover)' : 'var(--bg-surface-hover)',
                color: isDark ? '#fbbf24' : '#6366f1',
                transition: 'all var(--transition-normal)',
            }}
            onMouseEnter={e => { e.currentTarget.style.transform = 'scale(1.1) rotate(15deg)'; }}
            onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1) rotate(0deg)'; }}
        >
            <span
                style={{
                    position: 'absolute',
                    transition: 'all var(--transition-normal)',
                    opacity: isDark ? 1 : 0,
                    transform: isDark ? 'rotate(0deg) scale(1)' : 'rotate(90deg) scale(0)',
                }}
            >
                <Moon size={18} />
            </span>
            <span
                style={{
                    position: 'absolute',
                    transition: 'all var(--transition-normal)',
                    opacity: isDark ? 0 : 1,
                    transform: isDark ? 'rotate(-90deg) scale(0)' : 'rotate(0deg) scale(1)',
                }}
            >
                <Sun size={18} />
            </span>
        </button>
    );
}
