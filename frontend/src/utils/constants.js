export const STEPS = [
  { id: 1, label: 'Basic Info' },
  { id: 2, label: 'Connector Config' },
  { id: 3, label: 'Select Sources' },
  { id: 4, label: 'Mapping Options' },
  { id: 5, label: 'Finish' },
];

export const CONNECTOR_TYPES = [
  { value: 'pbix', label: 'Local PBIX File' },
  { value: 'fabric', label: 'Microsoft Fabric' },
  { value: 'snowflake', label: 'Snowflake' },
  { value: 'databricks', label: 'Databricks' },
];

export const TARGET_CONNECTOR_TYPES = [
  { value: 'snowflake', label: 'Snowflake' },
  { value: 'fabric', label: 'Microsoft Fabric' },
  { value: 'databricks', label: 'Databricks' },
];

export const INTERMEDIATE_FORMAT_TYPES = [
  { value: 'osi', label: 'OSI (Open Semantic Interchange)' },
  { value: 'sml', label: 'SML' },
];

export const PBIX_SOURCE_MODES = [
  { value: 'TAG', label: 'Use Folder Tag' },
  { value: 'MANUAL', label: 'Upload PBIX File' },
];

export const INPUT = {
  display: 'block', width: '100%',
  background: 'var(--bg-input)', border: '1px solid var(--border-main)',
  borderRadius: 8, color: 'var(--text-primary)', padding: '8px 12px',
  fontSize: 13, outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box',
};

export const LABEL = { display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 };

export const SECTION_CARD = {
  border: '1px solid rgba(255, 255, 255, 0.05)',
  borderRadius: 16,
  background: 'var(--bg-surface)',
  padding: 24,
  boxShadow: '0 4px 20px rgba(0, 0, 0, 0.2)',
};

export const MAPPING_FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'auto', label: 'Auto' },
  { id: 'manual', label: 'Manual' },
  { id: 'unmapped', label: 'Unmapped' },
  { id: 'collision', label: 'Collision/Error' },
];

export const SNOWFLAKE_RESERVED = new Set([
  'SELECT', 'GROUP', 'ORDER', 'TABLE', 'COLUMN', 'DATE', 'FROM', 'WHERE',
  'BY', 'JOIN', 'VIEW', 'UNION', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP',
]);
