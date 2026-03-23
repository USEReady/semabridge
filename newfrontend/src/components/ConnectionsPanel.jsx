import { useState, useEffect, useRef } from 'react';
import {
    Settings,
    Cloud,
    Snowflake,
    CheckCircle2,
    AlertCircle,
    Loader2,
    Eye,
    EyeOff,
    Trash2,
    Zap,
    X,
    ShieldCheck,
    Database,
    LogIn,
    LogOut,
    ExternalLink,
    Copy,
    User,
    Save,
    FileCode2,
    RotateCcw,
    Key,
} from 'lucide-react';
import { api } from '../utils/api';
import { useLogs } from '../context/LogsContext';

function withTimeout(promise, ms, fallbackValue = null) {
    return Promise.race([
        promise,
        new Promise(resolve => setTimeout(() => resolve(fallbackValue), ms)),
    ]);
}

/* ───────────────────────────────────────────────
   Fabric Interactive Login Card
   ─────────────────────────────────────────────── */

function FabricLoginCard() {
    const [authStatus, setAuthStatus] = useState(null);
    const [loginPhase, setLoginPhase] = useState('idle'); // idle | requesting | code_shown | polling | success | failed
    const [deviceCode, setDeviceCode] = useState(null);
    const [error, setError] = useState(null);
    const [copied, setCopied] = useState(false);
    const pollTimer = useRef(null);
    // Pre-fetched device code — ready before user clicks "Sign in"
    const prefetchedCode = useRef(null);
    const prefetchInFlight = useRef(false);
    const { addLog } = useLogs();

    // Workspace discovery state
    const [workspaces, setWorkspaces] = useState([]);
    const [selectedWorkspaceId, setSelectedWorkspaceId] = useState('');
    const [savedWorkspaceName, setSavedWorkspaceName] = useState('');
    const [loadingWorkspaces, setLoadingWorkspaces] = useState(false);
    const [workspaceSaving, setWorkspaceSaving] = useState(false);

    // Load auth status on mount
    useEffect(() => {
        loadAuthStatus();
        return () => { if (pollTimer.current) clearTimeout(pollTimer.current); };
    }, []);

    /**
     * Pre-fetch the device code in the background so it is ready the
     * moment the user clicks "Sign in with Microsoft".
     */
    const prefetchDeviceCode = () => {
        if (prefetchInFlight.current || prefetchedCode.current) return;
        prefetchInFlight.current = true;
        api.fabricLogin()
            .then(result => { prefetchedCode.current = result; })
            .catch(() => { /* silent — will fetch fresh on click */ })
            .finally(() => { prefetchInFlight.current = false; });
    };

    const loadAuthStatus = async () => {
        try {
            const status = await withTimeout(api.fabricAuthStatus(), 4000, null);
            if (status) {
                setAuthStatus(status);
            }

            if (status?.logged_in) {
                setLoginPhase('success');
                fetchWorkspaces();
            } else {
                // Not logged in — silently pre-fetch a device code in the background
                prefetchDeviceCode();
            }

            // Also load stored workspace_id
            const connStatus = await withTimeout(api.getConnectionsStatus(), 4000, null);
            if (connStatus?.fabric?.credentials?.workspace_id) {
                setSelectedWorkspaceId(connStatus.fabric.credentials.workspace_id);
            }
            if (connStatus?.fabric?.credentials?.workspace_name) {
                setSavedWorkspaceName(connStatus.fabric.credentials.workspace_name);
            }
        } catch { /* backend not running */ }
    };

    const fetchWorkspaces = async () => {
        setLoadingWorkspaces(true);
        try {
            const result = await api.fabricListWorkspaces();
            setWorkspaces(result.workspaces || []);
            addLog('info', 'Fabric', `Discovered ${result.workspaces?.length || 0} workspaces`);
        } catch (err) {
            addLog('warning', 'Fabric', `Could not fetch workspaces: ${err.message}`);
        } finally {
            setLoadingWorkspaces(false);
        }
    };

    const handleLogin = async () => {
        setError(null);

        // Use the pre-fetched code if it arrived in time — otherwise fetch now
        if (prefetchedCode.current) {
            const result = prefetchedCode.current;
            prefetchedCode.current = null;
            setDeviceCode(result);
            setLoginPhase('code_shown');
            addLog('info', 'Fabric Auth', `Device code: ${result.user_code}`);
            window.open(result.verification_uri, '_blank', 'noopener,noreferrer');
            startPolling();
            // Pre-fetch the next one quietly for any re-login
            setTimeout(prefetchDeviceCode, 2000);
            return;
        }

        // Fallback: fetch fresh (MSAL app is warm so this is ~300 ms)
        setLoginPhase('requesting');
        try {
            const result = await api.fabricLogin();
            setDeviceCode(result);
            setLoginPhase('code_shown');
            addLog('info', 'Fabric Auth', `Device code: ${result.user_code}`);
            window.open(result.verification_uri, '_blank', 'noopener,noreferrer');
            startPolling();
        } catch (err) {
            setError(err.message);
            setLoginPhase('failed');
            addLog('error', 'Fabric Auth', err.message);
        }
    };

    const startPolling = () => {
        setLoginPhase('polling');
        pollTimer.current = setTimeout(async () => {
            try {
                const result = await api.fabricPoll();
                if (result.status === 'success') {
                    setLoginPhase('success');
                    setAuthStatus({
                        auth_method: 'interactive',
                        username: result.username,
                        tenant_id: result.tenant_id,
                        logged_in: true,
                        token_valid: true,
                    });
                    addLog('info', 'Fabric Auth', `Signed in as ${result.username}`);
                    fetchWorkspaces();
                } else if (result.status === 'pending') {
                    startPolling();
                } else {
                    setError(result.message);
                    setLoginPhase('failed');
                }
            } catch (err) {
                setError(err.message);
                setLoginPhase('failed');
            }
        }, 2000);
    };

    const handleLogout = async () => {
        try {
            await api.fabricLogout();
            setAuthStatus(null);
            setLoginPhase('idle');
            setDeviceCode(null);
            setWorkspaces([]);
            setSelectedWorkspaceId('');
            setSavedWorkspaceName('');
            addLog('info', 'Fabric Auth', 'Logged out');
        } catch (err) {
            addLog('error', 'Fabric Auth', err.message);
        }
    };

    const handleSaveWorkspace = async () => {
        if (!selectedWorkspaceId) return;
        setWorkspaceSaving(true);
        const ws = workspaces.find(w => w.id === selectedWorkspaceId);
        const name = ws?.displayName || '';
        try {
            await api.fabricSelectWorkspace(selectedWorkspaceId, name);
            setSavedWorkspaceName(name);
            addLog('info', 'Fabric', `Workspace set: ${name} (${selectedWorkspaceId})`);
        } catch (err) {
            addLog('error', 'Fabric', err.message);
        } finally {
            setWorkspaceSaving(false);
        }
    };

    const copyCode = () => {
        if (deviceCode?.user_code) {
            navigator.clipboard.writeText(deviceCode.user_code);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        }
    };

    const isLoggedIn = authStatus?.logged_in;

    return (
        <div className="rounded-xl border overflow-hidden"
            style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface)' }}>
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b"
                style={{ borderColor: 'var(--border-main)' }}>
                <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center"
                        style={{ background: 'rgba(99,102,241,0.1)' }}>
                        <Cloud size={16} style={{ color: '#6366f1' }} />
                    </div>
                    <div>
                        <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
                            Microsoft Fabric
                        </h3>
                        <p className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                            Sign in with your Microsoft account
                        </p>
                    </div>
                </div>
                {isLoggedIn ? (
                    <span className="flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded-full"
                        style={{ background: 'rgba(52,211,153,0.1)', color: '#34d399' }}>
                        <CheckCircle2 size={10} /> SIGNED IN
                    </span>
                ) : (
                    <span className="flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded-full"
                        style={{ background: 'rgba(245,158,11,0.1)', color: '#f59e0b' }}>
                        <AlertCircle size={10} /> NOT SIGNED IN
                    </span>
                )}
            </div>

            <div className="px-4 py-4">
                {/* ── Logged In State ── */}
                {isLoggedIn && (
                    <div className="space-y-3">
                        <div className="flex items-center gap-3 p-3 rounded-lg"
                            style={{ background: 'rgba(52,211,153,0.05)', border: '1px solid rgba(52,211,153,0.15)' }}>
                            <div className="w-9 h-9 rounded-full flex items-center justify-center"
                                style={{ background: 'rgba(99,102,241,0.15)' }}>
                                <User size={16} style={{ color: '#6366f1' }} />
                            </div>
                            <div>
                                <div className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>
                                    {authStatus?.username}
                                </div>
                                <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                                    Tenant: {authStatus?.tenant_id?.substring(0, 8)}...
                                </div>
                            </div>
                        </div>

                        {/* Workspace Selector */}
                        <div>
                            <label className="block text-[11px] font-medium mb-1"
                                style={{ color: 'var(--text-secondary)' }}>
                                Workspace
                                {savedWorkspaceName && (
                                    <span className="ml-1.5 text-[10px] font-normal"
                                        style={{ color: 'var(--text-tertiary)' }}>
                                        (active: {savedWorkspaceName})
                                    </span>
                                )}
                            </label>
                            <div className="flex gap-2">
                                {loadingWorkspaces ? (
                                    <div className="flex-1 flex items-center gap-2 px-3 py-2 text-xs"
                                        style={{ color: 'var(--text-tertiary)' }}>
                                        <Loader2 size={12} className="animate-spin" />
                                        Loading workspaces...
                                    </div>
                                ) : workspaces.length > 0 ? (
                                    <select
                                        value={selectedWorkspaceId}
                                        onChange={e => setSelectedWorkspaceId(e.target.value)}
                                        className="flex-1 px-3 py-2 rounded-lg text-xs border outline-none appearance-none cursor-pointer"
                                        style={{
                                            background: 'var(--bg-input, var(--bg-primary))',
                                            borderColor: 'var(--border-main)',
                                            color: 'var(--text-primary)',
                                        }}>
                                        <option value="">Select a workspace...</option>
                                        {workspaces.map(ws => (
                                            <option key={ws.id} value={ws.id}>
                                                {ws.displayName}
                                            </option>
                                        ))}
                                    </select>
                                ) : (
                                    <div className="flex-1 flex items-center gap-2 px-3 py-2 text-xs rounded-lg"
                                        style={{ color: 'var(--text-tertiary)', background: 'var(--bg-input, var(--bg-primary))', border: '1px solid var(--border-main)' }}>
                                        No workspaces found
                                        <button onClick={fetchWorkspaces} className="underline text-[10px]"
                                            style={{ color: 'var(--color-accent)' }}>Retry</button>
                                    </div>
                                )}
                                <button onClick={handleSaveWorkspace}
                                    disabled={!selectedWorkspaceId || workspaceSaving}
                                    className="px-3 py-2 rounded-lg text-xs font-bold transition-all"
                                    style={{
                                        background: selectedWorkspaceId ? 'var(--color-accent)' : 'var(--border-main)',
                                        color: '#fff',
                                        opacity: (!selectedWorkspaceId || workspaceSaving) ? 0.5 : 1,
                                    }}>
                                    {workspaceSaving ? <Loader2 size={12} className="animate-spin" /> : 'Save'}
                                </button>
                            </div>
                            {workspaces.length > 0 && (
                                <p className="text-[10px] mt-1" style={{ color: 'var(--text-tertiary)' }}>
                                    {workspaces.length} workspace{workspaces.length !== 1 ? 's' : ''} available
                                </p>
                            )}
                        </div>
                    </div>
                )}

                {/* ── Device Code Flow ── */}
                {!isLoggedIn && loginPhase === 'idle' && (
                    <button onClick={handleLogin}
                        className="w-full flex items-center justify-center gap-2 py-3 rounded-lg text-sm font-bold transition-all hover:brightness-110"
                        style={{ background: 'linear-gradient(135deg, #6366f1, #8b5cf6)', color: '#fff' }}>
                        <LogIn size={16} />
                        Sign in with Microsoft
                    </button>
                )}

                {loginPhase === 'requesting' && (
                    <div className="flex items-center justify-center gap-2 py-6 text-xs"
                        style={{ color: 'var(--text-secondary)' }}>
                        <Loader2 size={16} className="animate-spin" />
                        Requesting device code...
                    </div>
                )}

                {(loginPhase === 'code_shown' || loginPhase === 'polling') && deviceCode && (
                    <div className="space-y-3">
                        <div className="text-center py-3 px-4 rounded-xl"
                            style={{ background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.2)' }}>
                            <p className="text-[11px] mb-2" style={{ color: 'var(--text-secondary)' }}>
                                A browser window opened — paste this code to sign in:
                            </p>
                            <div className="flex items-center justify-center gap-2 mb-3">
                                <code className="text-2xl font-mono font-black tracking-[0.2em] px-4 py-2 rounded-lg"
                                    style={{ background: 'var(--bg-primary)', color: '#6366f1', border: '1px solid rgba(99,102,241,0.3)' }}>
                                    {deviceCode.user_code}
                                </code>
                                <button onClick={copyCode} className="p-2 rounded-lg hover:bg-surface-hover"
                                    title="Copy code">
                                    {copied
                                        ? <CheckCircle2 size={16} style={{ color: '#34d399' }} />
                                        : <Copy size={16} style={{ color: 'var(--text-tertiary)' }} />}
                                </button>
                            </div>
                            <a href={deviceCode.verification_uri}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold transition-all hover:brightness-110"
                                style={{ background: '#6366f1', color: '#fff' }}>
                                <ExternalLink size={12} />
                                Reopen login page
                            </a>
                        </div>

                        {loginPhase === 'polling' && (
                            <div className="flex items-center justify-center gap-2 text-[11px]"
                                style={{ color: 'var(--text-tertiary)' }}>
                                <Loader2 size={12} className="animate-spin" />
                                Waiting for you to sign in...
                            </div>
                        )}
                    </div>
                )}

                {loginPhase === 'failed' && (
                    <div className="text-center py-4 space-y-2">
                        <div className="text-xs px-3 py-2 rounded-lg"
                            style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>
                            {error}
                        </div>
                        <button onClick={() => { setLoginPhase('idle'); setError(null); }}
                            className="text-xs underline" style={{ color: 'var(--text-tertiary)' }}>
                            Try again
                        </button>
                    </div>
                )}
            </div>

            {/* Actions Footer */}
            {isLoggedIn && (
                <div className="flex items-center justify-between px-4 py-3 border-t"
                    style={{ borderColor: 'var(--border-main)' }}>
                    <div className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                        <ShieldCheck size={10} style={{ color: '#34d399' }} />
                        Token stored in DuckDB
                    </div>
                    <button onClick={handleLogout}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs hover:bg-red-500/10 transition-colors"
                        style={{ color: '#ef4444' }}>
                        <LogOut size={12} />
                        Sign Out
                    </button>
                </div>
            )}
        </div>
    );
}


