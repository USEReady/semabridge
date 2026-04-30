  const [globalStatus, setGlobalStatus] = useState(null);
  useEffect(() => {
    api.getGlobalStatus().then(status => setGlobalStatus(status)).catch(() => {});
  }, []);

  const isConfigured = (connectorValue) => {
    if (connectorValue === 'pbix') return true;
    if (!globalStatus) return true; // Assume true while loading to prevent jitter
    if (connectorValue === 'fabric') return globalStatus?.fabric?.configured === true || globalStatus?.fabric?.fields_stored > 0 || !!globalStatus?.fabric?.credentials?.workspace_id;
    if (connectorValue === 'snowflake') return globalStatus?.snowflake?.configured === true || globalStatus?.snowflake?.fields_stored > 0;
    if (connectorValue === 'databricks') return globalStatus?.databricks?.configured === true;
    return true;
  };
