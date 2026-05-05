import React from 'react';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo);
    this.setState({ errorInfo });
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary" style={{ padding: 20, border: '1px solid #ef4444', borderRadius: 8, background: 'rgba(239, 68, 68, 0.1)', color: '#ef4444', margin: '20px 0' }}>
          <h2 style={{ margin: '0 0 10px', fontSize: 16 }}>Something went wrong in this component</h2>
          <details style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>
            <summary style={{ cursor: 'pointer', fontWeight: 'bold' }}>Error details</summary>
            <div style={{ marginTop: 10 }}>
              {this.state.error && this.state.error.toString()}
              <br />
              {this.state.errorInfo && this.state.errorInfo.componentStack}
            </div>
          </details>
          <button 
            onClick={() => window.location.reload()}
            style={{ marginTop: 16, padding: '8px 16px', background: 'transparent', border: '1px solid #ef4444', color: '#ef4444', borderRadius: 6, cursor: 'pointer' }}
          >
            Reload Page
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