/* ───────────────────────────────────────────────
   Snowflake Credentials Card (Password + SSO)
   ─────────────────────────────────────────────── */

const SNOWFLAKE_FIELDS = [
    { key: 'account', label: 'Account', placeholder: 'abc123.us-east-1', secret: false, required: true },
    { key: 'user', label: 'Username', placeholder: 'your_username', secret: false, required: true },
    { key: 'password', label: 'Password', placeholder: 'Enter your Snowflake password', secret: true, passwordOnly: true },
    { key: 'private_key', label: 'Private Key (PEM)', placeholder: 'Paste your private key content', secret: true, keypairOnly: true },
    { key: 'warehouse', label: 'Warehouse', placeholder: 'COMPUTE_WH', secret: false },
    { key: 'database', label: 'Database', placeholder: 'MY_DATABASE', secret: false },
    { key: 'schema_name', label: 'Schema', placeholder: 'PUBLIC', secret: false },
    { key: 'role', label: 'Role (optional)', placeholder: 'SYSADMIN', secret: false },
];

function SnowflakeCard({ status }) {
    const [formData, setFormData] = useState({});
    const [showSecrets, setShowSecrets] = useState({});
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState(null);
    const [authMode, setAuthMode] = useState('password'); // 'password' | 'sso' | 'keypair'
    const [ssoLoading, setSsoLoading] = useState(false);
    const [ssoResult, setSsoResult] = useState(null);
    const { addLog } = useLogs();

    useEffect(() => {
        if (status?.credentials) {
            const initial = {};
            for (const field of SNOWFLAKE_FIELDS) {
                const stored = status.credentials[field.key];
                // Secret fields come back masked from the backend — never pre-fill them
                initial[field.key] = (field.secret || !stored) ? '' : stored;
            }
            setFormData(initial);

            // Detect previously configured auth mode
            const storedAuthType = status.credentials.auth_type;
            if (storedAuthType === 'keypair') {
                setAuthMode('keypair');
            } else if (storedAuthType === 'externalbrowser' || status.credentials.authenticator === 'externalbrowser') {
                setAuthMode('sso');
            } else {
                setAuthMode('password');
            }
        }
    }, [status]);

    const handleSave = async () => {
        setSaving(true);
        setTestResult(null);
        try {
            const filtered = {};
            for (const [k, v] of Object.entries(formData)) { if (v) filtered[k] = v; }

            // Explicitly set auth_type and clean stale fields per mode
            if (authMode === 'password') {
                filtered.auth_type = 'password';
                delete filtered.private_key;
                delete filtered.authenticator;
            } else if (authMode === 'sso') {
                filtered.auth_type = 'externalbrowser';
                filtered.authenticator = 'externalbrowser';
                delete filtered.password;
                delete filtered.private_key;
            } else if (authMode === 'keypair') {
                filtered.auth_type = 'keypair';
                delete filtered.password;
                delete filtered.authenticator;
            }

            await api.saveConnection('snowflake', filtered);
            addLog('info', 'Connections', `Snowflake credentials saved (${authMode})`);
        } catch (err) {
            addLog('error', 'Connections', err.message);
        } finally { setSaving(false); }
    };

    const handleTest = async () => {
        setTesting(true);
        setTestResult(null);
        try {
            const result = await api.testConnection('snowflake');
            setTestResult(result);
            addLog(result.status === 'success' ? 'info' : 'warning', 'Connections', result.message);
        } catch (err) {
            setTestResult({ status: 'failed', message: err.message });
        } finally { setTesting(false); }
    };

    const handleSsoLogin = async () => {
        // First save account/user so the backend knows where to connect
        const account = formData.account?.trim();
        const user = formData.user?.trim();
        if (!account || !user) {
            setTestResult({ status: 'failed', message: 'Please enter Account and Username before signing in via SSO.' });
            return;
        }

        setSsoLoading(true);
        setSsoResult(null);
        setTestResult(null);
        try {
            // Save creds first (without password)
            const saveData = {};
            for (const [k, v] of Object.entries(formData)) {
                if (v && k !== 'password') saveData[k] = v;
            }
            await api.saveConnection('snowflake', saveData);

            // Trigger SSO login (opens browser)
            addLog('info', 'Snowflake SSO', 'Opening browser for SSO login...');
            const result = await api.snowflakeSsoLogin();
            setSsoResult(result);
            if (result.status === 'success') {
                addLog('info', 'Snowflake SSO', result.message);
            } else {
                addLog('warning', 'Snowflake SSO', result.message);
            }
        } catch (err) {
            setSsoResult({ status: 'failed', message: err.message });
            addLog('error', 'Snowflake SSO', err.message);
        } finally { setSsoLoading(false); }
    };

    const handleDelete = async () => {
        if (!confirm('Remove all stored Snowflake credentials?')) return;
        try {
            await api.deleteConnection('snowflake');
            setFormData({});
            setTestResult(null);
            setSsoResult(null);
        } catch (err) { addLog('error', 'Connections', err.message); }
    };

    const isConfigured = status?.configured;

    // Filter fields based on auth mode
    const visibleFields = SNOWFLAKE_FIELDS.filter(f => {
        if (authMode === 'sso' && (f.passwordOnly || f.keypairOnly)) return false;
        if (authMode === 'password' && f.keypairOnly) return false;
        if (authMode === 'keypair' && f.passwordOnly) return false;
        return true;
    });

    return (
        <div className="rounded-xl border overflow-hidden"
            style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface)' }}>
            <div className="flex items-center justify-between px-4 py-3 border-b"
                style={{ borderColor: 'var(--border-main)' }}>
                <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center"
                        style={{ background: 'rgba(41,181,232,0.1)' }}>
                        <Snowflake size={16} style={{ color: '#29b5e8' }} />
                    </div>
                    <div>
                        <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Snowflake</h3>
                        <p className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                            {authMode === 'sso' ? 'Browser SSO Authentication' : 'Account Credentials'}
                        </p>
                    </div>
                </div>
                {isConfigured ? (
                    <span className="flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded-full"
                        style={{ background: 'rgba(52,211,153,0.1)', color: '#34d399' }}>
                        <CheckCircle2 size={10} /> CONFIGURED
                    </span>
                ) : (
                    <span className="flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded-full"
                        style={{ background: 'rgba(245,158,11,0.1)', color: '#f59e0b' }}>
                        <AlertCircle size={10} /> NOT CONFIGURED
                    </span>
                )}
            </div>

            {/* Auth Mode Toggle */}
            <div className="flex mx-4 mt-3 rounded-lg overflow-hidden border"
                style={{ borderColor: 'var(--border-main)' }}>
                <button
                    onClick={() => { setAuthMode('password'); setTestResult(null); setSsoResult(null); }}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-bold transition-all"
                    style={{
                        background: authMode === 'password' ? 'var(--color-accent)' : 'transparent',
                        color: authMode === 'password' ? '#fff' : 'var(--text-tertiary)',
                    }}>
                    <Database size={11} /> Password
                </button>
                <button
                    onClick={() => { setAuthMode('sso'); setTestResult(null); setSsoResult(null); }}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-bold transition-all"
                    style={{
                        background: authMode === 'sso' ? 'linear-gradient(135deg, #29b5e8, #0ea5e9)' : 'transparent',
                        color: authMode === 'sso' ? '#fff' : 'var(--text-tertiary)',
                    }}>
                    <LogIn size={11} /> SSO (Browser)
                </button>
                <button
                    onClick={() => { setAuthMode('keypair'); setTestResult(null); setSsoResult(null); }}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-bold transition-all"
                    style={{
                        background: authMode === 'keypair' ? 'var(--color-accent)' : 'transparent',
                        color: authMode === 'keypair' ? '#fff' : 'var(--text-tertiary)',
                    }}>
                    <Key size={11} /> Key Pair
                </button>
            </div>

            <div className="px-4 py-3 space-y-3">
                {visibleFields.map(field => (
                    <div key={field.key}>
                        <label className="block text-[11px] font-medium mb-1"
                            style={{ color: 'var(--text-secondary)' }}>{field.label}</label>
                        <div className="relative">
                            {field.multiline ? (
                                <textarea
                                    value={formData[field.key] || ''}
                                    onChange={e => { setFormData(p => ({ ...p, [field.key]: e.target.value })); setTestResult(null); }}
                                    placeholder={field.placeholder}
                                    rows={4}
                                    className="w-full px-3 py-2 rounded-lg text-xs border outline-none focus:ring-1 transition-colors resize-none font-mono"
                                    style={{ background: 'var(--bg-input, var(--bg-primary))', borderColor: 'var(--border-main)', color: 'var(--text-primary)' }}
                                />
                            ) : (
                                <input
                                    type={field.secret && !showSecrets[field.key] ? 'password' : 'text'}
                                    value={formData[field.key] || ''}
                                    onChange={e => { setFormData(p => ({ ...p, [field.key]: e.target.value })); setTestResult(null); }}
                                    placeholder={field.placeholder}
                                    className="w-full px-3 py-2 rounded-lg text-xs border outline-none focus:ring-1 transition-colors"
                                    style={{ background: 'var(--bg-input, var(--bg-primary))', borderColor: 'var(--border-main)', color: 'var(--text-primary)' }}
                                />
                            )}
                            {field.secret && !field.multiline && (
                                <button onClick={() => setShowSecrets(p => ({ ...p, [field.key]: !p[field.key] }))}
                                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded opacity-40 hover:opacity-100" type="button">
                                    {showSecrets[field.key]
                                        ? <EyeOff size={12} style={{ color: 'var(--text-secondary)' }} />
                                        : <Eye size={12} style={{ color: 'var(--text-secondary)' }} />}
                                </button>
                            )}
                        </div>
                    </div>
                ))}

                {/* SSO Login Button */}
                {authMode === 'sso' && (
                    <button onClick={handleSsoLogin} disabled={ssoLoading}
                        className="w-full flex items-center justify-center gap-2 py-3 rounded-lg text-sm font-bold transition-all hover:brightness-110"
                        style={{
                            background: 'linear-gradient(135deg, #29b5e8, #0ea5e9)',
                            color: '#fff',
                            opacity: ssoLoading ? 0.7 : 1,
                        }}>
                        {ssoLoading ? (
                            <><Loader2 size={16} className="animate-spin" /> Opening browser...</>
                        ) : (
                            <><LogIn size={16} /> Sign in via SSO</>
                        )}
                    </button>
                )}

                {/* SSO Result */}
                {ssoResult && (
                    <div className="px-3 py-2 rounded-lg text-xs flex items-center gap-2"
                        style={{
                            background: ssoResult.status === 'success' ? 'rgba(52,211,153,0.1)' : 'rgba(239,68,68,0.1)',
                            color: ssoResult.status === 'success' ? '#34d399' : '#ef4444',
                        }}>
                        {ssoResult.status === 'success' ? <CheckCircle2 size={12} /> : <AlertCircle size={12} />}
                        {ssoResult.message}
                    </div>
                )}
            </div>

            {testResult && (
                <div className="mx-4 mb-3 px-3 py-2 rounded-lg text-xs flex items-center gap-2"
                    style={{
                        background: testResult.status === 'success' ? 'rgba(52,211,153,0.1)' : 'rgba(239,68,68,0.1)',
                        color: testResult.status === 'success' ? '#34d399' : '#ef4444',
                    }}>
                    {testResult.status === 'success' ? <CheckCircle2 size={12} /> : <AlertCircle size={12} />}
                    {testResult.message}
                </div>
            )}

            <div className="flex items-center gap-2 px-4 py-3 border-t" style={{ borderColor: 'var(--border-main)' }}>
                <button onClick={handleSave} disabled={saving}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold"
                    style={{ background: 'var(--color-accent)', color: '#fff', opacity: saving ? 0.6 : 1 }}>
                    {saving ? <Loader2 size={12} className="animate-spin" /> : <Database size={12} />} Save
                </button>
                <button onClick={handleTest} disabled={testing || !isConfigured}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium border"
                    style={{ borderColor: 'var(--border-main)', color: 'var(--text-secondary)', opacity: (testing || !isConfigured) ? 0.4 : 1 }}>
                    {testing ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />} Test
                </button>
                {isConfigured && (
                    <button onClick={handleDelete} className="ml-auto p-2 rounded-lg hover:bg-red-500/10"
                        style={{ color: 'var(--text-tertiary)' }} title="Remove"><Trash2 size={14} /></button>
                )}
            </div>
        </div>
    );
}


