import React, { useState, useMemo, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Target, CheckCircle2, X, Plus, Upload, FileText, Sparkles,
  ShieldCheck, Clock, Calendar, Check, AlertTriangle, AlertCircle, HelpCircle, Search
} from 'lucide-react';
import { authFetch } from '../utils/api';
import { useProjects, normalizeProjectId } from '../hooks/useProjects';
import { useDeployedViews, normalizeViewName } from '../hooks/useDeployedViews';
import Modal from '../components/common/Modal';
import WarehouseFilter from '../components/common/WarehouseFilter';
import GroupedProjectList from '../components/common/GroupedProjectList';
import BiTruthToolbar from '../components/bitruth/BiTruthToolbar';
import BiTruthCasesList from '../components/bitruth/BiTruthCasesList';
import BiTruthCaseDetail from '../components/bitruth/BiTruthCaseDetail';
import BiTruthGateStatus from '../components/bitruth/BiTruthGateStatus';
import BiTruthSchedules from '../components/bitruth/BiTruthSchedules';
import InjectDialog from '../components/bitruth/InjectDialog';
import BiTruthHistoryModal from '../components/bitruth/BiTruthHistoryModal';
import { useBiTruthCases } from '../hooks/useBiTruthCases';
import { useBiTruthRuns } from '../hooks/useBiTruthRuns';
import { useBiTruthInjection } from '../hooks/useBiTruthInjection';

const INPUT = {
  padding: '6px 10px',
  borderRadius: 6,
  background: 'var(--bg-surface-raised)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
};

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  padding: '5px 10px',
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
};

const BTN_PRIMARY = {
  ...BTN,
  borderColor: 'var(--accent-blue)',
  background: 'var(--accent-blue)',
  color: '#fff',
};

