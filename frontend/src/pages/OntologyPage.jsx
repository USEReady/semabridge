import React from 'react';
import OntologyDomainManager from '../components/OntologyDomainManager';
import MetricComparatorPage from './MetricComparatorPage';

/**
 * OntologyPage (/ontology): shared business definitions every BI tool and AI
 * agent uses. Tabs: Overview, Domains (glossary + metric drawer), Map, Health.
 */
export default function OntologyPage({ mode = 'explorer' }) {
  if (mode === 'comparator') {
    return <MetricComparatorPage />;
  }

  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
      <OntologyDomainManager mode={mode} />
    </div>
  );
}
