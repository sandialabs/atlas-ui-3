import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './themes/theme-presets.css'
import './themes/baseThemes.generated.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
