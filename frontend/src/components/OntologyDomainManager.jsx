import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Layers, Upload, FileCode, Sparkles, CheckCircle2,
  AlertTriangle, RefreshCw, Check, Plus,
  Search, Database, Network, ShieldCheck,
  Table, CheckCheck, Code2,
  X, GitBranch, Lock, Activity
} from 'lucide-react';
import { authFetch } from '../utils/api';
import OntologyGraph from './OntologyGraph';
import OverviewView from './ontology/OverviewView';
import DomainsView from './ontology/DomainsView';
import MetricDrawer from './ontology/MetricDrawer';
import HealthView from './ontology/HealthView';
import { WhatIfSimulator } from './ontology/GovernancePanels';
import { Pill } from './ontology/ui';
import { describeApiError, timeAgo, BTN, BTN_PRIMARY } from './ontology/styles';
import {
  buildHealthResults,
  domainIssueCount,
  guardrailText,
  lintIssuesFor,
  metricStatus,
  scopeCollisions,
  usedInText,
} from './ontology/healthChecks';

// ?tab= values (old and new names) → internal tab ids
const TAB_ALIASES = {
  overview: 'overview',
  mesh: 'mesh',
  domains: 'mesh',
  graph: 'graph',
  map: 'graph',
  governance: 'governance',
  health: 'governance',
};

