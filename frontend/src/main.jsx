import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from './context/ThemeProvider'
import { AuthProvider } from './context/AuthContext'
import './index.css'
import App from './App'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ThemeProvider>
  </StrictMode>,
)
