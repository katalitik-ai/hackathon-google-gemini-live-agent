import { Routes, Route } from 'react-router-dom'
import Homepage from './pages/Homepage'
import LoginPage from './auth/LoginPage' 

function App() {
  return (
    <div className="w-full h-screen">
      <Routes>
        {/* Route untuk halaman utama */}
        <Route path="/" element={<Homepage />} />
        
        {/* Route untuk halaman login */}
        {/* <Route path="/login" element={<LoginPage />} /> */}
      </Routes>
    </div>
  )
}

export default App