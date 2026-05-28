import { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import { useAuth } from './AuthContext';

const LogsContext = createContext(null);

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const WS_URL = (() => {
    const explicit = import.meta.env.VITE_WS_ALERTS_URL;
    if (explicit) return explicit;

    if (/^https?:\/\//.test(API_BASE_URL)) {
        return `${API_BASE_URL.replace(/^http/, 'ws').replace(/\/api$/, '')}/ws/alerts`;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/alerts`;
})();
const RECONNECT_STEPS_MS = [5000, 10000, 30000];
const TOAST_DURATION_MS = 6000;
const WS_PING_INTERVAL_MS = 30000;

export function LogsProvider({ children }) {
    const [logs, setLogs] = useState([]);
    const [toasts, setToasts] = useState([]);
    const [wsConnected, setWsConnected] = useState(false);
    const wsRef = useRef(null);
    const reconnectAttempt = useRef(0);
    const reconnectTimer = useRef(null);
    const pingTimer = useRef(null);
    const { isAuthenticated, token } = useAuth();

    const getReconnectDelay = useCallback((attempt) => {
        if (attempt <= 0) return RECONNECT_STEPS_MS[0];
        return RECONNECT_STEPS_MS[Math.min(attempt, RECONNECT_STEPS_MS.length - 1)];
    }, []);

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
        if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) return;
        if (reconnectTimer.current) {
            clearTimeout(reconnectTimer.current);
            reconnectTimer.current = null;
        }

        try {
            const ws = new WebSocket(WS_URL);
            wsRef.current = ws;

            ws.onopen = () => {
                setWsConnected(true);
                reconnectAttempt.current = 0;
                console.log('[SemaBridge] WebSocket connected to alerts');
                // Start keepalive pings to prevent idle disconnects
                if (pingTimer.current) clearInterval(pingTimer.current);
                pingTimer.current = setInterval(() => {
                    if (wsRef.current?.readyState === WebSocket.OPEN) {
                        wsRef.current.send('ping');
                    }
                }, WS_PING_INTERVAL_MS);
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
                if (pingTimer.current) { clearInterval(pingTimer.current); pingTimer.current = null; }
                // Auto-reconnect with exponential backoff: 5s, 10s, then 30s capped.
                const delayMs = getReconnectDelay(reconnectAttempt.current);
                reconnectTimer.current = setTimeout(() => {
                    reconnectAttempt.current += 1;
                    connectWebSocket();
                }, delayMs);
            };

            ws.onerror = () => {
                ws.close();
            };
        } catch {
            // WebSocket constructor can throw if URL is invalid
            setWsConnected(false);

            const delayMs = getReconnectDelay(reconnectAttempt.current);
            reconnectTimer.current = setTimeout(() => {
                reconnectAttempt.current += 1;
                connectWebSocket();
            }, delayMs);
        }
    }, [addLog, getReconnectDelay]);

    // Connect on mount (when authenticated), cleanup on unmount
    useEffect(() => {
        if (!isAuthenticated && !token) return;
        connectWebSocket();
        return () => {
            if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
            if (pingTimer.current) clearInterval(pingTimer.current);
            if (wsRef.current) wsRef.current.close();
        };
    }, [connectWebSocket, isAuthenticated, token]);

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
