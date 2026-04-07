import React, { createContext, useState, useEffect } from 'react';

export const SyncContext = createContext();

export const SyncProvider = ({ children }) => {
  const [activeRuns, setActiveRuns] = useState([]);

  useEffect(() => {
    let timerId;
    let isMounted = true;

    const pollGlobalRuns = async () => {
      try {
        const response = await fetch('/api/jobs/runs');
        if (!response.ok) {
          const body = await response.text();
          throw new Error(`Polling failed ${response.status}: ${body || response.statusText}`);
        }
        const data = await response.json();
        if (isMounted) {
          setActiveRuns(data);
          timerId = setTimeout(pollGlobalRuns, 1500);
        }
      } catch (error) {
        console.error('Global polling error', error);
        if (isMounted) timerId = setTimeout(pollGlobalRuns, 3000);
      }
    };

    pollGlobalRuns();
    return () => {
      isMounted = false;
      clearTimeout(timerId);
    };
  }, []);

  return (
    <SyncContext.Provider value={{ activeRuns }}>
      {children}
    </SyncContext.Provider>
  );
};
