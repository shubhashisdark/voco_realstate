"use client"

import { useState } from "react"
import { useNavigate } from "react-router-dom"
import "./CallCenter.css"

const CallCenter = () => {
  const [phoneNumbers, setPhoneNumbers] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [lastCallId, setLastCallId] = useState("")
  const [error, setError] = useState(null)
  const [redirecting, setRedirecting] = useState(false)
  const [provider] = useState("gemini") // "gemini" or "nvidia" - default to Gemini

  const navigate = useNavigate()

  const backendUrl = process.env.REACT_APP_BACKEND_URL || "http://localhost:5000";

  const handleSubmit = async (e) => {
    e.preventDefault()

    if (!phoneNumbers.trim()) {
      setError("Please enter at least one phone number")
      return
    }

    const numbersArray = phoneNumbers
      .split(",")
      .map((num) => num.trim())
      .filter((num) => num)

    if (numbersArray.length === 0) {
      setError("Please enter valid phone numbers")
      return
    }

    setIsLoading(true)
    setError(null)
    setResult(null)

    try {
      // Select endpoint based on provider
      const endpoint = provider === "gemini" ? "/api/outbound-call" : "/api/nvidia/make-calls"

      console.log(
        `Making request to: ${backendUrl}${endpoint} (Provider: ${provider.toUpperCase()})`,
      )
      console.log("Request payload:", { phoneNumbers: numbersArray })

      const response = await fetch(`${backendUrl}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "1",
        },
        body: JSON.stringify(
          provider === "gemini"
            ? { phoneNumbers: numbersArray, contact_id: null }
            : { phoneNumbers: numbersArray }
        ),
      })

      console.log("Response status:", response.status)

      if (!response.ok) {
        const errorText = await response.text()
        console.error("Response error:", errorText)
        throw new Error(`HTTP ${response.status}: ${errorText}`)
      }

      const data = await response.json()
      console.log("Response data:", data)

      if (data.call_sid || data.success) {
        const resolvedCallId = data.call_sid || data?.results?.[0]?.data?.id || ""
        setLastCallId(resolvedCallId)
        setResult({
          ...data,
          success: true,
          message: data.message || "Calls initiated successfully",
          results: data.results || [],
        })
        setPhoneNumbers("")

        console.log("Calls initiated successfully:", data)

        // Show success message briefly, then redirect
        setRedirecting(true)

        // Redirect to dashboard after 2 seconds to show success message
        setTimeout(() => {
          navigate("/dashboard")
        }, 2000)

      } else {
        setError(data.message || "Failed to initiate calls")
      }
    } catch (err) {
      console.error("Fetch error:", err)

      if (err.name === "TypeError" && err.message.includes("fetch")) {
        setError("Network error: Unable to connect to the server. Please check your internet connection.")
      } else if (err.message.includes("JSON")) {
        setError("Server returned invalid response. Please try again.")
      } else {
        setError(err.message || "An error occurred while making the API call")
      }
    } finally {
      setIsLoading(false)
    }
  }

  // Helper function to get success count
  const getSuccessCount = () => {
    if (!result) return 0
    if (result.results && Array.isArray(result.results)) {
      return result.results.filter((call) => call.success).length
    }
    return result.success ? 1 : 0
  }

  // Handle immediate redirect (skip waiting)
  const handleRedirectNow = () => {
    navigate("/dashboard")
  }

  return (
    <div className="call-center fade-in">
      <h1 className="page-title-call">Call Dialer</h1>

      <div className="card call-form-card">
        <h2>Make Outbound Calls</h2>
        <p className="form-description">
          Enter phone numbers separated by commas to initiate outbound calls using the AI assistant.
        </p>

        {error && (
          <div className="alert alert-error">
            <p>{error}</p>
          </div>
        )}

        {result && result.success && (
          <div className="alert alert-success">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div>
                <p>{result.message}</p>
                <p>{getSuccessCount()} calls initiated successfully</p>
                {result.note && (
                  <p>
                    <small>{result.note}</small>
                  </p>
                )}
                {redirecting && (
                  <p style={{ color: "#28a745", fontWeight: "bold" }}>
                    ✅ Redirecting to dashboard in 2 seconds...
                  </p>
                )}
                {lastCallId && (
                  <p style={{ marginTop: "8px" }}>
                    Last Call ID: <strong>{lastCallId}</strong>
                  </p>
                )}
              </div>
              {redirecting && (
                <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                  {lastCallId && (
                    <button
                      onClick={() => navigate(`/call-detail/${lastCallId}`)}
                      style={{
                        padding: "8px 16px",
                        backgroundColor: "#0d6efd",
                        color: "white",
                        border: "none",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "14px"
                      }}
                    >
                      View Details
                    </button>
                  )}
                  <button
                    onClick={() => navigate('/call-history')}
                    style={{
                      padding: "8px 16px",
                      backgroundColor: "#6c757d",
                      color: "white",
                      border: "none",
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "14px"
                    }}
                  >
                    Call History
                  </button>
                  <button
                    onClick={handleRedirectNow}
                    style={{
                      padding: "8px 16px",
                      backgroundColor: "#28a745",
                      color: "white",
                      border: "none",
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "14px"
                    }}
                  >
                    Dashboard
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="phoneNumbers">Phone Numbers</label>
            <textarea
              id="phoneNumbers"
              className="form-control"
              style={{ border: "1px solid #FFDCCC" }}
              value={phoneNumbers}
              onChange={(e) => setPhoneNumbers(e.target.value)}
              placeholder="Enter phone numbers separated by commas (e.g., +91XXXXXXXXXX, +91XXXXXXXXXX)"
              rows={5}
              disabled={isLoading || redirecting}
            />
            <small className="form-hint">Format: Include country code (e.g., +91 for India)</small>
          </div>

          <button
            type="submit"
            className={`btn btn-primary ${isLoading ? "loading" : ""}`}
            disabled={isLoading || redirecting}
            style={{ backgroundColor: "#FFB4A2", borderColor: "#FFB4A2" }}
          >
            {isLoading ? (
              <>
                <span className="spinner spin"></span>
                <span>Initiating Calls...</span>
              </>
            ) : redirecting ? (
              <>
                <span>✅ Success! Redirecting...</span>
              </>
            ) : (
              "Make Calls"
            )}
          </button>
        </form>
      </div>

      {result && result.results && !redirecting && (
        <div className="card results-card">
          <h2>Call Results</h2>
          <div className="call-results">
            {result.results.map((call, index) => (
              <div key={index} className={`call-result ${call.success ? "success" : "error"}`}>
                <div className="call-number">{call.number}</div>
                <div className="call-status">{call.success ? "Success" : "Failed"}</div>
                {call.success && call.data && call.data.id && <div className="call-id">Call ID: {call.data.id}</div>}
                {!call.success && call.error && <div className="call-error">{call.error}</div>}
              </div>
            ))}
          </div>

          <div style={{ marginTop: "15px", textAlign: "center" }}>
            {lastCallId && (
              <button
                onClick={() => navigate(`/call-detail/${lastCallId}`)}
                style={{
                  padding: "10px 20px",
                  marginRight: "8px",
                  backgroundColor: "#0d6efd",
                  color: "white",
                  border: "none",
                  borderRadius: "4px",
                  cursor: "pointer",
                  fontSize: "16px"
                }}
              >
                View Last Call Details →
              </button>
            )}
            <button
              onClick={() => navigate('/call-history')}
              style={{
                padding: "10px 20px",
                marginRight: "8px",
                backgroundColor: "#6c757d",
                color: "white",
                border: "none",
                borderRadius: "4px",
                cursor: "pointer",
                fontSize: "16px"
              }}
            >
              Call History →
            </button>
            <button
              onClick={handleRedirectNow}
              style={{
                padding: "10px 20px",
                backgroundColor: "#007bff",
                color: "white",
                border: "none",
                borderRadius: "4px",
                cursor: "pointer",
                fontSize: "16px"
              }}
            >
              View Dashboard →
            </button>
          </div>
        </div>
      )}


    </div>
  )
}

export default CallCenter
