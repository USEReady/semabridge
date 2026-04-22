import { useTheme } from '../context/ThemeProvider';
import { useLogs } from '../context/LogsContext';
import { Sun, Moon, Monitor } from 'lucide-react';
import { useState, useRef, useEffect } from 'react';

export default function ThemeToggle() {
    const { theme, setTheme, resolvedTheme } = useTheme();
    const { addLog } = useLogs();
    const [isOpen, setIsOpen] = useState(false);
    const dropdownRef = useRef(null);

    const isDark = resolvedTheme === 'dark';

    // Close dropdown when clicking outside
    useEffect(() => {
        function handleClickOutside(event) {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
                setIsOpen(false);
            }
        }
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const handleThemeChange = (newTheme) => {
        setTheme(newTheme);
        setIsOpen(false);
        const label = newTheme === 'system' ? 'System Default' : newTheme.charAt(0).toUpperCase() + newTheme.slice(1) + ' Mode';
        addLog('info', 'System', `Theme changed to ${label}`);
    };

    return (
        <div className="relative" ref={dropdownRef}>
            <button
                id="theme-toggle"
                onClick={() => setIsOpen(!isOpen)}
                aria-label="Change theme"
                className="relative w-10 h-10 rounded-xl flex items-center justify-center cursor-pointer border-none outline-none transition-none shadow-none"
                style={{
                    background: 'var(--bg-surface-hover)',
                    color: isDark ? '#fbbf24' : '#6366f1',
                    transform: 'none', // Ensure no hover transform from elsewhere
                }}
                onMouseEnter={e => { 
                    // Override any global hover styles
                    e.currentTarget.style.background = 'var(--bg-surface-hover)';
                    e.currentTarget.style.transform = 'none';
                }}
            >
                <div className="relative w-full h-full flex items-center justify-center pointer-events-none">
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
                </div>
            </button>

            {isOpen && (
                <div
                    className="absolute right-0 mt-2 w-48 rounded-xl shadow-lg ring-1 ring-black ring-opacity-5 z-50 overflow-hidden"
                    style={{
                        background: 'var(--bg-surface)',
                        border: '1px solid var(--border-main)',
                        boxShadow: 'var(--shadow-lg)',
                    }}
                >
                    <div className="py-1">
                        {[
                            { id: 'light', label: 'Light Mode', icon: Sun },
                            { id: 'dark', label: 'Dark Mode', icon: Moon },
                            { id: 'system', label: 'System Default', icon: Monitor },
                        ].map((option) => (
                            <button
                                key={option.id}
                                onClick={() => handleThemeChange(option.id)}
                                className={`flex items-center w-full px-4 py-2.5 text-xs font-medium border-none outline-none cursor-pointer transition-colors ${
                                    theme === option.id ? 'text-accent-blue bg-surface-hover' : 'text-primary'
                                }`}
                                style={{
                                    background: theme === option.id ? 'var(--bg-surface-hover)' : 'transparent',
                                    color: theme === option.id ? 'var(--accent-blue)' : 'var(--text-primary)',
                                }}
                                onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                                onMouseLeave={e => { if (theme !== option.id) e.currentTarget.style.background = 'transparent'; }}
                            >
                                <option.icon size={14} className="mr-3 opacity-70" />
                                {option.label}
                                {theme === option.id && <div className="ml-auto w-1.5 h-1.5 rounded-full bg-accent-blue" />}
                            </button>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
