/** Tabs of the Verify page; the active one lives in ?tab=. */
export const VERIFY_TABS = [
  { id: 'questions', label: 'Power BI questions', hint: 'Business questions answered on the deployed view and compared with Power BI.' },
  { id: 'measures', label: 'Measure certification', hint: 'Compare a measure with Power BI and certify it.' },
  { id: 'targets', label: 'Across targets', hint: 'Check that Snowflake and Databricks give the same numbers.' },
];

export function verifyTab(search) {
  const tab = new URLSearchParams(search || '').get('tab');
  return VERIFY_TABS.some((t) => t.id === tab) ? tab : 'questions';
}
