import { useCallback } from 'react';
import { api } from '../utils/api';
import { useProjectWizardStore } from '../store/projectWizardStore';
import { buildDryRunPayload } from '../utils/dryRunPayload';
import { validateTargetName } from '../utils/validateTargetName';
import { normalizeRows } from '../utils/projectHelpers';
import { escapeYamlString } from '../utils/yaml';
import { isBlockingRow as isDryRunBlockingRow } from '../components/DryRunMappingTable';

/**
 * useProjectMappings
 * Owns all mapping-related logic for Step 4:
 * dry-run, field edit, deploy, bulk resolve, and table/column target updates.
 */
export function useProjectMappings({
  sourceConnector,
  targetConnectors,
  fabricAccountId,
  fabricWorkspaceId,
  snowflakeDatabase,
  targetDatabase,
  selectedModelNames,
  createdProject,
  setCreatedProject,
  currentMappingSignature,
  detectedMappings,
  setDetectedMappings,
  setDetectedEntityMappings,
  setDryRunData,
  setMappingError,
  setMappingDryRunStatus,
  setMappingDryRunError,
  setMappingDryRunSignature,
  setMappingReadyToProceed,
  setUnmappedAcknowledged,
  setEditingRow,
  setIsSavingEdit,
  setIsDeploying,
  setDeployError,
  setStep,
  addLog,
}) {
  const handleDryRun = useCallback(async () => {
    setMappingDryRunStatus('loading');
    setMappingError('');

    const projectId = createdProject?.id || createdProject?.project_id || 'preview';
    const { sourceConfig, targetConfig } = buildDryRunPayload({
      sourceConnector, targetConnectors, fabricAccountId,
      fabricWorkspaceId, snowflakeDatabase, targetDatabase, selectedModelNames,
    });

    try {
      const response = await api.runProjectDryRun(projectId, {
        source_config: sourceConfig,
        target_config: targetConfig,
        selected_sources: selectedModelNames,
      });

      if (response?.success === false) {
        setMappingError(response?.error || 'Dry run failed.');
        setMappingDryRunStatus('error');
        return;
      }

      setDetectedMappings(normalizeRows(response));
      setDryRunData(response);
      setMappingDryRunStatus('success');
      setMappingDryRunSignature(currentMappingSignature);
      setUnmappedAcknowledged(false);
      setMappingReadyToProceed(true);
    } catch (err) {
      setMappingError(err?.message || 'Dry run failed.');
      setMappingDryRunStatus('error');
    }
  }, [
    createdProject, sourceConnector, fabricWorkspaceId, snowflakeDatabase,
    targetConnectors, targetDatabase, selectedModelNames, currentMappingSignature,
    fabricAccountId, setMappingDryRunStatus, setMappingError, setDetectedMappings,
    setDryRunData, setMappingDryRunSignature, setUnmappedAcknowledged, setMappingReadyToProceed,
  ]);

  const handleFieldEdit = useCallback(async (rowId, updates) => {
    setIsSavingEdit(true);
    const targetPlatform  = Array.from(targetConnectors)[0] || 'snowflake';
    const editingRowData  = detectedMappings.find(r => r.id === rowId);
    const validation      = validateTargetName(updates.target_name, targetPlatform, editingRowData?.source_type || '', updates.target_data_type);

    if (!validation.isValid) { setIsSavingEdit(false); return; }

    const projectId = createdProject?.id || createdProject?.project_id || 'preview';
    try {
      await api.updateMapping(projectId, rowId, {
        target_name: updates.target_name,
        target_data_type: updates.target_data_type,
        synonyms: updates.synonyms,
        status: 'manual',
      });
      setDetectedMappings(prev => prev.map(row =>
        row.id === rowId
          ? { ...row, target_field: updates.target_name, target_type: updates.target_data_type, synonyms: updates.synonyms, status: 'manual', isDirty: true }
          : row
      ));
      setEditingRow(null);
    } catch (err) {
      setMappingError(err?.message || 'Failed to save field edit.');
    } finally {
      setIsSavingEdit(false);
    }
  }, [targetConnectors, detectedMappings, createdProject, setIsSavingEdit, setDetectedMappings, setEditingRow, setMappingError]);

  const handleDeploy = useCallback(async () => {
    const blockingRows = detectedMappings.filter(row => isDryRunBlockingRow(row));
    if (blockingRows.length > 0) { setDeployError('Resolve all collisions before deploying.'); return; }

    setIsDeploying(true);
    setDeployError('');

    const projectId    = createdProject?.id || createdProject?.project_id || 'preview';
    const fieldMappings = detectedMappings.map(row => ({
      id: row.id, source_name: row.source_field, target_name: row.target_field,
      target_data_type: row.target_type, status: row.status, entity_kind: row.entity_kind,
    }));

    try {
      const result = await api.deployMappings(projectId, fieldMappings);
      if (result?.success) {
        if (result.project_id) setCreatedProject({ id: result.project_id, project_id: result.project_id });
        setMappingReadyToProceed(true);
        setStep(5);
      } else {
        const errMsg = (Array.isArray(result?.errors) && result.errors[0])
          ? `Deploy failed: ${result.errors[0]}` : 'Deploy failed: unknown error';
        setDeployError(errMsg);
      }
    } catch (err) {
      setDeployError(err?.message || 'Deploy failed.');
    } finally {
      setIsDeploying(false);
    }
  }, [detectedMappings, createdProject, setCreatedProject, setIsDeploying, setDeployError, setMappingReadyToProceed, setStep]);

  const handleDeployMapping = useCallback(async (fieldMappings, goNext) => {
    const projectId = createdProject?.id || createdProject?.project_id || 'preview';
    try {
      const result = await api.deployMappings(projectId, fieldMappings);
      if (result.success) {
        if (result.project_id && projectId === 'preview') setCreatedProject({ id: result.project_id, project_id: result.project_id });
        setMappingReadyToProceed(true);
        goNext();
      } else {
        setMappingError('Deployment returned without success.');
      }
    } catch (err) {
      setMappingError(err.message || 'Deploy failed');
      console.error('Deploy failed:', err);
    }
  }, [createdProject, setCreatedProject, setMappingReadyToProceed, setMappingError]);

  const handleBulkResolved = useCallback((resolvedMap) => {
    if (!resolvedMap || typeof resolvedMap !== 'object') return;
    setDetectedMappings(prev => prev.map(row => {
      const nextTarget = resolvedMap[row.id];
      if (!nextTarget) return row;
      return { ...row, target_field: nextTarget, status: 'auto_resolved', validation_status: 'valid', validation_code: 'OK', validation_message: '', collision_detected: false, auto_resolved: true, isDirty: true };
    }));
  }, [setDetectedMappings]);

  const updateTableMappingTarget = useCallback((mappingId, nextTarget) => {
    const normalized = String(nextTarget || '').trim();
    setDetectedMappings(prev => prev.map(m => m.id === mappingId ? { ...m, target: normalized, status: 'manual' } : m));
    setDetectedEntityMappings(prev => prev.map(m => m.id === mappingId ? { ...m, target_name: normalized, is_user_edited: true, status: 'manual' } : m));
  }, [setDetectedMappings, setDetectedEntityMappings]);

  const updateColumnMappingTarget = useCallback((tableMappingId, sourceColumnName, nextTarget) => {
    const normalized = String(nextTarget || '').trim();
    let updatedSourcePath = '';
    setDetectedMappings(prev => prev.map(m => {
      if (m.id !== tableMappingId) return m;
      updatedSourcePath = String(m.source_path || '');
      return { ...m, status: 'manual', columns: (m.columns || []).map(c => String(c.source || '') === String(sourceColumnName || '') ? { ...c, target: normalized } : c) };
    }));
    setDetectedEntityMappings(prev => prev.map(m => {
      const isMatch = String(m.entity_kind || '') === 'column'
        && String(m.parent_source_path || '') === String(updatedSourcePath || '')
        && String(m.source_name || '') === String(sourceColumnName || '');
      return isMatch ? { ...m, target_name: normalized, is_user_edited: true, status: 'manual' } : m;
    }));
  }, [setDetectedMappings, setDetectedEntityMappings]);

  return {
    handleDryRun,
    handleFieldEdit,
    handleDeploy,
    handleDeployMapping,
    handleBulkResolved,
    updateTableMappingTarget,
    updateColumnMappingTarget,
  };
}

