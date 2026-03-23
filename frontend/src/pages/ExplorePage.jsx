import { useState } from 'react';
import RepositoryMap from '../components/RepositoryMap/RepositoryMap';
import TimeMachine from '../components/RepositoryMap/TimeMachine';

/**
 * ExplorePage — Visual semantic model map.
 * Wraps the existing RepositoryMap component (React Flow graph + file tree + detail panel).
 * No onClose needed since navigation is handled by the sidebar.
 */
export default function ExplorePage() {
  const [selectedSnapshotId, setSelectedSnapshotId] = useState(null);
  const [compareSnapshotId, setCompareSnapshotId] = useState(null);
  const [diffMode, setDiffMode] = useState(false);
  
  return (
    <div style={{ height: 'calc(100vh - 64px)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <RepositoryMap
        snapshotId={selectedSnapshotId}
        compareSnapshotId={compareSnapshotId}
        diffMode={diffMode}
      />
      <TimeMachine
        modelName="__all__"
        onSnapshotSelect={setSelectedSnapshotId}
        onCompareSnapshotSelect={setCompareSnapshotId}
        onDiffModeChange={setDiffMode}
        selectedSnapshotId={selectedSnapshotId}
        compareSnapshotId={compareSnapshotId}
        diffMode={diffMode}
      />
    </div>
  );
}
