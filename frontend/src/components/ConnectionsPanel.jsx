        // Utility for case-insensitive status check
        const isConnected = (statusObj) =>
            typeof statusObj?.status === 'string' && statusObj.status.toLowerCase() === 'connected';
import { useState, useEffect, useRef, useCallback } from 'react';
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
    Edit2,
} from 'lucide-react';
import { api } from '../utils/api';
import { useLogs } from '../context/LogsContext';
import GlobalConfigEditor from './GlobalConfigEditor';

const FABRIC_CONNECTIONS_STORAGE_KEY = 'semabridge-fabric-connections';

function generateConnectionId() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    return `conn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function readFabricConnectionsFromStorage() {
    try {
        const raw = localStorage.getItem(FABRIC_CONNECTIONS_STORAGE_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

function writeFabricConnectionsToStorage(connections) {
    try {
        localStorage.setItem(FABRIC_CONNECTIONS_STORAGE_KEY, JSON.stringify(connections || []));
    } catch {}
}

function withTimeout(promise, ms, fallbackValue = null) {
    return Promise.race([
        promise,
        new Promise(resolve => setTimeout(() => resolve(fallbackValue), ms)),
    ]);
}

/* ───────────────────────────────────────────────
   Account Form: Fabric
   ─────────────────────────────────────────────── */

function FabricAccountForm({ initialTag, onSave, onCancel }) {
    const [authMode, setAuthMode] = useState('interactive'); // 'interactive' | 'service_principal'
    const [spTenantId, setSpTenantId] = useState('');
    const [spClientId, setSpClientId] = useState('');
    const [spClientSecret, setSpClientSecret] = useState('');
    const [authStatus, setAuthStatus] = useState(null);
    const [loginPhase, setLoginPhase] = useState('idle'); // idle | requesting | code_shown | polling | success | failed
    const [deviceCode, setDeviceCode] = useState(null);
    const [error, setError] = useState(null);
    const [copied, setCopied] = useState(false);
    const pollTimer = useRef(null);
    // Pre-fetched device code — ready before user clicks "Sign in"
    const prefetchedCode = useRef(null);
    const prefetchInFlight = useRef(false);
    // Per-session flow_id for multi-user isolation.
    const flowIdRef = useRef(null);
    const { addLog } = useLogs();

    const [workspaceSaving, setWorkspaceSaving] = useState(false);
    const [connectionId, setConnectionId] = useState('');

    // We are creating a NEW identity Vault entry. 
    // Do NOT load the global auth status on mount, otherwise it pulls the existing session.
    useEffect(() => {
        prefetchDeviceCode();
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

    const handleLogin = async () => {
        setError(null);

        // Use the pre-fetched code if it arrived in time — otherwise fetch now
        if (prefetchedCode.current) {
            const result = prefetchedCode.current;
            prefetchedCode.current = null;
            flowIdRef.current = result.flow_id;
            setDeviceCode(result);
            setLoginPhase('code_shown');
            addLog('info', 'Fabric Auth', `Device code: ${result.user_code}`);
            
            // Append prompt=select_account to force MS picker
            let uri = result.verification_uri;
            if (uri && !uri.includes('prompt=')) {
                uri += (uri.includes('?') ? '&' : '?') + 'prompt=select_account';
            }
            window.open(uri, '_blank', 'noopener,noreferrer');
            startPolling();
            // Pre-fetch the next one quietly for any re-login
            setTimeout(prefetchDeviceCode, 2000);
            return;
        }

        // Fallback: fetch fresh
        setLoginPhase('requesting');
        try {
            const result = await api.fabricLogin();
            flowIdRef.current = result.flow_id;
            setDeviceCode(result);
            setLoginPhase('code_shown');
            addLog('info', 'Fabric Auth', `Device code: ${result.user_code}`);
            
            // Append prompt=select_account to force MS picker
            let uri = result.verification_uri;
            if (uri && !uri.includes('prompt=')) {
                uri += (uri.includes('?') ? '&' : '?') + 'prompt=select_account';
            }
            window.open(uri, '_blank', 'noopener,noreferrer');
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
                const result = await api.fabricPoll(flowIdRef.current);
                if (result.status === 'success') {
                    const generatedId = generateConnectionId();
                    setConnectionId(generatedId);
                    setLoginPhase('success');
                    setAuthStatus({
                        auth_method: 'interactive',
                        username: result.username,
                        tenant_id: result.tenant_id,
                        access_token: result.access_token,
                        connection_id: generatedId,
                        logged_in: true,
                        token_valid: true,
                    });
                    addLog('info', 'Fabric Auth', `Signed in successfully as ${result.username}`);
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
            addLog('info', 'Fabric Auth', 'Logged out');
        } catch (err) {
            addLog('error', 'Fabric Auth', err.message);
        }
    };

    const copyCode = () => {
        if (deviceCode?.user_code) {
            navigator.clipboard.writeText(deviceCode.user_code);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        }
    };

    const handleSaveIdentity = async () => {
        try {
            setWorkspaceSaving(true);
            const effectiveConnectionId = connectionId || authStatus?.connection_id || generateConnectionId();

            const vaultAccounts = await api.createAccount({
                connection_id: effectiveConnectionId,
                connector_type: 'FABRIC',
                tag: initialTag,
                identity_email: authStatus?.username || 'N/A',
                encrypted_token: authStatus?.access_token || null,
            });

            const existing = readFabricConnectionsFromStorage();
            const newConnection = {
                id: effectiveConnectionId,
                tag: initialTag,
                identity_email: authStatus?.username || 'N/A',
                connector_type: 'FABRIC',
                status: 'Active',
            };
            const next = [...existing.filter((conn) => conn?.id !== newConnection.id), newConnection];
            writeFabricConnectionsToStorage(next);

            addLog('info', 'Connections', `Fabric account ${initialTag} stored in DB.`);
            if (onSave) {
                onSave({
                    vaultAccounts,
                    createdConnection: newConnection,
                });
            }
        } catch (err) {
            setError(err.message);
            addLog('error', 'Connections', `Failed to create Fabric account: ${err.message}`);
        } finally {
            setWorkspaceSaving(false);
        }
    };

    const handleSaveServicePrincipal = async () => {
        try {
            setWorkspaceSaving(true);
            setError(null);
            if (!spTenantId.trim() || !spClientId.trim() || !spClientSecret.trim()) {
                setError('Tenant ID, Client ID, and Client Secret are required.');
                return;
            }
            const effectiveConnectionId = connectionId || generateConnectionId();
            const vaultAccounts = await api.createAccount({
                connection_id: effectiveConnectionId,
                connector_type: 'FABRIC',
                tag: initialTag,
                identity_email: spClientId.trim(),
                credentials: {
                    auth_type: 'service_principal',
                    tenant_id: spTenantId.trim(),
                    client_id: spClientId.trim(),
                    client_secret: spClientSecret.trim(),
                },
            });
            const existing = readFabricConnectionsFromStorage();
            const newConnection = {
                id: effectiveConnectionId,
                tag: initialTag,
                identity_email: spClientId.trim(),
                connector_type: 'FABRIC',
                status: 'Active',
                auth_type: 'service_principal',
            };
            const next = [...existing.filter((conn) => conn?.id !== newConnection.id), newConnection];
            writeFabricConnectionsToStorage(next);
            addLog('info', 'Connections', `Fabric service principal account ${initialTag} stored in DB.`);
            if (onSave) {
                onSave({
                    vaultAccounts,
                    createdConnection: newConnection,
                });
            }
        } catch (err) {
            setError(err.message);
            addLog('error', 'Connections', `Failed to create Fabric service principal account: ${err.message}`);
        } finally {
            setWorkspaceSaving(false);
        }
    };

    const isLoggedIn = authStatus?.logged_in;

    return (
        <div className="rounded-xl border overflow-hidden p-4 mt-4"
            style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface)' }}>
            <div className="flex justify-between items-center mb-4">
                <h4 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Authenticate New Account</h4>
                <button onClick={onCancel} className="text-xs text-gray-400 hover:text-white"><X size={14}/></button>
            </div>
            
            <div className="space-y-4">
                {/* Auth Mode Tabs */}
                <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: 'var(--border-main)' }}>
                    <button
                        type="button"
                        onClick={() => { setAuthMode('interactive'); setError(null); }}
                        className="flex-1 py-2 text-xs font-semibold text-center transition-colors"
                        style={{
                            background: authMode === 'interactive' ? 'var(--color-accent)' : 'transparent',
                            color: authMode === 'interactive' ? '#fff' : 'var(--text-secondary)',
                        }}
                    >
                        Interactive (Device Code)
                    </button>
                    <button
                        type="button"
                        onClick={() => { setAuthMode('service_principal'); setError(null); }}
                        className="flex-1 py-2 text-xs font-semibold text-center transition-colors"
                        style={{
                            background: authMode === 'service_principal' ? 'var(--color-accent)' : 'transparent',
                            color: authMode === 'service_principal' ? '#fff' : 'var(--text-secondary)',
                        }}
                    >
                        Service Principal
                    </button>
                </div>

                {/* Tag/Alias Input locked by ConnectionManager or passed in */}
                <div>
                   <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>Account Tag</label>
                   <input type="text" readOnly value={initialTag} className="w-full px-3 py-2 rounded-lg text-xs border" style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-tertiary)' }} />
                </div>

                {/* ── Service Principal Mode ── */}
                {authMode === 'service_principal' && (
                    <div className="space-y-3">
                        <div>
                            <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>Tenant ID</label>
                            <input
                                type="text"
                                placeholder="e.g. 72f988bf-86f1-41af-91ab-2d7cd011db47"
                                value={spTenantId}
                                onChange={(e) => setSpTenantId(e.target.value)}
                                className="w-full px-3 py-2 rounded-lg text-xs border"
                                style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-primary)' }}
                            />
                        </div>
                        <div>
                            <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>Client ID (Application ID)</label>
                            <input
                                type="text"
                                placeholder="e.g. 00000000-0000-0000-0000-000000000000"
                                value={spClientId}
                                onChange={(e) => setSpClientId(e.target.value)}
                                className="w-full px-3 py-2 rounded-lg text-xs border"
                                style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-primary)' }}
                            />
                        </div>
                        <div>
                            <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>Client Secret</label>
                            <input
                                type="password"
                                placeholder="••••••••"
                                value={spClientSecret}
                                onChange={(e) => setSpClientSecret(e.target.value)}
                                className="w-full px-3 py-2 rounded-lg text-xs border"
                                style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-primary)' }}
                            />
                        </div>
                        {error && (
                            <div className="text-xs p-2 rounded bg-red-500/10 text-red-400 border border-red-500/20">{error}</div>
                        )}
                        <button
                            onClick={handleSaveServicePrincipal}
                            disabled={workspaceSaving || !spTenantId.trim() || !spClientId.trim() || !spClientSecret.trim()}
                            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-xs font-bold transition-all hover:brightness-110 disabled:opacity-50"
                            style={{ background: 'var(--color-accent)', color: '#fff' }}
                        >
                            {workspaceSaving ? <Loader2 size={14} className="animate-spin" /> : null}
                            Save Service Principal
                        </button>
                    </div>
                )}

                {/* ── Interactive Mode ── */}
                {authMode === 'interactive' && (<>

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

                        <p className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                            Sign in and save the Fabric identity only. Workspace selection is handled in the project wizard.
                        </p>
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
                            <a href={(() => {
                                // Always force account-selection prompt when reopening
                                let uri = deviceCode.verification_uri || '';
                                if (uri && !uri.includes('prompt=')) {
                                    uri += (uri.includes('?') ? '&' : '?') + 'prompt=select_account';
                                }
                                return uri;
                            })()}
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
                </>)}
            </div>

            {isLoggedIn && (
                <div className="pt-4 flex justify-end gap-2 border-t" style={{ borderColor: 'var(--border-main)' }}>
                    <button
                        onClick={handleSaveIdentity}
                        disabled={workspaceSaving}
                        className="px-4 py-2 bg-indigo-500 hover:bg-indigo-600 text-white text-xs font-bold rounded-lg transition-colors disabled:opacity-60"
                    >
                        Save Identity
                    </button>
                    <button onClick={handleLogout} className="px-4 py-2 border border-red-500/30 text-red-500 hover:bg-red-500/10 text-xs font-bold rounded-lg transition-colors">
                        Sign Out
                    </button>
                </div>
            )}
        </div>
    );
}

/* ───────────────────────────────────────────────
   Account Form: Snowflake
   ─────────────────────────────────────────────── */

const SNOWFLAKE_FIELDS = [
    { key: 'account', label: 'Account URL', placeholder: 'abc123.us-east-1', secret: false, required: true },
    { key: 'user', label: 'Username', placeholder: 'your_username', secret: false, required: true },
    { key: 'password', label: 'Password', placeholder: 'Enter your Snowflake password', secret: true, passwordOnly: true },
    { key: 'private_key', label: 'Private Key (PEM)', placeholder: 'Paste your private key content', secret: true, keypairOnly: true, multiline: true },
    { key: 'private_key_passphrase', label: 'Private Key Passphrase', placeholder: 'Optional — required if key is encrypted', secret: true, keypairOnly: true, hint: 'Leave blank if your private key is unencrypted. Required if it was generated with a passphrase (recommended and most common).' },
    { key: 'oauth_client_id', label: 'OAuth Client ID', placeholder: 'Enter your OAuth client ID', secret: false, oauthOnly: true },
    { key: 'oauth_client_secret', label: 'OAuth Client Secret', placeholder: 'Enter your OAuth client secret', secret: true, oauthOnly: true },
    { key: 'warehouse', label: 'Warehouse', placeholder: 'COMPUTE_WH', secret: false, required: true },
    { key: 'database', label: 'Database', placeholder: 'SEMABRIDGE_DB', secret: false, required: true },
];

function SnowflakeAccountForm({ initialTag, status, onSave, onCancel }) {
    const [formData, setFormData] = useState({});
    const [showSecrets, setShowSecrets] = useState({});
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState(null);
    const [authMode, setAuthMode] = useState('password'); // 'password' | 'oauth' | 'keypair'
    const [oauthLoading, setOauthLoading] = useState(false);
    const [oauthResult, setOauthResult] = useState(null);
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
            } else if (storedAuthType === 'oauth') {
                setAuthMode('oauth');
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
            filtered.account_tag = initialTag;

            // Explicitly set auth_type and clean stale fields per mode
            if (authMode === 'password') {
                filtered.auth_type = 'password';
                delete filtered.private_key;
                delete filtered.private_key_passphrase;
                delete filtered.oauth_client_id;
                delete filtered.oauth_client_secret;
                delete filtered.oauth_token_endpoint;
                delete filtered.oauth_scope;
            } else if (authMode === 'oauth') {
                filtered.auth_type = 'oauth';
                delete filtered.password;
                delete filtered.private_key;
                delete filtered.private_key_passphrase;
            } else if (authMode === 'keypair') {
                filtered.auth_type = 'keypair';
                delete filtered.password;
                delete filtered.oauth_client_id;
                delete filtered.oauth_client_secret;
                delete filtered.oauth_token_endpoint;
                delete filtered.oauth_scope;
            }

            await api.saveConnection('snowflake', filtered);
            
            const vaultAccounts = await api.createAccount({
                connector_type: 'SNOWFLAKE',
                tag: initialTag,
                identity_email: filtered.user || filtered.account || 'N/A',
                credentials: filtered,
            });

            addLog('info', 'Connections', `Snowflake credentials saved successfully (Auth: ${authMode})`);
            if (onSave) onSave(vaultAccounts);
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

    const handleOAuthTest = async () => {
        const account = formData.account?.trim();
        const user = formData.user?.trim();
        const clientId = formData.oauth_client_id?.trim();
        const clientSecret = formData.oauth_client_secret?.trim();
        if (!account || !user) {
            setTestResult({ status: 'failed', message: 'Please enter Account and Username before testing OAuth.' });
            return;
        }
        if (!clientId || !clientSecret) {
            setTestResult({ status: 'failed', message: 'Please fill in Client ID and Client Secret.' });
            return;
        }

        setOauthLoading(true);
        setOauthResult(null);
        setTestResult(null);
        try {
            const result = await api.snowflakeOAuthTest({
                account, user,
                oauth_client_id: clientId,
                oauth_client_secret: clientSecret,
                warehouse: formData.warehouse?.trim() || '',
                database: formData.database?.trim() || '',
            });
            setOauthResult(result);
            if (result.status === 'success') {
                addLog('info', 'Snowflake OAuth', `OAuth S2S test successful: ${result.message}`);
            } else {
                addLog('warning', 'Snowflake OAuth', result.message);
            }
        } catch (err) {
            setOauthResult({ status: 'failed', message: err.message });
            addLog('error', 'Snowflake OAuth', err.message);
        } finally { setOauthLoading(false); }
    };

    const handleDelete = async () => {
        if (!confirm('Remove all stored Snowflake credentials?')) return;
        try {
            await api.deleteConnection('snowflake');
            setFormData({});
            setTestResult(null);
            setOauthResult(null);
        } catch (err) { addLog('error', 'Connections', err.message); }
    };

    const isConfigured = status?.configured;

    // Filter fields based on auth mode
    const visibleFields = SNOWFLAKE_FIELDS.filter(f => {
        if (authMode === 'oauth' && (f.passwordOnly || f.keypairOnly)) return false;
        if (authMode === 'password' && (f.keypairOnly || f.oauthOnly)) return false;
        if (authMode === 'keypair' && (f.passwordOnly || f.oauthOnly)) return false;
        return true;
    });

    return (
        <div className="rounded-xl border overflow-hidden p-4 mt-4"
            style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface)' }}>
            <div className="flex justify-between items-center mb-4">
                <h4 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Authenticate New Account</h4>
                <button onClick={onCancel} className="text-xs text-gray-400 hover:text-white"><X size={14}/></button>
            </div>
            
            {/* Tag/Alias display */}
            <div className="mb-4">
               <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>Account Tag</label>
               <input type="text" readOnly value={initialTag} className="w-full px-3 py-2 rounded-lg text-xs border" style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-tertiary)' }} />
            </div>

            {/* Auth Mode Toggle */}
            <div className="flex mx-4 mt-3 rounded-lg overflow-hidden border"
                style={{ borderColor: 'var(--border-main)' }}>
                <button
                    onClick={() => { setAuthMode('password'); setTestResult(null); setOauthResult(null); }}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-bold transition-all"
                    style={{
                        background: authMode === 'password' ? 'var(--color-accent)' : 'transparent',
                        color: authMode === 'password' ? '#fff' : 'var(--text-tertiary)',
                    }}>
                    <Database size={11} /> Password
                </button>
                <button
                    onClick={() => { setAuthMode('oauth'); setTestResult(null); setOauthResult(null); }}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-bold transition-all"
                    style={{
                        background: authMode === 'oauth' ? 'linear-gradient(135deg, #29b5e8, #0ea5e9)' : 'transparent',
                        color: authMode === 'oauth' ? '#fff' : 'var(--text-tertiary)',
                    }}>
                    <LogIn size={11} /> OAuth S2S
                </button>
                <button
                    onClick={() => { setAuthMode('keypair'); setTestResult(null); setOauthResult(null); }}
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
                                    className={`w-full px-3 py-2 rounded-lg text-xs border outline-none focus:ring-1 transition-colors resize-none font-mono ${field.secret ? 'pr-8' : ''}`}
                                    style={{ 
                                        background: 'var(--bg-input, var(--bg-primary))', 
                                        borderColor: 'var(--border-main)', 
                                        color: 'var(--text-primary)',
                                        WebkitTextSecurity: (field.secret && !showSecrets[field.key]) ? 'disc' : 'none'
                                    }}
                                />
                            ) : (
                                <input
                                    type={field.secret && !showSecrets[field.key] ? 'password' : 'text'}
                                    value={formData[field.key] || ''}
                                    onChange={e => { setFormData(p => ({ ...p, [field.key]: e.target.value })); setTestResult(null); }}
                                    placeholder={field.placeholder}
                                    className={`w-full px-3 py-2 rounded-lg text-xs border outline-none focus:ring-1 transition-colors ${field.secret ? 'pr-8' : ''}`}
                                    style={{ background: 'var(--bg-input, var(--bg-primary))', borderColor: 'var(--border-main)', color: 'var(--text-primary)' }}
                                />
                            )}
                            {field.secret && (
                                <button onClick={() => setShowSecrets(p => ({ ...p, [field.key]: !p[field.key] }))}
                                    className={`absolute right-2 p-1 rounded opacity-40 hover:opacity-100 ${field.multiline ? 'top-2' : 'top-1/2 -translate-y-1/2'}`} type="button">
                                    {showSecrets[field.key]
                                        ? <EyeOff size={12} style={{ color: 'var(--text-secondary)' }} />
                                        : <Eye size={12} style={{ color: 'var(--text-secondary)' }} />}
                                </button>
                            )}
                        </div>
                        {field.hint && (
                            <p className="mt-1 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>{field.hint}</p>
                        )}
                    </div>
                ))}


                {/* OAuth Test Button */}
                {authMode === 'oauth' && (
                    <button onClick={handleOAuthTest} disabled={oauthLoading}
                        className="w-full flex items-center justify-center gap-2 py-3 rounded-lg text-sm font-bold transition-all hover:brightness-110"
                        style={{
                            background: 'linear-gradient(135deg, #29b5e8, #0ea5e9)',
                            color: '#fff',
                            opacity: oauthLoading ? 0.7 : 1,
                        }}>
                        {oauthLoading ? (
                            <><Loader2 size={16} className="animate-spin" /> Testing OAuth...</>
                        ) : (
                            <><LogIn size={16} /> Test OAuth Connection</>
                        )}
                    </button>
                )}

                {/* OAuth Result */}
                {oauthResult && (
                    <div className="px-3 py-2 rounded-lg text-xs flex items-center gap-2"
                        style={{
                            background: oauthResult.status === 'success' ? 'rgba(52,211,153,0.1)' : 'rgba(239,68,68,0.1)',
                            color: oauthResult.status === 'success' ? '#34d399' : '#ef4444',
                        }}>
                        {oauthResult.status === 'success' ? <CheckCircle2 size={12} /> : <AlertCircle size={12} />}
                        {oauthResult.message}
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
   Connection Manager (Identity Vault)
   ─────────────────────────────────────────────── */
function ConnectionManager({ type, title, subtitle, icon: Icon, color, FormComponent, status }) {
    const [accounts, setAccounts] = useState([]);
    const [isAdding, setIsAdding] = useState(false);
    const [newTag, setNewTag] = useState('');
    const [reconnectingAccount, setReconnectingAccount] = useState(null);
    const [editingAccId, setEditingAccId] = useState(null);
    const [editingTag, setEditingTag] = useState('');
    const [isSavingEdit, setIsSavingEdit] = useState(false);
    const [testingAccId, setTestingAccId] = useState(null);
    const [testResults, setTestResults] = useState({});
    const { addLog } = useLogs();

    useEffect(() => {
        if (String(type || '').toUpperCase() !== 'FABRIC') return;
        const cached = readFabricConnectionsFromStorage();
        if (!cached.length) return;
        const normalized = cached.map((v) => ({
            id: v.id,
            connection_id: v.connection_id || v.id,
            tag: v.tag,
            identity: v.identity_email || 'N/A',
            status: v.status || 'Active',
            auth_type: v.auth_type,
            token_expires_at: v.token_expires_at,
            token_status: v.token_status || 'valid',
        }));
        setAccounts(normalized);
    }, [type]);

    const updateVaultAccountsInState = (vaultAccounts) => {
        if (vaultAccounts && Array.isArray(vaultAccounts)) {
            const vaultList = vaultAccounts.map(v => ({
                id: v.id,
                connection_id: v.connection_id,
                tag: v.tag,
                identity: v.identity_email || 'N/A',
                status: v.status || 'Active',
                auth_type: v.auth_type,
                token_expires_at: v.token_expires_at,
                token_status: v.token_status || 'valid',
            }));
            setAccounts(vaultList);
            if (String(type || '').toUpperCase() === 'FABRIC') {
                const toCache = vaultAccounts.map((v) => ({
                    id: v.id,
                    connection_id: v.connection_id,
                    tag: v.tag,
                    identity_email: v.identity_email || 'N/A',
                    connector_type: v.connector_type || 'FABRIC',
                    status: v.status || 'Active',
                    auth_type: v.auth_type,
                    token_expires_at: v.token_expires_at,
                    token_status: v.token_status || 'valid',
                }));
                writeFabricConnectionsToStorage(toCache);
            }
        } else {
            setAccounts([]);
        }
    };

    const fetchAccounts = () => {
        api.getAccounts(type.toUpperCase()).then(vaultAccounts => {
            updateVaultAccountsInState(vaultAccounts);
        }).catch(err => {
            console.error("Failed to fetch vault accounts", err);
            updateVaultAccountsInState(null);
        });
    };

    useEffect(() => {
        fetchAccounts();
    }, [type, status]);

    const handleDelete = async (accId) => {
        if (!confirm('Remove this credential?')) return;
        try {
            const upToDateVaultAccounts = await api.deleteAccount(accId);
            updateVaultAccountsInState(upToDateVaultAccounts);
            addLog('info', 'Connections', 'Account deleted');
        } catch (err) {
            console.error('Failed to delete account:', err);
            addLog('error', 'Connections', `Failed to delete account${err?.message ? `: ${err.message}` : ''}`);
        }
    };

    const handleTest = async (accId) => {
        setTestingAccId(accId);
        setTestResults(prev => ({ ...prev, [accId]: null }));
        try {
            const res = await api.testAccount(accId);
            const isOk = res && res.connected !== false;
            setTestResults(prev => ({
                ...prev,
                [accId]: {
                    success: isOk,
                    message: res?.message || (isOk ? 'Connection verified' : 'Connection failed')
                }
            }));
            addLog(isOk ? 'info' : 'warn', 'Connections', `Test connection ${accId}: ${res?.message || (isOk ? 'OK' : 'Failed')}`);
        } catch (err) {
            setTestResults(prev => ({
                ...prev,
                [accId]: {
                    success: false,
                    message: err?.message || 'Connection test failed'
                }
            }));
            addLog('error', 'Connections', `Test connection ${accId} error: ${err?.message}`);
        } finally {
            setTestingAccId(null);
        }
    };

    const handleStartEdit = (acc) => {
        setEditingAccId(acc.id);
        setEditingTag(acc.tag || '');
    };

    const handleCancelEdit = () => {
        setEditingAccId(null);
        setEditingTag('');
    };

    const handleSaveEdit = async (accId) => {
        const trimmed = editingTag.trim();
        if (!trimmed) return;
        setIsSavingEdit(true);
        try {
            await api.updateAccount(accId, { tag: trimmed });
            setAccounts(prev => prev.map(a => a.id === accId ? { ...a, tag: trimmed } : a));
            setEditingAccId(null);
            setEditingTag('');
            fetchAccounts();
            addLog('info', 'Connections', `Account tag updated to "${trimmed}"`);
        } catch (err) {
            addLog('error', 'Connections', `Failed to update account tag: ${err?.message}`);
        } finally {
            setIsSavingEdit(false);
        }
    };

    return (
        <div className="mb-8">
            <div className="mb-4">
                <div className="flex items-center gap-2 mb-1">
                    <Icon size={18} style={{ color: color }} />
                    <h2 className="text-sm font-bold text-white">{title}</h2>
                </div>
                <p className="text-xs text-gray-400">{subtitle}</p>
            </div>

            {/* List Header */}
            {accounts.length > 0 && (
                <div className="grid grid-cols-12 gap-3 px-4 py-2 border-b border-gray-700/50 text-[11px] font-bold text-gray-500 uppercase tracking-wider mb-2">
                    <div className="col-span-4">Identity</div>
                    <div className="col-span-3">Tag</div>
                    <div className="col-span-3">Status</div>
                    <div className="col-span-2 text-right">Actions</div>
                </div>
            )}

            {/* Account Rows */}
            <div className="space-y-2 mb-4">
                {accounts.length === 0 ? (
                    <div className="p-6 text-center text-xs text-gray-500 border border-gray-800 rounded-lg">
                        No identities configured yet. Ensure to add an account to sync.
                    </div>
                ) : (
                    accounts.map(acc => (
                        <div key={acc.id} className="space-y-1.5 p-3 bg-[#131620] border border-gray-100/10 rounded-lg hover:border-gray-100/20 transition-colors">
                            <div className="grid grid-cols-12 gap-3 items-center">
                                {/* Identity */}
                                <div className="col-span-4 text-xs text-gray-300 truncate flex items-center gap-1.5">
                                    <span className="truncate" title={acc.identity}>{acc.identity}</span>
                                    {acc.auth_type === 'service_principal' && (
                                        <span className="px-1.5 py-0.5 rounded text-[9px] bg-purple-500/10 text-purple-400 border border-purple-500/20 font-mono shrink-0">
                                            SPN
                                        </span>
                                    )}
                                </div>

                                {/* Tag */}
                                <div className="col-span-3 text-xs">
                                    {editingAccId === acc.id ? (
                                        <div className="flex items-center gap-1">
                                            <input
                                                value={editingTag}
                                                onChange={(e) => setEditingTag(e.target.value)}
                                                className="px-2 py-0.5 rounded bg-black/50 border border-indigo-500 text-xs text-white outline-none w-28"
                                                autoFocus
                                            />
                                            <button
                                                onClick={() => handleSaveEdit(acc.id)}
                                                disabled={isSavingEdit}
                                                className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500 text-white font-bold disabled:opacity-50"
                                            >
                                                {isSavingEdit ? '...' : 'Save'}
                                            </button>
                                            <button
                                                onClick={handleCancelEdit}
                                                className="text-[10px] text-gray-400 hover:text-white"
                                            >
                                                ✕
                                            </button>
                                        </div>
                                    ) : (
                                        <div className="flex items-center gap-1.5 group">
                                            <span className="font-bold text-[#e2e8f0] truncate" title={acc.tag}>{acc.tag}</span>
                                            <button
                                                onClick={() => handleStartEdit(acc)}
                                                className="text-gray-500 hover:text-indigo-400 p-0.5 opacity-60 group-hover:opacity-100 transition-opacity"
                                                title="Edit tag"
                                            >
                                                <Edit2 size={11} />
                                            </button>
                                        </div>
                                    )}
                                </div>

                                {/* Status / Expiry */}
                                <div className="col-span-3 text-xs">
                                    {acc.token_status === 'expired' || acc.token_status === 'reauth_required' ? (
                                        <div className="flex items-center gap-1.5 flex-wrap">
                                            <span className="px-2 py-0.5 rounded text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/20 font-semibold whitespace-nowrap">
                                                Sign in again
                                            </span>
                                            <button
                                                onClick={() => setReconnectingAccount(acc)}
                                                className="flex items-center gap-1 text-[10px] font-semibold text-indigo-400 hover:text-indigo-300 transition-colors"
                                                title="Reconnect this account"
                                            >
                                                <RotateCcw size={10} /> Reconnect
                                            </button>
                                        </div>
                                    ) : (
                                        <div className="flex flex-col">
                                            <span className="text-[11px] text-emerald-400 flex items-center gap-1 font-semibold">
                                                <CheckCircle2 size={11} /> Valid
                                            </span>
                                            {acc.token_expires_at ? (
                                                <span
                                                    className="text-[9px] text-gray-500 truncate"
                                                    title={new Date(acc.token_expires_at * 1000).toLocaleString()}
                                                >
                                                    Exp: {new Date(acc.token_expires_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                                </span>
                                            ) : null}
                                        </div>
                                    )}
                                </div>

                                {/* Actions */}
                                <div className="col-span-2 flex items-center justify-end gap-2 text-[11px] font-bold">
                                    <button
                                        onClick={() => handleTest(acc.id)}
                                        disabled={testingAccId === acc.id}
                                        className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-semibold border border-gray-700 hover:border-gray-500 text-gray-300 hover:text-white transition-colors disabled:opacity-50"
                                        title="Test connection"
                                    >
                                        {testingAccId === acc.id ? <Loader2 size={10} className="animate-spin" /> : <Zap size={10} />}
                                        Test
                                    </button>
                                    <button
                                        className="text-gray-500 hover:text-red-400 transition-colors text-[10px]"
                                        onClick={() => handleDelete(acc.id)}
                                        title="Delete credential"
                                    >
                                        Logout
                                    </button>
                                </div>
                            </div>

                            {/* Inline Test Result */}
                            {testResults[acc.id] && (
                                <div
                                    className={`text-[10px] px-2 py-1 rounded flex items-center gap-1.5 ${
                                        testResults[acc.id].success
                                            ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                                            : 'bg-red-500/10 text-red-400 border border-red-500/20'
                                    }`}
                                >
                                    {testResults[acc.id].success ? <CheckCircle2 size={10} /> : <AlertCircle size={10} />}
                                    <span>{testResults[acc.id].message}</span>
                                </div>
                            )}
                        </div>
                    ))
                )}
            </div>

            {/* Reconnect Account Modal / Form */}
            {reconnectingAccount && (
                <div className="p-4 bg-[#11131a] border border-amber-500/50 rounded-xl space-y-3 mb-4">
                    <div className="flex justify-between items-center">
                        <h4 className="text-xs font-bold text-amber-400">
                            Reconnect Account: {reconnectingAccount.tag} ({reconnectingAccount.identity})
                        </h4>
                        <button
                            onClick={() => setReconnectingAccount(null)}
                            className="text-xs text-gray-400 hover:text-white"
                        >
                            <X size={14} />
                        </button>
                    </div>
                    <FormComponent
                        key={`reconnect-${reconnectingAccount.id}`}
                        initialTag={reconnectingAccount.tag}
                        connectionId={reconnectingAccount.connection_id || reconnectingAccount.id}
                        status={null}
                        onSave={(result) => {
                            setReconnectingAccount(null);
                            const vaultAccounts = result?.vaultAccounts || result;
                            if (vaultAccounts) {
                                updateVaultAccountsInState(vaultAccounts);
                            } else {
                                fetchAccounts();
                            }
                        }}
                        onCancel={() => setReconnectingAccount(null)}
                    />
                </div>
            )}

            {/* Add Account Flow */}
            {!isAdding ? (
                <button 
                    onClick={() => {
                        // Reset all stale form state before opening the add form
                        setNewTag('');
                        setIsAdding(true);
                    }}
                    className="flex items-center gap-2 px-4 py-2 text-xs font-bold text-indigo-400 bg-indigo-500/10 hover:bg-indigo-500/20 rounded-lg border border-indigo-500/30 transition-colors"
                >
                    + Add New {title.split(' ')[1] || title} Account
                </button>
            ) : (
                <div className="p-4 bg-[#11131a] border border-indigo-500/50 rounded-xl space-y-3">
                    <label className="block text-[11px] font-bold text-gray-400 uppercase tracking-wider">Name your connection (Tag)</label>
                    <div className="flex items-center gap-2">
                        <input 
                            autoFocus
                            type="text" 
                            placeholder="e.g. Production-Azure" 
                            value={newTag}
                            onChange={(e) => setNewTag(e.target.value)}
                            className="flex-1 bg-black/40 border border-gray-700 px-3 py-2 rounded-lg text-xs outline-none focus:border-indigo-500 text-white"
                        />
                        <button 
                            onClick={() => { setIsAdding(false); setNewTag(''); }} 
                            className="px-3 py-2 text-xs font-bold text-gray-400 hover:text-white"
                        >
                            Cancel
                        </button>
                    </div>
                    {newTag.trim().length > 0 && (
                        <FormComponent 
                            key={newTag}
                            initialTag={newTag} 
                            status={null} // Never prefill existing global status for a NEW account
                            onSave={(result) => {
                                setIsAdding(false); 
                                setNewTag('');

                                const createdConnection = result?.createdConnection || null;
                                if (createdConnection) {
                                    setAccounts((prev) => {
                                        const next = [...prev.filter((item) => item.id !== createdConnection.id), {
                                            id: createdConnection.id,
                                            tag: createdConnection.tag,
                                            identity: createdConnection.identity_email || 'N/A',
                                            status: createdConnection.status || 'Active',
                                        }];
                                        if (String(type || '').toUpperCase() === 'FABRIC') {
                                            const nextStorage = next.map((item) => ({
                                                id: item.id,
                                                tag: item.tag,
                                                identity_email: item.identity,
                                                connector_type: 'FABRIC',
                                                status: item.status,
                                            }));
                                            writeFabricConnectionsToStorage(nextStorage);
                                        }
                                        return next;
                                    });
                                }

                                const vaultAccounts = result?.vaultAccounts || result;
                                if (vaultAccounts) {
                                    updateVaultAccountsInState(vaultAccounts);
                                } else {
                                    fetchAccounts();
                                }
                            }} 
                            onCancel={() => { setIsAdding(false); setNewTag(''); }} 
                        />
                    )}
                </div>
            )}
        </div>
    );
}

/* ───────────────────────────────────────────────
   Global Config Editor (~/.semabridge/config.yaml)
   ─────────────────────────────────────────────── */


/* ───────────────────────────────────────────────
   Account Form: Databricks (MSAL Device Code — like Fabric)
   ─────────────────────────────────────────────── */

function DatabricksAccountForm({ initialTag, status, onSave, onCancel }) {
    const [authType, setAuthType] = useState('pat'); // 'pat' | 'service_principal' | 'interactive'
    const [authStatus, setAuthStatus] = useState(null);
    const [loginPhase, setLoginPhase] = useState('idle'); // idle | requesting | polling | success | failed
    const [error, setError] = useState(null);
    const pollTimer = useRef(null);
    const flowIdRef = useRef(null);
    const { addLog } = useLogs();

    // Connection config
    const [hostInput, setHostInput] = useState('');
    const [warehouseId, setWarehouseId] = useState('');
    const [catalog, setCatalog] = useState('');
    const [schemaName, setSchemaName] = useState('');
    const [configSaving, setConfigSaving] = useState(false);

    // Structured test connection & warehouse start state
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState(null);
    const [startingWarehouse, setStartingWarehouse] = useState(false);
    const [warehouseStartMsg, setWarehouseStartMsg] = useState('');

    // Dropdown metadata populated by Test Connection
    const [availableCatalogs, setAvailableCatalogs] = useState([]);
    const [schemasByCatalog, setSchemasByCatalog] = useState({});
    const [schemasLoading, setSchemasLoading] = useState(false);

    // Permissions check state
    const [checkingPerms, setCheckingPerms] = useState(false);
    const [permResult, setPermResult] = useState(null);
    const [permError, setPermError] = useState(null);
    const [copiedGrants, setCopiedGrants] = useState(false);

    // Auth-specific fields
    const [clientId, setClientId] = useState('');
    const [clientSecret, setClientSecret] = useState('');
    const [patToken, setPatToken] = useState('');

    const isMaskedValue = (v) => {
        const s = String(v || '').trim();
        return !s || s === '••••••••' || s === '********';
    };

    useEffect(() => {
        if (status?.credentials) {
            setHostInput(status.credentials.host || '');
            setWarehouseId(status.credentials.warehouse_id || '');
            setCatalog(status.credentials.catalog || '');
            setSchemaName(status.credentials.schema_name || '');

            if (status.credentials.auth_type) {
                setAuthType(status.credentials.auth_type);
            }
            if (status.credentials.client_id) {
                setClientId(status.credentials.client_id);
            }
            if (status.credentials.client_secret) {
                setClientSecret(status.credentials.client_secret);
            }
            if (status.credentials.token) {
                setPatToken(status.credentials.token);
            }

            if (status.configured && (status.credentials.auth_type === 'service_principal' || status.credentials.auth_type === 'pat')) {
                setAuthStatus({
                    auth_method: status.credentials.auth_type,
                    username: status.credentials.auth_type === 'pat' ? 'Personal Access Token' : 'Service Principal',
                    logged_in: true,
                });
            }
        }
        return () => { if (pollTimer.current) clearTimeout(pollTimer.current); };
    }, [status]);

    const buildDraftPayload = (overrides = {}) => {
        const payload = {
            host: hostInput.trim(),
            warehouse_id: warehouseId.trim(),
            catalog: (overrides.catalog !== undefined ? overrides.catalog : catalog).trim(),
            schema_name: (overrides.schema_name !== undefined ? overrides.schema_name : schemaName).trim(),
            auth_type: authType,
        };
        if (status?.account_id) {
            payload.connection_id = status.account_id;
        }
        if (authType === 'interactive' || authType === 'service_principal') {
            if (clientId.trim()) payload.client_id = clientId.trim();
        }
        if (authType === 'service_principal' && !isMaskedValue(clientSecret)) {
            payload.client_secret = clientSecret.trim();
        }
        if (authType === 'pat' && !isMaskedValue(patToken)) {
            payload.token = patToken.trim();
        }
        return payload;
    };

    const handleLogin = async () => {
        setError(null);
        if (!hostInput || !clientId) {
            setError("Server Hostname and Client ID are required to log in.");
            setLoginPhase('failed');
            return;
        }

        setLoginPhase('requesting');
        try {
            const redirectUri = window.location.origin + "/api/connections/databricks/callback";
            const result = await api.databricksLogin(hostInput, clientId, redirectUri);
            flowIdRef.current = result.flow_id;

            setLoginPhase('polling');
            addLog('info', 'Databricks Auth', 'Opening Databricks login page...');

            window.open(result.verification_uri, '_blank', 'noopener,noreferrer');
            startPolling();
        } catch (err) {
            setError(err.message);
            setLoginPhase('failed');
            addLog('error', 'Databricks Auth', err.message);
        }
    };

    const startPolling = () => {
        setLoginPhase('polling');
        pollTimer.current = setTimeout(async () => {
            try {
                const connectionConfig = {
                    host: hostInput,
                    warehouse_id: warehouseId,
                    catalog,
                    schema_name: schemaName,
                };
                const result = await api.databricksPoll(flowIdRef.current, connectionConfig);
                if (result.status === 'success') {
                    setLoginPhase('success');
                    setAuthStatus({
                        auth_method: 'interactive',
                        username: result.username,
                        tenant_id: result.tenant_id,
                        logged_in: true,
                        token_valid: true,
                    });
                    addLog('info', 'Databricks Auth', `Signed in successfully as ${result.username}`);
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
            await api.databricksLogout();
            setAuthStatus(null);
            setLoginPhase('idle');
            setTestResult(null);
            setPermResult(null);
            addLog('info', 'Databricks Auth', 'Logged out');
        } catch (err) {
            addLog('error', 'Databricks Auth', err.message);
        }
    };

    const handleTest = async () => {
        setTesting(true);
        setTestResult(null);
        setWarehouseStartMsg('');
        setError(null);
        try {
            const result = await api.testDatabricksConnection(buildDraftPayload());
            setTestResult(result);

            const nextCatalogs = Array.isArray(result?.catalogs) ? result.catalogs : [];
            setAvailableCatalogs(nextCatalogs);
            const nextSchemasByCat = result?.schemas_by_catalog && typeof result.schemas_by_catalog === 'object'
                ? result.schemas_by_catalog
                : {};
            setSchemasByCatalog(nextSchemasByCat);

            let chosenCatalog = catalog;
            if ((!chosenCatalog || !nextCatalogs.includes(chosenCatalog)) && nextCatalogs.length > 0) {
                chosenCatalog = nextCatalogs[0];
                setCatalog(chosenCatalog);
            }
            const catSchemas = nextSchemasByCat[chosenCatalog] || result?.schemas || [];
            if ((!schemaName || !catSchemas.includes(schemaName)) && catSchemas.length > 0) {
                setSchemaName(catSchemas[0]);
            }

            addLog(result.status === 'failed' ? 'warning' : 'info', 'Connections', result.message || 'Databricks test complete');
        } catch (err) {
            setTestResult({ status: 'failed', message: err.message || 'Test failed', steps: [] });
        } finally {
            setTesting(false);
        }
    };

    const handleStartWarehouse = async () => {
        setStartingWarehouse(true);
        setWarehouseStartMsg('');
        try {
            const res = await api.startDatabricksWarehouse(buildDraftPayload());
            setWarehouseStartMsg(res?.message || 'Start request sent. Re-test in a moment.');
            setTestResult((prev) => prev ? ({
                ...prev,
                warehouse: { ...(prev.warehouse || {}), state: 'STARTING', can_start: false },
            }) : prev);
            addLog('info', 'Connections', res?.message || 'Databricks SQL warehouse starting');
        } catch (err) {
            setWarehouseStartMsg(err?.message || 'Failed to start SQL warehouse');
        } finally {
            setStartingWarehouse(false);
        }
    };

    const handleCatalogChange = async (nextCatalog) => {
        setCatalog(nextCatalog);
        setPermResult(null);
        if (!nextCatalog) {
            setSchemaName('');
            return;
        }
        if (Array.isArray(schemasByCatalog[nextCatalog])) {
            const list = schemasByCatalog[nextCatalog];
            setSchemaName(list.includes(schemaName) ? schemaName : (list[0] || ''));
            return;
        }
        setSchemasLoading(true);
        try {
            const res = await api.listDatabricksSchemas(buildDraftPayload({ catalog: nextCatalog }));
            const list = Array.isArray(res?.schemas) ? res.schemas : [];
            setSchemasByCatalog((prev) => ({ ...prev, [nextCatalog]: list }));
            setSchemaName(list[0] || '');
        } catch (err) {
            addLog('warning', 'Connections', `Could not load schemas for ${nextCatalog}: ${err.message}`);
            setSchemaName('');
        } finally {
            setSchemasLoading(false);
        }
    };

    const handleCheckPermissions = async () => {
        setCheckingPerms(true);
        setPermResult(null);
        setPermError(null);
        setCopiedGrants(false);
        try {
            const result = await api.checkDatabricksPermissions(buildDraftPayload());
            setPermResult(result);
        } catch (err) {
            setPermError(err?.message || 'Permission check failed');
        } finally {
            setCheckingPerms(false);
        }
    };

    const handleCopyGrants = () => {
        if (!permResult?.recommendation_text) return;
        navigator.clipboard?.writeText(permResult.recommendation_text);
        setCopiedGrants(true);
        setTimeout(() => setCopiedGrants(false), 2000);
    };

    const handleSaveConfig = async () => {
        setConfigSaving(true);
        setError(null);
        try {
            const configData = {
                host: hostInput.trim(),
                warehouse_id: warehouseId.trim(),
                catalog: catalog.trim(),
                schema_name: schemaName.trim(),
                auth_type: authType,
                account_tag: initialTag,
            };

            if (authType === 'interactive' || authType === 'service_principal') {
                configData.client_id = clientId.trim();
            }
            if (authType === 'service_principal') {
                if (!isMaskedValue(clientSecret)) {
                    configData.client_secret = clientSecret.trim();
                }
            } else if (authType === 'pat') {
                if (!isMaskedValue(patToken)) {
                    configData.token = patToken.trim();
                }
            }

            await api.saveConnection('databricks', configData);

            await api.createAccount({
                connector_type: 'DATABRICKS',
                tag: initialTag,
                identity_email: testResult?.principal || authStatus?.username || (authType === 'service_principal' ? clientId || 'Service Principal' : hostInput) || 'N/A',
                credentials: configData,
            }).catch(e => console.log('Vault sync skipped', e));

            addLog('info', 'Connections', `Databricks config saved for ${initialTag}`);

            if (authType !== 'interactive') {
                setAuthStatus({
                    auth_method: authType,
                    username: testResult?.principal || (authType === 'service_principal' ? 'Service Principal' : 'Personal Access Token'),
                    logged_in: true,
                    token_valid: true,
                });
            }

            if (onSave) onSave();
        } catch (err) {
            setError(err.message);
            addLog('error', 'Connections', err.message);
        } finally {
            setConfigSaving(false);
        }
    };

    const isLoggedIn = authStatus?.logged_in;
    const currentSchemas = schemasByCatalog[catalog] || [];
    const catalogOptions = availableCatalogs.length > 0
        ? availableCatalogs
        : (catalog ? [catalog] : []);
    const schemaOptions = currentSchemas.length > 0
        ? currentSchemas
        : (schemaName ? [schemaName] : []);

    const stepTone = (st) => {
        if (st === 'passed') return { bg: 'rgba(52,211,153,0.1)', color: '#34d399', border: 'rgba(52,211,153,0.25)' };
        if (st === 'warning') return { bg: 'rgba(245,158,11,0.1)', color: '#f59e0b', border: 'rgba(245,158,11,0.25)' };
        if (st === 'failed') return { bg: 'rgba(239,68,68,0.1)', color: '#ef4444', border: 'rgba(239,68,68,0.25)' };
        return { bg: 'var(--bg-surface-raised)', color: 'var(--text-tertiary)', border: 'var(--border-main)' };
    };

    return (
        <div className="rounded-xl border overflow-hidden p-4 mt-4"
            style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface)' }}>
            <div className="flex justify-between items-center mb-4">
                <h4 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Configure Databricks Connection</h4>
                <button onClick={onCancel} className="text-xs text-gray-400 hover:text-white"><X size={14} /></button>
            </div>

            <div className="space-y-4">
                {/* Auth Mode Picker */}
                <div className="flex rounded-lg overflow-hidden border"
                    style={{ borderColor: 'var(--border-main)' }}>
                    <button
                        type="button"
                        onClick={() => { setAuthType('pat'); setTestResult(null); setPermResult(null); }}
                        className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-bold transition-all border-r"
                        style={{
                            background: authType === 'pat' ? 'var(--color-accent)' : 'transparent',
                            color: authType === 'pat' ? '#fff' : 'var(--text-tertiary)',
                            borderColor: 'var(--border-main)',
                        }}>
                        <Key size={11} /> PAT Token
                    </button>
                    <button
                        type="button"
                        onClick={() => { setAuthType('service_principal'); setTestResult(null); setPermResult(null); }}
                        className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-bold transition-all border-r"
                        style={{
                            background: authType === 'service_principal' ? 'var(--color-accent)' : 'transparent',
                            color: authType === 'service_principal' ? '#fff' : 'var(--text-tertiary)',
                            borderColor: 'var(--border-main)',
                        }}>
                        <Database size={11} /> Service Principal
                    </button>
                    <button
                        type="button"
                        onClick={() => { setAuthType('interactive'); setTestResult(null); setPermResult(null); }}
                        className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-bold transition-all"
                        style={{
                            background: authType === 'interactive' ? 'var(--color-accent)' : 'transparent',
                            color: authType === 'interactive' ? '#fff' : 'var(--text-tertiary)',
                        }}>
                        <LogIn size={11} /> Browser Sign-In
                    </button>
                </div>

                {/* Account Tag */}
                <div>
                    <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>Account Tag</label>
                    <input type="text" readOnly value={initialTag} className="w-full px-3 py-2 rounded-lg text-xs border" style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-tertiary)' }} />
                </div>

                {/* Workspace Host, Auth Credentials, Warehouse */}
                <div className="space-y-3">
                    <div>
                        <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>Workspace Host <span className="text-red-400">*</span></label>
                        <input type="text" value={hostInput} onChange={e => setHostInput(e.target.value)}
                            placeholder="e.g. adb-123456789.11.azuredatabricks.net"
                            className="w-full px-3 py-2 rounded-lg text-xs border outline-none"
                            style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-primary)' }} />
                    </div>

                    {(authType === 'interactive' || authType === 'service_principal') && (
                        <div>
                            <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>OAuth Client ID <span className="text-red-400">*</span></label>
                            <input type="text" value={clientId} onChange={e => setClientId(e.target.value)}
                                placeholder="Service Principal or Custom OAuth App Client ID"
                                className="w-full px-3 py-2 rounded-lg text-xs border outline-none"
                                style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-primary)', fontFamily: 'monospace' }} />
                            {authType === 'interactive' && (
                                <div className="text-[10px] mt-1" style={{ color: 'var(--text-tertiary)' }}>
                                    Redirect URI: <code>{window.location.origin}/api/connections/databricks/callback</code>
                                </div>
                            )}
                        </div>
                    )}

                    {authType === 'service_principal' && (
                        <div>
                            <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>OAuth Client Secret <span className="text-red-400">*</span></label>
                            <input type="password" value={clientSecret} onChange={e => setClientSecret(e.target.value)}
                                placeholder="Service Principal secret (stored encrypted)"
                                className="w-full px-3 py-2 rounded-lg text-xs border outline-none"
                                style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-primary)', fontFamily: 'monospace' }} />
                        </div>
                    )}

                    {authType === 'pat' && (
                        <div>
                            <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>Personal Access Token <span className="text-red-400">*</span></label>
                            <input type="password" value={patToken} onChange={e => setPatToken(e.target.value)}
                                placeholder="dapi... (stored encrypted)"
                                className="w-full px-3 py-2 rounded-lg text-xs border outline-none"
                                style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-primary)', fontFamily: 'monospace' }} />
                        </div>
                    )}

                    <div>
                        <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>
                            SQL Warehouse ID <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(Optional)</span>
                        </label>
                        <input type="text" value={warehouseId} onChange={e => setWarehouseId(e.target.value)}
                            placeholder="e.g. abc12345def67890 (optional default; Catalog & Schema are selected per project)"
                            className="w-full px-3 py-2 rounded-lg text-xs border outline-none"
                            style={{ background: 'var(--bg-input)', borderColor: 'var(--border-main)', color: 'var(--text-primary)', fontFamily: 'monospace' }} />
                    </div>

                    {/* Interactive Sign-In CTA when needed */}
                    {authType === 'interactive' && !isLoggedIn && loginPhase === 'idle' && (
                        <button type="button" onClick={handleLogin} disabled={!hostInput || !clientId}
                            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-xs font-bold transition-all"
                            style={{ background: 'var(--color-accent)', color: '#fff', opacity: (!hostInput || !clientId) ? 0.5 : 1 }}>
                            <LogIn size={14} /> Sign in with Databricks
                        </button>
                    )}

                    {authType === 'interactive' && loginPhase === 'polling' && (
                        <div className="flex items-center justify-center gap-2 py-3 px-4 rounded-lg text-xs"
                            style={{ background: 'rgba(255,54,33,0.06)', border: '1px solid rgba(255,54,33,0.2)', color: 'var(--text-secondary)' }}>
                            <Loader2 size={14} className="animate-spin" style={{ color: '#ff3621' }} />
                            Waiting for browser sign-in to complete…
                        </div>
                    )}

                    {isLoggedIn && (
                        <div className="flex items-center gap-3 p-2.5 rounded-lg"
                            style={{ background: 'rgba(52,211,153,0.05)', border: '1px solid rgba(52,211,153,0.18)' }}>
                            <User size={14} style={{ color: '#ff3621' }} />
                            <div className="flex-1 min-w-0">
                                <div className="text-xs font-bold truncate" style={{ color: 'var(--text-primary)' }}>
                                    {authStatus?.username}
                                </div>
                                <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                                    Databricks · {authType}
                                </div>
                            </div>
                            <button type="button" onClick={handleLogout}
                                className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-bold border"
                                style={{ borderColor: 'rgba(239,68,68,0.3)', color: '#ef4444' }}>
                                <LogOut size={10} /> Sign Out
                            </button>
                        </div>
                    )}

                    {/* Test Connection Button & Step-by-Step Results */}
                    <div className="pt-1">
                        <button
                            type="button"
                            onClick={handleTest}
                            disabled={testing || !hostInput}
                            className="w-full flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-bold border transition-all"
                            style={{
                                borderColor: 'var(--color-accent)',
                                color: 'var(--color-accent)',
                                background: 'transparent',
                                opacity: (testing || !hostInput) ? 0.5 : 1,
                            }}
                        >
                            {testing ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
                            {testing ? 'Testing connection…' : 'Test connection'}
                        </button>
                    </div>

                    {testResult && (
                        <div className="rounded-lg border p-3 space-y-2"
                            style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface-raised)' }}>
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-[11px] font-bold" style={{
                                    color: testResult.status === 'success' ? '#34d399'
                                        : testResult.status === 'warning' ? '#f59e0b' : '#ef4444',
                                }}>
                                    {testResult.status === 'success' ? 'Connection verified'
                                        : testResult.status === 'warning' ? 'Connected with warnings' : 'Connection test failed'}
                                </span>
                                {testResult.warehouse?.state && testResult.warehouse.state !== 'UNKNOWN' && (
                                    <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                                        style={{
                                            background: testResult.warehouse.state === 'RUNNING' ? 'rgba(52,211,153,0.12)' : 'rgba(245,158,11,0.12)',
                                            color: testResult.warehouse.state === 'RUNNING' ? '#34d399' : '#f59e0b',
                                        }}>
                                        Warehouse: {testResult.warehouse.state}
                                    </span>
                                )}
                            </div>

                            {Array.isArray(testResult.steps) && testResult.steps.length > 0 ? (
                                <div className="space-y-1.5">
                                    {testResult.steps.map((step) => {
                                        const tone = stepTone(step.status);
                                        return (
                                            <div key={step.id} className="flex items-start gap-2 px-2.5 py-1.5 rounded-md border text-[11px]"
                                                style={{ background: tone.bg, borderColor: tone.border, color: tone.color }}>
                                                {step.status === 'passed'
                                                    ? <CheckCircle2 size={12} className="mt-0.5 shrink-0" />
                                                    : <AlertCircle size={12} className="mt-0.5 shrink-0" />}
                                                <div className="flex-1 min-w-0">
                                                    <span className="font-semibold">{step.label}: </span>
                                                    <span>{step.message}</span>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            ) : (
                                <div className="text-[11px]" style={{ color: '#ef4444' }}>{testResult.message}</div>
                            )}

                            {testResult.warehouse?.can_start && (
                                <div className="pt-1 flex items-center gap-2">
                                    <button
                                        type="button"
                                        onClick={handleStartWarehouse}
                                        disabled={startingWarehouse}
                                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] font-bold"
                                        style={{ background: '#f59e0b', color: '#111827', opacity: startingWarehouse ? 0.6 : 1 }}
                                    >
                                        {startingWarehouse ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
                                        {startingWarehouse ? 'Starting warehouse…' : 'Start warehouse'}
                                    </button>
                                    <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                                        Only starts when you click.
                                    </span>
                                </div>
                            )}
                            {warehouseStartMsg && (
                                <div className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>{warehouseStartMsg}</div>
                            )}
                        </div>
                    )}

                    {/* Optional: Catalog/Schema Permissions Check (Catalog & Schema are selected per project in the wizard) */}
                    <details className="rounded-lg border p-3" style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface-raised)' }}>
                        <summary className="cursor-pointer text-[11px] font-semibold flex items-center gap-1.5" style={{ color: 'var(--text-secondary)' }}>
                            <ShieldCheck size={13} style={{ color: 'var(--color-accent)' }} />
                            Optional: Check Unity Catalog permissions (Catalog &amp; Schema are chosen per project)
                        </summary>
                        <div className="space-y-3 mt-3">
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>
                                Target Catalog
                            </label>
                            <select
                                value={catalog}
                                onChange={(e) => handleCatalogChange(e.target.value)}
                                disabled={catalogOptions.length === 0}
                                className="w-full px-3 py-2 rounded-lg text-xs border outline-none"
                                style={{
                                    background: 'var(--bg-input)',
                                    borderColor: 'var(--border-main)',
                                    color: catalogOptions.length === 0 ? 'var(--text-tertiary)' : 'var(--text-primary)',
                                }}
                            >
                                {catalogOptions.length === 0 ? (
                                    <option value="">Run Test connection to load catalogs</option>
                                ) : (
                                    <>
                                        <option value="">Select catalog…</option>
                                        {catalogOptions.map((cat) => (
                                            <option key={cat} value={cat}>{cat}</option>
                                        ))}
                                    </>
                                )}
                            </select>
                        </div>
                        <div>
                            <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>
                                Target Schema {schemasLoading && <Loader2 size={10} className="inline animate-spin ml-1" />}
                            </label>
                            <select
                                value={schemaName}
                                onChange={(e) => { setSchemaName(e.target.value); setPermResult(null); }}
                                disabled={schemaOptions.length === 0 || schemasLoading}
                                className="w-full px-3 py-2 rounded-lg text-xs border outline-none"
                                style={{
                                    background: 'var(--bg-input)',
                                    borderColor: 'var(--border-main)',
                                    color: schemaOptions.length === 0 ? 'var(--text-tertiary)' : 'var(--text-primary)',
                                }}
                            >
                                {schemaOptions.length === 0 ? (
                                    <option value="">{catalog ? 'No schemas loaded' : 'Select catalog first'}</option>
                                ) : (
                                    <>
                                        <option value="">Select schema…</option>
                                        {schemaOptions.map((sch) => (
                                            <option key={sch} value={sch}>{sch}</option>
                                        ))}
                                    </>
                                )}
                            </select>
                        </div>
                    </div>

                    {/* Check Permissions Panel */}
                    <div className="rounded-lg border p-3 space-y-2"
                        style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface-raised)' }}>
                        <div className="flex items-center justify-between gap-2">
                            <div className="flex items-center gap-1.5 text-xs font-bold" style={{ color: 'var(--text-primary)' }}>
                                <ShieldCheck size={13} style={{ color: 'var(--color-accent)' }} />
                                Unity Catalog Permissions
                            </div>
                            <button
                                type="button"
                                onClick={handleCheckPermissions}
                                disabled={checkingPerms || !hostInput || !catalog || !schemaName}
                                className="flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold border"
                                style={{
                                    borderColor: 'var(--border-main)',
                                    color: 'var(--text-primary)',
                                    opacity: (checkingPerms || !hostInput || !catalog || !schemaName) ? 0.5 : 1,
                                }}
                            >
                                {checkingPerms ? <Loader2 size={11} className="animate-spin" /> : <ShieldCheck size={11} />}
                                {checkingPerms ? 'Checking…' : 'Check permissions'}
                            </button>
                        </div>

                        {permError && (
                            <div className="text-[11px] px-2.5 py-1.5 rounded-md" style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>
                                {permError}
                            </div>
                        )}

                        {permResult && (
                            <div className="space-y-2 pt-1">
                                <div className="text-[11px] font-semibold" style={{
                                    color: permResult.status === 'ok' ? '#34d399'
                                        : permResult.status === 'warning' ? '#f59e0b' : '#ef4444',
                                }}>
                                    {permResult.status === 'ok'
                                        ? `Least-privilege access verified for ${permResult.principal || 'principal'}.`
                                        : permResult.status === 'warning'
                                            ? `Required privileges present, but excessive grants detected for ${permResult.principal || 'principal'}.`
                                            : `Missing required Unity Catalog privileges for ${permResult.principal || 'principal'}.`}
                                </div>

                                {(permResult.checks || []).map((chk) => (
                                    <div key={`${chk.securable_type}:${chk.full_name}`}
                                        className="text-[11px] px-2.5 py-1.5 rounded border flex flex-col gap-0.5"
                                        style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface)' }}>
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="font-mono font-semibold" style={{ color: 'var(--text-primary)' }}>
                                                {chk.securable_type.toUpperCase()} {chk.full_name}
                                            </span>
                                            <span style={{ color: chk.passed ? '#34d399' : '#ef4444', fontWeight: 600 }}>
                                                {chk.passed ? 'OK' : `Missing: ${(chk.missing || []).join(', ')}`}
                                            </span>
                                        </div>
                                        {chk.excessive && chk.excessive.length > 0 && (
                                            <div style={{ color: '#f59e0b' }}>
                                                Excessive: {chk.excessive.join(', ')}
                                            </div>
                                        )}
                                    </div>
                                ))}

                                {(permResult.warnings || []).map((w, idx) => (
                                    <div key={idx} className="text-[11px] px-2.5 py-1.5 rounded border"
                                        style={{ background: 'rgba(245,158,11,0.1)', borderColor: 'rgba(245,158,11,0.28)', color: '#f59e0b' }}>
                                        {w}
                                    </div>
                                ))}

                                {permResult.recommendation_text && (
                                    <div className="space-y-1">
                                        <div className="flex items-center justify-between">
                                            <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: 'var(--text-tertiary)' }}>
                                                Recommended Least-Privilege SQL
                                            </span>
                                            <button
                                                type="button"
                                                onClick={handleCopyGrants}
                                                className="flex items-center gap-1 text-[10px] font-semibold"
                                                style={{ color: 'var(--color-accent)' }}
                                            >
                                                <Copy size={10} /> {copiedGrants ? 'Copied' : 'Copy SQL'}
                                            </button>
                                        </div>
                                        <pre className="p-2.5 rounded-md text-[10.5px] overflow-x-auto border"
                                            style={{
                                                background: 'var(--bg-input)',
                                                borderColor: 'var(--border-main)',
                                                color: 'var(--text-primary)',
                                                fontFamily: 'monospace',
                                                margin: 0,
                                            }}>
                                            {permResult.recommendation_text}
                                        </pre>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                        </div>
                    </details>
                </div>

                {error && (
                    <div className="p-2.5 rounded-lg text-xs flex items-center gap-1.5"
                        style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>
                        <AlertCircle size={12} />
                        <span>{error}</span>
                    </div>
                )}

                <div className="flex items-center gap-2 pt-2">
                    <button
                        type="button"
                        onClick={handleSaveConfig}
                        disabled={configSaving || !hostInput}
                        className="flex-1 flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold"
                        style={{ background: 'var(--color-accent)', color: '#fff', opacity: (configSaving || !hostInput) ? 0.6 : 1 }}
                    >
                        {configSaving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                        {configSaving ? 'Saving…' : 'Save Databricks Connection'}
                    </button>
                </div>
            </div>
        </div>
    );
}


export default function ConnectionsPanel({ isOpen, onClose, selectedConnector }) {
    const [activeTab, setActiveTab] = useState('connections');
    const [snowflakeStatus, setSnowflakeStatus] = useState(null);
    const [databricksStatus, setDatabricksStatus] = useState(null);
    const [loading, setLoading] = useState(true);


    // Polling state
    const pollRef = useRef(null);
    const pollTimeout = 3000; // 3 seconds
    const pollMaxAttempts = 20; // ~1 minute
    const [polling, setPolling] = useState(false);
    const pollAttempts = useRef(0);

    const fetchAndSetStatus = useCallback(async () => {
        setLoading(true);
        try {
            const data = await withTimeout(api.getConnectionsStatus(), 4000, null);
            setSnowflakeStatus(data?.snowflake ?? null);
            setDatabricksStatus(data?.databricks ?? null);
            // Add more providers as needed
            return data;
        } catch {
            return null;
        } finally {
            setLoading(false);
        }
    }, []);

    // Polling logic
    const pollStatus = useCallback(async () => {
        setPolling(true);
        pollAttempts.current = 0;
        const poll = async () => {
            pollAttempts.current += 1;
            const data = await fetchAndSetStatus();
            const allConnected = ['snowflake', 'databricks'].every(
                key => typeof data?.[key]?.status === 'string' && data[key].status.toLowerCase() === 'connected'
            );
            if (allConnected || pollAttempts.current >= pollMaxAttempts) {
                setPolling(false);
                return;
            }
            pollRef.current = setTimeout(poll, pollTimeout);
        };
        poll();
    }, [fetchAndSetStatus]);

    // Initial fetch on open
    useEffect(() => {
        if (isOpen) {
            fetchAndSetStatus();
        }
        return () => { if (pollRef.current) clearTimeout(pollRef.current); };
    }, [isOpen, fetchAndSetStatus]);

    // Call this after any connect/save action for any provider
    const handleAfterConnect = useCallback(() => {
        fetchAndSetStatus();
        pollStatus();
    }, [fetchAndSetStatus, pollStatus]);

    // Pass handleAfterConnect to child components (ConnectionManager, etc.) as needed

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
                    {(() => {
                        const connectorConfig = {
                            fabric: { label: 'Microsoft Fabric', icon: Cloud, color: '#8b5cf6' },
                            snowflake: { label: 'Snowflake', icon: Snowflake, color: '#0ea5e9' },
                            databricks: { label: 'Databricks', icon: Database, color: '#ff3621' },
                        };
                        const currentConfig = (activeTab === 'connections' && selectedConnector)
                            ? connectorConfig[selectedConnector.toLowerCase()]
                            : null;
                        const modalTitle = currentConfig ? currentConfig.label : (activeTab === 'global-config' ? 'Global Configuration' : 'Settings');
                        const HeaderIcon = currentConfig ? currentConfig.icon : (activeTab === 'global-config' ? FileCode2 : Settings);
                        const headerColor = currentConfig ? currentConfig.color : 'var(--color-accent)';

                        return (
                            <div className="flex items-center gap-3">
                                <div className="w-9 h-9 rounded-xl flex items-center justify-center"
                                    style={{ background: 'var(--color-accent-faint, rgba(99,102,241,.1))' }}>
                                    <HeaderIcon size={18} style={{ color: headerColor }} />
                                </div>
                                <div>
                                    <h2 className="text-base font-bold" style={{ color: 'var(--text-primary)' }}>{modalTitle}</h2>
                                    <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
                                        {activeTab === 'global-config' ? 'Environment · Project Defaults' : 'Connections · Configuration'}
                                    </p>
                                </div>
                            </div>
                        );
                    })()}
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
                                {(!selectedConnector || selectedConnector === 'fabric') && (
                                    <ConnectionManager 
                                        type="fabric" title="Microsoft Fabric" 
                                        subtitle="Manage your Microsoft identities for data extraction." 
                                        icon={Cloud} color="#8b5cf6" 
                                        FormComponent={FabricAccountForm} 
                                    />
                                )}
                                {(!selectedConnector || selectedConnector === 'snowflake') && (
                                    <ConnectionManager 
                                        type="snowflake" title="Snowflake" 
                                        subtitle="Manage your Snowflake data warehouse identities." 
                                        icon={Snowflake} color="#0ea5e9" 
                                        FormComponent={SnowflakeAccountForm} 
                                        status={snowflakeStatus} 
                                    />
                                )}
                                {(!selectedConnector || selectedConnector === 'databricks') && (
                                    <ConnectionManager 
                                        type="databricks" title="Databricks" 
                                        subtitle="Manage your Databricks cluster identities." 
                                        icon={Database} color="#ff3621" 
                                        FormComponent={DatabricksAccountForm} 
                                        status={databricksStatus} 
                                    />
                                )}

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
