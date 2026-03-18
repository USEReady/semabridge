import { useState, useEffect } from 'react';
import { Play, Pause, SkipBack, SkipForward, GitCommit } from 'lucide-react';

export default function TimeMachine({ modelName, onSnapshotSelect }) {
  const [snapshots, setSnapshots] = useState([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    async function fetchHistory() {
      if (!modelName) return;
      try {
        const resp = await fetch(`/api/graph/${modelName}/snapshots`);
        if (resp.ok) {
          const data = await resp.json();
          // Assume data is sorted oldest to newest or we sort here
          setSnapshots(data || []);
          if (data.length > 0) {
             setCurrentIndex(data.length - 1);
             onSnapshotSelect(data[data.length - 1].snapshot_id);
          }
        }
      } catch (e) {
        console.error("Failed to load history", e);
      }
    }
    fetchHistory();
  }, [modelName]);

  useEffect(() => {
    let timer;
    if (isPlaying && currentIndex < snapshots.length - 1) {
      timer = setTimeout(() => {
        const nextIdx = currentIndex + 1;
        setCurrentIndex(nextIdx);
        onSnapshotSelect(snapshots[nextIdx].snapshot_id);
      }, 1500);
    } else if (currentIndex >= snapshots.length - 1) {
      setIsPlaying(false);
    }
    return () => clearTimeout(timer);
  }, [isPlaying, currentIndex, snapshots]);

  const handleSliderChange = (e) => {
    const idx = parseInt(e.target.value, 10);
    setCurrentIndex(idx);
    if (snapshots[idx]) {
      onSnapshotSelect(snapshots[idx].snapshot_id);
    }
  };

  if (!snapshots.length) return null;

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '12px 24px',
      background: 'var(--bg-surface)', borderTop: '1px solid var(--border-color)'
    }}>
      <button onClick={() => setIsPlaying(!isPlaying)} style={{ background: 'transparent', border: 'none', color: 'var(--text-primary)', cursor: 'pointer' }}>
         {isPlaying ? <Pause size={18} /> : <Play size={18} />}
      </button>
      
      <input 
        type="range" 
        min="0" 
        max={Math.max(0, snapshots.length - 1)} 
        value={currentIndex} 
        onChange={handleSliderChange}
        style={{ flex: 1, accentColor: '#818CF8' }}
      />
      
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
        <GitCommit size={14} />
        {snapshots[currentIndex]?.snapshot_id || 'Unknown'}
      </div>
    </div>
  );
}
