import { useSearchParams } from 'react-router-dom';
import RepositoryMap from '../components/RepositoryMap/RepositoryMap';

/**
 * ExplorePage — "Model map" (/map).
 * Wraps RepositoryMap (React Flow diagram, schema table and detail panel).
 * Supports a `?model=<id or name>` deep link to open a specific model.
 */
export default function ExplorePage() {
  const [searchParams] = useSearchParams();
  const initialModel = searchParams.get('model');
  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
      <RepositoryMap initialModel={initialModel} />
    </div>
  );
}
