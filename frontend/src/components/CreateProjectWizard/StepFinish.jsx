import React from 'react';
import { Check, Loader2, Settings2 } from 'lucide-react';
import { ToggleOption, footerBtn } from '../WizardUIComponents';

export function StepFinish({
  name,
  saving,
  createReverseProject,
  setCreateReverseProject,
  createdProject,
  createError,
  runWarning,
  sourceConnector,
  targetConnectors,
  intermediateFormat,
  selectedWorkspace,
  navigate,
  editMode,
  diff,
  selectedModels,
  savedModels,
  selectedModelNameByKey,
  onClear,
}) {
  const createdProjectId = createdProject?.id || createdProject?.project_id;

  if (saving && !createdProject) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 0' }}>
        <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'var(--accent-blue)20', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
          <Loader2 size={28} style={{ color: 'var(--accent-blue)', animation: 'spin 1s linear infinite' }} />
        </div>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>{editMode ? 'Saving Changes' : 'Creating Project'}</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', marginBottom: 24 }}>
          {editMode ? `Updating configuration for "${name}"...` : `Setting up configurations and initializing "${name}"...`}
        </p>
      </div>
    );
  }

  if (createdProject) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '20px 0' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'var(--color-success)20', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
            <Check size={28} style={{ color: 'var(--color-success)' }} />
          </div>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>{editMode ? 'Project Updated!' : 'Project Created!'}</h2>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', marginBottom: 24 }}>
            {editMode ? `Project "${name}" has been updated successfully. You can now review your configuration or start your first sync.` : `Project "${name}" has been created successfully. You can now review your configuration or start your first sync.`}
          </p>
        </div>
        {runWarning && (
          <div style={{ maxWidth: 640, padding: '12px 14px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.5, textAlign: 'left' }}>
            {runWarning}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
          <button onClick={() => { onClear?.(); navigate('/projects'); }} style={footerBtn('secondary')}>Back to Projects</button>
          {createdProjectId ? (
            <button onClick={() => navigate(`/projects/${createdProjectId}/config`)} style={footerBtn('primary')}>
              Go to Config &amp; Sync
            </button>
          ) : (
            <button disabled style={{ ...footerBtn('secondary'), opacity: 0.6, cursor: 'not-allowed' }}>
              Configure Project (ID unavailable)
            </button>
          )}
        </div>
      </div>
    );
  }

  const renderDiff = (key, label) => {
    if (!diff || !diff[key]) return null;
    return (
      <div style={{ padding: '12px 14px', borderRadius: 8, background: 'var(--bg-surface-raised)', border: '1px solid var(--accent-blue)40', marginBottom: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent-blue)', textTransform: 'uppercase', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Settings2 size={12} /> Edited: {label}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginBottom: 2 }}>Original</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', textDecoration: 'line-through' }}>{diff[key].old || <span style={{ fontStyle: 'italic', opacity: 0.5 }}>Empty</span>}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginBottom: 2 }}>New</div>
            <div style={{ fontSize: 12, color: 'var(--text-primary)', fontWeight: 500 }}>{diff[key].new || <span style={{ fontStyle: 'italic', opacity: 0.5 }}>Empty</span>}</div>
          </div>
        </div>
      </div>
    );
  };

  const showDiffs = editMode && diff && Object.keys(diff).length > 0;

  const currentSelectionNames = selectedModels?.size > 0
    ? Array.from(selectedModels)
        .map(id => selectedModelNameByKey?.[id] || id.split('::').pop())
        .sort()
        .join(', ')
    : 'Everything (*)';

  const wasSelectionNames = savedModels?.size > 0
    ? Array.from(savedModels)
        .map(id => selectedModelNameByKey?.[id] || id.split('::').pop())
        .sort()
        .join(', ')
    : 'Everything (*)';

  const modelsChanged = editMode && currentSelectionNames !== wasSelectionNames;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>
          {editMode ? 'Review Changes' : 'Ready to Create'}
        </h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          {editMode ? 'Review your changes and save the project configuration.' : 'Review your choices and create the project.'}
        </p>
      </div>

      {showDiffs && (
        <div style={{ padding: '16px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12, textTransform: 'uppercase' }}>Configuration Changes</div>
          {renderDiff('name', 'Project Name')}
          {renderDiff('description', 'Description')}
          {renderDiff('tags', 'Tags')}
          {renderDiff('sourceConnector', 'Source Connector')}
          {renderDiff('targetConnectors', 'Target Connector(s)')}
        </div>
      )}

      {/* The beautiful summary layout */}
      <div style={{ padding: '16px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', display: 'grid', gap: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Project Name</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{name}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Source</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{sourceConnector}</div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Intermediate Format</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{intermediateFormat}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Workspace</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{selectedWorkspace?.name || 'My workspace'}</div>
          </div>
        </div>
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Selected Models</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{currentSelectionNames}</span>
              {!modelsChanged && editMode && (
                <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--accent-blue)', background: 'var(--accent-blue)20', padding: '2px 6px', borderRadius: 4, textTransform: 'uppercase' }}>Saved</span>
              )}
            </div>
            {modelsChanged && (
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textDecoration: 'line-through' }}>
                Was: {wasSelectionNames}
              </div>
            )}
          </div>
        </div>
      </div>

      {createError && (
        <div style={{ padding: '12px 14px', borderRadius: 10, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.28)', color: 'var(--text-primary)', fontSize: 12, lineHeight: 1.5 }}>
          {createError}
        </div>
      )}

      {!editMode && (
        <ToggleOption
          label="Create Reverse Project"
          description={`Also create ${name || 'the project'}_${Array.from(targetConnectors)[0] || 'target'}_to_${sourceConnector} using reversed source/target roles.`}
          checked={createReverseProject}
          onChange={setCreateReverseProject}
        />
      )}
    </div>
  );
}
