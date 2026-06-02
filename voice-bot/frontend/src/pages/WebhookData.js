"use client"

import { useState, useEffect } from "react"
import "./WebhookData.css"

const WebhookData = () => {
  const [webhookData, setWebhookData] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error] = useState(null)
  const [searchTerm, setSearchTerm] = useState("")

  // Mock data for demonstration
  useEffect(() => {
    // Simulate API call to fetch webhook data
    setTimeout(() => {
      const mockData = [
        {
          id: "1",
          name: "John Doe",
          email: "john.doe@example.com",
          phoneNumber: "+10000000001",
          details: "Interested in product demo",
          timestamp: new Date(Date.now() - 3600000).toISOString(),
        },
        {
          id: "2",
          name: "Jane Smith",
          email: "jane.smith@example.com",
          phoneNumber: "+10000000002",
          details: "Requesting pricing information",
          timestamp: new Date(Date.now() - 7200000).toISOString(),
        },
        {
          id: "3",
          name: "Robert Johnson",
          email: "robert.johnson@example.com",
          phoneNumber: "+10000000003",
          details: "Technical support inquiry",
          timestamp: new Date(Date.now() - 10800000).toISOString(),
        },
      ]

      setWebhookData(mockData)
      setIsLoading(false)
    }, 1000)
  }, [])

  const formatDate = (dateString) => {
    const date = new Date(dateString)
    return date.toLocaleString()
  }

  const filteredData = webhookData.filter((item) => {
    const searchLower = searchTerm.toLowerCase()
    return (
      item.name.toLowerCase().includes(searchLower) ||
      item.email.toLowerCase().includes(searchLower) ||
      item.phoneNumber.includes(searchTerm) ||
      item.details.toLowerCase().includes(searchLower)
    )
  })

  if (isLoading) {
    return (
      <div className="loading-container">
        <div className="loading-spinner spin"></div>
        <p>Loading webhook data...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="webhook-data fade-in">
        <h1 className="page-title">Webhook Data</h1>
        <div className="alert alert-error">
          <p>{error}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="webhook-data fade-in">
      <h1 className="page-title">Webhook Data</h1>

      <div className="search-container">
        <input
          type="text"
          className="search-input"
          placeholder="Search by name, email, phone, or details..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </div>

      {webhookData.length === 0 ? (
        <div className="no-data">
          <p>No webhook data available</p>
        </div>
      ) : (
        <>
          <div className="data-count">
            Showing {filteredData.length} of {webhookData.length} entries
          </div>

          <div className="webhook-table-container">
            <table className="webhook-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Phone Number</th>
                  <th>Details</th>
                  <th>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {filteredData.map((item) => (
                  <tr key={item.id} className="data-row">
                    <td>{item.name}</td>
                    <td>{item.email}</td>
                    <td>{item.phoneNumber}</td>
                    <td>{item.details}</td>
                    <td>{formatDate(item.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

export default WebhookData
