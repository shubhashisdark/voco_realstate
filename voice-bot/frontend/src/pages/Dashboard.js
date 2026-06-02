"use client"

import { useState, useEffect } from "react"
import { useNavigate } from "react-router-dom"
import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  AreaChart,
  Area,
} from "recharts"
import "./Dashboard.css"

const Dashboard = () => {
  const navigate = useNavigate()
  const [stats, setStats] = useState({
    totalCalls: 0,
    overallSentiment: 0,
    positiveResponseRate: 0,
    escalationRecommended: 0,
    pendingCalls: 0,
    totalContacts: 0,
    positiveSentiment: 0,
    negativeSentiment: 0,
    neutralSentiment: 0,
  })

  const [contacts, setContacts] = useState([])
  const [pendingCalls, setPendingCalls] = useState([])
  const [latestCall, setLatestCall] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [deleteLoading, setDeleteLoading] = useState({})
  const [showAnalytics, setShowAnalytics] = useState(true)
  const [showPendingCalls, setShowPendingCalls] = useState(false)

  const backendUrl = (process.env.REACT_APP_BACKEND_URL || "http://localhost:5000").replace(/\/$/, "")

  const fetchJson = async (url, options = {}) => {
    const response = await fetch(url, options)
    const contentType = response.headers.get("content-type") || ""

    if (!contentType.includes("application/json")) {
      const body = await response.text()
      throw new Error(
        `Expected JSON from ${url}, but received ${contentType || "no content-type"} with status ${response.status}. ` +
        `Response preview: ${body.slice(0, 160)}`,
      )
    }

    const data = await response.json()

    if (!response.ok) {
      throw new Error(data?.detail || data?.message || `Request failed with status ${response.status}`)
    }

    return data
  }

  useEffect(() => {
    fetchDashboardData(true) // Initial load with spinner
    // Set interval to 5 minutes (300,000ms) for silent background updates
    const interval = setInterval(() => fetchDashboardData(false), 300000)

    // Failsafe: stop loading after 5 seconds no matter what
    const failsafe = setTimeout(() => {
      setLoading(false)
    }, 5000)

    return () => {
      clearInterval(interval)
      clearTimeout(failsafe)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchContacts(searchQuery)
    }, 300)

    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery])

  const fetchDashboardData = async (isInitial = true) => {
    try {
      if (isInitial) setLoading(true)
      setError(null)

      // Fetch stats
      const statsData = await fetchJson(`${backendUrl}/api/dashboard-stats`, {
        headers: { "ngrok-skip-browser-warning": "1" },
      })

      if (statsData.success) {
        setStats(statsData.stats)
      } else {
        setError("Failed to fetch dashboard stats")
        console.error("Dashboard stats error:", statsData)
      }

      // Fetch contacts and ongoing calls - respect current search query
      await Promise.all([fetchContacts(searchQuery), fetchPendingCalls()])

      // Fetch latest call from history
      try {
        const callsData = await fetchJson(`${backendUrl}/api/calls`, {
          headers: { "ngrok-skip-browser-warning": "1" },
        })
        if (callsData.success && Array.isArray(callsData.calls) && callsData.calls.length > 0) {
          setLatestCall(callsData.calls[0])
        }
      } catch (err) {
        console.error("Failed to fetch latest call:", err)
      }
    } catch (error) {
      console.error("Error fetching dashboard data:", error)
      setError("Network error occurred while fetching data")
    } finally {
      setLoading(false)
    }
  }

  const fetchContacts = async (search = "") => {
    try {
      const url = search
        ? `${backendUrl}/api/contacts?search=${encodeURIComponent(search)}`
        : `${backendUrl}/api/contacts`

      const contactsData = await fetchJson(url, {
        headers: { "ngrok-skip-browser-warning": "1" },
      })

      if (contactsData.success) {
        console.log("Fetched processed contacts:", contactsData.contacts.length)

        // Process contacts - Use REAL names from DB or phone number if name is missing
        const processedContacts = contactsData.contacts.map((contact) => {
          return {
            phoneNumber: contact.phoneNumber || contact.phone || "Unknown",
            displayName: contact.contactName || contact.name || contact.phoneNumber || contact.phone || "Unknown Customer",
            sentiment: contact.sentiment || "neutral",
            sentimentDescription: contact.sentimentDescription || "No description available",
            callId: contact.callId || contact.call_sid || contact.callSid || "",
            updated_at: contact.updated_at || contact.created_at || null,
            processed_by_n8n: contact.processed_by_n8n || false,
          }
        }).sort((a, b) => {
          const dateA = a.updated_at ? new Date(a.updated_at) : new Date(0)
          const dateB = b.updated_at ? new Date(b.updated_at) : new Date(0)
          return dateB - dateA
        })

        setContacts(processedContacts)
      } else {
        setError("Failed to fetch contacts data: " + (contactsData.message || "Unknown error"))
        console.error("Contacts fetch error:", contactsData)
      }
    } catch (error) {
      console.error("Error fetching contacts:", error)
      setError("Network error occurred while fetching contacts")
    }
  }

  const fetchPendingCalls = async () => {
    try {
      const data = await fetchJson(`${backendUrl}/api/pending-calls`, {
        headers: { "ngrok-skip-browser-warning": "1" },
      })

      if (data.success) {
        setPendingCalls(data.pending_calls || [])
        console.log("Fetched ongoing calls:", data.pending_calls.length)
      } else {
        console.error("Failed to fetch ongoing calls:", data.message)
      }
    } catch (error) {
      console.error("Error fetching ongoing calls:", error)
    }
  }

  const handleContactClick = (contact) => {
    console.log("Contact clicked:", contact)
    if (contact.callId && contact.callId.trim() !== "") {
      navigate(`/call-detail/${contact.callId}`, {
        state: {
          contact: contact,
          phoneNumber: contact.phoneNumber,
          sentiment: contact.sentiment,
        },
      })
    } else {
      alert("No call ID available for this contact")
    }
  }

  const handleDeleteContact = async (phoneNumber) => {
    if (!window.confirm(`Are you sure you want to delete the sentiment data for ${phoneNumber}?`)) {
      return
    }

    try {
      setDeleteLoading((prev) => ({ ...prev, [phoneNumber]: true }))

      const encodedPhoneNumber = encodeURIComponent(phoneNumber)
      const result = await fetchJson(`${backendUrl}/api/delete-sentiment/${encodedPhoneNumber}`, {
        method: "DELETE",
        headers: { "ngrok-skip-browser-warning": "1" },
      })

      if (result.success) {
        setContacts((prev) => prev.filter((contact) => contact.phoneNumber !== phoneNumber))
        // Refresh stats after deletion
        fetchDashboardData()
        console.log("Contact deleted successfully:", phoneNumber)
      } else {
        alert("Failed to delete contact: " + result.message)
        console.error("Delete error:", result)
      }
    } catch (error) {
      console.error("Error deleting contact:", error)
      alert("Error deleting contact")
    } finally {
      setDeleteLoading((prev) => ({ ...prev, [phoneNumber]: false }))
    }
  }

  const downloadLeadsCSV = async () => {
    try {
      const url = `${backendUrl}/api/leads/export`

      const response = await fetch(url, {
        method: "GET",
        headers: {
          "ngrok-skip-browser-warning": "1",
          "x-api-key": process.env.REACT_APP_API_KEY || "voco-secret-key-2024" // Added authentication
        },
      })

      if (!response.ok) {
        throw new Error(`Failed to download CSV: ${response.status} ${response.statusText}`)
      }

      // Get the blob and trigger download
      const blob = await response.blob()
      const downloadUrl = window.URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = downloadUrl
      a.download = "leads.csv"
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(downloadUrl)
      document.body.removeChild(a)

      console.log("CSV downloaded successfully")
    } catch (error) {
      console.error("Error downloading CSV:", error)
      alert("Failed to download leads CSV: " + error.message)
    }
  }

  const handleDeletePendingCall = async (phoneNumber) => {
    if (!window.confirm(`Are you sure you want to delete the pending call for ${phoneNumber}?`)) {
      return
    }

    try {
      setDeleteLoading((prev) => ({ ...prev, [phoneNumber]: true }))

      const encodedPhoneNumber = encodeURIComponent(phoneNumber)
      const result = await fetchJson(`${backendUrl}/api/delete-pending-call/${encodedPhoneNumber}`, {
        method: "DELETE",
        headers: { "ngrok-skip-browser-warning": "1" },
      })

      if (result.success) {
        setPendingCalls((prev) => prev.filter((call) => call.phoneNumber !== phoneNumber))
        // Refresh stats after deletion
        fetchDashboardData()
        console.log("Pending call deleted successfully:", phoneNumber)
      } else {
        alert("Failed to delete pending call: " + result.message)
        console.error("Delete pending call error:", result)
      }
    } catch (error) {
      console.error("Error deleting pending call:", error)
      alert("Error deleting pending call")
    } finally {
      setDeleteLoading((prev) => ({ ...prev, [phoneNumber]: false }))
    }
  }

  const getSentimentColor = (sentiment) => {
    switch (sentiment?.toLowerCase()) {
      case "positive":
        return "#A7D477"
      case "negative":
        return "#EF5A6F"
      case "neutral":
        return "#F8ED8C"
      default:
        return "#F8ED8C"
    }
  }

  const getSentimentIcon = (sentiment) => {
    switch (sentiment?.toLowerCase()) {
      case "positive":
        return ""
      case "negative":
        return ""
      case "neutral":
        return ""
      default:
        return ""
    }
  }

  const formatDateTime = (dateStr) => {
    if (!dateStr) return "N/A"
    try {
      const parsedStr = typeof dateStr === 'string' && !dateStr.endsWith('Z') && !dateStr.includes('+') ? `${dateStr}Z` : dateStr
      const date = new Date(parsedStr)
      if (isNaN(date.getTime())) return "N/A"
      return date.toLocaleString("en-GB", {
        day: "2-digit",
        month: "2-digit",
        year: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
      })
    } catch (e) {
      return "N/A"
    }
  }

  // Helper function to get overall sentiment status
  const getOverallSentimentStatus = (score) => {
    if (score >= 70) return "Excellent"
    if (score >= 50) return "Good"
    if (score >= 30) return "Fair"
    return "Needs Attention"
  }

  // Helper function to get escalation urgency
  const getEscalationUrgency = (count, total) => {
    if (total === 0) return "None"
    const percentage = (count / total) * 100
    if (percentage >= 30) return "High"
    if (percentage >= 15) return "Medium"
    if (percentage > 0) return "Low"
    return "None"
  }

  // Analytics data preparation
  const getSentimentDistributionData = () => {
    const total = stats.totalContacts || stats.totalCalls
    return [
      {
        name: "Positive",
        value: stats.positiveSentiment,
        percentage: total > 0 ? Math.round((stats.positiveSentiment / total) * 100) : 0,
        color: "#A7D477",
      },
      {
        name: "Negative",
        value: stats.negativeSentiment,
        percentage: total > 0 ? Math.round((stats.negativeSentiment / total) * 100) : 0,
        color: "#EF5A6F",
      },
      {
        name: "Neutral",
        value: stats.neutralSentiment,
        percentage: total > 0 ? Math.round((stats.neutralSentiment / total) * 100) : 0,
        color: "#F8ED8C",
      },
    ]
  }

  const getSentimentTrendData = () => {
    if (!contacts || contacts.length === 0) return []

    // Get the last 7 days
    const today = new Date()
    const lastWeek = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000)

    const dateGroups = {}

    // Initialize all days in the last week with zero values
    for (let i = 6; i >= 0; i--) {
      const date = new Date(today.getTime() - i * 24 * 60 * 60 * 1000)
      const dateKey = date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })

      dateGroups[dateKey] = {
        date: dateKey,
        positive: 0,
        negative: 0,
        neutral: 0,
        total: 0,
      }
    }

    // Filter contacts from the last week and group by date
    contacts.forEach((contact) => {
      if (contact.updated_at) {
        const contactDate = new Date(contact.updated_at)

        // Only include contacts from the last week
        if (contactDate >= lastWeek && contactDate <= today) {
          const dateKey = contactDate.toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
          })

          if (dateGroups[dateKey]) {
            if (contact.sentiment === "positive") {
              dateGroups[dateKey].positive += 1
            } else if (contact.sentiment === "negative") {
              dateGroups[dateKey].negative += 1
            } else if (contact.sentiment === "neutral") {
              dateGroups[dateKey].neutral += 1
            }
            dateGroups[dateKey].total += 1
          }
        }
      }
    })

    const trendData = Object.values(dateGroups).map((item) => {
      let overallSentiment = "Neutral"
      if (item.positive > item.negative && item.positive > item.neutral) {
        overallSentiment = "Positive"
      } else if (item.negative > item.positive && item.negative > item.neutral) {
        overallSentiment = "Negative"
      }

      return {
        ...item,
        overallSentiment,
      }
    })

    // Sort by date to ensure proper chronological order
    trendData.sort((a, b) => {
      const dateA = new Date(a.date + ", " + today.getFullYear())
      const dateB = new Date(b.date + ", " + today.getFullYear())
      return dateA - dateB
    })

    return trendData
  }

  const CustomTick = (props) => {
    const { x, y, payload } = props
    const data = sentimentTrendData.find((item) => item.date === payload.value)

    return (
      <g transform={`translate(${x},${y})`}>
        <text x={0} y={0} dy={16} textAnchor="middle" fill="#666" fontSize="12">
          {payload.value}
        </text>
        <text x={0} y={0} dy={32} textAnchor="middle" fill="#888" fontSize="10">
          {data?.overallSentiment || ""}
        </text>
      </g>
    )
  }

  const getSentimentScoreData = () => {
    const total = stats.totalContacts || stats.totalCalls
    if (total === 0) return []

    return [
      {
        category: "Customer Satisfaction",
        score: Math.round(((stats.positiveSentiment - stats.negativeSentiment) / total) * 100),
      },
      {
        category: "Service Quality",
        score: Math.round((stats.positiveSentiment / total) * 100),
      },
      {
        category: "Overall Experience",
        score: Math.round(((stats.positiveSentiment + stats.neutralSentiment * 0.5) / total) * 100),
      },
    ]
  }

  // Enhanced refresh function
  const refreshData = async () => {
    console.log("=== Refreshing all data ===")
    await fetchDashboardData()
    console.log("=== Data refresh complete ===")
  }

  if (loading) {
    return (
      <div className="loading-container">
        <div className="loading-spinner spin"></div>
        <p>Loading dashboard data...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="error-container">
        <div className="error-message">
          <h3>⚠️ Error Loading Dashboard</h3>
          <p>{error}</p>
          <button onClick={refreshData} className="refresh-button">
            Try Again
          </button>
        </div>
      </div>
    )
  }

  const sentimentDistributionData = getSentimentDistributionData()
  const sentimentTrendData = getSentimentTrendData()
  const sentimentScoreData = getSentimentScoreData()
  


  return (
    <div className="dashboard fade-in">
      <h1 className="page-title">Dashboard</h1>

      <div className="quick-actions-card">
        <div className="quick-actions-header">
          <h2>Quick Actions</h2>
          <p>Open call history or jump into the latest call details.</p>
        </div>
        <div className="quick-actions-buttons">
          <button className="quick-action-btn primary" onClick={() => navigate("/call-history")}>Call History</button>
          <button className="quick-action-btn secondary" onClick={() => navigate("/call-center")}>Call Dialer</button>
          <button
            className="quick-action-btn success"
            onClick={downloadLeadsCSV}
          >
            Download Leads (CSV)
          </button>
          {latestCall && (
            <button
              className="quick-action-btn accent"
              onClick={() =>
                navigate(`/call-detail/${latestCall.call_sid || latestCall.id}`, {
                  state: {
                    phoneNumber: latestCall.phone || latestCall.customer_phone || "",
                    sentiment: latestCall.sentiment || "neutral",
                  },
                })
              }
            >
              Latest Call Details
            </button>
          )}
        </div>
      </div>

      {/* Updated Stats Grid with Enhanced Styling */}
      <div className="stats-grid">
        <div className="stat-card">
          <h3>Processed Calls</h3>
          <div className="stat-value">{stats.totalCalls}</div>
          <div className="stat-subtitle">{stats.totalCalls === 1 ? "Call analyzed" : "Calls analyzed"}</div>
        </div>

        <div className="stat-card">
          <h3>Ongoing Calls</h3>
          <div className="stat-value warning">{stats.ongoingCalls || 0}</div>
          <div className="stat-subtitle">{stats.ongoingCalls === 1 ? "Active call" : "Active calls"}</div>
        </div>

        <div className="stat-card">
          <h3>Overall Sentiment</h3>
          <div className="stat-value success">{stats.overallSentiment}%</div>
          <div className="stat-subtitle">{getOverallSentimentStatus(stats.overallSentiment)}</div>
        </div>

        <div className="stat-card">
          <h3>Escalation Recommended</h3>
          <div className="stat-value error">{stats.escalationRecommended}</div>
          <div className="stat-subtitle">
            {getEscalationUrgency(stats.escalationRecommended, stats.totalCalls)} priority
          </div>
        </div>
      </div>

      {/* Enhanced ongoing calls Section */}
      {pendingCalls.length > 0 && (
        <div className="pending-calls-section">
          <div className="pending-calls-header">
            <h3>Calls Processing({pendingCalls.length})</h3>
            <button className="pending-toggle-btn" onClick={() => setShowPendingCalls(!showPendingCalls)}>
              {showPendingCalls ? "Hide Pending" : "Show Pending"}
            </button>
          </div>

          {showPendingCalls && (
            <div className="pending-calls-grid">
              {pendingCalls.map((call, index) => (
                <div key={index} className="pending-call-card">
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDeletePendingCall(call.phoneNumber)
                    }}
                    disabled={deleteLoading[call.phoneNumber]}
                    className="delete-button"
                    title="Delete this pending call"
                  >
                    {deleteLoading[call.phoneNumber] ? "..." : "×"}
                  </button>
                  <div className="pending-call-header">
                    {/*<div className="pending-call-number">{call.phoneNumber}</div>*/}
                    <div className="pending-call-name">{call.phoneNumber || "Unknown Caller"}</div>
                    <div className="pending-status-badge">Processing</div>
                  </div>
                  <div className="pending-call-details">
                    <div className="call-id">Call ID: {call.callId || "N/A"}</div>
                    <div className="pending-call-time">
                      <small>Started: {new Date(call.created_at).toLocaleString()}</small>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Analytics Section */}
      <div className="analytics-section">
        <div className="analytics-header">
          <h2>📊 Sentiment Analytics</h2>
          <button className="analytics-toggle-btn" onClick={() => setShowAnalytics(!showAnalytics)}>
            {showAnalytics ? "Hide Analytics" : "Show Analytics"}
          </button>
        </div>

        {showAnalytics && (
          <div className="analytics-content">
            <div className="analytics-grid">
              {/* Overall Sentiment Distribution */}
              <div className="analytics-chart">
                <h4>🎯 Overall Sentiment Distribution</h4>
                <ResponsiveContainer width="100%" height={250}>
                  <PieChart>
                    <Pie
                      data={sentimentDistributionData}
                      cx="50%"
                      cy="50%"
                      labelLine={false}
                      label={({ name, percentage, value }) => value > 0 ? `${name}: ${percentage}%` : null}
                      outerRadius={80}
                      fill="#8884d8"
                      dataKey="value"
                    >
                      {sentimentDistributionData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value) => [`${value} contacts`, "Count"]} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="analytics-legend-centered">
                  {sentimentDistributionData.map((item, index) => (
                    <div key={index} className="legend-item">
                      <div className="legend-color" style={{ backgroundColor: item.color }}></div>
                      <span className="legend-text">
                        {item.name}: {item.value} contacts ({item.percentage}%)
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Sentiment Scores */}
              <div className="analytics-chart">
                <h4>📈 Sentiment Performance Metrics</h4>
                <ResponsiveContainer width="100%" height={400}>
                  <BarChart data={sentimentScoreData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="category" angle={-40} textAnchor="end" height={100} />
                    <YAxis domain={[-100, 100]} />
                    <Tooltip formatter={(value) => [`${value}%`, "Score"]} />
                    <Bar dataKey="score" fill="#8b5cf6">
                      {sentimentScoreData.map((entry, index) => (
                        <Cell
                          key={`cell-${index}`}
                          fill={entry.score > 50 ? "#A7D477" : entry.score > 0 ? "#F8ED8C" : "#EF5A6F"}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* Recent Sentiment Trend */}
              {sentimentTrendData.length > 0 && (
                <div className="analytics-chart">
                  <h4>📊 Last Week Sentiment Trend</h4>
                  <ResponsiveContainer width="100%" height={400}>
                    <AreaChart data={sentimentTrendData} margin={{ top: 20, right: 30, left: 0, bottom: -25 }}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="date" tick={<CustomTick />} height={80} interval={0} />
                      <YAxis />
                      <Tooltip />
                      <Area type="monotone" dataKey="positive" stackId="1" stroke="#A7D477" fill="#A7D477" />
                      <Area type="monotone" dataKey="neutral" stackId="1" stroke="#F8ED8C" fill="#F8ED8C" />
                      <Area type="monotone" dataKey="negative" stackId="1" stroke="#EF5A6F" fill="#EF5A6F" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            {/* Enhanced Insights */}
            <div className="sentiment-insights">
              <h4>🔍 Key Insights</h4>
              <div className="insights-grid">
                <div className="insight-card">
                  <h5>Processing Status</h5>
                  <p>
                    {stats.totalCalls} calls have been processed with sentiment analysis.
                    {stats.ongoingCalls > 0
                      ? ` ${stats.ongoingCalls} call${stats.ongoingCalls === 1 ? "" : "s"} are currently active and being processed.`
                      : " All calls have been processed."}
                  </p>
                </div>
                <div className="insight-card">
                  <h5>Overall Health</h5>
                  <p>
                    Overall sentiment score is {stats.overallSentiment}% -{" "}
                    {getOverallSentimentStatus(stats.overallSentiment)}.
                    {stats.overallSentiment >= 70
                      ? " Excellent customer satisfaction levels maintained."
                      : stats.overallSentiment >= 50
                        ? " Good performance with room for improvement."
                        : " Immediate attention required to improve customer experience."}
                  </p>
                </div>
                <div className="insight-card">
                  <h5>Escalation Priority</h5>
                  <p>
                    {stats.escalationRecommended} calls require escalation (
                    {getEscalationUrgency(stats.escalationRecommended, stats.totalCalls)} priority).
                    {stats.escalationRecommended === 0
                      ? " No immediate follow-up needed."
                      : " Review negative sentiment calls for improvement opportunities."}
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      <h2 className="section-title-dashboard">Call Details</h2>

      {/* Search Section */}
      <div className="search-section">
        <input
          type="text"
          placeholder="Search phone numbers..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="search-input"
        />
      </div>

      <div className="contacts-container">
        {contacts.length === 0 ? (
          <div className="no-contacts">
            <p>
              {searchQuery
                ? `No processed contacts found matching "${searchQuery}"`
                : "No processed contacts found. Contacts will appear here after n8n processes call sentiment data."}
            </p>
            {!searchQuery && (
              <div style={{ marginTop: "15px" }}>
                <p>No processed contacts found yet. Contacts will appear here after real calls are processed.</p>
              </div>
            )}
            {pendingCalls.length > 0 && !searchQuery && (
              <div className="pending-info-banner">
                <p>
                  ℹ️ {pendingCalls.length} call{pendingCalls.length === 1 ? " is" : "s are"} currently being processed.
                  Contact cards will appear here once processing is complete.
                </p>
              </div>
            )}
          </div>
        ) : (
          <div className="contacts-grid">
            {contacts.map((contact, index) => {
              return (
                <div
                  key={`${contact.phoneNumber}-${index}`}
                  className="contact-card processed-contact"
                  style={{
                    cursor: contact.callId && contact.callId.trim() !== "" ? "pointer" : "default",
                  }}
                  onClick={() => handleContactClick(contact)}
                >
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDeleteContact(contact.phoneNumber)
                    }}
                    disabled={deleteLoading[contact.phoneNumber]}
                    className="delete-button"
                    title="Delete this contact"
                  >
                    {deleteLoading[contact.phoneNumber] ? "..." : "×"}
                  </button>

                  {/* Processing status indicator */}

                  <div className="contact-header">
                    <div className="contact-number-name" style={{ fontSize: "18px", fontWeight: "700", letterSpacing: "-0.3px" }}>
                      {contact.displayName}
                    </div>
                    <div
                      className={`sentiment-badge ${contact.sentiment}`}
                      style={{
                        background: getSentimentColor(contact.sentiment),
                        color: contact.sentiment === "neutral" ? "black" : "white",
                        fontWeight: "600",
                        padding: "4px 10px",
                        borderRadius: "20px",
                      }}
                    >
                      <span className="sentiment-icon">{getSentimentIcon(contact.sentiment)}</span>
                      <span className="sentiment-text" style={{ textTransform: "uppercase", fontSize: "11px" }}>{contact.sentiment}</span>
                    </div>
                  </div>

                  {/* Extra Details Row for identifying them */}
                  <div className="contact-identifiers" style={{ display: "flex", flexDirection: "column", gap: "6px", margin: "10px 0", fontSize: "13px", color: "#555" }}>
                    {contact.displayName !== contact.phoneNumber && contact.phoneNumber !== "Unknown" && (
                      <div className="identifier-item" style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <span>📞</span> <span style={{ fontFamily: "monospace", fontWeight: "600" }}>{contact.phoneNumber}</span>
                      </div>
                    )}
                    {contact.callId && contact.callId.trim() !== "" && (
                      <div className="identifier-item" style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <span>🆔</span> <code style={{ fontSize: "11px", background: "rgba(102, 126, 234, 0.08)", padding: "2px 6px", borderRadius: "6px", fontFamily: "monospace", color: "#667eea" }}>#{contact.callId.slice(-8)}</code>
                      </div>
                    )}
                    {contact.updated_at && (
                      <div className="identifier-item" style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <span>🕐</span> <span style={{ fontSize: "12px", color: "#666" }}>{formatDateTime(contact.updated_at)}</span>
                      </div>
                    )}
                  </div>

                  {/* Enhanced description display */}
                  <div className="sentiment-description" style={{ margin: "14px 0", padding: "12px 14px", background: "rgba(255,255,255,0.7)", border: "1px solid rgba(0,0,0,0.05)", borderRadius: "14px", boxShadow: "inset 0 1px 3px rgba(0,0,0,0.02)" }}>
                    <strong style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px", color: "#888" }}>Summary:</strong>
                    <div style={{ marginTop: "6px", fontSize: "13.5px", lineHeight: "1.5", color: "#333" }}>
                      {contact.sentimentDescription &&
                        contact.sentimentDescription.trim() !== "" &&
                        contact.sentimentDescription !== "undefined" &&
                        contact.sentimentDescription !== "null" ? (
                        <span>
                          {contact.sentimentDescription.length > 140
                            ? `${contact.sentimentDescription.substring(0, 140)}...`
                            : contact.sentimentDescription}
                        </span>
                      ) : (
                        <span style={{ color: "#999", fontStyle: "italic" }}>No details captured yet.</span>
                      )}
                    </div>
                  </div>

                  {/* Premium Action Footer */}
                  {contact.callId && contact.callId.trim() !== "" && (
                    <div className="contact-meta" style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", borderTop: "1px solid rgba(0,0,0,0.05)", paddingTop: "10px", marginTop: "10px" }}>
                      <div className="click-hint" style={{ fontSize: "11px", color: "#667eea", fontWeight: "600", display: "flex", alignItems: "center", gap: "4px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                        View Call Details <span style={{ transition: "transform 0.2s ease" }}>→</span>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="refresh-section">
        <button className="refresh-button" onClick={refreshData} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh Data"}
        </button>
      </div>

      {/* --- New Master Reports Section --- */}
      <div className="reports-section fade-in">
        <h2 className="section-title-dashboard">Reports & External Data</h2>
        <div className="report-card">
          <div className="report-info">
            <div>
              <h3>Lead Database Export <span className="csv-badge">CSV</span></h3>
              <p>Download the complete leads database as a structured CSV file for offline analysis and CRM imports.</p>
            </div>
          </div>
          <button
            className="download-csv-btn"
            onClick={downloadLeadsCSV}
          >
            Download CSV
          </button>
        </div>
      </div>
    </div>
  )
}

export default Dashboard
