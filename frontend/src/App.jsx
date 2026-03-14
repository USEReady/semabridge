import { useState } from 'react';
import Header from './components/Header';
import StatusBar from './components/StatusBar';
import SourceBrowser from './components/SourceBrowser';
import YamlEditor from './components/YamlEditor';
import HistoryTimeline from './components/HistoryTimeline';
import DiffViewer from './components/DiffViewer';
import TerminalPanel from './components/TerminalPanel';
import LogsPanel from './components/LogsPanel';
import VersionControlPanel from './components/VersionControlPanel';
import ToastContainer from './components/ToastContainer';
import RepositoryMap from './components/RepositoryMap/RepositoryMap';
import LiveValidator from './components/LiveValidator';
import ConnectionsPanel from './components/ConnectionsPanel';
import CommandPalette from './components/CommandPalette';
import { LogsProvider } from './context/LogsContext';
import { WorkspaceProvider } from './context/WorkspaceContext';

export default function App() {
  const [selectedItems, setSelectedItems] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [showTerminal, setShowTerminal] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const [showVersionControl, setShowVersionControl] = useState(false);
  const [showRepoMap, setShowRepoMap] = useState(false);
  const [showConnections, setShowConnections] = useState(false);
  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [activeModelId, setActiveModelId] = useState(null);
  const [sourceType, setSourceType] = useState('fabric');
  const [targetType, setTargetType] = useState('snowflake');
  const [pbixFolder, setPbixFolder] = useState('');
  const [pbixImportedModel, setPbixImportedModel] = useState(null);
  const selectedModelForVersionControl = selectedItems[0] || activeModelId || null;

  return (
    <WorkspaceProvider>
      <LogsProvider>
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          height: '100vh',
          width: '100vw',
          overflow: 'hidden',
          background: 'var(--bg-app)',
          color: 'var(--text-primary)',
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          transition: 'background 0.25s ease, color 0.25s ease'
        }}>
          {/* Header */}
          <Header
            onToggleLogs={() => setShowLogs(!showLogs)}
            onToggleVersionControl={() => setShowVersionControl(true)}
            onToggleRepoMap={() => setShowRepoMap(v => !v)}
            onToggleConnections={() => setShowConnections(true)}
            onToggleTerminal={() => setShowTerminal(v => !v)}
            onOpenCommandPalette={() => setShowCommandPalette(true)}
            showRepoMap={showRepoMap}
          />

          {/* ──── Repository Map (full-screen mode) ──── */}
          {showRepoMap ? (
            <div style={{ flex: 1, overflow: 'hidden' }}>
              <RepositoryMap onClose={() => setShowRepoMap(false)} />
            </div>
          ) : (
            <>
              {/* Main Content */}
              <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
                {/* Left Sidebar */}
                <div style={{ width: 280, minWidth: 220, borderRight: '1px solid var(--border-color)', overflow: 'auto' }}>
                  <SourceBrowser
                    selectedItems={selectedItems}
                    onSelectItems={setSelectedItems}
                    onOpenModel={setActiveModelId}
                    onSourceTypeChange={setSourceType}
                    onTargetTypeChange={setTargetType}
                    onPbixPathChange={setPbixFolder}
                    onPbixImported={setPbixImportedModel}
                  />
                </div>

                {/* Center */}
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                  <div style={{ flex: 1, overflow: 'auto' }}>
                    {showDiff
                      ? <DiffViewer />
                      : <YamlEditor
                        selectedItems={selectedItems}
                        activeModelId={activeModelId}
                        sourceType={sourceType}
                        targetType={targetType}
                        pbixFolder={pbixFolder}
                        importedPbixModel={pbixImportedModel}
                      />
                    }
                  </div>
                  {showTerminal && (
                    <div style={{ height: 200, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                      <TerminalPanel />
                    </div>
                  )}
                </div>

                {/* Right Sidebar */}
                {showHistory && (
                  <div style={{ width: 300, minWidth: 240, borderLeft: '1px solid rgba(255,255,255,0.06)', overflow: 'auto' }}>
                    <HistoryTimeline />
                  </div>
                )}

              </div>
            </>
          )}

          {/* Status Bar */}
          <StatusBar />

          {/* Overlays */}
          <ToastContainer onOpenLogs={() => setShowLogs(true)} />
          <LiveValidator />
          <LogsPanel isOpen={showLogs} onClose={() => setShowLogs(false)} />
          <VersionControlPanel
            isOpen={showVersionControl}
            onClose={() => setShowVersionControl(false)}
            activeModelId={selectedModelForVersionControl}
          />
          <ConnectionsPanel
            isOpen={showConnections}
            onClose={() => setShowConnections(false)}
          />
          <CommandPalette
            isOpen={showCommandPalette}
            onClose={() => setShowCommandPalette(false)}
            onAction={(action, payload) => {
              switch (action) {
                case 'openPalette': setShowCommandPalette(true); break;
                case 'toggleRepoMap': setShowRepoMap(v => !v); break;
                case 'toggleVersionControl': setShowVersionControl(true); break;
                case 'toggleLogs': setShowLogs(v => !v); break;
                case 'toggleTerminal': setShowTerminal(v => !v); break;
                case 'toggleConnections': setShowConnections(true); break;
                case 'save': document.dispatchEvent(new CustomEvent('semabridge:save')); break;
                case 'sync': document.dispatchEvent(new CustomEvent('semabridge:sync')); break;
                case 'openModel': setActiveModelId(payload); break;
                default: break;
              }
            }}
          />
        </div>
      </LogsProvider>
    </WorkspaceProvider>
  );
}

