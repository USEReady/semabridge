import { Component } from 'react';

/**
 * Error boundary for optional panels (UPSERT delta, table bindings, ...):
 * a rendering error in a panel shows a short note instead of crashing the
 * whole page. The error is still logged to the console for diagnosis.
 */
export default class PanelBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error(`[PanelBoundary] ${this.props.name || 'panel'} failed to render:`, error, info?.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ margin: '8px 0', fontSize: 12, color: 'var(--text-tertiary)' }}>
          {this.props.name || 'This panel'} could not be displayed.
        </div>
      );
    }
    return this.props.children;
  }
}
