import React, { createContext, useState, useEffect } from 'react';
import { api } from '../utils/api';
import { useAuth } from './AuthContext';

export const SyncContext = createContext();
const ACTIVE_POLL_MS = 5000;
const BACKGROUND_POLL_MS = 15000;

export const SyncProvider = ({ children }) => {
  const [activeRuns, setActiveRuns] = useState([]);
  const { token } = useAuth();

  useEffect(() => {
    if (!token) {
      return undefined;
    }

    let timerId;
    let isMounted = true;
    let consecutiveErrors = 0;

    const pollGlobalRuns = async () => {
      try {
        const data = await api.listJobRuns();
        if (isMounted) {
          consecutiveErrors = 0; // Reset on success
          setActiveRuns(data);
          const interval = document.hidden ? BACKGROUND_POLL_MS : ACTIVE_POLL_MS;
          timerId = setTimeout(pollGlobalRuns, interval);
        }
      } catch (error) {
        // Silently handle auth-related errors during polling.
        // The AuthContext silentRecover will handle token refresh.
        // Don't spam the console on transient 401s.
        if (isMounted) {
          consecutiveErrors++;
          // Exponential backoff: 3s, 6s, 12s, max 30s
          const backoff = Math.min(3000 * Math.pow(2, consecutiveErrors - 1), 30000);
          if (consecutiveErrors <= 3) {
            // Suppress first few errors — likely just a token refresh in progress
          } else {
            console.warn('Global polling error (attempt %d)', consecutiveErrors, error.message);
          }
          timerId = setTimeout(pollGlobalRuns, backoff);
        }
      }
    };

    pollGlobalRuns();
    return () => {
      isMounted = false;
      clearTimeout(timerId);
    };
  }, [token]);

  return (
    <SyncContext.Provider value={{ activeRuns }}>
      {children}
    </SyncContext.Provider>
  );
};
