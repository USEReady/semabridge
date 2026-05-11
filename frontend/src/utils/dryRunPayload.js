export function buildDryRunPayload({
  sourceConnector,
  targetConnectors,
  fabricAccountId,
  fabricWorkspaceId,
  snowflakeDatabase,
  targetDatabase,
  selectedModelNames,
}) {
  const sourceConfig = { type: sourceConnector };

  if (sourceConnector === 'fabric') {
    if (fabricAccountId) sourceConfig.identity_id = fabricAccountId;
    if (fabricWorkspaceId) sourceConfig.workspace_id = fabricWorkspaceId;
  }

  if (sourceConnector === 'snowflake') {
    if (snowflakeDatabase) sourceConfig.database = snowflakeDatabase;
  }

  if (Array.isArray(selectedModelNames) && selectedModelNames.length > 0) {
    sourceConfig.models = selectedModelNames;
  }

  const targetConfig = { type: Array.from(targetConnectors || [])[0] || '' };

  if (targetConfig.type === 'snowflake') {
    if (targetDatabase) targetConfig.database = targetDatabase;
  }

  if (targetConfig.type === 'fabric') {
    if (fabricAccountId) targetConfig.identity_id = fabricAccountId;
    if (fabricWorkspaceId) targetConfig.workspace_id = fabricWorkspaceId;
  }

  return { sourceConfig, targetConfig };
}