// Run async jobs with a small concurrency limit
async function runPool(items, limit, fn) {
  const out = [];
  let i = 0;
  const worker = async () => {
    while (i < items.length) {
      const idx = i++;
      out[idx] = await fn(items[idx]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

const PILL_BTN_DARK = {
  background: '#0F172A',
  color: '#FFFFFF',
  border: '1px solid rgba(255,255,255,0.12)',
  borderRadius: 999,
  padding: '9px 18px',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  whiteSpace: 'nowrap',
};

const SELECT_STYLE = {
  padding: '8px 12px',
  borderRadius: 10,
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  outline: 'none',
};

const INPUT_STYLE = {
  padding: '8px 12px',
  borderRadius: 10,
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
};

/**
 * /ontology and the project Ontology tab: Overview, Domains, Map, Health.
 * Asking lives on its own page (AskAiPage, /ask); /ontology?tab=ai and
 * "Ask AI about this metric" links go there.
 */
export default function OntologyDomainManager({ projectId: propProjectId = null, mode = 'explorer' }) {
  const location = useLocation();
  const navigate = useNavigate();

  // Main tabs: 'overview' | 'mesh' | 'graph' | 'governance' | 'blast-radius'.
  // On /ontology the tab lives in ?tab=; embedded on a project page it is local state.
  const [localTab, setLocalTab] = useState('overview');
  const urlTab = TAB_ALIASES[new URLSearchParams(location.search).get('tab')];
  const activeTab = mode === 'comparator'
    ? 'governance'
    : mode === 'blast-radius'
    ? 'blast-radius'
    : (propProjectId ? localTab : urlTab || 'overview');

  const selectTab = (tab) => {
    if (propProjectId) {
      setLocalTab(tab);
      return;
    }
    const params = new URLSearchParams(location.search);
    if (tab === 'overview') params.delete('tab');
    else params.set('tab', tab);
    const qs = params.toString();
    navigate({ search: qs ? `?${qs}` : '' }, { replace: true });
  };

  useEffect(() => {
    if (propProjectId) return;
    if (new URLSearchParams(location.search).get('tab') === 'ai') {
      navigate('/ask', { replace: true });
    }
  }, [location.search, propProjectId, navigate]);

  const [loading, setLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState(null);

  // Domains & Governance State
  const [domainData, setDomainData] = useState({
    domains: [],
    total_concepts: 0,
    total_metrics: 0,
    total_relationships: 0,
    total_policies: 0,
    collisions: [],
    unresolved_references: [],
    load_errors: [],
  });

  // Project Scope State (supports per-project filtering or embedded project mode)
  const [projectsList, setProjectsList] = useState([]);
  const [selectedProjectSlug, setSelectedProjectSlug] = useState(() => {
    if (propProjectId) return propProjectId;
    const params = new URLSearchParams(window.location.search);
    return params.get('project') || '';
  });
  const [attachedDomainIds, setAttachedDomainIds] = useState(null); // null = all domains

  // Domain Glossary State (Section 1)
  const [inspectingDomain, setInspectingDomain] = useState(null);
  const [inspectingDomainData, setInspectingDomainData] = useState(null);
  const [inspectorActiveTab, setInspectorActiveTab] = useState('metrics'); // 'metrics' | 'concepts' | 'policies' | 'yaml'
  const [glossarySearchQuery, setGlossarySearchQuery] = useState('');
  const [selectedGlossaryItem, setSelectedGlossaryItem] = useState(null); // opens detail modal
  const [editorYaml, setEditorYaml] = useState('');
  const [validationResult, setValidationResult] = useState(null);
  const [showCollisionsModal, setShowCollisionsModal] = useState(false);

  // Upload Modal State
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [uploadError, setUploadError] = useState(null);
  const fileInputRef = useRef(null);

  // Bootstrap from OSI Modal State
  const [bootstrapModalOpen, setBootstrapModalOpen] = useState(false);
  const [bootstrapProject, setBootstrapProject] = useState('');
  const [bootstrapDomain, setBootstrapDomain] = useState('');
  const [bootstrapping, setBootstrapping] = useState(false);

  // Domains: every domain's detail (for overview counts and Run all checks), and the drawer
  const [domainDetails, setDomainDetails] = useState({});
  const [drawerMetric, setDrawerMetric] = useState(null);

  // Health: results list
  const [healthFilter, setHealthFilter] = useState('all');
  const [healthExpanded, setHealthExpanded] = useState(null);
  const [impactReports, setImpactReports] = useState({}); // metric name -> blast-radius report
  const [impactLoading, setImpactLoading] = useState({});
  const [checksRanAt, setChecksRanAt] = useState(null);
  const [runningChecks, setRunningChecks] = useState(false);

  // 1. Impact ("What if…" blast-radius simulator) & Governance scope
  const [govProjectId, setGovProjectId] = useState('');
  const [blastTarget, setBlastTarget] = useState('');
  const [blastType, setBlastType] = useState('METRIC');
  const [blastChangeType, setBlastChangeType] = useState('MODIFY_EXPRESSION');
  const [blastTableContext, setBlastTableContext] = useState('');
  const [blastReport, setBlastReport] = useState(null);
  const [runningBlast, setRunningBlast] = useState(false);

  // 2. Semantic Quality Linter (Steward defaults to WARNING for BI-derived models)
  const [lintRequireSteward, setLintRequireSteward] = useState(true);
  const [lintRequireDesc, setLintRequireDesc] = useState(true);
  const [lintSnakeCase, setLintSnakeCase] = useState(true);
  const [lintStewardSeverity, setLintStewardSeverity] = useState('WARNING');
  const [lintReport, setLintReport] = useState(null);
  const [runningLint, setRunningLint] = useState(false);

  // 3. Golden Data Reconciliation (live SQL + DAX execution/upload parity)
  const [reconMetric, setReconMetric] = useState('');
  const [reconExpr, setReconExpr] = useState('');
  const [reconDaxExpr, setReconDaxExpr] = useState('');
  const [reconCustomSql, setReconCustomSql] = useState('');
  const [parityProjectMetrics, setParityProjectMetrics] = useState([]);
  const [reconTable, setReconTable] = useState('');
  const [reconTargetKind, setReconTargetKind] = useState('semantic_view');
  const [reconDims, setReconDims] = useState('');
  const [reconDaxText, setReconDaxText] = useState('');
  const [reconSqlText, setReconSqlText] = useState('');
  const [reconAbsTol, setReconAbsTol] = useState('0.01');
  const [reconRelTol, setReconRelTol] = useState('0.0001');
  const [reconMaxMismatchPct, setReconMaxMismatchPct] = useState('0');
  const [reconRegisterVerified, setReconRegisterVerified] = useState(false);
  const [reconQueries, setReconQueries] = useState(null);
  const [reconResult, setReconResult] = useState(null);
  const [reconSliceRows, setReconSliceRows] = useState([]);
  const [reconError, setReconError] = useState(null);
  const [runningRecon, setRunningRecon] = useState(false);
  const [certificationsByMetric, setCertificationsByMetric] = useState({});

  // 4. Bidirectional RLS Policy Translator (explicit target DB/schema, no defaults)
  const [rlsRoleName, setRlsRoleName] = useState('');
  const [rlsTableName, setRlsTableName] = useState('');
  const [rlsDaxExpr, setRlsDaxExpr] = useState('');
  const [rlsTargetDb, setRlsTargetDb] = useState('');
  const [rlsTargetSchema, setRlsTargetSchema] = useState('');
  const [rlsDdl, setRlsDdl] = useState('');
  const [rlsStatus, setRlsStatus] = useState(null);
  const [rlsReason, setRlsReason] = useState('');
  const [runningRls, setRunningRls] = useState(false);

  const [bootstrapDuplicateError, setBootstrapDuplicateError] = useState(null);

  const fetchCertifications = async (projectId, domainName) => {
    try {
      const qs = new URLSearchParams();
      if (projectId) qs.set('project_id', projectId);
      if (domainName) qs.set('domain', domainName);
      const qStr = qs.toString() ? `?${qs.toString()}` : '';
      const res = await authFetch(`/api/reconciliation/certifications${qStr}`);
      if (!res.ok) return;
      const data = await res.json();
      const map = {};
      for (const rec of data?.certifications || []) {
        const key = String(rec.metric_name || '').toLowerCase();
        if (key && !map[key]) map[key] = rec;
      }
      setCertificationsByMetric(map);
    } catch {
      /* ignore certification fetch failure */
    }
  };

  useEffect(() => {
    fetchDomains(true);
    fetchProjects();
    fetchCertifications();
  }, []);

  // When selectedProjectSlug changes, load that project's attached domains
  useEffect(() => {
    if (!selectedProjectSlug) {
      setAttachedDomainIds(null);
      return;
    }
    authFetch(`/api/ontology/projects/${encodeURIComponent(selectedProjectSlug)}/domains`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && Array.isArray(data.domains) && data.domains.length > 0) {
          setAttachedDomainIds(data.domains.map((d) => d.id || d.domain));
        } else {
          setAttachedDomainIds(null);
        }
      })
      .catch(() => setAttachedDomainIds(null));
  }, [selectedProjectSlug]);

  // Governance scope follows the selected project; cached impact is scope-specific so drop it on change
  useEffect(() => {
    setGovProjectId(selectedProjectSlug || '');
  }, [selectedProjectSlug]);

  const resetParityState = () => {
    setReconSliceRows([]);
    setReconQueries(null);
    setReconResult(null);
    setReconError(null);
    setReconDaxText('');
    setReconSqlText('');
  };

  const effectiveGovProjectId = govProjectId || selectedProjectSlug || '';
  useEffect(() => {
    setImpactReports({});
    setImpactLoading({});
    setBlastReport(null);
    setLintReport(null);
    setHealthExpanded(null);
    resetParityState();
  }, [effectiveGovProjectId]);

  const fetchDomains = async (autoSelectFirst = false) => {
    setLoading(true);
    try {
      const res = await authFetch('/api/ontology/domains');
      if (res.ok) {
        const data = await res.json();
        setDomainData(data);
        if ((autoSelectFirst || !inspectingDomain) && data.domains?.length > 0) {
          handleOpenDomainInspector(data.domains[0].domain, 'metrics');
        }
        return { data, details: await loadDomainDetails(data.domains || []) };
      }
    } catch (err) {
      console.error('Failed to load domains:', err);
    } finally {
      setLoading(false);
    }
    return null;
  };

  // Load every domain's metrics and concepts (overview counts, Run all checks)
  const loadDomainDetails = async (domains) => {
    const entries = await runPool(domains.slice(0, 100), 6, async (d) => {
      try {
        const res = await authFetch(`/api/ontology/domains/${encodeURIComponent(d.domain)}`);
        return res.ok ? [d.domain, await res.json()] : null;
      } catch {
        return null;
      }
    });
    const map = Object.fromEntries(entries.filter(Boolean));
    setDomainDetails(map);
    return map;
  };


  const fetchProjects = async () => {
    try {
      const res = await authFetch('/api/projects?limit=50');
      if (res.ok) {
        const data = await res.json();
        const list = Array.isArray(data) ? data : data.projects || [];
        setProjectsList(list);
        if (list.length > 0 && !bootstrapProject) {
          setBootstrapProject(list[0].id || list[0].name);
        }
      }
    } catch (err) {
      console.warn('Could not load projects:', err);
    }
  };

  const handleOpenDomainInspector = async (domainName, initialTab = 'metrics') => {
    if (!domainName) return;
    setInspectingDomain(domainName);
    setInspectorActiveTab(initialTab);
    setValidationResult(null);
    try {
      const res = await authFetch(`/api/ontology/domains/${encodeURIComponent(domainName)}`);
      if (res.ok) {
        const data = await res.json();
        setInspectingDomainData(data);
        setEditorYaml(data.yaml_content || data.yaml || '');
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
    } catch {
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
          text:
            savedName !== inspectingDomain
              ? `Domain '${inspectingDomain}' renamed to '${savedName}' and saved.`
              : `Domain '${savedName}' saved and synchronized!`,
        });
        fetchDomains();
        handleOpenDomainInspector(savedName, inspectorActiveTab);
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
          text:
            `Ingested ${data.uploaded_domains.length} domain ontolog${
              data.uploaded_domains.length === 1 ? 'y' : 'ies'
            } (${data.uploaded_domains.join(', ')}).` +
            (fileErrors.length
              ? `\nRejected ${fileErrors.length} file(s):\n${fileErrors
                  .map((e) => `${e.file}: ${e.error}`)
                  .join('\n')}`
              : ''),
        });
        setSelectedFiles([]);
        setUploadModalOpen(false);
        fetchDomains();
        handleOpenDomainInspector(data.uploaded_domains[0], 'metrics');
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

  const handleBootstrapFromOsi = async (syncExisting = false) => {
    if (!bootstrapProject) return;
    setBootstrapping(true);
    setBootstrapDuplicateError(null);
    try {
      const res = await authFetch('/api/ontology/domains/from-osi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: bootstrapProject,
          domain_name: bootstrapDomain.trim() || undefined,
          save_to_disk: true,
          sync_existing: Boolean(syncExisting),
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setStatusMessage({
          type: 'success',
          text: `Generated ontology domain '${data.domain}' from OSI model (${data.concept_count} concepts, ${data.metric_count} metrics)!`,
        });
        setBootstrapDuplicateError(null);
        setBootstrapModalOpen(false);
        fetchDomains();
        handleOpenDomainInspector(data.domain, 'metrics');
      } else if (res.status === 409) {
        setBootstrapDuplicateError(describeApiError(data, 'This project already has a domain.'));
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

  const handleUpdateMetricMeta = async (metricName, updates) => {
    if (!inspectingDomain || !metricName) return;
    const res = await authFetch(
      `/api/ontology/domains/${encodeURIComponent(inspectingDomain)}/metrics/${encodeURIComponent(metricName)}/metadata`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      }
    );
    const data = await res.json();
    if (res.ok) {
      setStatusMessage({ type: 'success', text: `Updated metadata for '${metricName}'.` });
      fetchDomains();
      handleOpenDomainInspector(data.domain || inspectingDomain, inspectorActiveTab);
    } else {
      setStatusMessage({ type: 'error', text: describeApiError(data, 'Failed to update metric metadata.') });
    }
  };

  const handleSyncWithOsi = async (domainName) => {
    if (!domainName) return;
    setLoading(true);
    try {
      const res = await authFetch('/api/ontology/domains/from-osi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain_name: domainName,
          save_to_disk: true,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setStatusMessage({
          type: 'success',
          text: `Synchronized '${domainName}' with latest OSI model while preserving human-authored edits!`,
        });
        fetchDomains();
        handleOpenDomainInspector(domainName, inspectorActiveTab);
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

  const handleJumpToAiTest = (metricName) => {
    const domConcepts = inspectingDomainData?.parsed?.concepts || allConcepts || [];
    const firstDim = domConcepts
      .flatMap((c) => c.attributes || [])
      .find((a) => !a.is_hidden && !/INT|DEC|FLOAT|NUM/i.test(a.data_type || ''));
    const question = firstDim ? `What is ${metricName} by ${firstDim.label || firstDim.name}?` : `What is ${metricName}?`;
    const projQuery = selectedProjectSlug ? `&project=${encodeURIComponent(selectedProjectSlug)}` : '';
    navigate(`/ask?q=${encodeURIComponent(question)}${projQuery}`);
  };

  // Impact (blast radius) for one object; throws with a readable message on failure
  const fetchImpact = async (name, type = 'METRIC', changeType = 'MODIFY_EXPRESSION', tableCtx = '') => {
    const res = await authFetch('/api/governance/blast-radius', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_name: name,
        target_type: type,
        change_type: changeType,
        table_context: tableCtx || undefined,
        project_id: effectiveGovProjectId || undefined,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(describeApiError(data, 'Impact check failed.'));
    return data;
  };

  // Drawer "Impact → View": open Health's What if… with this metric filled in
  const handleJumpToBlastRadius = (name, type = 'METRIC') => {
    setBlastTarget(name);
    setBlastType(type);
    setBlastChangeType('MODIFY_EXPRESSION');
    setBlastReport(type === 'METRIC' ? impactReports[name] || null : null);
    setHealthFilter('all');
    setHealthExpanded('whatif');
    selectTab('governance');
  };

  // Impact runs automatically for the metric selected in the drawer
  const ensureImpact = async (name) => {
    if (!name || impactReports[name] || impactLoading[name]) return;
    setImpactLoading((prev) => ({ ...prev, [name]: true }));
    try {
      const report = await fetchImpact(name);
      setImpactReports((prev) => ({ ...prev, [name]: report }));
    } catch (err) {
      console.warn('Impact check failed:', err);
    } finally {
      setImpactLoading((prev) => ({ ...prev, [name]: false }));
    }
  };

  const handleSelectMetric = (name) => {
    setDrawerMetric(name);
    ensureImpact(name);
    if (!lintReport && !runningLint) handleRunLinter();
  };

  // Health "Open" on a finding: go to the metric in Domains and open its drawer
  const handleOpenMetricFromHealth = (name, domain) => {
    let target = domain;
    if (!target || !domainDetails[target]) {
      target = Object.keys(domainDetails).find((d) =>
        (domainDetails[d]?.parsed?.metrics || []).some((m) => m.name === name)
      ) || target;
    }
    if (target && target !== inspectingDomain) handleOpenDomainInspector(target, 'metrics');
    else setInspectorActiveTab('metrics');
    setGlossarySearchQuery('');
    handleSelectMetric(name);
    selectTab('mesh');
  };

  const openBootstrap = () => {
    if (projectsList.length > 0 && !bootstrapProject) {
      setBootstrapProject(selectedProjectSlug || projectsList[0].id || projectsList[0].name);
    }
    setBootstrapModalOpen(true);
  };

  // Governance Handlers
  const handleRunBlastRadius = async (targetOverride) => {
    const target = (typeof targetOverride === 'string' ? targetOverride : blastTarget).trim();
    if (!target) return;
    setRunningBlast(true);
    try {
      setBlastReport(await fetchImpact(target, blastType, blastChangeType, blastTableContext.trim()));
    } catch (err) {
      setStatusMessage({ type: 'error', text: String(err.message || err) });
    } finally {
      setRunningBlast(false);
    }
  };

  // "Run all checks": refresh domains (collisions), impact for every metric, quality lint
  const handleRunAllChecks = async (scopeDomains) => {
    setRunningChecks(true);
    try {
      const fresh = await fetchDomains(false);
      const details = fresh?.details || domainDetails;
      const names = [
        ...new Set(
          scopeDomains.flatMap((d) => (details[d.domain]?.parsed?.metrics || []).map((m) => m.name))
        ),
      ].slice(0, 300);
      const lintPromise = handleRunLinter();
      const reports = await runPool(names, 6, async (n) => {
        try {
          return [n, await fetchImpact(n)];
        } catch {
          return null;
        }
      });
      setImpactReports(Object.fromEntries(reports.filter(Boolean)));
      await lintPromise;
      setChecksRanAt(Date.now());
    } finally {
      setRunningChecks(false);
    }
  };

  const prefillParity = (m) => {
    if (!m) return;
    if (m.name !== reconMetric) {
      setReconSliceRows([]);
      setReconQueries(null);
      setReconResult(null);
      setReconError(null);
      setReconDaxText('');
      setReconSqlText('');
    }
    const concept = Object.values(domainDetails)
      .flatMap((d) => d?.parsed?.concepts || [])
      .find((c) => c.name === m.concept);
    const logicalMetric = m.logical_name && concept?.logical_name
      ? `${concept.logical_name}.${m.logical_name}`
      : (m.logical_name || m.name);
    const mName = m.name || logicalMetric;
    setReconMetric(mName);
    const sqlEx = m.sql_expression || logicalMetric;
    setReconExpr(sqlEx);
    setReconCustomSql(sqlEx);
    setReconDaxExpr(m.dax_expression || '');
    const svName = m.semantic_view_name || m.target_table_or_view || inspectingDomainData?.parsed?.semantic_view_name || concept?.physical_table || m.concept || '';
    setReconTable(svName);
    const isSv = Boolean(m.semantic_view_name || inspectingDomainData?.parsed?.semantic_view_name || m.target_kind === 'semantic_view');
    setReconTargetKind(isSv ? 'semantic_view' : 'table');
    let chosenDim = '';
    if (m.dimensions?.length) {
      chosenDim = m.dimensions[0];
      setReconDims(chosenDim);
    } else if (concept?.attributes?.length) {
      const firstDim = concept.attributes.find((a) => !a.is_hidden && !/INT|DEC|FLOAT|NUM/i.test(a.data_type || ''));
      if (firstDim) {
        chosenDim = firstDim.logical_name && concept.logical_name
          ? `${concept.logical_name}.${firstDim.logical_name}`
          : firstDim.name;
        setReconDims(chosenDim);
      }
    }
    // Auto-generate verification queries for this metric
    if (mName && svName) {
      authFetch('/api/reconciliation/generate-queries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          metric_name: mName.trim(),
          metric_sql_expression: sqlEx.trim(),
          target_table_or_view: svName.trim(),
          target_kind: isSv ? 'semantic_view' : 'table',
          dimensions: chosenDim ? [chosenDim.trim()] : [],
          filters: [],
          project_id: _reconProjectId(),
        }),
      }).then((res) => {
        if (res.ok) res.json().then((data) => setReconQueries(data)).catch(() => {});
      }).catch(() => {});
    }
  };

  const openParityFor = (m) => {
    prefillParity(m);
    setHealthFilter('all');
    setHealthExpanded('parity');
    selectTab('governance');
  };

  const handleRunLinter = async (stewardOverride) => {
    const activeSteward = stewardOverride || lintStewardSeverity;
    setRunningLint(true);
    try {
      const res = await authFetch('/api/governance/lint', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          require_steward: activeSteward !== 'OFF',
          steward_severity: activeSteward,
          require_description: lintRequireDesc,
          enforce_snake_case_metrics: lintSnakeCase,
          project_id: govProjectId || selectedProjectSlug || undefined,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setLintReport(data);
      } else {
        const err = await res.json();
        setStatusMessage({ type: 'error', text: describeApiError(err, 'Semantic linter failed.') });
      }
    } catch (err) {
      setStatusMessage({ type: 'error', text: String(err) });
    } finally {
      setRunningLint(false);
    }
  };

  const _reconDimList = () => reconDims.split(',').map((d) => d.trim()).filter(Boolean);

  const _reconTolerancePayload = () => ({
    absolute_tolerance: Math.max(0, Number(reconAbsTol) || 0),
    relative_tolerance: Math.max(0, Number(reconRelTol) || 0),
    max_mismatch_percentage: Math.max(0, Number(reconMaxMismatchPct) || 0),
  });

  const _updateParityView = (vData) => {
    setReconResult(vData);
    const dimKey = _reconDimList()[0];
    const samples = vData?.comparison_rows || vData?.mismatch_samples || vData?.sample_mismatches || [];
    if (samples.length) {
      setReconSliceRows(samples.map((s) => {
        const daxVal = s.dax_value ?? 0;
        const sqlVal = s.sql_value ?? 0;
        const absDiff = s.absolute_delta ?? (Number(sqlVal) - Number(daxVal));
        return {
          slice: s.dimension_key ? (dimKey && typeof s.dimension_key === 'object' ? s.dimension_key[dimKey] : (typeof s.dimension_key === 'string' ? s.dimension_key : JSON.stringify(s.dimension_key))) : 'Total',
          daxValue: daxVal,
          sqlValue: sqlVal,
          absDiff,
          deltaPct: Number(s.percentage_delta ?? 0).toFixed(2),
          matched: Boolean(s.matched ?? s.is_match),
        };
      }));
    } else {
      setReconSliceRows([]);
    }
    fetchCertifications(govProjectId || selectedProjectSlug || undefined, inspectingDomain || undefined);
  };

  const _reconProjectId = () => (
    govProjectId
    || selectedProjectSlug
    || inspectingDomainData?.source_project_id
    || domainDetails[inspectingDomain]?.source_project_id
    || domainData.domains?.find((d) => d.domain === inspectingDomain)?.source_project_id
    || undefined
  );

  const handleUpdateMetricSql = async (newSql, reRun = false) => {
    const targetM = reconMetric.trim();
    if (!targetM || !newSql?.trim()) return;
    setRunningRecon(true);
    setReconError(null);
    try {
      const res = await authFetch('/api/reconciliation/update-metric-sql', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: _reconProjectId(),
          domain: inspectingDomain || undefined,
          metric_name: targetM,
          sql_expression: newSql.trim(),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setReconError(describeApiError(data, 'Failed to update Snowflake SQL.'));
        return;
      }
      setReconExpr(newSql.trim());
      setReconCustomSql(newSql.trim());
      setStatusMessage({ type: 'success', text: `Updated Snowflake SQL for ${targetM}.` });
      fetchDomainData();
      fetchProjectReconMetrics(govProjectId || selectedProjectSlug || undefined);
      if (reRun) {
        // Run with custom SQL query
        const customSql = reconQueries?.sql_query
          ? reconQueries.sql_query.replace(new RegExp(reconExpr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), newSql.trim())
          : undefined;
        await handleRunAutoDaxCertify({ customSqlQuery: customSql, overrideSqlExpr: newSql.trim() });
      } else {
        await handleGenerateReconQueries({ overrideSqlExpr: newSql.trim() });
      }
    } catch (err) {
      setReconError(String(err));
    } finally {
      setRunningRecon(false);
    }
  };

  const fetchProjectReconMetrics = useCallback(async (projId) => {
    try {
      const q = projId ? `?project_id=${encodeURIComponent(projId)}` : '';
      const res = await authFetch(`/api/reconciliation/project-metrics${q}`);
      if (res.ok) {
        const data = await res.json();
        setParityProjectMetrics(data.metrics || []);
      }
    } catch {
      // ignore
    }
  }, [authFetch]);

  useEffect(() => {
    fetchProjectReconMetrics(govProjectId || selectedProjectSlug || undefined);
  }, [govProjectId, selectedProjectSlug, fetchProjectReconMetrics]);

  const handleGenerateReconQueries = async (opts = {}) => {
    if (!reconMetric.trim() || !reconTable.trim()) return;
    setRunningRecon(true);
    setReconError(null);
    try {
      const sqlExpr = opts.overrideSqlExpr || reconExpr.trim() || reconMetric.trim();
      const res = await authFetch('/api/reconciliation/generate-queries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          metric_name: reconMetric.trim(),
          metric_sql_expression: sqlExpr,
          target_table_or_view: reconTable.trim(),
          target_kind: reconTargetKind,
          dimensions: _reconDimList(),
          filters: [],
          project_id: _reconProjectId(),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setReconError(describeApiError(data, 'Could not generate reconciliation queries.'));
        return;
      }
      setReconQueries(data);
    } catch (err) {
      setReconError(String(err));
    } finally {
      setRunningRecon(false);
    }
  };

  const handleRunLiveSql = async () => {
    setRunningRecon(true);
    setReconError(null);
    try {
      let query = reconQueries?.sql_query;
      if (!query && reconMetric.trim() && reconTable.trim()) {
        const qRes = await authFetch('/api/reconciliation/generate-queries', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            metric_name: reconMetric.trim(),
            metric_sql_expression: reconExpr.trim() || reconMetric.trim(),
            target_table_or_view: reconTable.trim(),
            target_kind: reconTargetKind,
            dimensions: _reconDimList(),
            filters: [],
            project_id: _reconProjectId(),
          }),
        });
        if (qRes.ok) {
          const qData = await qRes.json();
          setReconQueries(qData);
          query = qData.sql_query;
        }
      }
      if (!query) {
        setReconError('Please select a metric and deployed view/table first.');
        return;
      }
      const res = await authFetch('/api/ai/execute-query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sql: query,
          target: 'SNOWFLAKE',
          project_id: _reconProjectId(),
          limit: 500,
        }),
      });
      const data = await res.json();
      if (!res.ok || data.success === false) {
        setReconError(data.error || data.message || describeApiError(data, 'Live SQL execution failed.'));
        return;
      }
      const cols = data.columns || [];
      const rowObjs = (data.rows || []).map((r) => Object.fromEntries(cols.map((c, idx) => [c, r[idx]])));
      setReconSqlText(JSON.stringify(rowObjs, null, 2));
      setStatusMessage({ type: 'success', text: `Successfully executed Snowflake SQL: ${rowObjs.length} rows returned.` });
    } catch (err) {
      setReconError(String(err));
    } finally {
      setRunningRecon(false);
    }
  };

  const handleRunAutoDaxCertify = async (opts = {}) => {
    if (!reconMetric.trim() || !reconTable.trim()) return;
    setRunningRecon(true);
    setReconError(null);
    try {
      let parsedDax = opts.daxResults !== undefined ? opts.daxResults : undefined;
      if (parsedDax === undefined && reconDaxText.trim()) {
        try {
          parsedDax = JSON.parse(reconDaxText);
        } catch {
          // ignore
        }
      }
      const sqlExpr = opts.overrideSqlExpr || reconExpr.trim() || reconMetric.trim();
      const res = await authFetch('/api/reconciliation/certify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: _reconProjectId(),
          domain: inspectingDomain || undefined,
          metric_name: reconMetric.trim(),
          view_metric: reconMetric.trim(),
          metric_sql_expression: sqlExpr,
          target_table_or_view: reconTable.trim(),
          target_kind: reconTargetKind,
          dimensions: _reconDimList(),
          dax_results: parsedDax,
          custom_sql_query: opts.customSqlQuery || undefined,
          tolerance: _reconTolerancePayload(),
          register_verified_query: Boolean(reconRegisterVerified),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setReconError(describeApiError(data, 'Auto-Run DAX & Certify failed.'));
        return;
      }
      if (data.verification) {
        _updateParityView(data.verification);
        setReconQueries({
          dax_query: data.certification?.dax_query || reconQueries?.dax_query || '',
          sql_query: data.certification?.sql_query || reconQueries?.sql_query || '',
        });
      }
    } catch (err) {
      setReconError(String(err));
    } finally {
      setRunningRecon(false);
    }
  };

  const handleRunReconciliation = async () => {
    setRunningRecon(true);
    setReconError(null);
    try {
      const daxRows = JSON.parse(reconDaxText || '[]');
      const sqlRows = JSON.parse(reconSqlText || '[]');
      const vRes = await authFetch('/api/reconciliation/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          metric_name: reconMetric.trim(),
          dax_results: daxRows,
          sql_results: sqlRows,
          dimension_keys: _reconDimList(),
          dax_query: reconQueries?.dax_query || '',
          sql_query: reconQueries?.sql_query || '',
          tolerance: _reconTolerancePayload(),
          project_id: govProjectId || selectedProjectSlug || undefined,
          domain: inspectingDomain || undefined,
          view_metric: reconMetric.trim(),
        }),
      });
      const vData = await vRes.json();
      if (!vRes.ok) {
        setReconError(describeApiError(vData, 'Verification failed.'));
        return;
      }
      _updateParityView(vData);
    } catch (err) {
      setReconError(String(err));
    } finally {
      setRunningRecon(false);
    }
  };

  const handleTranslateRls = async () => {
    setRunningRls(true);
    setRlsStatus(null);
    setRlsReason('');
    try {
      const res = await authFetch('/api/security/rls/tmdl-to-snowflake', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role: {
            name: rlsRoleName,
            description: `Governed RLS policy for ${rlsRoleName}`,
            table_permissions: [
              {
                table_name: rlsTableName,
                dax_filter_expression: rlsDaxExpr,
              },
            ],
            members: [],
          },
          target_database: rlsTargetDb,
          target_schema: rlsTargetSchema,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setRlsStatus(data.status || (data.ddl ? 'translated' : 'not translated'));
        setRlsReason(data.reason || '');
        setRlsDdl(data.ddl || '');
      } else {
        setRlsStatus('not translated');
        setRlsReason(describeApiError(data, 'RLS translation failed.'));
        setRlsDdl('');
      }
    } catch (err) {
      setRlsStatus('not translated');
      setRlsReason(String(err));
    } finally {
      setRunningRls(false);
    }
  };

  // Filtered domains (by project scope if selected)
  const visibleDomains = useMemo(() => {
    const all = domainData.domains || [];
    if (!selectedProjectSlug) return all;
    const filteredByProject = all.filter(
      (d) =>
        d.source_project_id === selectedProjectSlug ||
        (attachedDomainIds && (attachedDomainIds.includes(d.id) || attachedDomainIds.includes(d.domain)))
    );
    return filteredByProject.length > 0 ? filteredByProject : all;
  }, [domainData.domains, selectedProjectSlug, attachedDomainIds]);

  const activeMetrics = useMemo(() => {
    const list = inspectingDomainData?.parsed?.metrics || [];
    if (!glossarySearchQuery.trim()) return list;
    const q = glossarySearchQuery.toLowerCase();
    return list.filter(
      (m) =>
        (m.name || '').toLowerCase().includes(q) ||
        (m.label || '').toLowerCase().includes(q) ||
        (m.sql_expression || '').toLowerCase().includes(q) ||
        (m.description || '').toLowerCase().includes(q)
    );
  }, [inspectingDomainData, glossarySearchQuery]);

  const activeConcepts = useMemo(() => {
    const list = inspectingDomainData?.parsed?.concepts || [];
    if (!glossarySearchQuery.trim()) return list;
    const q = glossarySearchQuery.toLowerCase();
    return list.filter(
      (c) =>
        (c.name || '').toLowerCase().includes(q) ||
        (c.physical_table || '').toLowerCase().includes(q) ||
        (c.description || '').toLowerCase().includes(q)
    );
  }, [inspectingDomainData, glossarySearchQuery]);

  const activePolicies = useMemo(() => {
    const list = inspectingDomainData?.parsed?.policies || [];
    if (!glossarySearchQuery.trim()) return list;
    const q = glossarySearchQuery.toLowerCase();
    return list.filter(
      (p) =>
        (p.name || '').toLowerCase().includes(q) ||
        (p.filter_expression || '').toLowerCase().includes(q) ||
        (p.description || '').toLowerCase().includes(q)
    );
  }, [inspectingDomainData, glossarySearchQuery]);

  const activeProjectName = useMemo(() => {
    if (!selectedProjectSlug) return 'All Projects (Enterprise)';
    const found = projectsList.find((p) => (p.id || p.name) === selectedProjectSlug);
    return found ? found.name || found.id : selectedProjectSlug;
  }, [selectedProjectSlug, projectsList]);

  // ── Derived data for Overview, Domains and Health ──────────────────────────
  const visibleNames = visibleDomains.map((d) => d.domain);
  const scopedCollisions = scopeCollisions(domainData.collisions, visibleNames);
  const collisionNames = new Set(scopedCollisions.map((c) => c.item_name));
  const reconDriftRows = reconSliceRows.filter((r) => !r.matched);
  const driftMetric = reconDriftRows.length ? reconMetric : null;
  const reconDimension = reconDims.split(',').map((d) => d.trim()).filter(Boolean)[0] || '';
  const allMetrics = visibleDomains.flatMap((d) => domainDetails[d.domain]?.parsed?.metrics || []);
  const allMetricNames = [...new Set(allMetrics.map((m) => m.name))];
  const allConcepts = visibleDomains.flatMap((d) => domainDetails[d.domain]?.parsed?.concepts || []);
  const allDimensions = [
    ...new Set(
      allConcepts.flatMap((c) =>
        (c.attributes || [])
          .filter((a) => !a.is_hidden && !/INT|DEC|FLOAT|NUM/i.test(a.data_type || ''))
          .map((a) => (a.logical_name && c.logical_name ? `${c.logical_name}.${a.logical_name}` : a.name))
      )
    ),
  ];
  const allSemanticViews = [
    ...new Set(
      visibleDomains
        .map((d) => domainDetails[d.domain]?.parsed?.semantic_view_name)
        .filter(Boolean)
    ),
  ];
  const sumOf = (key) => visibleDomains.reduce((n, d) => n + (d[key] || 0), 0);

  const overviewStats = {
    metrics: sumOf('metric_count'),
    concepts: sumOf('concept_count'),
    relationships: sumOf('relationship_count'),
    boundMetrics: allMetrics.filter((m) => (m.maps_to || []).length > 0).length,
    tables: new Set(allConcepts.map((c) => c.physical_table).filter(Boolean)).size,
    detailsLoaded: visibleDomains.length > 0 && visibleDomains.every((d) => domainDetails[d.domain]),
  };

  const unresolvedRefs = domainData.unresolved_references || [];
  const attention = {
    collisions: scopedCollisions.length,
    unresolved: unresolvedRefs.length,
    parityDrift: driftMetric ? 1 : 0,
  };
  attention.total = attention.collisions + attention.unresolved + attention.parityDrift;

  // Lint runs on the whole federation; keep only the visible domains' findings
  const scopedLint =
    lintReport && selectedProjectSlug
      ? {
          ...lintReport,
          issues: (lintReport.issues || []).filter((i) =>
            (i.domain || '').split(',').some((d) => visibleNames.includes(d.trim()))
          ),
        }
      : lintReport;

  const healthRows = buildHealthResults({
    collisions: scopedCollisions,
    unresolved: unresolvedRefs,
    loadErrors: domainData.load_errors || [],
    lintReport: scopedLint,
    impactReports: checksRanAt
      ? Object.fromEntries(Object.entries(impactReports).filter(([n]) => allMetricNames.includes(n)))
      : {},
    impactRan: !!checksRanAt,
    recon: { rows: reconSliceRows, metric: reconMetric, dimension: reconDimension },
    policyCount: sumOf('policy_count'),
    rls: { ddl: rlsDdl, role: rlsRoleName, table: rlsTableName },
  });
  const healthIssueCount = healthRows.filter((r) => r.severity === 'breaking' || r.severity === 'warning').length;

  const domainPolicies = inspectingDomainData?.parsed?.policies || [];
  const metricRowInfo = (m) => {
    const certKey = String(m.logical_name || m.name || '').toLowerCase();
    const altKey = String(m.name || '').toLowerCase();
    const cert = certificationsByMetric[certKey] || certificationsByMetric[altKey] || null;
    return {
      status: metricStatus(m, { collisionNames, driftMetric }),
      cert,
      usedIn: usedInText(m, impactReports[m.name]),
      guardrail: guardrailText(m, domainPolicies),
    };
  };
  const domainMetricList = inspectingDomainData?.parsed?.metrics || [];
  const domainMetricNames = new Set(domainMetricList.map((m) => m.name));
  const drawerMetricObj = drawerMetric ? domainMetricList.find((m) => m.name === drawerMetric) : null;
  const drawerParity =
    drawerMetricObj && reconMetric === drawerMetricObj.name && reconSliceRows.length
      ? (() => {
          if (!reconDriftRows.length) return { drift: false };
          const worst = reconDriftRows.reduce((a, b) => (Number(b.deltaPct) > Number(a.deltaPct) ? b : a));
          return { drift: true, deltaPct: worst.deltaPct, slice: `${reconDimension} = ${worst.slice}` };
        })()
      : null;

  return (
    <div
      style={{
        height: '100%',
        overflowY: 'auto',
        background: 'var(--bg-app)',
        color: 'var(--text-primary)',
        padding: propProjectId ? '16px 0' : '28px 36px 40px',
      }}
    >
      <div style={{ maxWidth: 1320, margin: '0 auto' }}>
        {/* Header: title, subtitle and at most two actions */}
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            marginBottom: 14,
          }}
        >
          <div style={{ minWidth: 0 }}>
            {selectedProjectSlug && !propProjectId && (
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent-blue)', marginBottom: 4 }}>
                Project: {activeProjectName}
              </div>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '4px 12px' }}>
              <h1
                style={{
                  fontSize: propProjectId ? 20 : 24,
                  fontWeight: 800,
                  margin: 0,
                  color: 'var(--text-primary)',
                  letterSpacing: '-0.02em',
                }}
              >
                {mode === 'comparator'
                  ? 'Metric Comparator'
                  : mode === 'blast-radius'
                  ? 'Blast Radius'
                  : 'Explorer'}
              </h1>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {mode === 'comparator'
                  ? 'Golden parity reconciliation, quality linter, and access checks'
                  : mode === 'blast-radius'
                  ? 'What-if impact simulator for metric renames, drops, and formula changes'
                  : 'Shared business definitions every BI tool and AI agent uses'}
              </span>
            </div>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
            {!propProjectId && (
              <select
                value={selectedProjectSlug}
                onChange={(e) => setSelectedProjectSlug(e.target.value)}
                style={SELECT_STYLE}
                aria-label="Project scope"
              >
                <option value="">All projects</option>
                {projectsList.map((p) => (
                  <option key={p.id || p.name} value={p.id || p.name}>
                    {p.name || p.id}
                  </option>
                ))}
              </select>
            )}

            {activeTab === 'governance' && (
              <>
                {checksRanAt && (
                  <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Last run {timeAgo(checksRanAt)}</span>
                )}
                <button
                  type="button"
                  onClick={() => handleRunAllChecks(visibleDomains)}
                  disabled={runningChecks}
                  style={{ ...BTN_PRIMARY, opacity: runningChecks ? 0.7 : 1 }}
                >
                  <RefreshCw size={13} style={runningChecks ? { animation: 'spin 1s linear infinite' } : undefined} />
                  {runningChecks ? 'Running checks…' : 'Run all checks'}
                </button>
              </>
            )}
            {activeTab !== 'governance' && (
              <>
                <button type="button" onClick={() => setUploadModalOpen(true)} style={BTN}>
                  <Upload size={13} /> Import YAML
                </button>
                <button type="button" onClick={openBootstrap} style={BTN_PRIMARY}>
                  <Sparkles size={13} /> Generate from OSI
                </button>
              </>
            )}
          </div>
        </div>

        {/* Tabs: Overview · Domains · Map (Health / Blast Radius live on their own nav tabs) */}
        {mode === 'explorer' && (
          <div
            role="tablist"
            style={{
              display: 'flex',
              gap: 22,
              borderBottom: '1px solid var(--border-main)',
              marginBottom: 20,
              overflowX: 'auto',
            }}
          >
            {[
              { id: 'overview', label: 'Overview' },
              { id: 'mesh', label: 'Models', count: visibleDomains.length },
              { id: 'graph', label: 'Map' },
              ...(propProjectId ? [{ id: 'governance', label: 'Metric Comparator', badge: healthIssueCount }] : []),
            ].map((t) => {
            const on = activeTab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => selectTab(t.id)}
                style={{
                  padding: '8px 0 10px',
                  marginBottom: -1,
                  background: 'transparent',
                  border: 'none',
                  borderBottom: `2px solid ${on ? 'var(--accent-blue)' : 'transparent'}`,
                  color: on ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontWeight: on ? 700 : 500,
                  fontSize: 13,
                  cursor: 'pointer',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  whiteSpace: 'nowrap',
                }}
              >
                {t.label}
                {t.count !== undefined && <span style={{ color: 'var(--text-tertiary)', fontWeight: 500 }}>{t.count}</span>}
                {t.badge > 0 && <Pill tone="warn">{t.badge}</Pill>}
              </button>
            );
          })}
          </div>
        )}

        {/* Status Banner */}
        {statusMessage && (
          <div
            style={{
              marginBottom: 16,
              padding: '10px 14px',
              borderRadius: 10,
              background: statusMessage.type === 'success' ? 'var(--color-success-bg)' : 'var(--color-error-bg)',
              border: `1px solid ${statusMessage.type === 'success' ? 'var(--color-success)' : 'var(--color-error)'}`,
              color: statusMessage.type === 'success' ? 'var(--color-success)' : 'var(--color-error)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontSize: 13,
              fontWeight: 500,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {statusMessage.type === 'success' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
              <span style={{ whiteSpace: 'pre-line' }}>{statusMessage.text}</span>
            </div>
            <button
              type="button"
              onClick={() => setStatusMessage(null)}
              style={{ background: 'transparent', border: 'none', color: 'inherit', padding: 4, cursor: 'pointer' }}
            >
              <X size={15} />
            </button>
          </div>
        )}

        {activeTab === 'overview' && (
          <OverviewView
            domains={visibleDomains}
            stats={overviewStats}
            attention={attention}
            healthIssues={healthIssueCount}
            onGo={selectTab}
            onAsk={() => navigate('/ask')}
            onGenerate={openBootstrap}
            onImport={() => setUploadModalOpen(true)}
          />
        )}

        {activeTab === 'mesh' && (
          <DomainsView
            domains={visibleDomains}
            selectedDomain={inspectingDomain}
            domainDetail={inspectingDomainData}
            onSelectDomain={(name) => {
              setDrawerMetric(null);
              handleOpenDomainInspector(name, inspectorActiveTab === 'yaml' ? 'metrics' : inspectorActiveTab);
            }}
            issueCountFor={(d) => domainIssueCount(d, { collisions: scopedCollisions, lintReport })}
            subTab={inspectorActiveTab}
            setSubTab={(t) => {
              setInspectorActiveTab(t);
              if (t !== 'metrics') setDrawerMetric(null);
            }}
            search={glossarySearchQuery}
            setSearch={setGlossarySearchQuery}
            metrics={activeMetrics}
            concepts={activeConcepts}
            policies={activePolicies}
            rowInfo={metricRowInfo}
            selectedMetricName={drawerMetricObj ? drawerMetric : null}
            onSelectMetric={handleSelectMetric}
            onShowMap={() => selectTab('graph')}
            onOpenItem={setSelectedGlossaryItem}
            onSync={() => handleSyncWithOsi(inspectingDomain)}
            yaml={{
              value: editorYaml,
              onChange: setEditorYaml,
              validationResult,
              onValidate: handleValidateYaml,
              onSave: handleSaveDomain,
            }}
            drawer={
              drawerMetricObj && inspectorActiveTab === 'metrics' ? (
                <MetricDrawer
                  metric={drawerMetricObj}
                  status={metricRowInfo(drawerMetricObj).status}
                  certification={metricRowInfo(drawerMetricObj).cert}
                  guardrail={metricRowInfo(drawerMetricObj).guardrail}
                  domainMetricNames={domainMetricNames}
                  impact={impactReports[drawerMetricObj.name]}
                  impactLoading={!!impactLoading[drawerMetricObj.name]}
                  lintIssues={lintIssuesFor(lintReport, { domain: inspectingDomain, objectName: drawerMetricObj.name })}
                  lintLoading={runningLint}
                  lintRan={!!lintReport}
                  parity={drawerParity}
                  onClose={() => setDrawerMetric(null)}
                  onSelectMetric={handleSelectMetric}
                  onUpdateMeta={handleUpdateMetricMeta}
                  onImpactView={() => handleJumpToBlastRadius(drawerMetricObj.name, 'METRIC')}
                  onParity={() => openParityFor(drawerMetricObj)}
                  onLintView={() => {
                    if (!lintReport) {
                      handleRunLinter();
                      return;
                    }
                    setHealthFilter('quality');
                    setHealthExpanded(null);
                    selectTab('governance');
                  }}
                  onAsk={() => handleJumpToAiTest(drawerMetricObj.name)}
                  onEditYaml={() => {
                    setDrawerMetric(null);
                    setInspectorActiveTab('yaml');
                  }}
                  onShowMap={() => selectTab('graph')}
                />
              ) : null
            }
          />
        )}

        {/* ══════════════════════════════════════════════════════════════════════════
            TAB 2: CONNECTION MAP (Interactive Lineage DAG)
           ══════════════════════════════════════════════════════════════════════════ */}
        {activeTab === 'graph' && (
          <div
            style={{
              height: 640,
              borderRadius: 18,
              overflow: 'hidden',
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface)',
            }}
          >
            <OntologyGraph projects={projectsList} />
          </div>
        )}

        {activeTab === 'governance' && (
          <HealthView
            hideWhatIf={mode === 'comparator'}
            rows={healthRows}
            filter={healthFilter}
            setFilter={setHealthFilter}
            expandedId={healthExpanded}
            setExpandedId={setHealthExpanded}
            metricOptions={allMetricNames}
            onRunLint={handleRunLinter}
            onExpandParity={() => {
              if (!reconMetric.trim()) {
                const first = allMetrics.find((m) => m.sql_expression) || allMetrics[0];
                if (first) prefillParity(first);
              }
            }}
            onOpenMetric={handleOpenMetricFromHealth}
            gov={{
              govProjectId, setGovProjectId, projectsList,
              blastTarget, setBlastTarget, blastType, setBlastType, blastChangeType, setBlastChangeType,
              blastTableContext, setBlastTableContext,
              runningBlast, onRunBlast: handleRunBlastRadius, blastReport,
              lintStewardSeverity, setLintStewardSeverity,
              onRunLintWithSteward: (sev) => handleRunLinter(sev),
              dimensionOptions: allDimensions,
              semanticViewOptions: allSemanticViews,
              reconMetric, setReconMetric, reconExpr, setReconExpr, reconTable, setReconTable,
              reconDaxExpr, setReconDaxExpr, reconCustomSql, setReconCustomSql,
              parityProjectMetrics, allMetrics, prefillParity,
              onUpdateMetricSql: handleUpdateMetricSql,
              reconTargetKind, setReconTargetKind, reconDims, setReconDims,
              reconDaxText, setReconDaxText, reconSqlText, setReconSqlText,
              reconAbsTol, setReconAbsTol, reconRelTol, setReconRelTol,
              reconMaxMismatchPct, setReconMaxMismatchPct,
              reconRegisterVerified, setReconRegisterVerified,
              runningRecon,
              onGenerateReconQueries: handleGenerateReconQueries,
              onRunLiveSql: handleRunLiveSql,
              onRunAutoDaxCertify: handleRunAutoDaxCertify,
              onRunRecon: handleRunReconciliation,
              onResetRecon: resetParityState,
              reconResult, reconSliceRows, reconQueries, reconError,
              rlsRoleName, setRlsRoleName, rlsTableName, setRlsTableName, rlsDaxExpr, setRlsDaxExpr,
              rlsTargetDb, setRlsTargetDb, rlsTargetSchema, setRlsTargetSchema,
              runningRls, onTranslateRls: handleTranslateRls, rlsDdl, rlsStatus, rlsReason,
            }}
          />
        )}

        {activeTab === 'blast-radius' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{
              padding: 18,
              borderRadius: 12,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
            }}>
              <WhatIfSimulator
                gov={{
                  govProjectId, setGovProjectId, projectsList,
                  blastTarget, setBlastTarget, blastType, setBlastType, blastChangeType, setBlastChangeType,
                  blastTableContext, setBlastTableContext,
                  runningBlast, onRunBlast: handleRunBlastRadius, blastReport,
                }}
                metricOptions={allMetricNames}
              />
            </div>
          </div>
        )}
      </div>

      {/* ══════════════════════════════════════════════════════════════════════════
          ITEM DETAIL DRAWER / MODAL (When clicking "View Details" on a row)
         ══════════════════════════════════════════════════════════════════════════ */}
      {selectedGlossaryItem && (
        <div
          onClick={() => setSelectedGlossaryItem(null)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 60,
            background: 'rgba(2, 6, 23, 0.65)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 20,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: 580,
              borderRadius: 20,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              padding: 24,
              boxShadow: '0 20px 50px rgba(0,0,0,0.45)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <div>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#0284C7', textTransform: 'uppercase' }}>
                  {selectedGlossaryItem.type} Inspector
                </span>
                <h3 style={{ fontSize: 18, fontWeight: 800, margin: '4px 0 0' }}>
                  {selectedGlossaryItem.data.name}
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setSelectedGlossaryItem(null)}
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}
              >
                <X size={18} />
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, fontSize: 13 }}>
              {selectedGlossaryItem.data.description && (
                <div>
                  <strong style={{ color: 'var(--text-secondary)', display: 'block', fontSize: 11 }}>DESCRIPTION</strong>
                  <span>{selectedGlossaryItem.data.description}</span>
                </div>
              )}
              {selectedGlossaryItem.data.sql_expression && (
                <div>
                  <strong style={{ color: 'var(--text-secondary)', display: 'block', fontSize: 11 }}>SQL FORMULA</strong>
                  <code style={{ color: '#10B981', fontWeight: 700 }}>{selectedGlossaryItem.data.sql_expression}</code>
                </div>
              )}
              {selectedGlossaryItem.data.filter_expression && (
                <div>
                  <strong style={{ color: 'var(--text-secondary)', display: 'block', fontSize: 11 }}>
                    ENFORCED WHERE POLICY
                  </strong>
                  <code style={{ color: '#F59E0B', fontWeight: 700 }}>{selectedGlossaryItem.data.filter_expression}</code>
                </div>
              )}
              {selectedGlossaryItem.data.derives_from?.length > 0 && (
                <div>
                  <strong style={{ color: 'var(--text-secondary)', display: 'block', fontSize: 11 }}>
                    DERIVED FROM METRICS
                  </strong>
                  <span>{selectedGlossaryItem.data.derives_from.join(', ')}</span>
                </div>
              )}
              {selectedGlossaryItem.data.attributes?.length > 0 && (
                <div>
                  <strong style={{ color: 'var(--text-secondary)', display: 'block', fontSize: 11, marginBottom: 6 }}>
                    ATTRIBUTES ({selectedGlossaryItem.data.attributes.length})
                  </strong>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {selectedGlossaryItem.data.attributes.map((a) => (
                      <span
                        key={a.name}
                        style={{
                          padding: '4px 10px',
                          borderRadius: 8,
                          background: 'var(--bg-app)',
                          border: '1px solid var(--border-main)',
                          fontFamily: 'monospace',
                          fontSize: 11,
                        }}
                      >
                        {a.name} ({a.data_type || 'VARCHAR'})
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 22 }}>
              <button
                type="button"
                onClick={() => {
                  const name = selectedGlossaryItem.data.name;
                  setSelectedGlossaryItem(null);
                  handleJumpToAiTest(name);
                }}
                style={PILL_BTN_DARK}
              >
                Ask AI about this
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Upload YAML Modal */}
      {uploadModalOpen && (
        <div
          onClick={() => setUploadModalOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 60,
            background: 'rgba(2, 6, 23, 0.65)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 20,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: 480,
              borderRadius: 20,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              padding: 24,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Upload Domain YAML</h3>
              <button
                type="button"
                onClick={() => setUploadModalOpen(false)}
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}
              >
                <X size={18} />
              </button>
            </div>

            <div
              onClick={() => fileInputRef.current?.click()}
              style={{
                border: '2px dashed var(--border-main)',
                borderRadius: 14,
                padding: 32,
                textAlign: 'center',
                cursor: 'pointer',
                marginBottom: 16,
              }}
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
                style={{ display: 'none' }}
              />
              <FileCode size={28} color="#0284C7" style={{ marginBottom: 8 }} />
              <div style={{ fontSize: 13, fontWeight: 600 }}>Click to select .yaml or .yml files</div>
              {selectedFiles.length > 0 && (
                <div style={{ fontSize: 12, color: '#10B981', marginTop: 8 }}>
                  Selected: {selectedFiles.map((f) => f.name).join(', ')}
                </div>
              )}
            </div>

            {uploadError && (
              <div style={{ color: '#EF4444', fontSize: 12, marginBottom: 12, whiteSpace: 'pre-line' }}>
                {uploadError}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                type="button"
                onClick={() => setUploadModalOpen(false)}
                style={{
                  padding: '8px 16px',
                  borderRadius: 999,
                  background: 'transparent',
                  border: '1px solid var(--border-main)',
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={loading || selectedFiles.length === 0}
                onClick={handleFileUpload}
                style={PILL_BTN_DARK}
              >
                Upload &amp; Compile
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Auto-Generate from OSI Modal */}
      {bootstrapModalOpen && (
        <div
          onClick={() => setBootstrapModalOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 60,
            background: 'rgba(2, 6, 23, 0.65)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 20,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: 480,
              borderRadius: 20,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              padding: 24,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Auto-Generate Ontology from Project</h3>
              <button
                type="button"
                onClick={() => setBootstrapModalOpen(false)}
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}
              >
                <X size={18} />
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 20 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
                  SOURCE PROJECT
                </label>
                <select
                  value={bootstrapProject}
                  onChange={(e) => setBootstrapProject(e.target.value)}
                  style={{ ...SELECT_STYLE, width: '100%' }}
                >
                  <option value="">Select a project...</option>
                  {projectsList.map((p) => (
                    <option key={p.id || p.name} value={p.id || p.name}>
                      {p.name || p.id}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
                  MODEL NAME (OPTIONAL)
                </label>
                <input
                  type="text"
                  value={bootstrapDomain}
                  onChange={(e) => setBootstrapDomain(e.target.value)}
                  placeholder="Defaults to project name"
                  style={{ ...INPUT_STYLE, width: '100%' }}
                />
              </div>
            </div>

            {bootstrapDuplicateError && (
              <div style={{ padding: '10px 12px', borderRadius: 10, background: 'var(--color-warning-bg)', color: 'var(--color-warning)', fontSize: 12, marginBottom: 14 }}>
                <div>{bootstrapDuplicateError}</div>
                <button
                  type="button"
                  disabled={bootstrapping}
                  onClick={() => handleBootstrapFromOsi(true)}
                  style={{ ...BTN_PRIMARY, marginTop: 8 }}
                >
                  Sync Existing Domain
                </button>
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                type="button"
                onClick={() => {
                  setBootstrapDuplicateError(null);
                  setBootstrapModalOpen(false);
                }}
                style={{
                  padding: '8px 16px',
                  borderRadius: 999,
                  background: 'transparent',
                  border: '1px solid var(--border-main)',
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={bootstrapping || !bootstrapProject}
                onClick={() => handleBootstrapFromOsi(false)}
                style={PILL_BTN_DARK}
              >
                {bootstrapping ? 'Generating...' : 'Generate Ontology'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Collisions Modal */}
      {showCollisionsModal && (
        <div
          onClick={() => setShowCollisionsModal(false)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 60,
            background: 'rgba(2, 6, 23, 0.65)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 20,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: 560,
              borderRadius: 20,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              padding: 24,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700, margin: 0, color: '#F59E0B' }}>
                Cross-Domain Metric Collisions ({domainData.collisions.length})
              </h3>
              <button
                type="button"
                onClick={() => setShowCollisionsModal(false)}
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}
              >
                <X size={18} />
              </button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 360, overflowY: 'auto' }}>
              {domainData.collisions.map((col, idx) => (
                <div
                  key={idx}
                  style={{
                    padding: 14,
                    borderRadius: 12,
                    background: 'var(--bg-app)',
                    border: '1px solid var(--border-main)',
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: 700, marginBottom: 6 }}>{col.item_name}</div>
                  {Object.entries(col.expressions || {}).map(([dom, expr]) => (
                    <div key={dom} style={{ fontFamily: 'monospace', fontSize: 11, marginTop: 4 }}>
                      <strong style={{ color: '#0284C7' }}>{dom}:</strong> {expr || 'N/A'}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
