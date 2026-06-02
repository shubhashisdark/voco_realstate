"use client"

import { useState, useEffect } from "react"
import { BrowserRouter as Router, Routes, Route } from "react-router-dom"
import Navbar from "./components/Navbar"
import Dashboard from "./pages/Dashboard"
import CallCenter from "./pages/CallCenter"
import CallDetail from "./pages/CallDetail"
import CallHistory from "./pages/CallHistory"
import FileUpload from "./pages/FileUpload"
import WebhookData from "./pages/WebhookData"
import Dialer from "./pages/Dialer"
import Properties from "./pages/Properties"
import Appointments from "./pages/Appointments"
import "./App.css"

const backendUrl = process.env.REACT_APP_BACKEND_URL || "http://localhost:5000";

function App() {
  const [theme, setTheme] = useState("dark")
  const [assistantInfo, setAssistantInfo] = useState(null)

  useEffect(() => {
    const fetchAssistantInfo = async () => {
      try {
        const response = await fetch(`${backendUrl}/api/assistant-info`)
        const data = await response.json()
        if (data.success) {
          setAssistantInfo(data.assistant)
        }
      } catch (error) {
        console.error("Error fetching assistant info:", error)
      }
    }

    fetchAssistantInfo()
  }, [])

  const toggleTheme = () => {
    setTheme(theme === "dark" ? "light" : "dark")
  }

  return (
    <Router>
      <div className={`app ${theme}`}>
        {<Navbar theme={theme} toggleTheme={toggleTheme} assistantInfo={assistantInfo} /> }
        <div className="container">
          <Routes>
            <Route path="/" element={<Dashboard/>}/>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/call-center" element={<CallCenter />} />
            <Route path="/file-upload" element={<FileUpload />} />
            <Route path="/call-history" element={<CallHistory />} />
            <Route path="/call-detail/:callId" element={<CallDetail />} />
            <Route path="/webhook-data" element={<WebhookData />} />
            <Route path="/call-dialer" element={<Dialer />} />
            <Route path="/properties" element={<Properties />} />
            <Route path="/appointments" element={<Appointments />} />
          </Routes>
        </div>
      </div>
    </Router>
  )
}

export default App
