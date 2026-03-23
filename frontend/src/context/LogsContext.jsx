import { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';

const LogsContext = createContext(null);

const WS_URL = 'ws://127.0.0.1:8000/ws/alerts';
const RECONNECT_DELAY_MS = 3000;
const MAX_RECONNECT_DELAY_MS = 30000;
const TOAST_DURATION_MS = 6000;

export function LogsProvider({ children }) {
    const [logs, setLogs] = useState([]);
    const [toasts, setToasts] = useState([]);
    const [wsConnected, setWsConnected] = useState(false);
    const wsRef = useRef(null);
    const reconnectDelay = useRef(RECONNECT_DELAY_MS);
    const reconnectTimer = useRef(null);

    // --- Core: add a log entry + auto-toast for warnings/errors ---
    const addLog = useCallback((severity, source, message) => {
        const entry = {
            id: Date.now() + Math.random(),
            timestamp: new Date().toLocaleTimeString(),
            severity,
            source,
            message,
        };
        setLogs(prev => [entry, ...prev].slice(0, 500));

        if (severity === 'success' || severity === 'warning' || severity === 'error' || severity === 'critical') {
            const toast = { ...entry, visible: true };
            setToasts(prev => [...prev, toast]);
            setTimeout(() => {
                setToasts(prev => prev.filter(t => t.id !== toast.id));
            }, TOAST_DURATION_MS);
        }
    }, []);

    const clearLogs = useCallback(() => setLogs([]), []);

    const dismissToast = useCallback((id) => {
        setToasts(prev => prev.filter(t => t.id !== id));
    }, []);

    // --- WebSocket: connect to backend alerts ---
    const connectWebSocket = useCallback(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        try {
            const ws = new WebSocket(WS_URL);
            wsRef.current = ws;

            ws.onopen = () => {
                setWsConnected(true);
                reconnectDelay.current = RECONNECT_DELAY_MS;
                console.log('[SemaBridge] WebSocket connected to alerts');
            };

            ws.onmessage = (event) => {
                try {
                    const alert = JSON.parse(event.data);
                    if (alert.type === 'pong') return;

                    // Dispatch as a log + toast
                    addLog(
                        alert.severity || alert.level?.toLowerCase() || 'info',
                        alert.source || 'backend',
                        alert.message || 'Unknown alert'
                    );
                } catch {
                    // Non-JSON message, ignore
                }
            };

            ws.onclose = () => {
                setWsConnected(false);
                wsRef.current = null;
                // Auto-reconnect with exponential backoff
                reconnectTimer.current = setTimeout(() => {
                    reconnectDelay.current = Math.min(
                        reconnectDelay.current * 1.5,
                        MAX_RECONNECT_DELAY_MS
                    );
                    connectWebSocket();
                }, reconnectDelay.current);
            };

            ws.onerror = () => {
                ws.close();
            };
        } catch {
            // WebSocket constructor can throw if URL is invalid
            setWsConnected(false);
        }
    }, [addLog]);

    // Connect on mount, cleanup on unmount
    useEffect(() => {
        connectWebSocket();
        return () => {
            if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
            if (wsRef.current) wsRef.current.close();
        };
    }, [connectWebSocket]);

    return (
        <LogsContext.Provider value={{
            logs, toasts, addLog, clearLogs, dismissToast,
            wsConnected
        }}>
            {children}
        </LogsContext.Provider>
    );
}

export function useLogs() {
    const ctx = useContext(LogsContext);
    if (!ctx) throw new Error('useLogs must be used within LogsProvider');
    return ctx;
}
