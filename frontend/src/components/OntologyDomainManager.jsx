import React, { useState, useEffect, useRef } from 'react';
import {
  Layers, Upload, FileCode, Sparkles, CheckCircle2,
  AlertTriangle, RefreshCw, Copy, Check, Trash2, Plus,
  Search, Database, Network, ShieldCheck, ChevronRight,
  Play, Snowflake, Star, Table, CheckCheck, Code2, Server,
  HelpCircle, X, Info, ChevronDown, ChevronUp, ExternalLink,
  BookOpen, Compass, Cpu, Tag, ArrowRight
} from 'lucide-react';
import { authFetch } from '../utils/api';

// Turn a FastAPI error body into readable text (detail may be a string,
// {message, errors[]} or a pydantic error list).
function describeApiError(data, fallback) {
  const detail = data && data.detail !== undefined ? data.detail : data;
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  if (Array.isArray(detail.file_errors) && detail.file_errors.length) {
    return detail.file_errors.map((e) => `${e.file}: ${e.error}`).join('\n');
  }
  if (Array.isArray(detail.errors) && detail.errors.length) return detail.errors.join('\n');
  return detail.message || fallback;
}

export default function OntologyDomainManager() {
  // Main Tab Navigation: 'mesh' | 'ai'
  const [activeTab, setActiveTab] = useState('mesh');
  const [loading, setLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState(null);
  const [showHowItWorks, setShowHowItWorks] = useState(true);

  // Domains & Governance State
  const [domainData, setDomainData] = useState({
    domains: [],
    total_concepts: 0,
    total_metrics: 0,
    total_relationships: 0,
    collisions: [],
    unresolved_references: [],
    load_errors: [],
  });

  // Domain Glossary Inspector Drawer State
  const [inspectingDomain, setInspectingDomain] = useState(null);
  const [inspectingDomainData, setInspectingDomainData] = useState(null);
  const [inspectorActiveTab, setInspectorActiveTab] = useState('metrics'); // 'metrics' | 'concepts' | 'yaml'
  const [editorYaml, setEditorYaml] = useState('');
  const [validationResult, setValidationResult] = useState(null);

  // Upload Modal State
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploadProgress, setUploadProgress] = useState(null);
  // Validation errors for the files in the open upload modal. Shown inside the
  // modal (the page banner sits behind the modal backdrop and is unreadable).
  const [uploadError, setUploadError] = useState(null);
  const fileInputRef = useRef(null);

  // Bootstrap from OSI Modal State
  const [bootstrapModalOpen, setBootstrapModalOpen] = useState(false);
  const [bootstrapProject, setBootstrapProject] = useState('');
  const [bootstrapDomain, setBootstrapDomain] = useState('');
  const [bootstrapping, setBootstrapping] = useState(false);

  // Multi-Target AI/BI Playground State
  const [selectedTarget, setSelectedTarget] = useState('SNOWFLAKE'); // 'SNOWFLAKE' | 'DATABRICKS' | 'LOCAL'
  const [projectsList, setProjectsList] = useState([]);
  const [selectedProjectSlug, setSelectedProjectSlug] = useState('');
  const [nlPrompt, setNlPrompt] = useState('What is the gross margin and ARR by Customer in 2024?');
  const [nlResolutionPlan, setNlResolutionPlan] = useState(null);
  const [customSql, setCustomSql] = useState('');
  const [copiedSql, setCopiedSql] = useState(false);
  const [aiStatus, setAiStatus] = useState(null);

  // Query Execution & Verified Queries State
  const [executingQuery, setExecutingQuery] = useState(false);
  const [executionResult, setExecutionResult] = useState(null);
  const [savingVerified, setSavingVerified] = useState(false);
  const [verifiedSuccessMsg, setVerifiedSuccessMsg] = useState(null);

  // Initial Load
  useEffect(() => {
    fetchDomains();
    fetchAiStatus();
    fetchProjects();
  }, []);

  const fetchDomains = async () => {
    setLoading(true);
    try {
      const res = await authFetch('/api/ontology/domains');
      if (res.ok) {
        const data = await res.json();
        setDomainData(data);
      }
    } catch (err) {
      console.error('Failed to load domains:', err);
    } finally {
      setLoading(false);
    }
  };

  const fetchAiStatus = async () => {
    try {
      const res = await authFetch('/api/ai/status');
      if (res.ok) {
        const data = await res.json();
        setAiStatus(data);
      }
    } catch (err) {
      console.warn('Could not load AI status:', err);
    }
  };

  const fetchProjects = async () => {
    try {
      const res = await authFetch('/api/projects?limit=50');
      if (res.ok) {
        const data = await res.json();
        const list = Array.isArray(data) ? data : data.projects || [];
        setProjectsList(list);
        if (list.length > 0 && !selectedProjectSlug) {
          setSelectedProjectSlug(list[0].id || list[0].name);
        }
      }
    } catch (err) {
      console.warn('Could not load projects for AI playground:', err);
    }
  };

  const handleOpenDomainInspector = async (domainName, initialTab = 'metrics') => {
    setInspectingDomain(domainName);
    setInspectorActiveTab(initialTab);
    setInspectingDomainData(null);
    setValidationResult(null);
    try {
      const res = await authFetch(`/api/ontology/domains/${encodeURIComponent(domainName)}`);
      if (res.ok) {
        const data = await res.json();
        setInspectingDomainData(data);
        setEditorYaml(data.yaml_content || '');
      }
    } catch (err) {
      console.error('Failed to load domain detail:', err);
    }
  };

  const handleValidateYaml = async () => {
    try {
      const res = await authFetch('/api/ontology/domains/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml_content: editorYaml, original_domain: inspectingDomain }),
      });
      const data = await res.json();
      setValidationResult(data);
    } catch (err) {
      setStatusMessage({ type: 'error', text: 'Validation request failed' });
    }
  };

  const handleSaveDomain = async () => {
    setLoading(true);
    try {
      const res = await authFetch(`/api/ontology/domains/${encodeURIComponent(inspectingDomain)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml_content: editorYaml, original_domain: inspectingDomain }),
      });
      const data = await res.json();
      if (res.ok) {
        const savedName = data.domain || inspectingDomain;
        setStatusMessage({
          type: 'success',
          text: savedName !== inspectingDomain
            ? `Domain '${inspectingDomain}' renamed to '${savedName}' and saved.`
            : `Domain '${savedName}' saved and synchronized!`,
        });
        fetchDomains();
        fetchAiStatus();
        handleOpenDomainInspector(savedName, 'metrics');
      } else {
        setStatusMessage({ type: 'error', text: describeApiError(data, 'Save failed.') });
      }
    } catch (err) {
      setStatusMessage({ type: 'error', text: String(err) });
    } finally {
      setLoading(false);
      setTimeout(() => setStatusMessage(null), 4000);
    }
  };

  const handleFileUpload = async () => {
    const files = Array.from(selectedFiles);
    if (!files.length) return;

    setLoading(true);
    setUploadError(null);
    setUploadProgress('Ingesting and parsing domain ontologies…');

    const formData = new FormData();
    files.forEach((f) => formData.append('files', f));

    try {
      const res = await authFetch('/api/ontology/domains/upload', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (res.ok && (data.uploaded_domains || []).length > 0) {
        const fileErrors = data.file_errors || [];
        setStatusMessage({
          type: fileErrors.length ? 'error' : 'success',
          text: `Ingested ${data.uploaded_domains.length} domain ontolog${data.uploaded_domains.length === 1 ? 'y' : 'ies'} (${data.uploaded_domains.join(', ')}).`
            + (fileErrors.length ? `\nRejected ${fileErrors.length} file(s):\n${fileErrors.map((e) => `${e.file}: ${e.error}`).join('\n')}` : ''),
        });
        setSelectedFiles([]);
        setUploadModalOpen(false);
        fetchDomains();
        fetchAiStatus();
      } else {
        setUploadError(describeApiError(data, 'Upload failed: no file was ingested.'));
      }
    } catch (err) {
      setUploadError(String(err));
    } finally {
      setLoading(false);
      setUploadProgress(null);
      setTimeout(() => setStatusMessage(null), 12000);
    }
  };

  const handleBootstrapFromOsi = async () => {
    if (!bootstrapProject) return;
    setBootstrapping(true);
    try {
      const res = await authFetch('/api/ontology/domains/from-osi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: bootstrapProject,
          domain_name: bootstrapDomain.trim() || undefined,
          save_to_disk: true,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setStatusMessage({
          type: 'success',
          text: `Successfully generated ontology domain '${data.domain}' from OSI model (${data.concept_count} concepts, ${data.metric_count} metrics)!`,
        });
        setBootstrapModalOpen(false);
        fetchDomains();
        fetchAiStatus();
        handleOpenDomainInspector(data.domain, 'metrics');
      } else {
        setStatusMessage({ type: 'error', text: describeApiError(data, 'Failed to generate ontology from OSI.') });
      }
    } catch (err) {
      setStatusMessage({ type: 'error', text: String(err) });
    } finally {
      setBootstrapping(false);
      setTimeout(() => setStatusMessage(null), 5000);
    }
  };

  const handleSyncWithOsi = async (domainName) => {
    setLoading(true);
    try {
      const res = await authFetch('/api/ontology/domains/from-osi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The backend re-syncs from the project this domain was built from.
        body: JSON.stringify({
          domain_name: domainName,
          save_to_disk: true,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setStatusMessage({
          type: 'success',
          text: `Synchronized '${domainName}' with latest OSI model while preserving your human-authored edits!`,
        });
        fetchDomains();
        fetchAiStatus();
        handleOpenDomainInspector(domainName, 'metrics');
      } else {
        setStatusMessage({ type: 'error', text: describeApiError(data, 'OSI sync failed.') });
      }
    } catch (err) {
      setStatusMessage({ type: 'error', text: String(err) });
    } finally {
      setLoading(false);
      setTimeout(() => setStatusMessage(null), 4000);
    }
  };

  const handleResolveNlQuery = async () => {
    if (!nlPrompt.trim()) return;
    setLoading(true);
    setExecutionResult(null);
    setVerifiedSuccessMsg(null);
    try {
      const res = await authFetch('/api/ai/nl-query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: nlPrompt,
          dialect: selectedTarget,
          target: selectedTarget,
          project_id: selectedProjectSlug,
        }),
      });
      if (res.ok) {
        const plan = await res.json();
        setNlResolutionPlan(plan);
        setCustomSql(plan.generated_sql || '');
      }
    } catch (err) {
      console.error('NL resolution failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteQuery = async () => {
    const sqlToRun = customSql || nlResolutionPlan?.generated_sql;
    if (!sqlToRun) return;
    setExecutingQuery(true);
    setExecutionResult(null);
    try {
      const res = await authFetch('/api/ai/execute-query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sql: sqlToRun,
          target: selectedTarget,
          project_id: selectedProjectSlug,
          limit: 25,
        }),
      });
      if (res.ok) {
        const result = await res.json();
        setExecutionResult(result);
      } else {
        const errData = await res.json();
        setExecutionResult({
          success: false,
          error: errData.detail || 'Query execution failed',
          columns: [],
          rows: [],
          execution_time_ms: 0,
        });
      }
    } catch (err) {
      setExecutionResult({
        success: false,
        error: String(err),
        columns: [],
        rows: [],
        execution_time_ms: 0,
      });
    } finally {
      setExecutingQuery(false);
    }
  };

  const handleSaveVerifiedQuery = async () => {
    const sqlToSave = customSql || nlResolutionPlan?.generated_sql;
    if (!sqlToSave || !nlPrompt) return;
    setSavingVerified(true);
    try {
      const res = await authFetch('/api/ai/save-verified-query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: nlPrompt,
          sql: sqlToSave,
          target: selectedTarget,
          project_id: selectedProjectSlug,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setVerifiedSuccessMsg(data.message);
        setTimeout(() => setVerifiedSuccessMsg(null), 5000);
      }
    } catch (err) {
      console.error('Failed to save verified query:', err);
    } finally {
      setSavingVerified(false);
    }
  };

  const handleCopySql = (sqlText) => {
    navigator.clipboard.writeText(sqlText);
    setCopiedSql(true);
    setTimeout(() => setCopiedSql(false), 2000);
  };

  const handleJumpToAiTest = (metricName, formula) => {
    setNlPrompt(`Calculate ${metricName} by Customer in 2024`);
    setActiveTab('ai');
    setInspectingDomain(null);
    setExecutionResult(null);
  };

  return (
    <div className="flex flex-col h-full bg-app text-primary overflow-hidden">
      {/* ── Top Header Banner ── */}
      <div className="px-6 py-4 border-b border-main bg-surface flex flex-col md:flex-row md:items-center justify-between gap-4 flex-shrink-0">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-lg bg-accent-blue/10 text-accent-blue">
              <Network size={20} strokeWidth={2.2} />
            </div>
            <h1 className="text-lg font-bold tracking-tight text-primary m-0">
              Enterprise Semantic Mesh & Domain Ontologies
            </h1>
            <span className="px-2.5 py-0.5 text-[11px] font-semibold rounded-full bg-surface-raised border border-main text-secondary">
              Federated Architecture
            </span>
          </div>
          <p className="text-xs text-secondary mt-1 max-w-3xl leading-relaxed">
            Centralize cross-department business definitions (Finance, Sales, Operations) into an authoritative semantic graph.
            Eliminates metric conflicts and ensures AI agents (Snowflake Cortex & Databricks Genie) generate 100% accurate SQL.
          </p>
        </div>

        {/* Action Controls & KPI Badges */}
        <div className="flex items-center gap-3">
          <div className="hidden sm:flex items-center gap-2">
            <div className="px-3 py-1.5 rounded-lg bg-surface-raised border border-main text-center" title="Active business domain boundaries">
              <span className="text-[10px] text-tertiary uppercase tracking-wider block font-semibold">Domains</span>
              <span className="font-bold text-sm text-primary">{domainData.domains.length}</span>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-surface-raised border border-main text-center" title="Entities, dimensions, and physical tables mapped">
              <span className="text-[10px] text-tertiary uppercase tracking-wider block font-semibold">Concepts</span>
              <span className="font-bold text-sm text-primary">{domainData.total_concepts}</span>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-surface-raised border border-main text-center" title="Authoritative formulas governed across all teams">
              <span className="text-[10px] text-tertiary uppercase tracking-wider block font-semibold">Metrics</span>
              <span className="font-bold text-sm text-emerald-400">{domainData.total_metrics}</span>
            </div>
          </div>

          <button
            type="button"
            onClick={() => {
              if (projectsList.length > 0 && !bootstrapProject) {
                setBootstrapProject(projectsList[0].id || projectsList[0].name);
              }
              setBootstrapModalOpen(true);
            }}
            className="px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold flex items-center gap-1.5 transition-all shadow-sm"
            title="Auto-generate a domain ontology from translated BI reports or OSI models"
          >
            <Sparkles size={14} className="text-amber-200" />
            <span>Generate from OSI</span>
          </button>

          <button
            type="button"
            onClick={() => setUploadModalOpen(true)}
            className="px-3 py-2 rounded-lg bg-surface-raised hover:bg-surface-hover border border-main text-xs font-semibold text-primary flex items-center gap-1.5 transition-all"
            title="Upload department-authored domain ontology YAML files"
          >
            <Upload size={14} className="text-accent-blue" />
            <span>Ingest YAML</span>
          </button>

          <button
            type="button"
            onClick={fetchDomains}
            className="p-2 rounded-lg bg-surface-raised hover:bg-surface-hover border border-main text-secondary hover:text-primary transition-all"
            title="Refresh semantic mesh metadata"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* ── Status Toast ── */}
      {statusMessage && (
        <div
          className={`px-6 py-2.5 text-xs font-medium flex items-center gap-2 border-b transition-all ${
            statusMessage.type === 'success'
              ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
              : 'bg-red-500/10 text-red-300 border-red-500/20'
          }`}
        >
          {statusMessage.type === 'success' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          <span style={{ whiteSpace: 'pre-line' }}>{statusMessage.text}</span>
        </div>
      )}

      {/* ── Clean 2-Tab Navigation ── */}
      <div className="flex items-center justify-between px-6 border-b border-main bg-surface flex-shrink-0">
        <div className="flex items-center gap-1 text-xs">
          <button
            type="button"
            onClick={() => setActiveTab('mesh')}
            className={`flex items-center gap-2 py-3 px-4 font-semibold border-b-2 transition-all ${
              activeTab === 'mesh'
                ? 'border-accent-blue text-accent-blue'
                : 'border-transparent text-secondary hover:text-primary'
            }`}
          >
            <Network size={15} />
            <span>Domain Mesh & Business Glossary</span>
            <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-surface-raised text-secondary border border-main">
              {domainData.domains.length}
            </span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('ai')}
            className={`flex items-center gap-2 py-3 px-4 font-semibold border-b-2 transition-all ${
              activeTab === 'ai'
                ? 'border-accent-blue text-accent-blue'
                : 'border-transparent text-secondary hover:text-primary'
            }`}
          >
            <Sparkles size={15} className="text-amber-400" />
            <span>AI/BI Conversational Studio</span>
            <span className="px-1.5 py-0.2 rounded-full text-[10px] font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30">
              Cortex & Genie
            </span>
          </button>
        </div>

        <button
          type="button"
          onClick={() => setShowHowItWorks((prev) => !prev)}
          className="text-[11px] text-tertiary hover:text-secondary flex items-center gap-1 transition-colors py-1"
        >
          <Info size={13} />
          <span>{showHowItWorks ? 'Hide Architecture Guide' : 'How Semantic Mesh Works'}</span>
          {showHowItWorks ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </button>
      </div>

      {/* ── Main Scroll Area ── */}
      <div className="flex-1 overflow-y-auto p-6 min-h-0 bg-app">
        {/* Collapsible Architecture Guide */}
        {showHowItWorks && (
          <div className="max-w-6xl mx-auto mb-6 p-4 rounded-xl bg-surface border border-main relative transition-all">
            <button
              onClick={() => setShowHowItWorks(false)}
              className="absolute right-3 top-3 text-tertiary hover:text-primary p-1"
              title="Dismiss guide"
            >
              <X size={14} />
            </button>
            <div className="flex items-start gap-3">
              <div className="p-2 rounded-lg bg-accent-blue/10 text-accent-blue mt-0.5">
                <BookOpen size={18} />
              </div>
              <div className="space-y-1.5">
                <h3 className="font-bold text-xs text-primary">How Enterprise Semantic Ontologies Work in SemaBridge</h3>
                <p className="text-[11px] text-secondary leading-relaxed">
                  Different departments author business terms and formulas in their respective domains. SemaBridge's engine merges them, monitors for cross-department naming conflicts, and compiles a unified semantic graph for AI query engines.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2 text-xs">
                  <div className="p-2.5 rounded-lg bg-surface-raised border border-main">
                    <span className="font-semibold text-primary block text-[11px] mb-0.5">1. Federated Boundaries</span>
                    <span className="text-[11px] text-tertiary">Finance, Sales, and Ops manage their own metric formulas independently.</span>
                  </div>
                  <div className="p-2.5 rounded-lg bg-surface-raised border border-main">
                    <span className="font-semibold text-primary block text-[11px] mb-0.5">2. Collision Governance</span>
                    <span className="text-[11px] text-tertiary">Detects when 2 teams define the same metric name differently before deployment.</span>
                  </div>
                  <div className="p-2.5 rounded-lg bg-surface-raised border border-main">
                    <span className="font-semibold text-primary block text-[11px] mb-0.5">3. Multi-Target AI Parity</span>
                    <span className="text-[11px] text-tertiary">Snowflake Cortex Analyst and Databricks Genie query verified definitions with 0 hallucinations.</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════════════════════════════
            TAB 1: DOMAIN MESH & BUSINESS GLOSSARY
           ══════════════════════════════════════════════════════════════════════════ */}
        {activeTab === 'mesh' && (
          <div className="max-w-6xl mx-auto space-y-6">
            {/* Executive Governance Status Card */}
            {(domainData.load_errors || []).length > 0 && (
              <div
                role="alert"
                style={{
                  marginBottom: 12, padding: '10px 14px', borderRadius: 8,
                  border: '1px solid #f59e0b', background: 'rgba(245,158,11,0.08)', fontSize: 12,
                }}
              >
                <div style={{ fontWeight: 700, marginBottom: 4 }}>
                  {domainData.load_errors.length} ontology file(s) could not be loaded
                </div>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {domainData.load_errors.map((e, i) => (
                    <li key={i}><code>{e.file}</code>: {e.error}</li>
                  ))}
                </ul>
              </div>
            )}
            {domainData.collisions.length > 0 ? (
              <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2 text-amber-400 font-bold text-xs uppercase tracking-wider">
                    <AlertTriangle size={16} />
                    Cross-Domain Metric Collisions Detected ({domainData.collisions.length})
                  </div>
                  <span className="text-[11px] text-amber-300">Action Recommended</span>
                </div>
                <p className="text-xs text-secondary mb-3">
                  Multiple domains define competing mathematical formulas or join expressions for the same business term.
                  AI conversational queries will encounter ambiguity unless domain precedence is defined.
                </p>
                <div className="space-y-2">
                  {domainData.collisions.map((col, idx) => (
                    <div key={idx} className="p-3 rounded-lg bg-surface border border-main text-xs space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-primary">{col.item_name}</span>
                        <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-300">
                          {col.severity} Severity
                        </span>
                      </div>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] font-mono bg-surface-raised p-2.5 rounded-lg border border-main">
                        {Object.entries(col.expressions || {}).map(([dom, expr]) => (
                          <div key={dom}>
                            <span className="text-accent-blue font-bold block">{dom} Definition:</span>
                            <span className="text-primary">{expr || 'N/A'}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="p-4 rounded-xl bg-surface border border-main flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400">
                    <ShieldCheck size={20} />
                  </div>
                  <div>
                    <h3 className="font-bold text-xs text-primary flex items-center gap-2">
                      100% Conflict-Free Semantic Governance
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                        CLEAN MESH
                      </span>
                    </h3>
                    <p className="text-[11px] text-secondary mt-0.5">
                      All {domainData.total_metrics} enterprise metrics and join paths are reconciled. AI conversational queries will resolve deterministically.
                    </p>
                  </div>
                </div>
                <span className="text-[11px] text-tertiary hidden md:inline">Automated Pre-Deploy Check</span>
              </div>
            )}

            {/* Section Header */}
            <div className="flex items-center justify-between pt-2">
              <div>
                <h2 className="text-sm font-bold text-primary">Registered Business Domains</h2>
                <p className="text-xs text-secondary">
                  Click any domain card to inspect metrics, mathematical formulas, synonyms, and physical tables.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setUploadModalOpen(true)}
                className="text-xs font-semibold text-accent-blue hover:underline flex items-center gap-1"
              >
                <Plus size={14} /> Add Domain
              </button>
            </div>

            {/* Domain Cards Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {domainData.domains.map((d) => (
                <div
                  key={d.domain}
                  className="rounded-xl border border-main bg-surface p-5 hover:border-accent-blue/40 transition-all flex flex-col justify-between shadow-sm group"
                >
                  <div>
                    <div className="flex items-start justify-between mb-2">
                      <div>
                        <h3 className="font-bold text-sm text-primary flex items-center gap-2">
                          {d.domain}
                          <span className="text-[10px] px-1.5 py-0.2 font-mono rounded bg-surface-raised text-secondary border border-main">
                            v{d.version || '1.0'}
                          </span>
                        </h3>
                        <p className="text-xs text-secondary mt-1 line-clamp-2 leading-relaxed">
                          {d.description || 'Enterprise domain definitions and authoritative metrics.'}
                        </p>
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2 my-4 text-center">
                      <div className="p-2 rounded-lg bg-surface-raised border border-main">
                        <span className="text-[10px] text-tertiary uppercase font-semibold block">Concepts</span>
                        <span className="font-bold text-xs text-primary">{d.concept_count}</span>
                      </div>
                      <div className="p-2 rounded-lg bg-surface-raised border border-main">
                        <span className="text-[10px] text-tertiary uppercase font-semibold block">Metrics</span>
                        <span className="font-bold text-xs text-emerald-400">{d.metric_count}</span>
                      </div>
                      <div className="p-2 rounded-lg bg-surface-raised border border-main">
                        <span className="text-[10px] text-tertiary uppercase font-semibold block">Joins</span>
                        <span className="font-bold text-xs text-primary">{d.relationship_count}</span>
                      </div>
                    </div>

                    <div className="text-xs text-secondary flex items-center gap-1.5 mb-4">
                      <span className="text-tertiary">Steward:</span>
                      <span className="text-primary font-medium text-[11px] truncate">{d.owner || 'Enterprise Data Team'}</span>
                    </div>
                  </div>

                  {/* Card Actions */}
                  <div className="flex items-center gap-2 pt-3 border-t border-main">
                    <button
                      type="button"
                      onClick={() => handleOpenDomainInspector(d.domain, 'metrics')}
                      className="flex-1 py-1.5 px-3 rounded-lg bg-accent-blue/10 hover:bg-accent-blue/20 text-accent-blue font-semibold text-xs flex items-center justify-center gap-1.5 transition-colors"
                      title="Inspect metrics, formulas, synonyms, and physical tables"
                    >
                      <BookOpen size={13} />
                      Inspect Glossary
                    </button>
                    <button
                      type="button"
                      onClick={() => handleOpenDomainInspector(d.domain, 'yaml')}
                      className="py-1.5 px-3 rounded-lg bg-surface-raised hover:bg-surface-hover text-secondary hover:text-primary text-xs font-medium border border-main transition-colors"
                      title="View or edit raw YAML schema"
                    >
                      <Code2 size={13} />
                    </button>
                  </div>
                </div>
              ))}

              {/* Add Domain Trigger Card */}
              <button
                type="button"
                onClick={() => setUploadModalOpen(true)}
                className="rounded-xl border-2 border-dashed border-main hover:border-accent-blue/50 bg-surface/40 p-6 flex flex-col items-center justify-center gap-2 text-secondary hover:text-primary transition-all text-center min-h-[220px]"
              >
                <div className="p-3 rounded-xl bg-surface-raised border border-main text-secondary group-hover:text-primary">
                  <Plus size={20} />
                </div>
                <div>
                  <span className="font-bold text-xs text-primary block">Ingest New Domain</span>
                  <span className="text-[11px] text-tertiary mt-0.5 block">
                    Upload .yaml or .yml domain definitions
                  </span>
                </div>
              </button>
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════════════════════════════
            TAB 2: MULTI-TARGET AI/BI CONVERSATIONAL STUDIO (CORTEX & GENIE)
           ══════════════════════════════════════════════════════════════════════════ */}
        {activeTab === 'ai' && (
          <div className="max-w-6xl mx-auto space-y-6 pb-12">
            {/* Target & Engine Switcher Bar */}
            <div className="p-4 rounded-xl bg-surface border border-main flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-accent-blue/10 text-accent-blue">
                  <Sparkles size={20} />
                </div>
                <div>
                  <h3 className="font-bold text-primary text-sm flex items-center gap-2 m-0">
                    Multi-Target Conversational Analytics Studio
                  </h3>
                  <p className="text-xs text-secondary m-0 mt-0.5">
                    Test plain-English questions before deploying semantic views to enterprise business users.
                  </p>
                </div>
              </div>

              {/* Target Selector Segmented Buttons */}
              <div className="flex items-center bg-surface-raised p-1 rounded-xl border border-main gap-1">
                <button
                  type="button"
                  onClick={() => {
                    setSelectedTarget('SNOWFLAKE');
                    setExecutionResult(null);
                  }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                    selectedTarget === 'SNOWFLAKE'
                      ? 'bg-blue-600 text-white shadow-sm'
                      : 'text-secondary hover:text-primary hover:bg-surface'
                  }`}
                  title="Target Snowflake Cortex Analyst and Semantic Views"
                >
                  <Snowflake size={14} className={selectedTarget === 'SNOWFLAKE' ? 'text-cyan-200' : 'text-cyan-400'} />
                  Snowflake Cortex Analyst
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedTarget('DATABRICKS');
                    setExecutionResult(null);
                  }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                    selectedTarget === 'DATABRICKS'
                      ? 'bg-amber-600 text-white shadow-sm'
                      : 'text-secondary hover:text-primary hover:bg-surface'
                  }`}
                  title="Target Databricks AI/BI Genie and Unity Catalog Metric Views"
                >
                  <Database size={14} className={selectedTarget === 'DATABRICKS' ? 'text-amber-200' : 'text-amber-400'} />
                  Databricks AI/BI Genie
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedTarget('LOCAL');
                    setExecutionResult(null);
                  }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                    selectedTarget === 'LOCAL'
                      ? 'bg-emerald-600 text-white shadow-sm'
                      : 'text-secondary hover:text-primary hover:bg-surface'
                  }`}
                  title="In-memory local compiler with zero cloud warehouse credit cost"
                >
                  <Server size={14} className={selectedTarget === 'LOCAL' ? 'text-emerald-200' : 'text-emerald-400'} />
                  Local (0 Credits)
                </button>
              </div>
            </div>

            {/* Target Capability Info & Model Context */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="md:col-span-2 p-3.5 rounded-xl bg-surface-raised/70 border border-main flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-primary">Active Engine:</span>
                  <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-surface border border-main text-accent-blue font-medium">
                    {selectedTarget === 'SNOWFLAKE'
                      ? 'Snowflake Cortex Analyst · Semantic Views & Cortex YAML'
                      : selectedTarget === 'DATABRICKS'
                      ? 'Databricks AI/BI Genie · Unity Catalog Metric Views'
                      : 'SemaBridge Local Engine · In-Memory AST Compiler'}
                  </span>
                </div>
                <span className="text-[11px] text-tertiary">
                  {selectedTarget === 'LOCAL' ? '0 Compute Credits' : 'Multi-Dialect Ready'}
                </span>
              </div>

              {/* Project Model Scope Selector */}
              <div className="p-2.5 rounded-xl bg-surface border border-main flex items-center justify-between text-xs">
                <span className="text-secondary font-medium">Model Context:</span>
                <select
                  value={selectedProjectSlug}
                  onChange={(e) => setSelectedProjectSlug(e.target.value)}
                  className="bg-surface-raised border border-main rounded-lg px-2.5 py-1 text-xs text-primary font-medium focus:outline-none focus:border-accent-blue"
                  title="Scope AI query resolution to a specific project or the global enterprise mesh"
                >
                  <option value="">Enterprise Federated Mesh</option>
                  {projectsList.map((p) => (
                    <option key={p.id || p.name} value={p.id || p.name}>
                      {p.name || p.id}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Natural Language Question Input */}
            <div className="p-5 rounded-xl bg-surface border border-main space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <label className="text-xs font-bold text-secondary uppercase tracking-wider block">
                    Natural Language Analytical Question
                  </label>
                  <span className="text-[11px] text-tertiary block mt-0.5">
                    Type a question in plain business terms. SemaBridge maps it to governed formulas and compiles SQL.
                  </span>
                </div>
                <span className="text-[11px] text-tertiary">
                  Target: <strong className="text-primary">{selectedTarget}</strong>
                </span>
              </div>

              {/* Dynamic Preset Prompt Chips */}
              <div className="flex flex-wrap gap-2 text-xs">
                {(selectedTarget === 'DATABRICKS'
                  ? [
                      'Compare monthly MRR and customer churn rate by tier in 2024',
                      'Show top 10 customers by annual sales in Unity Catalog',
                      'Calculate net profit margin and COGS by product category',
                    ]
                  : [
                      'What is the gross margin and ARR by Customer in 2024?',
                      'Show me ebitda and revenue by region',
                      'Calculate total gross margin for all customers',
                    ]
                ).map((chip, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => {
                      setNlPrompt(chip);
                      setExecutionResult(null);
                    }}
                    className="px-2.5 py-1 rounded-lg bg-surface-raised hover:bg-surface-hover border border-main text-secondary hover:text-primary text-[11px] transition-colors"
                  >
                    "{chip}"
                  </button>
                ))}
              </div>

              <div className="flex gap-2">
                <input
                  type="text"
                  value={nlPrompt}
                  onChange={(e) => setNlPrompt(e.target.value)}
                  placeholder={
                    selectedTarget === 'DATABRICKS'
                      ? "Ask a question in plain English (e.g. 'Show total revenue by region in Databricks')..."
                      : "Ask a question in plain English (e.g. 'What is the gross margin by customer in Snowflake?')..."
                  }
                  className="flex-1 px-4 py-2.5 rounded-xl bg-surface-raised border border-main text-xs text-primary focus:outline-none focus:border-accent-blue font-medium"
                />
                <button
                  type="button"
                  disabled={loading}
                  onClick={handleResolveNlQuery}
                  className={`px-5 py-2.5 rounded-xl text-white font-semibold text-xs flex items-center gap-2 transition-colors disabled:opacity-50 ${
                    selectedTarget === 'DATABRICKS'
                      ? 'bg-amber-600 hover:bg-amber-500'
                      : 'bg-blue-600 hover:bg-blue-500'
                  }`}
                  title="Parse natural language prompt and generate governed SQL"
                >
                  {loading ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
                  Generate Governed Query
                </button>
              </div>
            </div>

            {/* Entity Disambiguation & Generated SQL Card */}
            {nlResolutionPlan && (
              <div className="p-5 rounded-xl bg-surface border border-main space-y-4">
                <div className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-primary">Semantic Disambiguation & Entity Resolution</span>
                    <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400">
                      Confidence: {(nlResolutionPlan.confidence_score * 100).toFixed(0)}%
                    </span>
                  </div>
                  <span className="text-tertiary text-[11px]">
                    Primary Table: <strong className="text-secondary">{nlResolutionPlan.resolved_tables?.[0] || 'FACT'}</strong>
                  </span>
                </div>

                {/* Disambiguation Token Badges */}
                <div className="flex flex-wrap items-center gap-4 text-xs p-3 rounded-lg bg-surface-raised border border-main">
                  <div>
                    <span className="text-tertiary mr-1.5 font-medium">Metrics:</span>
                    {nlResolutionPlan.detected_metrics?.length > 0 ? (
                      nlResolutionPlan.detected_metrics.map((m) => (
                        <span key={m} className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 font-mono text-[11px] mr-1.5 border border-emerald-500/20 font-semibold">
                          {m}
                        </span>
                      ))
                    ) : (
                      <span className="text-tertiary italic text-[11px]">None detected</span>
                    )}
                  </div>
                  <div>
                    <span className="text-tertiary mr-1.5 font-medium">Dimensions:</span>
                    {nlResolutionPlan.detected_dimensions?.length > 0 ? (
                      nlResolutionPlan.detected_dimensions.map((d) => (
                        <span key={d} className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 font-mono text-[11px] mr-1.5 border border-blue-500/20 font-semibold">
                          {d}
                        </span>
                      ))
                    ) : (
                      <span className="text-tertiary italic text-[11px]">None detected</span>
                    )}
                  </div>
                  {nlResolutionPlan.filters?.length > 0 && (
                    <div>
                      <span className="text-tertiary mr-1.5 font-medium">Filters:</span>
                      {nlResolutionPlan.filters.map((f, i) => (
                        <span key={i} className="px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 font-mono text-[11px] mr-1.5 border border-purple-500/20">
                          {f}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* SQL Editor / Viewer Box */}
                <div className="relative rounded-lg bg-surface-raised border border-main p-4 overflow-hidden">
                  <div className="flex items-center justify-between pb-2 mb-2 border-b border-main text-xs">
                    <span className="text-secondary font-mono flex items-center gap-1.5">
                      <Code2 size={13} className="text-accent-blue" />
                      Compiled {selectedTarget} SQL
                    </span>
                    <button
                      type="button"
                      onClick={() => handleCopySql(customSql || nlResolutionPlan.generated_sql)}
                      className="px-2.5 py-1 rounded bg-surface hover:bg-surface-hover text-secondary hover:text-primary border border-main text-xs flex items-center gap-1 transition-colors"
                      title="Copy SQL to clipboard"
                    >
                      {copiedSql ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
                      {copiedSql ? 'Copied' : 'Copy SQL'}
                    </button>
                  </div>
                  <textarea
                    value={customSql}
                    onChange={(e) => setCustomSql(e.target.value)}
                    rows={8}
                    className="w-full bg-transparent font-mono text-xs text-primary focus:outline-none resize-y leading-relaxed"
                    spellCheck={false}
                  />
                </div>

                {/* Execution Trigger Bar */}
                <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-2">
                  <p className="text-xs text-secondary italic m-0">
                    Resolution: {nlResolutionPlan.explanation}
                  </p>

                  <button
                    type="button"
                    disabled={executingQuery}
                    onClick={handleExecuteQuery}
                    className={`px-5 py-2.5 rounded-xl text-white font-semibold text-xs flex items-center justify-center gap-2 transition-all shadow-sm disabled:opacity-50 ${
                      selectedTarget === 'DATABRICKS'
                        ? 'bg-amber-600 hover:bg-amber-500'
                        : selectedTarget === 'LOCAL'
                        ? 'bg-emerald-600 hover:bg-emerald-500'
                        : 'bg-blue-600 hover:bg-blue-500'
                    }`}
                  >
                    {executingQuery ? (
                      <RefreshCw size={14} className="animate-spin" />
                    ) : (
                      <Play size={14} fill="currentColor" />
                    )}
                    {selectedTarget === 'SNOWFLAKE'
                      ? 'Execute on Snowflake'
                      : selectedTarget === 'DATABRICKS'
                      ? 'Execute on Databricks'
                      : 'Run Local Query'}
                  </button>
                </div>
              </div>
            )}

            {/* Live Query Results Table */}
            {executionResult && (
              <div className="p-5 rounded-xl bg-surface border border-main space-y-4">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-main pb-3">
                  <div className="flex items-center gap-2">
                    <Table size={16} className="text-accent-blue" />
                    <span className="font-bold text-primary text-sm">Query Results & Data Preview</span>
                    <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-accent-blue/10 text-accent-blue border border-accent-blue/20">
                      {executionResult.row_count} rows · {executionResult.execution_time_ms}ms
                    </span>
                    {executionResult.is_simulated && (
                      <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-surface-raised text-secondary border border-main">
                        Verified Preview
                      </span>
                    )}
                  </div>

                  {/* Accuracy Multiplier Action Button */}
                  <button
                    type="button"
                    disabled={savingVerified}
                    onClick={handleSaveVerifiedQuery}
                    className="px-3.5 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors border border-amber-500/30 text-amber-300 hover:bg-amber-500/10 disabled:opacity-50"
                    title={
                      selectedTarget === 'DATABRICKS'
                        ? 'Save as a verified benchmark query in Databricks AI/BI Genie Space'
                        : 'Save as a verified query in Snowflake Cortex Analyst YAML'
                    }
                  >
                    {savingVerified ? (
                      <RefreshCw size={13} className="animate-spin" />
                    ) : (
                      <Star size={13} fill="currentColor" />
                    )}
                    {selectedTarget === 'DATABRICKS'
                      ? 'Save to Databricks Genie Benchmark'
                      : 'Add to Verified Queries (Cortex YAML)'}
                  </button>
                </div>

                {executionResult.message && (
                  <div className="text-xs p-2.5 rounded-lg bg-surface-raised border border-main text-secondary flex items-center gap-2">
                    <CheckCircle2 size={14} className="text-emerald-400 flex-shrink-0" />
                    <span>{executionResult.message}</span>
                  </div>
                )}

                {verifiedSuccessMsg && (
                  <div className="text-xs p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <CheckCheck size={16} />
                      <span className="font-semibold">{verifiedSuccessMsg}</span>
                    </div>
                    <span className="text-[10px] uppercase font-bold text-emerald-400 bg-emerald-950/50 px-2 py-0.5 rounded">
                      Accuracy Multiplier Active
                    </span>
                  </div>
                )}

                {/* Data Preview Table */}
                {executionResult.columns?.length > 0 ? (
                  <div className="rounded-lg border border-main overflow-x-auto bg-surface-raised">
                    <table className="w-full text-left text-xs">
                      <thead>
                        <tr className="border-b border-main bg-surface text-secondary uppercase font-mono text-[10px] tracking-wider">
                          {executionResult.columns.map((col, idx) => (
                            <th key={idx} className="px-4 py-2.5 font-bold">
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-main">
                        {executionResult.rows.map((row, rIdx) => (
                          <tr key={rIdx} className="hover:bg-surface-hover/50 transition-colors">
                            {row.map((val, cIdx) => (
                              <td key={cIdx} className="px-4 py-2.5 text-primary font-mono">
                                {val !== null && val !== undefined ? String(val) : '—'}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="text-center py-6 text-xs text-tertiary">
                    {executionResult.error || 'No rows returned.'}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ══════════════════════════════════════════════════════════════════════════
          SLIDE-OVER DOMAIN GLOSSARY INSPECTOR DRAWER
         ══════════════════════════════════════════════════════════════════════════ */}
      {inspectingDomain && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm transition-opacity">
          <div className="w-full max-w-2xl bg-surface border-l border-main h-full flex flex-col shadow-2xl animate-in slide-in-from-right duration-200">
            {/* Drawer Header */}
            <div className="p-5 border-b border-main flex items-center justify-between bg-surface-raised">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-base text-primary m-0">{inspectingDomain} Domain Glossary</h3>
                  <span className="text-[10px] px-2 py-0.5 font-mono rounded bg-surface border border-main text-secondary">
                    v{inspectingDomainData?.version || '1.0'}
                  </span>
                </div>
                <p className="text-xs text-secondary mt-1 m-0">
                  Steward: <strong className="text-primary">{inspectingDomainData?.owner || 'Enterprise Team'}</strong>
                </p>
              </div>

              <button
                type="button"
                onClick={() => setInspectingDomain(null)}
                className="p-1.5 rounded-lg text-tertiary hover:text-primary hover:bg-surface transition-colors"
                title="Close glossary inspector"
              >
                <X size={18} />
              </button>
            </div>

            {/* Drawer Inner Tabs */}
            <div className="flex items-center gap-1 px-5 border-b border-main bg-surface text-xs">
              <button
                type="button"
                onClick={() => setInspectorActiveTab('metrics')}
                className={`py-2.5 px-3 font-semibold border-b-2 transition-colors ${
                  inspectorActiveTab === 'metrics'
                    ? 'border-accent-blue text-accent-blue'
                    : 'border-transparent text-secondary hover:text-primary'
                }`}
              >
                Metrics & Formulas ({inspectingDomainData?.parsed?.metrics?.length || 0})
              </button>
              <button
                type="button"
                onClick={() => setInspectorActiveTab('concepts')}
                className={`py-2.5 px-3 font-semibold border-b-2 transition-colors ${
                  inspectorActiveTab === 'concepts'
                    ? 'border-accent-blue text-accent-blue'
                    : 'border-transparent text-secondary hover:text-primary'
                }`}
              >
                Entities & Tables ({inspectingDomainData?.parsed?.concepts?.length || 0})
              </button>
              <button
                type="button"
                onClick={() => setInspectorActiveTab('yaml')}
                className={`py-2.5 px-3 font-semibold border-b-2 transition-colors ${
                  inspectorActiveTab === 'yaml'
                    ? 'border-accent-blue text-accent-blue'
                    : 'border-transparent text-secondary hover:text-primary'
                }`}
              >
                YAML Definition
              </button>
            </div>

            {/* Drawer Body Content */}
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {/* Metrics Tab */}
              {inspectorActiveTab === 'metrics' && (
                <div className="space-y-3">
                  <div className="text-xs text-secondary">
                    Authoritative KPIs owned by the <strong className="text-primary">{inspectingDomain}</strong> team.
                  </div>
                  {inspectingDomainData?.parsed?.metrics?.length > 0 ? (
                    inspectingDomainData.parsed.metrics.map((m) => (
                      <div key={m.name} className="p-4 rounded-xl bg-surface-raised border border-main text-xs space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="font-bold text-primary text-sm flex items-center gap-1.5">
                            <span className="w-2 h-2 rounded-full bg-emerald-400" />
                            {m.label || m.name}
                          </span>
                          <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-surface border border-main text-secondary uppercase">
                            {m.aggregation_type || 'SUM'}
                          </span>
                        </div>
                        <p className="text-secondary text-[11px] m-0">{m.description || 'No description provided.'}</p>
                        {m.sql_expression ? (
                          <div className="p-2 rounded bg-surface border border-main font-mono text-[11px] text-emerald-400">
                            {m.sql_expression}
                          </div>
                        ) : (
                          <p className="text-tertiary text-[11px] italic m-0">
                            No formula in the ontology — the SQL comes from the mapped BI measure.
                          </p>
                        )}
                        {m.synonyms?.length > 0 && (
                          <div className="flex flex-wrap items-center gap-1 pt-1">
                            <span className="text-[10px] text-tertiary font-semibold mr-1">Synonyms:</span>
                            {m.synonyms.map((s, sIdx) => (
                              <span key={sIdx} className="px-2 py-0.5 rounded bg-surface border border-main text-[10px] text-secondary">
                                {s}
                              </span>
                            ))}
                          </div>
                        )}
                        <div className="pt-2 flex justify-end">
                          <button
                            type="button"
                            onClick={() => handleJumpToAiTest(m.label || m.name, m.sql_expression)}
                            className="px-2.5 py-1 rounded bg-accent-blue/10 hover:bg-accent-blue/20 text-accent-blue text-[11px] font-semibold flex items-center gap-1 transition-colors"
                          >
                            <Sparkles size={12} /> Test in AI Studio
                          </button>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="text-center py-8 text-xs text-tertiary">No metrics defined for this domain.</div>
                  )}
                </div>
              )}

              {/* Concepts Tab */}
              {inspectorActiveTab === 'concepts' && (
                <div className="space-y-3">
                  <div className="text-xs text-secondary">
                    Physical tables and entity structures mapped in <strong className="text-primary">{inspectingDomain}</strong>.
                  </div>
                  {inspectingDomainData?.parsed?.concepts?.length > 0 ? (
                    inspectingDomainData.parsed.concepts.map((c) => (
                      <div key={c.name} className="p-4 rounded-xl bg-surface-raised border border-main text-xs space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="font-bold text-primary text-sm flex items-center gap-1.5">
                            <Table size={14} className="text-accent-blue" />
                            {c.name}
                          </span>
                          {(c.physical_table || c.maps_to?.length > 0) && (
                            <span
                              className="font-mono text-[11px] px-2 py-0.5 rounded bg-surface border border-main text-secondary"
                              title="Model table this concept is bound to"
                            >
                              {c.physical_table || c.maps_to.join(', ')}
                            </span>
                          )}
                        </div>
                        <p className="text-secondary text-[11px] m-0">{c.description || 'Core physical concept'}</p>
                        {c.attributes?.length > 0 && (
                          <div className="space-y-1 pt-1">
                            <span className="text-[10px] text-tertiary font-semibold uppercase block">Attributes & Columns</span>
                            <div className="grid grid-cols-2 gap-1.5 font-mono text-[11px]">
                              {c.attributes.map((attr, aIdx) => (
                                <div key={aIdx} className="p-1.5 rounded bg-surface border border-main flex items-center justify-between">
                                  <span className="text-primary">{attr.name}</span>
                                  {/* The column binding is what decides where the ontology lands;
                                      data_type defaults to VARCHAR when the YAML omits it, so it
                                      is only shown when there is no binding. */}
                                  <span className="text-tertiary text-[10px]" title={attr.maps_to?.length ? 'Bound model column' : 'Declared type'}>
                                    {attr.maps_to?.length ? `→ ${attr.maps_to.join(', ')}` : attr.data_type}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="text-center py-8 text-xs text-tertiary">No concepts mapped for this domain.</div>
                  )}
                </div>
              )}

              {/* YAML Editor Tab */}
              {inspectorActiveTab === 'yaml' && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-secondary font-mono">{inspectingDomain.toLowerCase()}.ontology.yaml</span>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={handleValidateYaml}
                        className="px-3 py-1 rounded bg-surface hover:bg-surface-hover border border-main text-xs text-secondary hover:text-primary transition-colors flex items-center gap-1"
                      >
                        <ShieldCheck size={13} className="text-accent-blue" /> Validate Schema
                      </button>
                      <button
                        type="button"
                        disabled={loading}
                        onClick={() => handleSyncWithOsi(inspectingDomain)}
                        className="px-3 py-1 rounded bg-surface hover:bg-surface-hover border border-main text-xs text-secondary hover:text-primary transition-colors flex items-center gap-1"
                        title="Re-sync tables and calculation expressions from source OSI model while preserving your comments and governance definitions"
                      >
                        <RefreshCw size={12} className={loading ? 'animate-spin' : 'text-amber-400'} /> Sync from OSI
                      </button>
                      <button
                        type="button"
                        disabled={loading}
                        onClick={handleSaveDomain}
                        title="Saves the domain. Projects that attach it pick up the change on their next run."
                        className="px-3.5 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs flex items-center gap-1 transition-colors disabled:opacity-50"
                      >
                        {loading ? <RefreshCw size={13} className="animate-spin" /> : <Check size={13} />} Save domain
                      </button>
                    </div>
                  </div>

                  {validationResult && (
                    <div className={`p-3 rounded-lg text-xs border ${
                      validationResult.valid ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-red-500/10 text-red-400 border-red-500/20'
                    }`}>
                      {validationResult.valid ? (
                        <span>Valid schema: {validationResult.concept_count} concepts, {validationResult.metric_count} metrics.</span>
                      ) : (
                        <span>Validation errors: {validationResult.errors?.join(', ')}</span>
                      )}
                    </div>
                  )}

                  <textarea
                    value={editorYaml}
                    onChange={(e) => setEditorYaml(e.target.value)}
                    rows={22}
                    className="w-full p-4 rounded-xl bg-surface-raised border border-main text-primary font-mono text-xs focus:outline-none focus:border-accent-blue resize-none leading-relaxed"
                    spellCheck={false}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════════
          INGEST DOMAIN YAML MODAL
         ══════════════════════════════════════════════════════════════════════════ */}
      {uploadModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg bg-surface border border-main rounded-2xl shadow-2xl p-6 space-y-5 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between pb-3 border-b border-main">
              <div className="flex items-center gap-2.5">
                <div className="p-2 rounded-lg bg-accent-blue/10 text-accent-blue">
                  <Upload size={18} />
                </div>
                <div>
                  <h3 className="font-bold text-sm text-primary m-0">Ingest Domain YAML</h3>
                  <p className="text-[11px] text-secondary m-0 mt-0.5">Upload departmental semantic definitions into the mesh.</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  setUploadError(null);
                  setUploadModalOpen(false);
                }}
                className="p-1 rounded-lg text-tertiary hover:text-primary transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            <div
              onClick={() => fileInputRef.current?.click()}
              className="border-2 border-dashed border-main hover:border-accent-blue/50 rounded-xl p-8 flex flex-col items-center justify-center text-center cursor-pointer bg-surface-raised/40 hover:bg-surface-raised/70 transition-all"
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".yaml,.yml"
                onChange={(e) => {
                  setSelectedFiles(Array.from(e.target.files || []));
                  setUploadError(null);
                }}
                className="hidden"
              />
              <FileCode size={32} className="text-accent-blue mb-2" />
              <span className="font-bold text-xs text-primary block">Click or Drag & Drop YAML Files</span>
              <span className="text-[11px] text-tertiary mt-1 block">Supports .yaml and .yml files authored by domain teams</span>
            </div>

            {selectedFiles.length > 0 && (
              <div className="space-y-2 max-h-40 overflow-y-auto">
                <span className="text-[10px] text-tertiary uppercase font-bold tracking-wider">
                  Selected Files ({selectedFiles.length})
                </span>
                {selectedFiles.map((file, fIdx) => (
                  <div key={fIdx} className="p-2 rounded-lg bg-surface-raised border border-main text-xs flex items-center justify-between">
                    <span className="font-medium text-primary truncate max-w-xs">{file.name}</span>
                    <span className="text-emerald-400 font-semibold text-[11px]">Ready</span>
                  </div>
                ))}
              </div>
            )}

            {uploadError && (
              <div
                role="alert"
                className="p-3 rounded-lg border border-red-500/40 bg-red-500/10 text-xs text-red-500 flex items-start gap-2 max-h-48 overflow-y-auto"
              >
                <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                <div>
                  <span className="font-bold block mb-1">The file was rejected. Fix it and upload again:</span>
                  <span style={{ whiteSpace: 'pre-line' }}>{uploadError}</span>
                </div>
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-3 border-t border-main">
              <button
                type="button"
                onClick={() => {
                  setSelectedFiles([]);
                  setUploadError(null);
                  setUploadModalOpen(false);
                }}
                className="px-4 py-2 rounded-lg border border-main text-xs font-semibold text-secondary hover:text-primary transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={loading || selectedFiles.length === 0}
                onClick={handleFileUpload}
                className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold flex items-center gap-1.5 transition-colors disabled:opacity-50"
              >
                {loading ? <RefreshCw size={14} className="animate-spin" /> : <Upload size={14} />}
                Ingest & Compile
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════════
          GENERATE / SYNC FROM OSI MODAL
         ══════════════════════════════════════════════════════════════════════════ */}
      {bootstrapModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg bg-surface border border-main rounded-2xl shadow-2xl p-6 space-y-5 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between pb-3 border-b border-main">
              <div className="flex items-center gap-2.5">
                <div className="p-2 rounded-lg bg-accent-blue/10 text-accent-blue">
                  <Sparkles size={18} />
                </div>
                <div>
                  <h3 className="font-bold text-sm text-primary m-0">Generate Ontology from OSI Model</h3>
                  <p className="text-[11px] text-secondary m-0 mt-0.5">
                    Extract concepts, attributes, metrics, and relationships from existing BI models.
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setBootstrapModalOpen(false)}
                className="p-1 rounded-lg text-tertiary hover:text-primary transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            <div className="space-y-4 text-xs">
              <div>
                <label className="font-bold text-secondary uppercase tracking-wider block text-[11px] mb-1">
                  Source BI Project / Model
                </label>
                <select
                  value={bootstrapProject}
                  onChange={(e) => setBootstrapProject(e.target.value)}
                  className="w-full p-2.5 rounded-xl bg-surface-raised border border-main text-primary text-xs font-medium focus:outline-none focus:border-accent-blue"
                >
                  <option value="">Select a translated BI project...</option>
                  {projectsList.map((p) => (
                    <option key={p.id || p.name} value={p.id || p.name}>
                      {p.name || p.id}
                    </option>
                  ))}
                </select>
                <span className="text-[11px] text-tertiary block mt-1">
                  Uses the latest OSI intermediate snapshot compiled from Power BI, Fabric, or Snowflake.
                </span>
              </div>

              <div>
                <label className="font-bold text-secondary uppercase tracking-wider block text-[11px] mb-1">
                  Target Domain Name (Optional)
                </label>
                <input
                  type="text"
                  value={bootstrapDomain}
                  onChange={(e) => setBootstrapDomain(e.target.value)}
                  placeholder="e.g. Finance, Sales, SupplyChain (Defaults to project name)"
                  className="w-full p-2.5 rounded-xl bg-surface-raised border border-main text-primary text-xs focus:outline-none focus:border-accent-blue"
                />
              </div>

              <div className="p-3.5 rounded-xl bg-surface-raised/70 border border-main space-y-1.5 text-[11px]">
                <span className="font-bold text-primary flex items-center gap-1.5">
                  <ShieldCheck size={14} className="text-emerald-400" />
                  Human-Enrichment Preserving Merge
                </span>
                <p className="text-secondary leading-relaxed m-0">
                  If this domain already exists, your manually authored business rules, data stewards, custom synonyms, and metric overrides will be <strong>preserved</strong>. Only updated calculations, new tables, and columns will be refreshed.
                </p>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-3 border-t border-main">
              <button
                type="button"
                onClick={() => setBootstrapModalOpen(false)}
                className="px-4 py-2 rounded-lg border border-main text-xs font-semibold text-secondary hover:text-primary transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={bootstrapping || !bootstrapProject}
                onClick={handleBootstrapFromOsi}
                className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold flex items-center gap-1.5 transition-colors disabled:opacity-50"
              >
                {bootstrapping ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
                Generate & Save Domain
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

