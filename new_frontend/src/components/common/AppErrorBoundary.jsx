import React from 'react';

export default class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: '' };
  }

  static getDerivedStateFromError(error) {
    return {
      hasError: true,
      message: error?.message || 'Unexpected application error.',
    };
  }

  componentDidCatch(error, errorInfo) {
    console.error('[AppErrorBoundary] Render crash:', error, errorInfo);
  }

  handleReset = () => {
    localStorage.clear();
    sessionStorage.clear();
    window.location.href = '/';
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--bg-app)',
          color: 'var(--text-primary)',
          padding: 20,
        }}
      >
        <div
          style={{
            width: 'min(560px, 95vw)',
            border: '1px solid var(--border-main)',
            borderRadius: 10,
            background: 'var(--bg-surface)',
            padding: 20,
          }}
        >
          <h2 style={{ margin: 0, fontSize: 18, marginBottom: 10 }}>Application Error</h2>
          <p style={{ margin: 0, fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
            The UI crashed while rendering. This can happen when persisted state becomes invalid.
          </p>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 16 }}>
            {this.state.message}
          </p>
          <button
            onClick={this.handleReset}
            style={{
              border: 'none',
              borderRadius: 8,
              padding: '8px 14px',
              background: 'var(--color-error)',
              color: '#fff',
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: 700,
            }}
          >
            Reset Application
          </button>
        </div>
      </div>
    );
  }
}