export default function BiTruthPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const {
    projects,
    availableWarehouses,
    warehouseCounts,
    selectedWarehouse,
    setSelectedWarehouse,
    selectedProjectId,
    setSelectedProjectId,
    loading: projectsLoading,
  } = useProjects();

  const [projectSearch, setProjectSearch] = useState('');
  const [allCasesCounts, setAllCasesCounts] = useState({});

  // Fetch all cases once to populate project list case counts
  const fetchAllCasesCounts = useCallback(async () => {
    try {
      const res = await authFetch('/api/bi-truth/cases');
      if (res.ok) {
        const data = await res.json();
        const counts = {};
        for (const c of data.cases || []) {
          const pid = c.project_id || '';
          if (pid) {
            counts[pid] = (counts[pid] || 0) + 1;
            const norm = normalizeProjectId(pid);
            counts[norm] = (counts[norm] || 0) + 1;
          }
        }
        setAllCasesCounts(counts);
      }
    } catch {
      // Ignore background counts error
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    const load = async () => {
      try {
        const res = await authFetch('/api/bi-truth/cases');
        if (res.ok && !ignore) {
          const data = await res.json();
          const counts = {};
          for (const c of data.cases || []) {
            const pid = c.project_id || '';
            if (pid) {
              counts[pid] = (counts[pid] || 0) + 1;
              const norm = normalizeProjectId(pid);
              counts[norm] = (counts[norm] || 0) + 1;
            }
          }
          setAllCasesCounts(counts);
        }
      } catch {
        // Ignore background counts error
      }
    };
    load();
    return () => {
      ignore = true;
    };
  }, []);

  // Project selection handler: clears ?view before any new request is made
  const selectProject = useCallback((pid) => {
    setSelectedProjectId(pid);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('view');
      return next;
    }, { replace: true });
  }, [setSelectedProjectId, setSearchParams]);

  // Deployed views hook for currently selected project
  const {
    views: deployedViews,
    selectedView: activeDeployedView,
    loading: viewsLoading,
  } = useDeployedViews(selectedProjectId);

  // Sanitized view: only allow view parameter if it genuinely belongs to this project's deployed views
  const selectedView = useMemo(() => {
    if (!selectedProjectId || viewsLoading || !deployedViews || deployedViews.length === 0) {
      return '';
    }
    const raw = searchParams.get('view') || '';
    if (!raw) return activeDeployedView || '';
    const match = deployedViews.find(
      (v) =>
        normalizeViewName(v.semantic_view) === normalizeViewName(raw) ||
        normalizeViewName(v.view) === normalizeViewName(raw)
    );
    return match ? (match.semantic_view || match.view) : (activeDeployedView || '');
  }, [selectedProjectId, viewsLoading, deployedViews, searchParams, activeDeployedView]);

  const {
    cases,
    error: casesError,
    setError: setCasesError,
    fetchCases,
    setStatus,
    updateCritical,
    updateWindows,
    acceptBenchmark,
    deleteCase,
  } = useBiTruthCases(selectedProjectId);

  const {
    runs,
    latestRun,
    gateVerdict,
    loading: runsLoading,
    error: runsError,
    setError: setRunsError,
    runSuite,
  } = useBiTruthRuns(selectedProjectId);

  const { history: injectionHistory } = useBiTruthInjection(selectedProjectId);

  const [selectedCaseId, setSelectedCaseId] = useState(null);
  const [limit, setLimit] = useState(15);
  const [replaceDrafts] = useState(false);
  const [busy, setBusy] = useState(false);
  const [infoMsg, setInfoMsg] = useState('');

  // Modals
  const [openAddModal, setOpenAddModal] = useState(false);
  const [openUploadModal, setOpenUploadModal] = useState(false);
  const [openSchedulesModal, setOpenSchedulesModal] = useState(false);
  const [openInjectModal, setOpenInjectModal] = useState(false);
  const [openHistoryModal, setOpenHistoryModal] = useState(false);

  // Form states
  const [manualQuestion, setManualQuestion] = useState('');
  const [manualMetric, setManualMetric] = useState('');
  const [manualExpectedSql, setManualExpectedSql] = useState('');
  const [manualExpectedDax, setManualExpectedDax] = useState('');
  const [uploadText, setUploadText] = useState('');
  const [uploadFileName, setUploadFileName] = useState('');

  const casesCounts = useMemo(() => {
    if (!selectedProjectId || !cases) return allCasesCounts;
    return {
      ...allCasesCounts,
      [selectedProjectId]: cases.length,
      [normalizeProjectId(selectedProjectId)]: cases.length,
    };
  }, [allCasesCounts, selectedProjectId, cases]);

  // Derive activeCaseId directly without effect
  const activeCaseId = (selectedCaseId && cases.some((c) => c.id === selectedCaseId))
    ? selectedCaseId
    : (cases[0]?.id || null);

  const resultsMap = useMemo(() => {
    return new Map((latestRun?.results || []).map((r) => [r.case_id, r]));
  }, [latestRun]);

  const activeCase = useMemo(() => {
    return cases.find((c) => c.id === activeCaseId) || null;
  }, [cases, activeCaseId]);

  const activeResult = useMemo(() => {
    return activeCaseId ? resultsMap.get(activeCaseId) : null;
  }, [resultsMap, activeCaseId]);

  const approvedCount = useMemo(() => {
    return cases.filter((c) => c.status === 'approved').length;
  }, [cases]);

  const handleRunSuite = async (view) => {
    setBusy(true);
    setRunsError('');
    try {
      await runSuite(view);
      await fetchCases();
      await fetchAllCasesCounts();
    } catch (err) {
      setRunsError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleGenerate = async () => {
    if (!selectedProjectId || !selectedView) return;
    setBusy(true);
    setCasesError('');
    try {
      const res = await authFetch('/api/bi-truth/generate', {
        method: 'POST',
        timeoutMs: 120000,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: selectedProjectId,
          view: selectedView,
          limit: Number(limit) || 15,
          replace_drafts: replaceDrafts,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setCasesError(data?.detail || 'Failed to generate test cases.');
      } else {
        setInfoMsg(`Generated ${data.added_count || 0} questions.`);
        setTimeout(() => setInfoMsg(''), 4000);
      }
      await fetchCases();
      fetchAllCasesCounts();
    } catch (err) {
      setCasesError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleSaveManual = async (e) => {
    e?.preventDefault();
    if (!manualQuestion.trim()) return;
    setBusy(true);
    try {
      const res = await authFetch('/api/bi-truth/cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: selectedProjectId || undefined,
          question: manualQuestion.trim(),
          metric_name: manualMetric.trim() || undefined,
          expected_sql: manualExpectedSql.trim() || undefined,
          expected_dax: manualExpectedDax.trim() || undefined,
          status: 'draft',
        }),
      });
      if (res.ok) {
        const created = await res.json().catch(() => ({}));
        setInfoMsg('Custom question added as draft. Review and approve to include in runs.');
        setManualQuestion('');
        setManualMetric('');
        setManualExpectedSql('');
        setManualExpectedDax('');
        setOpenAddModal(false);
        setTimeout(() => setInfoMsg(''), 4000);
        await fetchCases();
        fetchAllCasesCounts();
        if (created?.id) setSelectedCaseId(created.id);
      }
    } catch (err) {
      setCasesError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleImportText = async () => {
    if (!uploadText.trim()) return;
    setBusy(true);
    try {
      const res = await authFetch('/api/bi-truth/cases/import-text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: selectedProjectId || undefined,
          content: uploadText.trim(),
          filename: uploadFileName || 'import.csv',
          status: 'draft',
        }),
      });
      if (res.ok) {
        setInfoMsg('Document benchmark questions imported as drafts. Review and approve to include in runs.');
        setUploadText('');
        setUploadFileName('');
        setOpenUploadModal(false);
        setTimeout(() => setInfoMsg(''), 4000);
        await fetchCases();
        fetchAllCasesCounts();
      }
    } catch (err) {
      setCasesError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleFileUpload = (file) => {
    if (!file) return;
    setUploadFileName(file.name);
    const reader = new FileReader();
    reader.onload = (e) => setUploadText(String(e.target?.result || ''));
    reader.readAsText(file);
  };

  const handleToggleCritical = (caseId, isCrit, tol) => {
    updateCritical(caseId, isCrit, tol);
  };

  const handleDeleteCase = async (caseId) => {
    await deleteCase(caseId);
    if (selectedCaseId === caseId) {
      const remaining = cases.filter((c) => c.id !== caseId);
      setSelectedCaseId(remaining.length > 0 ? remaining[0].id : null);
    }
    fetchAllCasesCounts();
  };

  const selectedProject = (projects || []).find(
    (p) => p.id === selectedProjectId || p.project_id === selectedProjectId
  );

  const activePlatform = (
    selectedProject?.warehouse?.platform ||
    selectedProject?.target_warehouse?.platform ||
    selectedProject?.warehouse?.type ||
    'SNOWFLAKE'
  ).toUpperCase();

  const qualifyingCount = useMemo(() => {
    if (!latestRun || !latestRun.results) return 0;
    const passSet = new Set(
      latestRun.results
        .filter((r) => r.verdict === 'PASS' || r.passed)
        .map((r) => r.case_id)
    );
    return (cases || []).filter(
      (c) => c.status === 'approved' &&
             (c.expected_value !== null && c.expected_value !== undefined) &&
             passSet.has(c.id || c.case_id)
    ).length;
  }, [cases, latestRun]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
      }}
    >
      {/* ── MAIN TWO-PANE LAYOUT ─────────────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
        {/* ── LEFT PANE: PROJECTS LIST GROUPED BY WAREHOUSE ────────────────── */}
        <div
          style={{
            width: 310,
            borderRight: '1px solid var(--border-main)',
            background: 'var(--bg-surface)',
            display: 'flex',
            flexDirection: 'column',
            flexShrink: 0,
          }}
        >
          {/* Projects Search Header & Warehouse Filter */}
          <div
            style={{
              padding: '12px 14px',
              borderBottom: '1px solid var(--border-main)',
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div
                style={{
                  fontSize: 11.5,
                  fontWeight: 700,
                  color: 'var(--text-tertiary)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                }}
              >
                Projects
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', fontWeight: 500 }}>
                {projects.length} projects
              </div>
            </div>

            <WarehouseFilter
              warehouses={availableWarehouses}
              selectedWarehouse={selectedWarehouse}
              onSelectWarehouse={setSelectedWarehouse}
              counts={warehouseCounts}
              totalCount={projects.length}
            />

            <div style={{ position: 'relative' }}>
              <Search
                size={14}
                style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-tertiary)' }}
              />
              <input
                type="text"
                placeholder="Filter projects…"
                value={projectSearch}
                onChange={(e) => setProjectSearch(e.target.value)}
                style={{
                  ...INPUT,
                  width: '100%',
                  boxSizing: 'border-box',
                  paddingLeft: 30,
                  paddingRight: projectSearch ? 26 : 8,
                }}
              />
              {projectSearch && (
                <button
                  type="button"
                  onClick={() => setProjectSearch('')}
                  style={{
                    position: 'absolute',
                    right: 8,
                    top: 8,
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'var(--text-tertiary)',
                    padding: 0,
                  }}
                  title="Clear search"
                  aria-label="Clear search"
                >
                  <X size={13} />
                </button>
              )}
            </div>
          </div>

          {/* Projects Scrollable Grouped List */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
            <GroupedProjectList
              projects={projects}
              selectedProjectId={selectedProjectId}
              onSelectProject={selectProject}
              selectedWarehouse={selectedWarehouse}
              searchQuery={projectSearch}
              mode="bitruth"
              casesCounts={casesCounts}
              onClearFilters={() => {
                setProjectSearch('');
                setSelectedWarehouse('');
              }}
              loading={projectsLoading && projects.length === 0}
            />
          </div>
        </div>

        {/* ── RIGHT PANE: BI TRUTH SUITE WORKBENCH ─────────────────────────── */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            padding: 14,
            overflow: 'hidden',
          }}
        >
          {/* Top Header Toolbar with View Selector */}
          <BiTruthToolbar
            projectId={selectedProjectId}
            selectedView={selectedView}
            platform={activePlatform}
            cases={cases}
            approvedCount={approvedCount}
            latestRun={latestRun}
            busy={busy || runsLoading}
            limit={limit}
            qualifyingCount={qualifyingCount}
            onLimitChange={setLimit}
            onRunSuite={handleRunSuite}
            onGenerate={handleGenerate}
            onOpenAdd={() => setOpenAddModal(true)}
            onOpenImport={() => setOpenUploadModal(true)}
            onOpenSchedules={() => setOpenSchedulesModal(true)}
            onOpenHistory={() => setOpenHistoryModal(true)}
            onOpenInject={() => setOpenInjectModal(true)}
          />

          {/* Deploy Gate Status Banner (Compact & Collapsible) */}
          {gateVerdict && <BiTruthGateStatus verdict={gateVerdict} />}

          {/* Alerts */}
          {(casesError || runsError) && (
            <div
              style={{
                padding: '8px 12px',
                borderRadius: 6,
                background: 'rgba(239,68,68,0.1)',
                color: 'var(--color-error)',
                fontSize: 12,
              }}
            >
              {casesError || runsError}
            </div>
          )}
          {infoMsg && (
            <div
              style={{
                padding: '8px 12px',
                borderRadius: 6,
                background: 'rgba(34,197,94,0.1)',
                color: 'var(--color-success)',
                fontSize: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <CheckCircle2 size={13} /> {infoMsg}
            </div>
          )}

          {/* ── Main Split View (Master List on Left, Detail Workbench on Right) ── */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(320px, 42%) 1fr',
              gap: 12,
              flex: 1,
              minHeight: 0,
              overflow: 'hidden',
            }}
          >
            {/* Master List Column */}
            <BiTruthCasesList
              cases={cases}
              resultsMap={resultsMap}
              selectedCaseId={activeCaseId}
              onSelectCase={setSelectedCaseId}
              busy={busy}
              onGenerate={handleGenerate}
              onToggleCritical={handleToggleCritical}
            />

            {/* Active Case Detail Column */}
            <BiTruthCaseDetail
              caseItem={activeCase}
              result={activeResult}
              onUpdateStatus={setStatus}
              onUpdateCritical={handleToggleCritical}
              onUpdateWindows={updateWindows}
              onAcceptBenchmark={acceptBenchmark}
              onDelete={handleDeleteCase}
            />
          </div>
        </div>
      </div>

      {/* ── Modal: Add Custom Question ── */}
      <Modal
        open={openAddModal}
        onClose={() => setOpenAddModal(false)}
        title="Add Ground Truth Question"
      >
        <form onSubmit={handleSaveManual} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ display: 'block', fontSize: 11.5, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Question (Natural Language prompt) *
            </label>
            <input
              type="text"
              required
              placeholder="e.g. What is Total Revenue for FY2024?"
              value={manualQuestion}
              onChange={(e) => setManualQuestion(e.target.value)}
              style={{ ...INPUT, width: '100%', boxSizing: 'border-box' }}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 11.5, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Target Metric / Measure Name (Optional)
            </label>
            <input
              type="text"
              placeholder="e.g. TOTAL_REVENUE"
              value={manualMetric}
              onChange={(e) => setManualMetric(e.target.value)}
              style={{ ...INPUT, width: '100%', boxSizing: 'border-box' }}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 11.5, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Certified Reference SQL (Optional)
            </label>
            <textarea
              rows={3}
              placeholder="SELECT SUM(AMOUNT) FROM SALES WHERE YEAR = 2024"
              value={manualExpectedSql}
              onChange={(e) => setManualExpectedSql(e.target.value)}
              style={{ ...INPUT, width: '100%', boxSizing: 'border-box', fontFamily: 'monospace' }}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 11.5, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Reference DAX Expression (Optional)
            </label>
            <textarea
              rows={2}
              placeholder="CALCULATE([Total Sales], 'Date'[Year] = 2024)"
              value={manualExpectedDax}
              onChange={(e) => setManualExpectedDax(e.target.value)}
              style={{ ...INPUT, width: '100%', boxSizing: 'border-box', fontFamily: 'monospace' }}
            />
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 8 }}>
            <button type="button" style={BTN} onClick={() => setOpenAddModal(false)}>
              Cancel
            </button>
            <button type="submit" disabled={busy || !manualQuestion.trim()} style={{ ...BTN_PRIMARY, background: 'var(--accent-blue)' }}>
              Add &amp; Approve
            </button>
          </div>
        </form>
      </Modal>

      {/* ── Modal: Import Benchmark Questions ── */}
      <Modal
        open={openUploadModal}
        onClose={() => setOpenUploadModal(false)}
        title="Import Benchmark Questions"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: 0 }}>
            Paste CSV / TSV text or upload a document containing benchmark questions and measures.
          </p>

          <input
            type="file"
            accept=".csv,.tsv,.txt,.json"
            onChange={(e) => handleFileUpload(e.target.files?.[0])}
            style={{ fontSize: 12 }}
          />

          <textarea
            rows={8}
            placeholder="Question,Metric,ExpectedValue\nWhat is ARR?,ARR,1200000"
            value={uploadText}
            onChange={(e) => setUploadText(e.target.value)}
            style={{ ...INPUT, width: '100%', boxSizing: 'border-box', fontFamily: 'monospace' }}
          />

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button type="button" style={BTN} onClick={() => setOpenUploadModal(false)}>
              Cancel
            </button>
            <button
              type="button"
              disabled={busy || !uploadText.trim()}
              style={{ ...BTN_PRIMARY, background: 'var(--accent-blue)' }}
              onClick={handleImportText}
            >
              Import Questions
            </button>
          </div>
        </div>
      </Modal>

      {/* ── Modal: Schedules ── */}
      <Modal
        open={openSchedulesModal}
        onClose={() => setOpenSchedulesModal(false)}
        title="BI Truth Verification Schedules"
      >
        <BiTruthSchedules projectId={selectedProjectId} />
      </Modal>

      {/* ── Modal: History (Reads vs Writes) ── */}
      <BiTruthHistoryModal
        open={openHistoryModal}
        onClose={() => setOpenHistoryModal(false)}
        runs={runs}
        injectionHistory={injectionHistory}
      />

      {/* ── Modal: Inject to AI (Write Action) ── */}
      <InjectDialog
        open={openInjectModal}
        onClose={() => setOpenInjectModal(false)}
        projectId={selectedProjectId}
        selectedView={selectedView}
        platform={activePlatform}
      />
    </div>
  );
}
