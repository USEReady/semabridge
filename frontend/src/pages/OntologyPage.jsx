import React from 'react';
import OntologyDomainManager from '../components/OntologyDomainManager';

/**
 * OntologyPage (/ontology): shared business definitions every BI tool and AI
 * agent uses. Tabs: Overview, Domains (glossary + metric drawer), Map, Health.
 */
export default function OntologyPage({ mode = 'explorer' }) {
  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
      <OntologyDomainManager mode={mode} />
    </div>
  );
}
