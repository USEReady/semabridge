import React, { useState, useEffect, useRef } from 'react';
import {
  Layers, Upload, FileCode, Sparkles, CheckCircle2,
  AlertTriangle, RefreshCw, Copy, Check, Trash2, Plus,
  Search, Database, Network, ShieldCheck, ChevronRight
} from 'lucide-react';

export default function OntologyDomainManager() {
  const [activeTab, setActiveTab] = useState('domains'); // 'domains' | 'upload' | 'editor' | 'ai'
  const [loading, setLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState(null);

  // Domains & Governance State
  const [domainData, setDomainData] = useState({
    domains: [],
    total_concepts: 0,
    total_metrics: 0,
    total_relationships: 0,
    collisions: [],
    unresolved_references: [],
  });

  // Editor State
  const [selectedDomain, setSelectedDomain] = useState('Finance');
  const [editorYaml, setEditorYaml] = useState('');
  const [validationResult, setValidationResult] = useState(null);

  // Upload State
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploadProgress, setUploadProgress] = useState(null);
  const fileInputRef = useRef(null);

  // Embedded AI Playground State
  const [aiSearchQuery, setAiSearchQuery] = useState('');
  const [aiSearchResults, setAiSearchResults] = useState([]);
  const [nlPrompt, setNlPrompt] = useState('What is the gross margin and ARR by Customer in 2024?');
  const [nlResolutionPlan, setNlResolutionPlan] = useState(null);
  const [copiedSql, setCopiedSql] = useState(false);
  const [aiStatus, setAiStatus] = useState(null);

  // Fetch initial domain mesh & AI status
  useEffect(() => {
    fetchDomains();
    fetchAiStatus();
  }, []);

  const fetchDomains = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/ontology/domains');
      if (res.ok) {
        const data = await res.json();
        setDomainData(data);
        if (data.domains && data.domains.length > 0 && !editorYaml) {
          loadDomainForEditor(data.domains[0].domain);
        }
      }
    } catch (err) {
      console.error('Failed to load domains:', err);
    } finally {
      setLoading(false);
    }
  };

  const fetchAiStatus = async () => {
    try {
      const res = await fetch('/api/ai/status');
      if (res.ok) {
        const data = await res.json();
        setAiStatus(data);
      }
    } catch (err) {
      console.error('Failed to load AI status:', err);
    }
  };

  const loadDomainForEditor = async (domainName) => {
    setSelectedDomain(domainName);
    try {
      const res = await fetch(`/api/ontology/domains/${encodeURIComponent(domainName)}`);
      if (res.ok) {
        const data = await res.json();
        setEditorYaml(data.yaml_content);
        setValidationResult(null);
      }
    } catch (err) {
      console.error('Failed to load domain YAML:', err);
    }
  };

  const handleValidateYaml = async () => {
    try {
      const res = await fetch('/api/ontology/domains/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml_content: editorYaml, domain_override: selectedDomain }),
      });
      const data = await res.json();
      setValidationResult(data);
    } catch (err) {
      setValidationResult({ valid: false, errors: [String(err)] });
    }
  };

  const handleSaveDomain = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/ontology/domains/${encodeURIComponent(selectedDomain)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml_content: editorYaml }),
      });
      if (res.ok) {
        setStatusMessage({ type: 'success', text: `Domain '${selectedDomain}' saved and deployed!` });
        fetchDomains();
        fetchAiStatus();
      } else {
        const err = await res.json();
        setStatusMessage({ type: 'error', text: err.detail || 'Failed to save domain' });
      }
    } catch (err) {
      setStatusMessage({ type: 'error', text: String(err) });
    } finally {
      setLoading(false);
      setTimeout(() => setStatusMessage(null), 4000);
    }
  };

  const handleFileUpload = async (filesToUpload) => {
    const files = Array.from(filesToUpload || selectedFiles);
    if (!files.length) return;

    setLoading(true);
    setUploadProgress('Uploading and compiling federated ontologies…');

    const formData = new FormData();
    files.forEach((f) => formData.append('files', f));

    try {
      const res = await fetch('/api/ontology/domains/upload', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (res.ok) {
        setStatusMessage({
          type: 'success',
          text: `Successfully ingested ${data.uploaded_domains.length} domain ontologies!`,
        });
        setSelectedFiles([]);
        fetchDomains();
        fetchAiStatus();
        if (data.uploaded_domains.length > 0) {
          loadDomainForEditor(data.uploaded_domains[0]);
        }
      } else {
        setStatusMessage({ type: 'error', text: data.detail || 'Upload failed.' });
      }
    } catch (err) {
      setStatusMessage({ type: 'error', text: String(err) });
    } finally {
      setLoading(false);
      setUploadProgress(null);
      setTimeout(() => setStatusMessage(null), 4000);
    }
  };

  const handleAiSearch = async (e) => {
    const q = e ? e.target.value : aiSearchQuery;
    setAiSearchQuery(q);
    if (!q.trim()) {
      setAiSearchResults([]);
      return;
    }
    try {
      const res = await fetch('/api/ai/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, limit: 6 }),
      });
      if (res.ok) {
        const hits = await res.json();
        setAiSearchResults(hits);
      }
    } catch (err) {
      console.error('AI search failed:', err);
    }
  };

  const handleResolveNlQuery = async () => {
    if (!nlPrompt.trim()) return;
    setLoading(true);
    try {
      const res = await fetch('/api/ai/nl-query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: nlPrompt, dialect: 'SNOWFLAKE' }),
      });
      if (res.ok) {
        const plan = await res.json();
        setNlResolutionPlan(plan);
      }
    } catch (err) {
      console.error('NL resolution failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCopySql = (sqlText) => {
    navigator.clipboard.writeText(sqlText);
    setCopiedSql(true);
    setTimeout(() => setCopiedSql(false), 2000);
  };

  return (
    <div className="flex flex-col h-full bg-app text-primary overflow-hidden">
      {/* ── Top Header Banner ── */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border-main bg-surface-raised flex-shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <Layers className="text-blue-500" size={22} />
            <h1 className="text-lg font-bold tracking-tight">Enterprise Domain Ontologies & Local AI</h1>
            <span className="px-2 py-0.5 text-xs font-semibold rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">
              Data Mesh
            </span>
            <span className="px-2 py-0.5 text-xs font-semibold rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1">
              <Sparkles size={12} />
              Zero-Credit AI Active
            </span>
          </div>
          <p className="text-xs text-secondary mt-0.5">
            Federated team ownership (Finance, Sales, Marketing), multi-YAML ingestion, collision governance, and embedded semantic search.
          </p>
        </div>

        {/* Global Stats */}
        <div className="flex items-center gap-4 text-xs">
          <div className="px-3 py-1.5 rounded-lg bg-surface border border-border-main text-center">
            <span className="text-secondary block">Domains</span>
            <span className="font-bold text-sm text-primary">{domainData.domains.length}</span>
          </div>
          <div className="px-3 py-1.5 rounded-lg bg-surface border border-border-main text-center">
            <span className="text-secondary block">Concepts</span>
            <span className="font-bold text-sm text-primary">{domainData.total_concepts}</span>
          </div>
          <div className="px-3 py-1.5 rounded-lg bg-surface border border-border-main text-center">
            <span className="text-secondary block">Metrics</span>
            <span className="font-bold text-sm text-primary">{domainData.total_metrics}</span>
          </div>
          {domainData.collisions.length > 0 && (
            <div className="px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-center text-amber-400">
              <span className="block font-medium">Collisions</span>
              <span className="font-bold text-sm">{domainData.collisions.length}</span>
            </div>
          )}
          <button
            onClick={fetchDomains}
            className="p-2 rounded-lg bg-surface hover:bg-surface-hover border border-border-main text-secondary hover:text-primary transition-colors"
            title="Refresh Ontology"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* ── Status Toast / Banner ── */}
      {statusMessage && (
        <div
          className={`px-6 py-2.5 text-xs font-medium flex items-center gap-2 border-b transition-all ${
            statusMessage.type === 'success'
              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
              : 'bg-red-500/10 text-red-400 border-red-500/20'
          }`}
        >
          {statusMessage.type === 'success' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          {statusMessage.text}
        </div>
      )}

      {/* ── Navigation Tabs ── */}
      <div className="flex items-center gap-1 px-6 border-b border-border-main bg-surface flex-shrink-0 text-sm">
        <button
          onClick={() => setActiveTab('domains')}
          className={`flex items-center gap-2 py-3 px-4 font-medium border-b-2 transition-colors ${
            activeTab === 'domains'
              ? 'border-blue-500 text-blue-400'
              : 'border-transparent text-secondary hover:text-primary'
          }`}
        >
          <Network size={16} />
          Domain Mesh & Governance
          <span className="ml-1 px-1.5 py-0.2 rounded-full text-[11px] bg-surface-raised text-secondary">
            {domainData.domains.length}
          </span>
        </button>

        <button
          onClick={() => setActiveTab('upload')}
          className={`flex items-center gap-2 py-3 px-4 font-medium border-b-2 transition-colors ${
            activeTab === 'upload'
              ? 'border-blue-500 text-blue-400'
              : 'border-transparent text-secondary hover:text-primary'
          }`}
        >
          <Upload size={16} />
          Multi-File YAML Ingestion
        </button>

        <button
          onClick={() => setActiveTab('editor')}
          className={`flex items-center gap-2 py-3 px-4 font-medium border-b-2 transition-colors ${
            activeTab === 'editor'
              ? 'border-blue-500 text-blue-400'
              : 'border-transparent text-secondary hover:text-primary'
          }`}
        >
          <FileCode size={16} />
          Live YAML Editor
        </button>

        <button
          onClick={() => setActiveTab('ai')}
          className={`flex items-center gap-2 py-3 px-4 font-medium border-b-2 transition-colors ${
            activeTab === 'ai'
              ? 'border-blue-500 text-blue-400'
              : 'border-transparent text-secondary hover:text-primary'
          }`}
        >
          <Sparkles size={16} className="text-amber-400" />
          Embedded AI & NL-to-SQL
        </button>
      </div>

      {/* ── Tab Content ── */}
      <div className="flex-1 overflow-y-auto p-6 min-h-0 bg-app">
        {/* TAB 1: DOMAIN MESH */}
        {activeTab === 'domains' && (
          <div className="space-y-6 max-w-6xl mx-auto">
            {/* Collision Alert Banner */}
            {domainData.collisions.length > 0 && (
              <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30">
                <div className="flex items-center gap-2 text-amber-400 font-semibold mb-2 text-sm">
                  <AlertTriangle size={17} />
                  Enterprise Metric & Concept Collisions Detected ({domainData.collisions.length})
                </div>
                <div className="space-y-2 text-xs">
                  {domainData.collisions.map((col, idx) => (
                    <div key={idx} className="p-3 rounded-lg bg-surface border border-amber-500/20 text-secondary">
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-bold text-primary text-sm">
                          {col.item_type.toUpperCase()}: {col.item_name}
                        </span>
                        <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-300">
                          {col.severity}
                        </span>
                      </div>
                      <p className="text-xs text-amber-300/90 mb-2">{col.message}</p>
                      <div className="grid grid-cols-2 gap-2 text-[11px] font-mono bg-surface-raised p-2 rounded">
                        {Object.entries(col.expressions || {}).map(([dom, expr]) => (
                          <div key={dom}>
                            <span className="text-blue-400 font-bold block">{dom} Definition:</span>
                            <span className="text-primary">{expr || 'N/A'}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Domain Cards Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {domainData.domains.map((d) => (
                <div
                  key={d.domain}
                  className="rounded-xl border border-border-main bg-surface p-5 hover:border-blue-500/50 transition-all flex flex-col justify-between shadow-sm"
                >
                  <div>
                    <div className="flex items-start justify-between mb-2">
                      <div>
                        <h3 className="font-bold text-base text-primary flex items-center gap-2">
                          {d.domain}
                          <span className="text-[10px] px-1.5 py-0.2 font-mono rounded bg-surface-raised text-secondary border border-border-main">
                            v{d.version}
                          </span>
                        </h3>
                        <p className="text-xs text-secondary mt-1 line-clamp-2">
                          {d.description || 'No description provided.'}
                        </p>
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2 my-4 text-center">
                      <div className="p-2 rounded-lg bg-surface-raised border border-border-main">
                        <span className="text-[10px] text-secondary uppercase block">Concepts</span>
                        <span className="font-bold text-sm text-primary">{d.concept_count}</span>
                      </div>
                      <div className="p-2 rounded-lg bg-surface-raised border border-border-main">
                        <span className="text-[10px] text-secondary uppercase block">Metrics</span>
                        <span className="font-bold text-sm text-primary">{d.metric_count}</span>
                      </div>
                      <div className="p-2 rounded-lg bg-surface-raised border border-border-main">
                        <span className="text-[10px] text-secondary uppercase block">Joins</span>
                        <span className="font-bold text-sm text-primary">{d.relationship_count}</span>
                      </div>
                    </div>

                    <div className="text-xs text-secondary space-y-1 mb-4">
                      <div>
                        <span className="text-tertiary">Owner: </span>
                        <span className="text-primary font-medium">{d.owner || 'Enterprise Team'}</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 pt-3 border-t border-border-main">
                    <button
                      onClick={() => {
                        loadDomainForEditor(d.domain);
                        setActiveTab('editor');
                      }}
                      className="flex-1 py-1.5 px-3 rounded-lg bg-blue-600/10 hover:bg-blue-600/20 text-blue-400 font-medium text-xs flex items-center justify-center gap-1 transition-colors"
                    >
                      <FileCode size={14} />
                      Edit YAML
                    </button>
                    <button
                      onClick={() => {
                        setAiSearchQuery(d.domain);
                        setActiveTab('ai');
                        handleAiSearch({ target: { value: d.domain } });
                      }}
                      className="py-1.5 px-3 rounded-lg bg-surface-raised hover:bg-surface-hover text-secondary hover:text-primary text-xs font-medium transition-colors"
                      title="Inspect Concepts"
                    >
                      <Search size={14} />
                    </button>
                  </div>
                </div>
              ))}

              {/* Add Domain Card */}
              <button
                onClick={() => {
                  setSelectedDomain('Marketing');
                  setEditorYaml(`domain: Marketing\nowner: marketing-analytics@corp.com\nversion: 1.0.0\ndescription: Campaign performance, customer acquisition cost, and attribution.\nconcepts: []\nmetrics: []\nrelationships: []\n`);
                  setActiveTab('editor');
                }}
                className="rounded-xl border-2 border-dashed border-border-main hover:border-blue-500/50 bg-surface/50 p-6 flex flex-col items-center justify-center gap-2 text-secondary hover:text-primary transition-all"
              >
                <Plus size={24} className="text-blue-500" />
                <span className="font-semibold text-sm">Add Domain Ontology</span>
                <span className="text-xs text-tertiary">Create new team domain or import YAML</span>
              </button>
            </div>
          </div>
        )}

        {/* TAB 2: MULTI-FILE UPLOAD */}
        {activeTab === 'upload' && (
          <div className="max-w-3xl mx-auto space-y-6">
            <div className="text-center mb-6">
              <h2 className="text-lg font-bold text-primary mb-1">Upload Domain Ontology YAML Files</h2>
              <p className="text-xs text-secondary">
                Drag and drop multiple team ontology files (e.g. <code className="font-mono text-blue-400">finance.yaml</code>,{' '}
                <code className="font-mono text-blue-400">sales.yaml</code>,{' '}
                <code className="font-mono text-blue-400">marketing.yaml</code>).
                SemaBridge validates schemas, resolves cross-domain joins, and builds the unified knowledge graph.
              </p>
            </div>

            {/* Drop Zone */}
            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (e.dataTransfer.files) {
                  setSelectedFiles(Array.from(e.dataTransfer.files));
                }
              }}
              onClick={() => fileInputRef.current?.click()}
              className="border-2 border-dashed border-border-main hover:border-blue-500/60 bg-surface rounded-2xl p-10 flex flex-col items-center justify-center cursor-pointer transition-all group"
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".yaml,.yml"
                className="hidden"
                onChange={(e) => {
                  if (e.target.files) {
                    setSelectedFiles(Array.from(e.target.files));
                  }
                }}
              />
              <div className="w-14 h-14 rounded-2xl bg-blue-500/10 text-blue-400 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
                <Upload size={28} />
              </div>
              <h3 className="font-bold text-base text-primary mb-1">Click to select or drag and drop YAML files</h3>
              <p className="text-xs text-secondary mb-3">Supports .yaml and .yml files for individual domains</p>
              <span className="px-3 py-1 text-xs font-semibold rounded-lg bg-surface-raised border border-border-main text-secondary">
                Browse Files
              </span>
            </div>

            {/* Selected Files List */}
            {selectedFiles.length > 0 && (
              <div className="p-4 rounded-xl bg-surface border border-border-main space-y-3">
                <div className="flex items-center justify-between text-xs font-semibold text-primary">
                  <span>Selected Files ({selectedFiles.length})</span>
                  <button
                    onClick={() => setSelectedFiles([])}
                    className="text-red-400 hover:text-red-300 transition-colors"
                  >
                    Clear All
                  </button>
                </div>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {selectedFiles.map((file, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between p-2.5 rounded-lg bg-surface-raised border border-border-main text-xs"
                    >
                      <div className="flex items-center gap-2">
                        <FileCode size={15} className="text-blue-400" />
                        <span className="font-medium text-primary">{file.name}</span>
                        <span className="text-tertiary">({(file.size / 1024).toFixed(1)} KB)</span>
                      </div>
                      <span className="text-emerald-400 font-medium flex items-center gap-1">
                        <CheckCircle2 size={13} /> Ready
                      </span>
                    </div>
                  ))}
                </div>

                <button
                  disabled={loading}
                  onClick={() => handleFileUpload()}
                  className="w-full py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
                >
                  {loading ? <RefreshCw size={15} className="animate-spin" /> : <Upload size={15} />}
                  Compile & Ingest All Selected Ontologies
                </button>
              </div>
            )}
          </div>
        )}

        {/* TAB 3: LIVE YAML EDITOR */}
        {activeTab === 'editor' && (
          <div className="max-w-6xl mx-auto flex flex-col h-full space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <label className="text-xs font-medium text-secondary">Active Domain:</label>
                <select
                  value={selectedDomain}
                  onChange={(e) => loadDomainForEditor(e.target.value)}
                  className="px-3 py-1.5 rounded-lg bg-surface border border-border-main text-xs text-primary font-semibold focus:outline-none focus:border-blue-500"
                >
                  {domainData.domains.map((d) => (
                    <option key={d.domain} value={d.domain}>
                      {d.domain}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={handleValidateYaml}
                  className="px-3 py-1.5 rounded-lg bg-surface hover:bg-surface-hover border border-border-main text-xs font-medium text-secondary hover:text-primary transition-colors flex items-center gap-1"
                >
                  <ShieldCheck size={14} className="text-blue-400" />
                  Validate Schema
                </button>
                <button
                  disabled={loading}
                  onClick={handleSaveDomain}
                  className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold transition-colors flex items-center gap-1"
                >
                  {loading ? <RefreshCw size={14} className="animate-spin" /> : <Check size={14} />}
                  Save & Deploy
                </button>
              </div>
            </div>

            {/* Validation Feedback */}
            {validationResult && (
              <div
                className={`p-3 rounded-lg text-xs border ${
                  validationResult.valid
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                    : 'bg-red-500/10 text-red-400 border-red-500/20'
                }`}
              >
                {validationResult.valid ? (
                  <div className="flex items-center gap-2 font-medium">
                    <CheckCircle2 size={15} />
                    Valid schema: {validationResult.concept_count} concepts, {validationResult.metric_count} metrics,{' '}
                    {validationResult.relationship_count} joins detected.
                  </div>
                ) : (
                  <div>
                    <span className="font-bold block mb-1">Validation Errors:</span>
                    <ul className="list-disc list-inside">
                      {validationResult.errors?.map((err, i) => (
                        <li key={i}>{err}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {/* Editor Textarea with syntax feel */}
            <div className="flex-1 rounded-xl border border-border-main bg-surface overflow-hidden flex flex-col min-h-[420px]">
              <div className="px-4 py-2 bg-surface-raised border-b border-border-main flex items-center justify-between text-xs text-secondary font-mono">
                <span>{selectedDomain.toLowerCase()}.ontology.yaml</span>
                <span>YAML 1.2</span>
              </div>
              <textarea
                value={editorYaml}
                onChange={(e) => setEditorYaml(e.target.value)}
                placeholder="Paste or write your team domain ontology YAML here..."
                className="w-full flex-1 p-4 bg-transparent text-primary font-mono text-xs focus:outline-none resize-none leading-relaxed"
                spellCheck={false}
              />
            </div>
          </div>
        )}

        {/* TAB 4: EMBEDDED AI PLAYGROUND */}
        {activeTab === 'ai' && (
          <div className="max-w-5xl mx-auto space-y-6">
            {/* Header info */}
            <div className="p-4 rounded-xl bg-gradient-to-r from-blue-900/20 via-purple-900/10 to-transparent border border-blue-500/20 flex items-center justify-between">
              <div>
                <h3 className="font-bold text-primary text-sm flex items-center gap-2">
                  <Sparkles size={16} className="text-amber-400" />
                  SemaBridge Embedded AI Layer (Zero Cortex Credits)
                </h3>
                <p className="text-xs text-secondary mt-0.5">
                  Full in-memory hybrid search (BM25 + Dense Semantic Vectors) and governed Natural Language to SQL compiler.
                </p>
              </div>
              {aiStatus && (
                <div className="text-right text-xs">
                  <span className="text-tertiary block">Indexed Objects</span>
                  <span className="font-bold text-blue-400">{aiStatus.total_indexed_documents} documents</span>
                </div>
              )}
            </div>

            {/* 1. Hybrid Semantic Search */}
            <div className="space-y-3">
              <label className="text-xs font-bold text-secondary uppercase tracking-wider block">
                1. Concept & Metric Semantic Search
              </label>
              <div className="relative">
                <Search size={16} className="absolute left-3.5 top-3 text-tertiary" />
                <input
                  type="text"
                  value={aiSearchQuery}
                  onChange={handleAiSearch}
                  placeholder="Search business terms, acronyms, formulas (e.g. 'gross margin', 'ARR', 'operating profit', 'client')..."
                  className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-surface border border-border-main text-xs text-primary focus:outline-none focus:border-blue-500 transition-colors"
                />
              </div>

              {/* Search Results */}
              {aiSearchResults.length > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
                  {aiSearchResults.map((hit) => (
                    <div
                      key={hit.item_id}
                      className="p-3 rounded-lg bg-surface border border-border-main text-xs space-y-1.5 hover:border-blue-500/40 transition-colors"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-primary flex items-center gap-1.5">
                          <span
                            className={`w-2 h-2 rounded-full ${
                              hit.item_type === 'METRIC' ? 'bg-emerald-400' : 'bg-blue-400'
                            }`}
                          />
                          {hit.name}
                        </span>
                        <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-surface-raised text-blue-400 border border-border-main">
                          {hit.match_type} ({(hit.score * 100).toFixed(0)}%)
                        </span>
                      </div>
                      <p className="text-xs text-secondary">{hit.description || 'No description available'}</p>
                      {hit.sql_expression && (
                        <div className="font-mono text-[11px] p-1.5 rounded bg-surface-raised text-emerald-400 border border-border-main">
                          {hit.sql_expression}
                        </div>
                      )}
                      <div className="flex items-center gap-2 text-[10px] text-tertiary pt-1">
                        <span>Domain: <strong className="text-secondary">{hit.domain || 'Enterprise'}</strong></span>
                        {hit.data_steward && (
                          <span>• Steward: <strong className="text-secondary">{hit.data_steward}</strong></span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 2. Natural Language to Governed SQL */}
            <div className="space-y-3 pt-4 border-t border-border-main">
              <label className="text-xs font-bold text-secondary uppercase tracking-wider block">
                2. Natural Language to Governed SQL
              </label>

              {/* Preset prompt chips */}
              <div className="flex flex-wrap gap-2 text-xs">
                {[
                  'What is the gross margin and ARR by Customer in 2024?',
                  'Show me ebitda and revenue by region',
                  'Calculate total gross margin for all customers',
                ].map((chip, idx) => (
                  <button
                    key={idx}
                    onClick={() => {
                      setNlPrompt(chip);
                    }}
                    className="px-2.5 py-1 rounded-lg bg-surface hover:bg-surface-hover border border-border-main text-tertiary hover:text-primary text-[11px] transition-colors"
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
                  placeholder="Ask any analytical question in plain English..."
                  className="flex-1 px-4 py-2.5 rounded-xl bg-surface border border-border-main text-xs text-primary focus:outline-none focus:border-blue-500"
                />
                <button
                  disabled={loading}
                  onClick={handleResolveNlQuery}
                  className="px-5 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs flex items-center gap-2 transition-colors disabled:opacity-50"
                >
                  {loading ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
                  Generate SQL
                </button>
              </div>

              {/* Resolution Plan & Generated SQL */}
              {nlResolutionPlan && (
                <div className="p-5 rounded-xl bg-surface border border-border-main space-y-4">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-semibold text-primary">Query Execution Plan</span>
                    <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400">
                      Confidence: {(nlResolutionPlan.confidence_score * 100).toFixed(0)}%
                    </span>
                  </div>

                  {/* Badges for resolved tokens */}
                  <div className="flex flex-wrap items-center gap-3 text-xs">
                    <div>
                      <span className="text-tertiary mr-1.5">Metrics:</span>
                      {nlResolutionPlan.detected_metrics?.map((m) => (
                        <span key={m} className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 font-mono mr-1">
                          {m}
                        </span>
                      ))}
                    </div>
                    <div>
                      <span className="text-tertiary mr-1.5">Dimensions:</span>
                      {nlResolutionPlan.detected_dimensions?.map((d) => (
                        <span key={d} className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 font-mono mr-1">
                          {d}
                        </span>
                      ))}
                    </div>
                    {nlResolutionPlan.filters?.length > 0 && (
                      <div>
                        <span className="text-tertiary mr-1.5">Filters:</span>
                        {nlResolutionPlan.filters.map((f, i) => (
                          <span key={i} className="px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 font-mono mr-1">
                            {f}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* SQL Box */}
                  <div className="relative rounded-lg bg-surface-raised border border-border-main p-4 overflow-hidden">
                    <button
                      onClick={() => handleCopySql(nlResolutionPlan.generated_sql)}
                      className="absolute right-3 top-3 p-1.5 rounded bg-surface hover:bg-surface-hover text-secondary hover:text-primary border border-border-main text-xs flex items-center gap-1 transition-colors"
                      title="Copy SQL"
                    >
                      {copiedSql ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
                      {copiedSql ? 'Copied' : 'Copy'}
                    </button>
                    <pre className="font-mono text-xs text-primary overflow-x-auto leading-relaxed">
                      {nlResolutionPlan.generated_sql}
                    </pre>
                  </div>

                  <p className="text-xs text-secondary italic">
                    Plan explanation: {nlResolutionPlan.explanation}
                  </p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