/**
 * useProjectBuilder
 * Owns buildSourceConfig, buildTargetConfigs, buildConfigYaml, and handleFinish.
 */
export function useProjectBuilder({
  name, description, sourceConnector, targetConnectors, intermediateFormat,
  tags, configMode, autoRelationships, generateDescriptions,
  fabricAccountId, fabricWorkspaceId, selectedWorkspace, selectedModelNames,
  snowflakeAccountId, snowflakeDatabase, snowflakeSchema,
  targetAccount, targetWarehouse, targetDatabase, targetSchema,
  databricksAccountId, pbixSourceMode, selectedLocalFolderId, selectedLocalFolderTag,
  selectedPbixFile, pbixFile, resolvedPbixPath, selectedModels, wsModels,
  detectedMappings, detectedEntityMappings, createReverseProject,
  createdProject, setCreatedProject,
  setSaving, setCreateError, setRunWarning, setPbixUploadPath,
  onSaveConfig, editMode, initialData, navigate, clearWizardState,
}) {
  const buildSourceConfig = useCallback(() => {
    const source = { type: sourceConnector };
    if (sourceConnector === 'fabric') {
      if (fabricAccountId)            source.identity_id  = fabricAccountId;
      if (fabricWorkspaceId)          source.workspace_id = fabricWorkspaceId;
      if (selectedWorkspace?.name)    source.workspace    = selectedWorkspace.name;
      if (selectedModelNames.length)  source.models       = selectedModelNames;
    }
    if (sourceConnector === 'snowflake') {
      if (snowflakeAccountId)         source.identity_id = snowflakeAccountId;
      if (snowflakeDatabase.trim())   source.database    = snowflakeDatabase.trim();
      if (snowflakeSchema.trim())     source.schema      = snowflakeSchema.trim();
      if (selectedModelNames.length)  source.models      = selectedModelNames;
    }
    if (sourceConnector === 'pbix') {
      if (pbixSourceMode === 'TAG') {
        if (selectedLocalFolderId)    source.local_folder_id  = selectedLocalFolderId;
        if (selectedLocalFolderTag)   source.local_folder_tag = selectedLocalFolderTag;
        if (selectedPbixFile?.name)   source.file_name        = selectedPbixFile.name;
      } else if (pbixFile?.name) {
        source.file_name = pbixFile.name;
      }
      if (resolvedPbixPath) { source.pbix_path = resolvedPbixPath; source.pbix_file_path = resolvedPbixPath; }
    }
    return source;
  }, [sourceConnector, fabricAccountId, fabricWorkspaceId, selectedWorkspace, selectedModelNames, snowflakeAccountId, snowflakeDatabase, snowflakeSchema, pbixSourceMode, selectedLocalFolderId, selectedLocalFolderTag, selectedPbixFile, pbixFile, resolvedPbixPath]);

  const buildTargetConfigs = useCallback(() => {
    return Array.from(targetConnectors).map(connector => {
      const target = { type: connector };
      if (connector === 'snowflake') {
        if (targetAccount.trim())   target.account    = targetAccount.trim();
        if (targetWarehouse.trim()) target.warehouse  = targetWarehouse.trim();
        if (targetDatabase.trim())  target.database   = targetDatabase.trim();
        if (targetSchema.trim())    target.schema     = targetSchema.trim();
        if (snowflakeAccountId)     target.identity_id = snowflakeAccountId;
      }
      if (connector === 'fabric') {
        if (fabricAccountId)        target.identity_id  = fabricAccountId;
        if (fabricWorkspaceId)      target.workspace_id = fabricWorkspaceId;
        if (selectedWorkspace?.name) target.workspace   = selectedWorkspace.name;
      }
      if (connector === 'databricks') {
        if (databricksAccountId)    target.identity_id = databricksAccountId;
      }
      return target;
    });
  }, [targetConnectors, targetAccount, targetWarehouse, targetDatabase, targetSchema, snowflakeAccountId, fabricAccountId, fabricWorkspaceId, selectedWorkspace, databricksAccountId]);

  const buildConfigYaml = useCallback((source, targets, overrides = {}) => {
    const projectName = String(overrides.projectName || name.trim() || 'Untitled Project').trim();
    const projectDescription = overrides.description ?? description.trim();
    const lines = [`project_name: "${escapeYamlString(projectName)}"`];
    if (projectDescription) lines.push(`description: "${escapeYamlString(projectDescription)}"`);

    lines.push('source:');
    lines.push(`  type: ${source.type}`);
    if (source.identity_id)    lines.push(`  identity_id: "${escapeYamlString(source.identity_id)}"`);
    if (source.workspace_id)   lines.push(`  workspace_id: "${escapeYamlString(source.workspace_id)}"`);
    if (source.workspace)      lines.push(`  workspace: "${escapeYamlString(source.workspace)}"`);
    if (source.database)       lines.push(`  database: "${escapeYamlString(source.database)}"`);
    if (source.schema)         lines.push(`  schema: "${escapeYamlString(source.schema)}"`);
    if (source.models?.length) { lines.push('  models:'); source.models.forEach(m => lines.push(`    - "${escapeYamlString(m)}"`)); }
    else if (source.model)     lines.push(`  model: "${escapeYamlString(source.model)}"`);
    if (source.pbix_path)          lines.push(`  pbix_path: "${escapeYamlString(source.pbix_path)}"`);
    if (source.pbix_file_path)     lines.push(`  pbix_file_path: "${escapeYamlString(source.pbix_file_path)}"`);
    if (source.local_folder_id)    lines.push(`  local_folder_id: "${escapeYamlString(source.local_folder_id)}"`);
    if (source.local_folder_tag)   lines.push(`  local_folder_tag: "${escapeYamlString(source.local_folder_tag)}"`);

    lines.push('targets:');
    targets.forEach(t => {
      lines.push(`  - type: ${t.type}`);
      if (t.database)    lines.push(`    database: "${escapeYamlString(t.database)}"`);
      if (t.schema)      lines.push(`    schema: "${escapeYamlString(t.schema)}"`);
      if (t.account)     lines.push(`    account: "${escapeYamlString(t.account)}"`);
      if (t.warehouse)   lines.push(`    warehouse: "${escapeYamlString(t.warehouse)}"`);
      if (t.identity_id) lines.push(`    identity_id: "${escapeYamlString(t.identity_id)}"`);
      if (t.workspace_id) lines.push(`    workspace_id: "${escapeYamlString(t.workspace_id)}"`);
      if (t.workspace)   lines.push(`    workspace: "${escapeYamlString(t.workspace)}"`);
    });

    const mappingOverrides = (detectedMappings || [])
      .map(row => ({ source_path: String(row?.source_path || '').trim(), target_name: String(row?.target_field || row?.target_name || row?.target || '').trim() }))
      .filter(row => row.source_path && row.target_name);
    if (mappingOverrides.length) {
      lines.push('mappings_overrides:');
      mappingOverrides.forEach(row => { lines.push(`  - source_path: "${escapeYamlString(row.source_path)}"`); lines.push(`    target_name: "${escapeYamlString(row.target_name)}"`); });
    }

    let relationships = [];
    try { const s = sessionStorage.getItem('detectedRelationships'); if (s) relationships = JSON.parse(s); } catch { relationships = []; }
    if (relationships.length > 0 && autoRelationships) {
      lines.push('relationships:');
      relationships.forEach(rel => {
        lines.push(`  - source: "${escapeYamlString(rel.source)}"`);
        lines.push(`    target: "${escapeYamlString(rel.target)}"`);
        lines.push(`    join_type: ${rel.joinType}`);
        lines.push(`    condition: "${escapeYamlString(rel.condition)}"`);
        lines.push(`    confidence: ${rel.confidence}`);
      });
    }

    lines.push('ui:');
    lines.push(`  intermediate_format: "${escapeYamlString(intermediateFormat)}"`);
    lines.push(`  editor_mode: "${escapeYamlString(configMode)}"`);

    if (selectedModels.size) {
      lines.push('selection:');
      lines.push('  model_ids:');
      const resolvedIds = new Set();
      [...selectedModels].forEach(modelKey => {
        const id = modelKey.includes('::') ? modelKey.split('::')[1] : modelKey;
        resolvedIds.add(id);
        const isGuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
        if (!isGuid && fabricWorkspaceId && wsModels[fabricWorkspaceId]) {
          const match = wsModels[fabricWorkspaceId].find(m => m.name === id);
          if (match?.id) resolvedIds.add(match.id);
        }
      });
      [...resolvedIds].forEach(id => lines.push(`    - "${escapeYamlString(id)}"`));
    }

    lines.push('options:');
    lines.push(`  auto_relationships: ${autoRelationships}`);
    lines.push(`  generate_descriptions: ${generateDescriptions}`);
    try {
      const ws = useProjectWizardStore.getState().wizard;
      const wsStrategy = String(ws?.write_strategy || 'copy').toLowerCase();
      if (wsStrategy) lines.push(`  write_strategy: ${wsStrategy}`);
    } catch { /* ignore */ }

    return lines.join('\n');
  }, [name, description, intermediateFormat, configMode, autoRelationships, generateDescriptions, detectedMappings, selectedModels, fabricWorkspaceId, wsModels]);

  const handleFinish = useCallback(async () => {
    setCreateError('');
    setRunWarning('');

    if ((sourceConnector === 'fabric' || sourceConnector === 'snowflake') && selectedModels.size > 0 && selectedModelNames.length === 0) {
      setCreateError('Selected models could not be resolved. Please reselect the model(s) and try again.');
      return;
    }
    if (sourceConnector === 'pbix' && !resolvedPbixPath) {
      setCreateError(pbixSourceMode === 'TAG' ? 'Select a PBIX file from the tagged folder before finishing.' : 'Upload a .pbix file before finishing.');
      return;
    }

    setSaving(true);
    try {
      const source  = buildSourceConfig();
      const targets = buildTargetConfigs();

      let relationships = [];
      try { const s = sessionStorage.getItem('detectedRelationships'); if (s) { relationships = JSON.parse(s); sessionStorage.removeItem('detectedRelationships'); } } catch { relationships = []; }

      if (editMode && onSaveConfig) {
        const configYaml = buildConfigYaml(source, targets);
        const syncMode   = (useProjectWizardStore.getState().wizard.write_strategy || 'copy');
        await onSaveConfig(configYaml, { name: name.trim(), description: description.trim(), tags: Array.from(tags), sync_mode: syncMode });
        if (initialData?.id) navigate(`/projects/${initialData.id}/config`);
        setSaving(false);
        return;
      }

      const payload = {
        name: name.trim(), description: description.trim() || undefined,
        source, targets, tags: [...tags],
        mappings: detectedMappings.length > 0 ? detectedMappings : undefined,
        relationships: relationships.length > 0 ? relationships : undefined,
        mapping_options: { auto_detect_relationships: autoRelationships, generate_descriptions: generateDescriptions },
        preferred_interface: 'ui',
        config_yaml: buildConfigYaml(source, targets),
      };

      if (sourceConnector === 'fabric' && selectedWorkspace) {
        payload.selectedWorkspaceId = selectedWorkspace.id || fabricWorkspaceId;
        payload.selectedAccountId   = selectedWorkspace.account_id || selectedWorkspace.accountId;
        payload.account_id          = selectedWorkspace.account_id || selectedWorkspace.accountId;
      }

      let project = await api.createProject(payload);
      if (!project?.id && !project?.project_id) {
        try {
          const all = await api.listProjects();
          const candidates = (all || []).filter(p => String(p?.name || '').trim() === payload.name).sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
          if (candidates.length > 0) project = candidates[0];
        } catch { /* keep original */ }
      }

      setCreatedProject(project);
      const projectId = project?.id || project?.project_id;

      if (projectId && detectedEntityMappings.length > 0) {
        try {
          await api.deleteMappings(projectId);
          for (const mapping of detectedEntityMappings) {
            await api.updateMapping(String(mapping.id), { ...mapping, project_id: projectId });
          }
        } catch (err) {
          setRunWarning(prev => `${prev ? prev + ' ' : ''}${err?.message || 'Mappings could not be fully persisted.'}`.trim());
        }
      }

      if (sourceConnector === 'pbix' && projectId && pbixFile) {
        try {
          const uploadResp   = await api.uploadProjectPbix(projectId, pbixFile);
          const persistedPath = String(uploadResp?.path || '').trim();
          if (persistedPath) {
            setPbixUploadPath(persistedPath);
            const updatedSource = { ...source, pbix_path: persistedPath, pbix_file_path: persistedPath };
            await api.saveProjectConfig(projectId, buildConfigYaml(updatedSource, targets));
            setCreatedProject(prev => ({ ...(prev || {}), pbix_file_path: persistedPath }));
          }
        } catch (err) {
          setRunWarning(prev => `${prev ? prev + ' ' : ''}${err?.message || 'PBIX file uploaded but could not be linked to project.'}`.trim());
        }
      }

      if (createReverseProject && targets.length > 0) {
        const reverseSource  = { ...targets[0] };
        const reverseTargets = [{ type: source.type }];
        const reverseName    = `${payload.name}_${targets[0].type}_to_${source.type}`;
        try {
          let reverseProject = await api.createProject({ ...payload, name: reverseName, source: reverseSource, targets: reverseTargets, target: reverseTargets[0], config_yaml: buildConfigYaml(reverseSource, reverseTargets, { projectName: reverseName }) });
          if (!reverseProject?.id && !reverseProject?.project_id) {
            const all = await api.listProjects();
            const candidates = (all || []).filter(p => String(p?.name || '').trim() === reverseName).sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
            if (candidates.length > 0) reverseProject = candidates[0];
          }
        } catch (err) {
          setRunWarning(prev => `${prev ? prev + ' ' : ''}Primary project was created. Reverse project warning: ${err?.message || 'Unknown error'}`.trim());
        }
      }

      if (projectId) {
        clearWizardState();
        navigate(`/projects/${projectId}/config`);
      }

    } catch (err) {
      setCreatedProject(null);
      setCreateError(err?.message || 'Create project failed.');
      console.error('Create project failed:', err);
    } finally {
      setSaving(false);
    }
  }, [
    sourceConnector, selectedModels, selectedModelNames, resolvedPbixPath, pbixSourceMode,
    buildSourceConfig, buildTargetConfigs, buildConfigYaml, editMode, onSaveConfig,
    name, description, tags, detectedMappings, detectedEntityMappings, autoRelationships,
    generateDescriptions, selectedWorkspace, fabricWorkspaceId, pbixFile, createReverseProject,
    setCreateError, setRunWarning, setSaving, setCreatedProject, setPbixUploadPath,
    initialData, navigate, clearWizardState,
  ]);

  return { buildSourceConfig, buildTargetConfigs, buildConfigYaml, handleFinish };
}
