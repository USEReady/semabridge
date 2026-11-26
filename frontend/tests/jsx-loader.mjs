import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import esbuild from 'esbuild';

export async function resolve(specifier, context, defaultResolve) {
  try {
    return await defaultResolve(specifier, context);
  } catch (err) {
    if (err.code === 'ERR_MODULE_NOT_FOUND') {
      for (const ext of ['.js', '.jsx', '/index.js', '/index.jsx']) {
        try {
          return await defaultResolve(specifier + ext, context);
        } catch {}
      }
    }
    throw err;
  }
}

export async function load(url, context, defaultLoad) {
  if (url.endsWith('.jsx') || (url.endsWith('.js') && url.includes('/src/'))) {
    const filePath = fileURLToPath(url);
    const source = fs.readFileSync(filePath, 'utf8');
    const transformed = esbuild.transformSync(source, {
      loader: url.endsWith('.jsx') ? 'jsx' : 'js',
      format: 'esm',
      jsx: 'transform',
      jsxFactory: 'React.createElement',
      jsxFragment: 'React.Fragment',
      define: {
        'import.meta.env': JSON.stringify({
          VITE_API_BASE_URL: '/api',
          VITE_CACHE_TTL_MS: '120000',
          VITE_API_TIMEOUT_MS: '60000',
          VITE_RUN_TIMEOUT_MS: '600000',
          VITE_HEALTH_TIMEOUT_MS: '5000',
          VITE_CACHE_MAX_ITEMS: '200',
          VITE_AUTH_BASE_URL: '/auth',
          DEV: true,
        }),
      },
    });
    return {
      format: 'module',
      shortCircuit: true,
      source: transformed.code,
    };
  }
  return defaultLoad(url, context);
}
