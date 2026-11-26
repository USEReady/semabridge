import React from 'react';
import DriftFindingsList from './DriftFindingsList';

/**
 * DriftFindingsTable Component
 * Reusable container bridging paged drift findings with toolbar controls,
 * category counters, kind badges, and reviewer action handlers.
 */
export default function DriftFindingsTable(props) {
  return <DriftFindingsList {...props} />;
}
