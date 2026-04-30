  // --- Hydrate from edit mode initialData ---
  useEffect(() => {
    if (editMode && initialData) {
      if (initialData.project_name) setName(initialData.project_name);
      if (initialData.description) setDescription(initialData.description);
      if (initialData.source_type) setSourceConnector(initialData.source_type);
      if (initialData.target_type) setTargetConnectors(new Set([initialData.target_type]));
      if (initialData.intermediate_format) setIntermediateFormat(initialData.intermediate_format);
      
      // Connection details mapping from configForm
      if (initialData.source_type === 'fabric') {
         setFabricAccountId(initialData.source_identity_id || '');
         setFabricWorkspaceId(initialData.source_workspace_id || '');
      } else if (initialData.source_type === 'snowflake') {
         setSnowflakeAccountId(initialData.source_identity_id || '');
         setSnowflakeDatabase(initialData.source_database || '');
         setSnowflakeSchema(initialData.source_schema || '');
      }

      // Target mapping
      if (initialData.target_type === 'snowflake') {
         setTargetAccount(initialData.target_account || '');
         setTargetDatabase(initialData.target_database || '');
         setTargetSchema(initialData.target_schema || '');
         setTargetWarehouse(initialData.target_warehouse || '');
      } else if (initialData.target_type === 'fabric') {
         // Fabric target logic
      }

      // Models
      if (initialData.allow_models) {
         const models = initialData.allow_models.split(',').map(m => m.trim()).filter(Boolean);
         setSelectedModels(new Set(models));
      }
    }
  }, [editMode, initialData]);
