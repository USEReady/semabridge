import React from 'react';
import { Cloud, Snowflake, Database, BarChart3, Folder } from 'lucide-react';

export default function SourceIcon({ source, size = 14 }) {
  const normalizedSource = String(source || '').toLowerCase();

  if (normalizedSource.includes('pbix') || normalizedSource.includes('powerbi')) {
    return <BarChart3 size={size} color="#F2C811" />;
  }

  if (normalizedSource.includes('fabric')) {
    return <Cloud size={size} color="#3b82f6" />;
  }

  if (normalizedSource.includes('snowflake')) {
    return <Snowflake size={size} color="#38bdf8" />;
  }

  if (normalizedSource.includes('databricks')) {
    return <Database size={size} color="#f97316" />;
  }

  return <Folder size={size} color="var(--text-tertiary)" />;
}
