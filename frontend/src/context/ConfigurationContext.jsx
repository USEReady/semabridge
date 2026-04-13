import { createContext, useContext, useState, useEffect } from 'react';
import { api } from '../utils/api';

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
      const data = await api.getConfig();
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
      const data = await api.saveConfig(newConfig);
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
