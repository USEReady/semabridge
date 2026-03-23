import { createContext, useContext, useState, useEffect } from 'react';

const ConfigurationContext = createContext();

export function ConfigurationProvider({ children }) {
  const [config, setConfig] = useState(null);
  const [globalConfig, setGlobalConfig] = useState(null);
  const [ghostConfig, setGhostConfig] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchConfig = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/config');
      if (!response.ok) throw new Error('Failed to fetch configuration');
      const data = await response.json();
      setConfig(data.project);
      setGlobalConfig(data.global_config);
      setGhostConfig(data.ghost);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  const saveConfig = async (newConfig) => {
    setError(null);
    try {
      const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newConfig)
      });
      if (!response.ok) {
        const errData = await response.json();
        throw errData;
      }
      // Re-fetch to get updated ghosting and confirmed saves
      await fetchConfig();
      return { success: true };
    } catch (err) {
      if (err.detail && Array.isArray(err.detail)) {
         return { success: false, validationErrors: err.detail };
      }
      setError(err.message || 'Error saving configuration');
      return { success: false, error: err.message };
    }
  };

  useEffect(() => {
    fetchConfig();
  }, []);

  return (
    <ConfigurationContext.Provider value={{
      config,
      globalConfig,
      ghostConfig,
      isLoading,
      error,
      saveConfig,
      fetchConfig
    }}>
      {children}
    </ConfigurationContext.Provider>
  );
}

export const useConfiguration = () => useContext(ConfigurationContext);
