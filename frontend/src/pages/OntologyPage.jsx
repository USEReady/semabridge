import React from 'react';
import OntologyDomainManager from '../components/OntologyDomainManager';

/**
 * OntologyPage — Enterprise Federated Domain Ontologies & Embedded AI.
 * Enables team-driven YAML uploads, conflict governance, and local zero-credit NL-to-SQL.
 */
export default function OntologyPage() {
  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
      <OntologyDomainManager />
    </div>
  );
}
