"use client"

import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import "./CallHistory.css"

const CallHistory = () => {
  const navigate = useNavigate()
  const [calls, setCalls] = useState([])
  const [contacts, setContacts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeView, setActiveView] = useState("all")
  const [searchTerm, setSearchTerm] = useState("")

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

  const fetchRecords = async () => {
    try {
      setLoading(true)
      setError(null)

      const [callsResult, contactsResult] = await Promise.all([
        fetchJson(`${backendUrl}/api/calls`, { headers: { "ngrok-skip-browser-warning": "1" } }),
        fetchJson(`${backendUrl}/api/contacts`, { headers: { "ngrok-skip-browser-warning": "1" } }),
      ])

      setCalls(callsResult.success ? callsResult.calls || [] : [])
      setContacts(contactsResult.success ? contactsResult.contacts || [] : [])
    } catch (err) {
      setError(err.message || "Failed to load records")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchRecords()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const normalizePhone = (value) => (value || "").toString().replace(/\D/g, "")

  const contactsByPhone = useMemo(() => {
    const map = new Map()

    contacts.forEach((contact) => {
      const phone = normalizePhone(contact.phoneNumber || contact.phone || contact.customer_phone)
      if (!phone) return
      map.set(phone, contact)
    })

    return map
  }, [contacts])

  const enrichedRecords = useMemo(() => {
    return calls.map((call) => {
      const phoneValue = call.customer_phone || call.phone_number || call.phone || call.customer?.phoneNumber || ""
      const phoneKey = normalizePhone(phoneValue)
      const matchedContact = phoneKey ? contactsByPhone.get(phoneKey) : null

      const customerName =
        call.customer_name ||
        call.customer?.name ||
        matchedContact?.contactName ||
        matchedContact?.name ||
        (phoneValue ? `Lead ${phoneValue.slice(-4)}` : "Unknown Customer")

      let sentiment = call.sentiment || matchedContact?.sentiment || "neutral"


      const sentimentDescription =
        call.sentiment_description ||
        call.sentimentDescription ||
        matchedContact?.sentimentDescription ||
        ""
      const summary =
        call.summary ||
        call.sentiment_description ||
        matchedContact?.sentimentDescription ||
        "No summary saved yet."

      const recordingStatus = call.recording_url ? "Available" : "Not saved"

      let safeRecordingUrl = call.recording_url;
      if (safeRecordingUrl) {
        try {
          const urlObj = new URL(safeRecordingUrl);
          safeRecordingUrl = `${backendUrl}${urlObj.pathname}${urlObj.search}`;
        } catch (e) {
          if (call.recording_sid || call.recordingSid) {
            safeRecordingUrl = `${backendUrl}/api/recording/media/${call.recording_sid || call.recordingSid}`;
          }
        }
      }

      return {
        ...call,
        customerName,
        phoneValue: phoneValue || matchedContact?.phoneNumber || "Unknown Number",
        sentiment,
        sentimentDescription,
        summary,
        recordingStatus,
        recording_url: safeRecordingUrl,
        contactSentimentDescription: matchedContact?.sentimentDescription || "",
        updatedAt: call.updated_at || matchedContact?.updated_at || null,
      }
    })
  }, [calls, contactsByPhone, backendUrl])

  const filteredRecords = useMemo(() => {
    const query = searchTerm.trim().toLowerCase()

    return enrichedRecords.filter((record) => {
      const matchesView =
        activeView === "all" ||
        (activeView === "leads" && record.phoneValue) ||
        (activeView === "calls" && record.call_sid) ||
        (activeView === "positive" && record.sentiment === "positive") ||
        (activeView === "negative" && record.sentiment === "negative")

      if (!query) return matchesView

      const haystack = [
        record.customerName,
        record.phoneValue,
        record.call_sid,
        record.summary,
        record.sentiment,
        record.status,
      ]
        .join(" ")
        .toLowerCase()

      return matchesView && haystack.includes(query)
    })
  }, [activeView, enrichedRecords, searchTerm])

  const stats = useMemo(() => {
    const total = enrichedRecords.length
    const positive = enrichedRecords.filter((record) => record.sentiment === "positive").length
    const negative = enrichedRecords.filter((record) => record.sentiment === "negative").length
    const withRecordings = enrichedRecords.filter((record) => record.recording_url).length

    return { total, positive, negative, withRecordings }
  }, [enrichedRecords])

  const getSentimentTone = (sentiment) => {
    switch ((sentiment || "neutral").toLowerCase()) {
      case "positive":
        return { bg: "#dcfce7", fg: "#166534" }
      case "negative":
        return { bg: "#fee2e2", fg: "#991b1b" }
      default:
        return { bg: "#fef3c7", fg: "#92400e" }
    }
  }

  return (
    <div className="call-history-page">
      <div className="call-history-hero">
        <div>
          <p className="eyebrow">MongoDB Leads & Records</p>
          <h1>Customer Records</h1>
          <p className="subtitle">
            All leads, customer details, call records, summaries, and sentiment in one place.
          </p>
        </div>

        <div className="hero-actions">
          <button className="refresh-history-btn" onClick={fetchRecords} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
          <div className="search-shell">
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search name, phone, call ID, sentiment..."
            />
          </div>
        </div>
      </div>

      <div className="metric-grid">
        <div className="metric-card">
          <span className="metric-label">Total Records</span>
          <span className="metric-value">{stats.total}</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Positive</span>
          <span className="metric-value positive">{stats.positive}</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Negative</span>
          <span className="metric-value negative">{stats.negative}</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">With Recordings</span>
          <span className="metric-value">{stats.withRecordings}</span>
        </div>
      </div>

      <div className="view-switcher">
        {[
          { id: "all", label: "All Records" },
          { id: "leads", label: "Leads" },
          { id: "calls", label: "Call Records" },
          { id: "positive", label: "Positive" },
          { id: "negative", label: "Negative" },
        ].map((tab) => (
          <button
            key={tab.id}
            className={`view-tab ${activeView === tab.id ? "active" : ""}`}
            onClick={() => setActiveView(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {error && <div className="history-error">{error}</div>}

      {loading ? (
        <div className="history-loading">Loading stored customer records...</div>
      ) : filteredRecords.length === 0 ? (
        <div className="history-empty">
          <h3>No matching records</h3>
          <p>
            Records appear here after the backend saves leads, call transcripts, sentiment, and recordings into MongoDB.
          </p>
        </div>
      ) : (
        <div className="records-grid">
          {filteredRecords.map((record) => {
            const tone = getSentimentTone(record.sentiment)

            return (
              <article key={record.call_sid || record._id} className="record-card">
                <div className="record-top">
                  <div>
                    <h3 className="record-name">{record.customerName}</h3>
                    <p className="record-phone">{record.phoneValue}</p>
                  </div>
                  <span className="status-pill">{record.status || "connected"}</span>
                </div>

                <div className="record-details">
                  <div>
                    <span className="detail-label">Call ID</span>
                    <span className="detail-value monospace">{record.call_sid || "N/A"}</span>
                  </div>
                  <div>
                    <span className="detail-label">Started</span>
                    <span className="detail-value">{record.started_at ? new Date(record.started_at + (record.started_at.includes('Z') ? '' : 'Z')).toLocaleString('en-GB', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true }).toUpperCase() : "Unknown"}</span>
                  </div>
                  <div>
                    <span className="detail-label">Recording</span>
                    <span className="detail-value">{record.recordingStatus}</span>
                  </div>
                </div>

                <div className="record-summary">{record.summary}</div>

                <div className="record-tags">
                  <span className="sentiment-chip" style={{ background: tone.bg, color: tone.fg }}>
                    Sentiment: {record.sentiment}
                  </span>
                  {record.sentimentDescription && <span className="sentiment-note">{record.sentimentDescription}</span>}
                  {record.recording_url && (
                    <a className="recording-link" href={record.recording_url} target="_blank" rel="noreferrer">
                      Open Recording
                    </a>
                  )}
                </div>

                <div className="record-actions">
                  <button
                    className="view-details-btn"
                    onClick={() => navigate(`/call-detail/${record.call_sid}`)}
                    disabled={!record.call_sid}
                  >
                    View Call Details
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default CallHistory