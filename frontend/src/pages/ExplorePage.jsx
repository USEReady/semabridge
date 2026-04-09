import RepositoryMap from '../components/RepositoryMap/RepositoryMap';

/**
 * ExplorePage — Visual semantic model map.
 * Wraps the existing RepositoryMap component (React Flow graph + file tree + detail panel).
 * No onClose needed since navigation is handled by the sidebar.
 */
export default function ExplorePage() {
  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
      <RepositoryMap />
    </div>
  );
}