/* ───────────────────────────────────────────────
   Connections Panel (Modal)
   ─────────────────────────────────────────────── */

/* ───────────────────────────────────────────────
   Global Config Editor (~/.semabridge/config.yaml)
   ─────────────────────────────────────────────── */

function GlobalConfigEditor() {
    const [content, setContent] = useState('');
    const [originalContent, setOriginalContent] = useState('');
    const [configPath, setConfigPath] = useState('');
    const [configExists, setConfigExists] = useState(false);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const [success, setSuccess] = useState(null);
    const { addLog } = useLogs();

    useEffect(() => {
        loadConfig();
    }, []);

    const loadConfig = async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await api.getGlobalConfig();
            setContent(data.content || '');
            setOriginalContent(data.content || '');
            setConfigPath(data.path || '~/.semabridge/config.yaml');
            setConfigExists(data.exists || false);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        setError(null);
        setSuccess(null);
        try {
            const result = await api.saveGlobalConfig(content);
            setOriginalContent(content);
            setConfigExists(true);
            setSuccess('Config saved successfully');
            addLog('info', 'Global Config', `Saved to ${result.path}`);
            setTimeout(() => setSuccess(null), 3000);
        } catch (err) {
            const msg = err.message || 'Failed to save';
            setError(msg);
            addLog('error', 'Global Config', msg);
        } finally {
            setSaving(false);
        }
    };

    const isDirty = content !== originalContent;

    if (loading) {
        return (
            <div className="flex items-center justify-center py-12">
                <Loader2 size={24} className="animate-spin" style={{ color: 'var(--color-accent)' }} />
            </div>
        );
    }

    return (
        <div className="space-y-3">
            {/* Path info */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <FileCode2 size={14} style={{ color: 'var(--text-tertiary)' }} />
                    <span className="text-[11px] font-mono" style={{ color: 'var(--text-secondary)' }}>
                        {configPath}
                    </span>
                    {!configExists && (
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-bold"
                            style={{ background: 'rgba(245,158,11,0.15)', color: '#d97706' }}>
                            NEW
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={loadConfig}
                        className="p-1.5 rounded-lg hover:bg-surface-hover"
                        style={{ color: 'var(--text-tertiary)' }}
                        title="Reload"
                    >
                        <RotateCcw size={14} />
                    </button>
                    <button
                        onClick={handleSave}
                        disabled={!isDirty || saving}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all"
                        style={{
                            background: isDirty ? 'var(--accent-blue)' : 'var(--bg-surface)',
                            color: isDirty ? '#fff' : 'var(--text-tertiary)',
                            opacity: (!isDirty || saving) ? 0.5 : 1,
                            cursor: (!isDirty || saving) ? 'not-allowed' : 'pointer',
                        }}
                    >
                        {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                        {saving ? 'Saving...' : 'Save'}
                    </button>
                </div>
            </div>

            {/* Error / Success messages */}
            {error && (
                <div className="flex items-start gap-2 p-3 rounded-lg text-[11px]"
                    style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)' }}>
                    <AlertCircle size={14} className="shrink-0 mt-0.5" style={{ color: '#ef4444' }} />
                    <span style={{ color: '#fca5a5' }}>{error}</span>
                </div>
            )}
            {success && (
                <div className="flex items-start gap-2 p-3 rounded-lg text-[11px]"
                    style={{ background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.2)' }}>
                    <CheckCircle2 size={14} className="shrink-0 mt-0.5" style={{ color: '#10b981' }} />
                    <span style={{ color: '#6ee7b7' }}>{success}</span>
                </div>
            )}

            {/* Editor */}
            <div className="rounded-lg overflow-hidden border" style={{ borderColor: 'var(--border-main)' }}>
                <textarea
                    value={content}
                    onChange={e => setContent(e.target.value)}
                    className="w-full font-mono text-[12px] leading-relaxed p-4 custom-scrollbar"
                    style={{
                        background: 'var(--bg-app)',
                        color: 'var(--text-primary)',
                        border: 'none',
                        outline: 'none',
                        resize: 'vertical',
                        minHeight: 300,
                        tabSize: 2,
                    }}
                    spellCheck={false}
                />
            </div>

            {/* Security note */}
            <div className="flex items-start gap-2 px-4 py-3 rounded-lg text-[11px]"
                style={{ background: 'var(--bg-surface)', color: 'var(--text-tertiary)' }}>
                <ShieldCheck size={14} className="shrink-0 mt-0.5" style={{ color: '#34d399' }} />
                <div>
                    <strong>Security:</strong> Never put passwords or tokens directly in config.
                    Use <code style={{ color: 'var(--accent-blue)' }}>_env</code> suffix keys
                    (e.g. <code style={{ color: 'var(--accent-blue)' }}>client_secret_env: MY_SECRET</code>)
                    to reference environment variables.
                </div>
            </div>
        </div>
    );
}

export default function ConnectionsPanel({ isOpen, onClose }) {
    const [activeTab, setActiveTab] = useState('connections');
    const [snowflakeStatus, setSnowflakeStatus] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (isOpen) {
            setLoading(true);
            withTimeout(api.getConnectionsStatus(), 4000, null)
                .then(data => setSnowflakeStatus(data?.snowflake ?? null))
                .catch(() => { })
                .finally(() => setLoading(false));
        }
    }, [isOpen]);

    if (!isOpen) return null;

    const tabs = [
        { id: 'connections', label: 'Connections', icon: Cloud },
        { id: 'global-config', label: 'Global Config', icon: FileCode2 },
    ];

    return (
        <div className="fixed inset-0 z-[200] flex items-center justify-center"
            style={{ background: 'var(--bg-backdrop)', backdropFilter: 'blur(4px)' }}>
            <div className="relative w-full max-w-2xl max-h-[85vh] rounded-2xl overflow-hidden shadow-2xl"
                style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-main)' }}>
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b"
                    style={{ borderColor: 'var(--border-main)' }}>
                    <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-xl flex items-center justify-center"
                            style={{ background: 'var(--color-accent-faint, rgba(99,102,241,.1))' }}>
                            <Settings size={18} style={{ color: 'var(--color-accent)' }} />
                        </div>
                        <div>
                            <h2 className="text-base font-bold" style={{ color: 'var(--text-primary)' }}>Settings</h2>
                            <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
                                Connections · Configuration
                            </p>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 rounded-lg hover:bg-surface-hover"
                        style={{ color: 'var(--text-tertiary)' }}>
                        <X size={18} />
                    </button>
                </div>

                {/* Tabs */}
                <div className="flex border-b" style={{ borderColor: 'var(--border-main)' }}>
                    {tabs.map(tab => {
                        const Icon = tab.icon;
                        const isActive = activeTab === tab.id;
                        return (
                            <button
                                key={tab.id}
                                onClick={() => setActiveTab(tab.id)}
                                className="flex items-center gap-2 px-5 py-2.5 text-[12px] font-semibold transition-colors"
                                style={{
                                    color: isActive ? 'var(--text-primary)' : 'var(--text-tertiary)',
                                    borderBottom: isActive ? '2px solid var(--accent-blue)' : '2px solid transparent',
                                    background: 'transparent',
                                    cursor: 'pointer',
                                }}
                            >
                                <Icon size={14} />
                                {tab.label}
                            </button>
                        );
                    })}
                </div>

                {/* Body */}
                <div className="overflow-y-auto p-6 space-y-4" style={{ maxHeight: 'calc(85vh - 140px)' }}>
                    {activeTab === 'connections' && (
                        loading ? (
                            <div className="flex items-center justify-center py-12">
                                <Loader2 size={24} className="animate-spin" style={{ color: 'var(--color-accent)' }} />
                            </div>
                        ) : (
                            <>
                                <FabricLoginCard />
                                <SnowflakeCard status={snowflakeStatus} />

                                <div className="flex items-start gap-2 px-4 py-3 rounded-lg text-[11px]"
                                    style={{ background: 'var(--bg-surface)', color: 'var(--text-tertiary)' }}>
                                    <ShieldCheck size={14} className="shrink-0 mt-0.5" style={{ color: '#34d399' }} />
                                    <div>
                                        <strong>Security:</strong> Fabric login uses the secure Microsoft Device Code flow —
                                        your password is never entered in this app. Snowflake credentials are stored
                                        locally in DuckDB and injected at runtime.
                                    </div>
                                </div>
                            </>
                        )
                    )}
                    {activeTab === 'global-config' && (
                        <GlobalConfigEditor />
                    )}
                </div>
            </div>
        </div>
    );
}
