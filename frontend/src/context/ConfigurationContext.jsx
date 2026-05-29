import { createContext, useContext, useState, useEffect } from 'react';
import { api } from '../utils/api';
import { useAuth } from './AuthContext';

const ConfigurationContext = createContext();

// Safe error message extraction — handles API error shapes, plain Error objects,
// strings, and null/undefined without throwing on unexpected shapes.
function getErrorMessage(err) {
  if (!err) return 'Unknown error';
  if (err.detail && Array.isArray(err.detail)) {
    return err.detail.map(d => d.msg || d).join(', ');
  }
  if (typeof err.message === 'string') return err.message;
  if (typeof err === 'string') return err;
  return 'An error occurred';
}

export function ConfigurationProvider({ children }) {
  const [config, setConfig] = useState(null);
  const [globalConfig, setGlobalConfig] = useState(null);
  const [ghostConfig, setGhostConfig] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const { isAuthenticated, token } = useAuth();

  const fetchConfig = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.getConfig();
      setConfig(data.project);
      setGlobalConfig(data.global_config);
      setGhostConfig(data.ghost);
    } catch (err) {
      setError(getErrorMessage(err));
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
      if (err && err.detail && Array.isArray(err.detail)) {
        return { success: false, validationErrors: err.detail };
      }
      const msg = getErrorMessage(err) || 'Error saving configuration';
      setError(msg);
      return { success: false, error: msg };
    }
  };

  useEffect(() => {
    if (isAuthenticated || token) {
      fetchConfig();
    } else {
      setConfig(null);
      setGlobalConfig(null);
      setGhostConfig(null);
      setIsLoading(false);
      setError(null);
    }
  }, [isAuthenticated, token]);

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

