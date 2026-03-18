import { useState, useRef, useEffect } from 'react';
import { ChevronDown } from 'lucide-react';

export default function DropdownMenu({ label, items }) {
    const [isOpen, setIsOpen] = useState(false);
    const dropdownRef = useRef(null);

    // Close when clicking outside
    useEffect(() => {
        function handleClickOutside(event) {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
                setIsOpen(false);
            }
        }
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    return (
        <div className="relative inline-block text-left" ref={dropdownRef}>
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer transition-all duration-200 border-none outline-none"
                style={{
                    background: isOpen ? 'var(--bg-surface-hover)' : 'transparent',
                    color: 'var(--text-secondary)',
                }}
                onMouseEnter={e => {
                    if (!isOpen) e.currentTarget.style.background = 'var(--bg-surface-hover)';
                }}
                onMouseLeave={e => {
                    if (!isOpen) e.currentTarget.style.background = 'transparent';
                }}
            >
                {label}
                <ChevronDown size={12} className={`transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} />
            </button>

            {isOpen && (
                <div
                    className="absolute left-0 mt-1 w-48 rounded-xl shadow-lg ring-1 ring-black ring-opacity-5 focus:outline-none z-50 overflow-hidden"
                    style={{
                        background: 'var(--bg-surface)',
                        border: '1px solid var(--border-main)',
                        boxShadow: 'var(--shadow-lg)',
                    }}
                >
                    <div className="py-1">
                        {items.map((item, index) => (
                            <button
                                key={index}
                                onClick={() => {
                                    if (item.onClick) item.onClick();
                                    setIsOpen(false);
                                }}
                                className={`flex items-center w-full px-4 py-2 text-xs font-medium theme-transition border-none outline-none cursor-pointer ${item.danger ? 'text-red-500' : 'text-primary'
                                    }`}
                                onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                                onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                            >
                                {item.icon && <item.icon size={14} className="mr-2 opacity-70" />}
                                {item.label}
                                {item.shortcut && (
                                    <span className="ml-auto text-[10px] opacity-40 font-mono">{item.shortcut}</span>
                                )}
                            </button>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
