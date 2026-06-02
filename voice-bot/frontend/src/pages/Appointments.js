"use client"

import { useState, useEffect } from "react"
import "./Appointments.css"

const Appointments = () => {
  const [appointments, setAppointments] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filterStatus, setFilterStatus] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [filterType, setFilterType] = useState("")
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [showDetailModal, setShowDetailModal] = useState(false)
  const [selectedAppointment, setSelectedAppointment] = useState(null)

  const statusColors = {
    scheduled: "#2196F3",
    confirmed: "#4CAF50",
    completed: "#9E9E9E",
    cancelled: "#F44336",
  }

  useEffect(() => {
    fetchAppointments()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const backendUrl = process.env.REACT_APP_BACKEND_URL || "http://localhost:5000"
  const apiKey = process.env.REACT_APP_API_KEY || ""

  const fetchAppointments = async () => {
    setLoading(true)
    try {
      const response = await fetch(`${backendUrl}/api/appointments`, {
        headers: {
          "X-API-Key": apiKey,
        },
      })
      const data = await response.json()
      
      if (data.success) {
        setAppointments(data.appointments)
      } else {
        setError("Failed to fetch appointments")
      }
    } catch (err) {
      setError("Connection error")
      console.error(err)
    }
    setLoading(false)
  }

  const handleStatusUpdate = async (appointmentId, newStatus) => {
    try {
      const response = await fetch(`${backendUrl}/api/appointments/${appointmentId}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        body: JSON.stringify({ status: newStatus }),
      })
      const data = await response.json()
      
      if (data.success) {
        fetchAppointments()
      } else {
        alert(data.message || "Failed to update appointment")
      }
    } catch (err) {
      alert("Error updating appointment")
      console.error(err)
    }
  }

  const handleDelete = async (appointmentId) => {
    if (!window.confirm("Are you sure you want to delete this appointment?")) return
    
    try {
      const response = await fetch(`${backendUrl}/api/appointments/${appointmentId}`, {
        method: "DELETE",
        headers: {
          "X-API-Key": apiKey,
        },
      })
      const data = await response.json()
      
      if (data.success) {
        fetchAppointments()
      } else {
        alert(data.message || "Failed to delete appointment")
      }
    } catch (err) {
      alert("Error deleting appointment")
      console.error(err)
    }
  }

  const openDetailModal = (appointment) => {
    setSelectedAppointment(appointment)
    setShowDetailModal(true)
  }

  const formatDate = (dateStr) => {
    if (!dateStr) return "N/A"
    try {
      return new Date(dateStr).toLocaleDateString("en-US", {
        weekday: "short",
        day: "2-digit",
        month: "short",
        year: "numeric",
      })
    } catch {
      return dateStr
    }
  }

  const formatTime = (timeStr) => {
    if (!timeStr) return "N/A"
    try {
      const parts = timeStr.split(':')
      if (parts.length >= 2) {
        let hour = parseInt(parts[0], 10)
        const minute = parts[1]
        const ampm = hour >= 12 ? 'PM' : 'AM'
        hour = hour % 12
        hour = hour ? hour : 12 // the hour '0' should be '12'
        return `${hour.toString().padStart(2, '0')}:${minute} ${ampm}`
      }
      return timeStr
    } catch {
      return timeStr
    }
  }

  const getStatusBadge = (status) => {
    const color = statusColors[status] || "#9E9E9E"
    return (
      <span className="status-badge" style={{ backgroundColor: color }}>
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </span>
    )
  }

  const getFilteredAppointments = () => {
    return appointments.filter((a) => {
      if (filterStatus && a.status !== filterStatus) return false;
      if (filterType && a.appointment_type !== filterType) return false;
      
      if (searchQuery) {
        const query = searchQuery.toLowerCase();
        const name = (a.customer_name || "").toLowerCase();
        const phone = (a.customer_phone || "").toLowerCase();
        const prop = (a.property_of_interest || "").toLowerCase();
        if (!name.includes(query) && !phone.includes(query) && !prop.includes(query)) {
          return false;
        }
      }
      
      if (dateFrom && a.preferred_date) {
        if (new Date(a.preferred_date) < new Date(dateFrom)) return false;
      }
      if (dateTo && a.preferred_date) {
        if (new Date(a.preferred_date) > new Date(dateTo)) return false;
      }
      
      return true;
    });
  }

  const getCountByStatus = (status) => {
    return appointments.filter((a) => a.status === status).length
  }

  if (loading) {
    return (
      <div className="appointments-page">
        <div className="loading">Loading appointments...</div>
      </div>
    )
  }

  return (
    <div className="appointments-page">
      <div className="page-header">
        <h1>Appointments</h1>
        <p className="page-subtitle">Manage site visits and callback appointments booked by VOCO AI</p>
        {error && <p style={{ color: 'red', marginTop: '10px' }}>{error}</p>}
      </div>

      {/* Status Summary Cards */}
      <div className="status-summary">
        <div className={`summary-card status-scheduled ${getCountByStatus("scheduled") === 0 ? "muted" : ""}`}>
          <div className="summary-icon scheduled">📅</div>
          <div className="summary-info">
            <span className="summary-count">{getCountByStatus("scheduled")}</span>
            <span className="summary-label">Scheduled</span>
          </div>
        </div>
        <div className={`summary-card status-confirmed ${getCountByStatus("confirmed") === 0 ? "muted" : ""}`}>
          <div className="summary-icon confirmed">✅</div>
          <div className="summary-info">
            <span className="summary-count">{getCountByStatus("confirmed")}</span>
            <span className="summary-label">Confirmed</span>
          </div>
        </div>
        <div className={`summary-card status-completed ${getCountByStatus("completed") === 0 ? "muted" : ""}`}>
          <div className="summary-icon completed">✔️</div>
          <div className="summary-info">
            <span className="summary-count">{getCountByStatus("completed")}</span>
            <span className="summary-label">Completed</span>
          </div>
        </div>
        <div className={`summary-card status-cancelled ${getCountByStatus("cancelled") === 0 ? "muted" : ""}`}>
          <div className="summary-icon cancelled">❌</div>
          <div className="summary-info">
            <span className="summary-count">{getCountByStatus("cancelled")}</span>
            <span className="summary-label">Cancelled</span>
          </div>
        </div>
      </div>

      {/* Filter */}
      <div className="filter-bar">
        <div className="filter-group search-group">
          <input 
            type="text" 
            placeholder="Search name, phone, property..." 
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="search-input"
          />
        </div>
        <div className="filter-group">
          <label>Status:</label>
          <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="">All Statuses</option>
            <option value="scheduled">Scheduled</option>
            <option value="confirmed">Confirmed</option>
            <option value="completed">Completed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </div>
        <div className="filter-group">
          <label>Type:</label>
          <select value={filterType} onChange={(e) => setFilterType(e.target.value)}>
            <option value="">All Types</option>
            <option value="site_visit">Site Visit</option>
            <option value="callback">Callback</option>
          </select>
        </div>
        <div className="filter-group date-group">
          <label>Date:</label>
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          <span className="date-separator">to</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>
      </div>

      {/* Appointments Table */}
      <div className="appointments-table-container">
        {getFilteredAppointments().length === 0 ? (
          <div className="empty-state">
            <p>No appointments found</p>
          </div>
        ) : (
          <table className="appointments-table">
            <thead>
              <tr>
                <th>Customer</th>
                <th>Phone</th>
                <th>Type</th>
                <th>Property</th>
                <th>Date</th>
                <th>Time</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {getFilteredAppointments().map((appt) => (
                <tr key={appt._id}>
                  <td>
                    <div className="customer-cell">
                      <div className="customer-avatar">
                        {(appt.customer_name && appt.customer_name !== "N/A" ? appt.customer_name[0].toUpperCase() : "?")}
                      </div>
                      <span className="customer-name">{appt.customer_name || "N/A"}</span>
                    </div>
                  </td>
                  <td>{appt.customer_phone || "N/A"}</td>
                  <td>
                    <span className={`type-badge ${appt.appointment_type === 'site_visit' ? 'site-visit' : 'callback'}`}>
                      {appt.appointment_type === "site_visit" ? "🏠 Site Visit" : "📞 Callback"}
                    </span>
                  </td>
                  <td>{appt.property_of_interest || "N/A"}</td>
                  <td className="date-cell">{formatDate(appt.preferred_date)}</td>
                  <td>{formatTime(appt.preferred_time)}</td>
                  <td>{getStatusBadge(appt.status)}</td>
                  <td className="actions-cell">
                    <button
                      className="appt-btn appt-btn-view"
                      onClick={() => openDetailModal(appt)}
                      title="View Details"
                    >
                      <span className="btn-icon">👁️</span>
                      <span className="btn-label">View</span>
                    </button>
                    {appt.status === "scheduled" && (
                      <button
                        className="appt-btn appt-btn-confirm"
                        onClick={() => handleStatusUpdate(appt._id, "confirmed")}
                        title="Confirm"
                      >
                        <span className="btn-icon">✅</span>
                        <span className="btn-label">Confirm</span>
                      </button>
                    )}
                    {appt.status === "confirmed" && (
                      <button
                        className="appt-btn appt-btn-complete"
                        onClick={() => handleStatusUpdate(appt._id, "completed")}
                        title="Mark Complete"
                      >
                        <span className="btn-icon">✔️</span>
                        <span className="btn-label">Complete</span>
                      </button>
                    )}
                    {(appt.status === "scheduled" || appt.status === "confirmed") && (
                      <button
                        className="appt-btn appt-btn-cancel"
                        onClick={() => handleStatusUpdate(appt._id, "cancelled")}
                        title="Cancel"
                      >
                        <span className="btn-icon">❌</span>
                        <span className="btn-label">Cancel</span>
                      </button>
                    )}
                    <button
                      className="appt-btn appt-btn-delete"
                      onClick={() => handleDelete(appt._id)}
                      title="Delete"
                    >
                      <span className="btn-icon">🗑️</span>
                      <span className="btn-label">Delete</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Detail Modal */}
      {showDetailModal && selectedAppointment && (
        <div className="modal-overlay" onClick={() => setShowDetailModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Appointment Details</h2>
              <button className="modal-close" onClick={() => setShowDetailModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="detail-grid">
                <div className="detail-item">
                  <label>Customer Name</label>
                  <span>{selectedAppointment.customer_name || "N/A"}</span>
                </div>
                <div className="detail-item">
                  <label>Phone Number</label>
                  <span>{selectedAppointment.customer_phone || "N/A"}</span>
                </div>
                <div className="detail-item">
                  <label>Appointment Type</label>
                  <span className="type-badge">{selectedAppointment.appointment_type === "site_visit" ? "🏠 Site Visit" : "📞 Callback"}</span>
                </div>
                <div className="detail-item">
                  <label>Status</label>
                  {getStatusBadge(selectedAppointment.status)}
                </div>
                <div className="detail-item">
                  <label>Property of Interest</label>
                  <span>{selectedAppointment.property_of_interest || "N/A"}</span>
                </div>
                <div className="detail-item">
                  <label>Preferred Date</label>
                  <span>{formatDate(selectedAppointment.preferred_date)}</span>
                </div>
                <div className="detail-item">
                  <label>Preferred Time</label>
                  <span>{formatTime(selectedAppointment.preferred_time)}</span>
                </div>
                <div className="detail-item full-width">
                  <label>Notes</label>
                  <span>{selectedAppointment.notes || "No notes"}</span>
                </div>
                <div className="detail-item">
                  <label>Created At</label>
                  <span>{formatDate(selectedAppointment.created_at)}</span>
                </div>
                <div className="detail-item">
                  <label>Call SID</label>
                  <span>{selectedAppointment.call_sid || "N/A"}</span>
                </div>
              </div>
            </div>
            <div className="modal-footer">
              {selectedAppointment.status === "scheduled" && (
                <button
                  className="modal-btn modal-btn-confirm"
                  onClick={() => {
                    handleStatusUpdate(selectedAppointment._id, "confirmed")
                    setShowDetailModal(false)
                  }}
                >
                  Confirm Appointment
                </button>
              )}
              {selectedAppointment.status === "confirmed" && (
                <button
                  className="modal-btn modal-btn-complete"
                  onClick={() => {
                    handleStatusUpdate(selectedAppointment._id, "completed")
                    setShowDetailModal(false)
                  }}
                >
                  Mark Complete
                </button>
              )}
              <button className="modal-btn modal-btn-cancel" onClick={() => setShowDetailModal(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default Appointments