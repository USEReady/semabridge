import React from 'react';

const mockData = [
  {
    "id": "proj-01",
    "displayName": "Finance Analytics",
    "type": "project",
    "children": [
      {
        "id": "model-01",
        "displayName": "Core_Finance_v1",
        "type": "model",
        "children": [
          {"id": "tab-01", "displayName": "Corporate Spend", "type": "table"},
          {"id": "tab-02", "displayName": "Customer Profitability", "type": "table"}
        ]
      }
    ]
  }
];

const MetadataTree = ({ data = mockData }) => {
  const renderNode = (node, depth = 0) => {
    return (
      <div key={node.id} style={{ paddingLeft: `${depth > 0 ? 20 : 0}px`, marginTop: '4px' }}>
        <div style={{ display: 'flex', alignItems: 'center', fontFamily: 'monospace', fontSize: '14px' }}>
          <span style={{ marginRight: '8px' }}>
            {node.type === 'project' && '📁'}
            {node.type === 'model' && '🛢️'}
            {node.type === 'table' && '📊'}
          </span>
          <span>{node.displayName || node.id}</span>
        </div>
        {node.children && node.children.length > 0 && (
          <div className="metadata-children">
            {node.children.map((child) => renderNode(child, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="metadata-tree-container" style={{ padding: '16px', background: '#1e1e1e', color: '#d4d4d4', borderRadius: '8px' }}>
      <h3 style={{ margin: '0 0 16px 0', fontSize: '16px', borderBottom: '1px solid #333', paddingBottom: '8px' }}>Metadata Explorer</h3>
      {data.map((node) => renderNode(node))}
    </div>
  );
};

export default MetadataTree;
