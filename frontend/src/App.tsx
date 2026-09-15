import { Routes, Route } from 'react-router-dom'
import './App.css'
import { NavBar } from './components/NavBar'
import { FleetDashboardPage } from './pages/FleetDashboardPage'
import { BatteryListPage } from './pages/BatteryListPage'
import { BatteryDetailPage } from './pages/BatteryDetailPage'

function App() {
  return (
    <>
      <NavBar />
      <Routes>
        <Route path="/" element={<FleetDashboardPage />} />
        <Route path="/batteries" element={<BatteryListPage />} />
        <Route path="/batteries/:batteryId" element={<BatteryDetailPage />} />
      </Routes>
    </>
  )
}

export default App
