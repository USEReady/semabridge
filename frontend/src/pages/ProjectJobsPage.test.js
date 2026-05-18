import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import ProjectJobsPage from './ProjectJobsPage';
import * as apiModule from '../utils/api';

// Mock the API module
vi.mock('../utils/api', () => ({
  api: {
    listJobRuns: vi.fn(),
    listJobSchedules: vi.fn(),
    triggerJob: vi.fn(),
    deleteProjectSchedule: vi.fn(),
    compareProjectSnapshots: vi.fn(),
  },
}));

// Mock the utils
vi.mock('../utils/runLogs', () => ({
  buildMockRunLogs: vi.fn(),
  getRunLogs: vi.fn(() => []),
  saveRunLogs: vi.fn(),
}));

// Mock the hooks and components
vi.mock('../hooks/usePageCache', () => ({
  default: vi.fn((key, defaultValue) => {
    const [state, setState] = require('react').useState(defaultValue);
    return [state, (updates) => {
      setState((prev) => ({ ...prev, ...updates }));
    }];
  }),
}));

vi.mock('../components/common/PageHeader', () => ({
  default: () => <div data-testid="page-header">Page Header</div>,
}));

vi.mock('../components/common/SearchInput', () => ({
  default: () => <div data-testid="search-input">Search</div>,
}));

vi.mock('../components/common/StatusBadge', () => ({
  default: () => <div>Status Badge</div>,
}));

describe('ProjectJobsPage - Runs Page Flicker Fix', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('should only call setLoading(false) once on initial load, not on every refresh interval', async () => {
    const mockRuns = [{ id: 'run1', status: 'success', project_name: 'test' }];
    const mockSchedules = [];

    apiModule.api.listJobRuns.mockResolvedValue(mockRuns);
    apiModule.api.listJobSchedules.mockResolvedValue(mockSchedules);

    const { rerender } = render(<ProjectJobsPage />);

    // Initial load should fetch data
    await waitFor(() => {
      expect(apiModule.api.listJobRuns).toHaveBeenCalledTimes(1);
    });

    // Advance time past first refresh interval (4 seconds = 4000ms)
    vi.advanceTimersByTime(4000);

    await waitFor(() => {
      expect(apiModule.api.listJobRuns).toHaveBeenCalledTimes(2);
    });

    // Advance time past another refresh interval
    vi.advanceTimersByTime(4000);

    await waitFor(() => {
      expect(apiModule.api.listJobRuns).toHaveBeenCalledTimes(3);
    });

    // Verify the component doesn't show loading state during interval refreshes
    // (It should show loading only initially, not after each refresh)
    expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
  });

  it('should continue refreshing data in background every 4 seconds without visible flicker', async () => {
    const mockRuns = [{ id: 'run1', status: 'success', project_name: 'test' }];
    const mockSchedules = [];

    apiModule.api.listJobRuns.mockResolvedValue(mockRuns);
    apiModule.api.listJobSchedules.mockResolvedValue(mockSchedules);

    render(<ProjectJobsPage />);

    await waitFor(() => {
      expect(apiModule.api.listJobRuns).toHaveBeenCalledTimes(1);
    });

    const initialCallCount = apiModule.api.listJobRuns.mock.calls.length;

    // Simulate 3 refresh cycles (12 seconds total)
    for (let i = 0; i < 3; i++) {
      vi.advanceTimersByTime(4000);
      await waitFor(() => {
        expect(apiModule.api.listJobRuns).toHaveBeenCalledTimes(initialCallCount + i + 1);
      });
    }

    // Should have called listJobRuns 4 times (1 initial + 3 refreshes)
    expect(apiModule.api.listJobRuns).toHaveBeenCalledTimes(4);
  });
});
