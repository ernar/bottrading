import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { useWebSocket } from './hooks/useWebSocket'
import { Header } from './components/Header'
import { Navigation } from './components/Navigation'
import { DuplicateInstanceModal } from './components/DuplicateInstanceModal'
import { ErrorBoundary } from './components/ErrorBoundary'
import { DashboardPage } from './pages/Dashboard'
import { PositionsPage } from './pages/Positions'
import { HistoryPage } from './pages/History'
import { AgentsPage } from './pages/Agents'
import { CoordinatorPage } from './pages/Coordinator'
import { ChatWidget } from './components/ChatWidget'
import { LofiPlayer } from './components/LofiPlayer'
import { SettingsPage } from './pages/Settings'

function App() {
  const { state, connected, coordination, duplicateInstance, clearDuplicate } = useWebSocket()

  return (
    <Router>
      <div className="min-h-screen bg-gray-900 text-white">
        {duplicateInstance && <DuplicateInstanceModal onClose={clearDuplicate} />}
        <Header state={state} connected={connected} />
        <Navigation />
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<DashboardPage state={state} />} />
            <Route path="/coordinator" element={<CoordinatorPage liveCoordination={coordination} />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/positions" element={<PositionsPage state={state} />} />
            <Route path="/history" element={<HistoryPage state={state} />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </ErrorBoundary>
        {/* Asistente flotante (burbuja abajo a la derecha), disponible en todas las vistas. */}
        <ChatWidget />
        {/* Reproductor de música LO-FI (abajo a la izquierda); arranca al entrar. */}
        <LofiPlayer />
      </div>
    </Router>
  )
}

export default App
