import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { useWebSocket } from './hooks/useWebSocket'
import { Header } from './components/Header'
import { Navigation } from './components/Navigation'
import { DuplicateInstanceModal } from './components/DuplicateInstanceModal'
import { DashboardPage } from './pages/Dashboard'
import { SignalsPage } from './pages/Signals'
import { PositionsPage } from './pages/Positions'
import { HistoryPage } from './pages/History'
import { AgentsPage } from './pages/Agents'

function App() {
  const { state, connected, duplicateInstance, clearDuplicate } = useWebSocket()

  return (
    <Router>
      <div className="min-h-screen bg-gray-900 text-white">
        {duplicateInstance && <DuplicateInstanceModal onClose={clearDuplicate} />}
        <Header state={state} connected={connected} />
        <Navigation />
        <Routes>
          <Route path="/" element={<DashboardPage state={state} />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/signals" element={<SignalsPage state={state} />} />
          <Route path="/positions" element={<PositionsPage state={state} />} />
          <Route path="/history" element={<HistoryPage state={state} />} />
        </Routes>
      </div>
    </Router>
  )
}

export default App
