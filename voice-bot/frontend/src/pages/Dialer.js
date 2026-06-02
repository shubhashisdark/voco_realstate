"use client"

import { useState } from "react"
import "./Dialer.css"

const Dialer = () => {
  const [activeMode, setActiveMode] = useState("manual")
  const [formData, setFormData] = useState({
    name: "",
    phoneNumber: "",
    email: "",
    referenceCode: "",
  })
  const [selectedFile, setSelectedFile] = useState(null)

  const handleInputChange = (e) => {
    const { name, value } = e.target
    setFormData((prev) => ({
      ...prev,
      [name]: value,
    }))
  }

  const handleFileSelect = (e) => {
    const file = e.target.files[0]
    setSelectedFile(file)
  }

  const handleAddToQueue = () => {
    console.log("Adding to queue:", formData)
    // Add your queue logic here
  }

  const handleViewQueue = () => {
    console.log("Viewing queue with file:", selectedFile)
    // Add your view queue logic here
  }

  const handleFileUploadClick = () => {
    document.getElementById("fileInput").click()
  }

  return (
    <div className="dialer-container">
      <h1 className="dialer-title">Dialer</h1>

      <div className="dialer-card">
        <div className="card-header">
          <h2>Make a call</h2>
        </div>

        <div className="mode-buttons">
          <button
            className={`mode-btn ${activeMode === "manual" ? "active" : ""}`}
            onClick={() => setActiveMode("manual")}
          >
            Manual Input
          </button>
          <button
            className={`mode-btn ${activeMode === "upload" ? "active" : ""}`}
            onClick={() => setActiveMode("upload")}
          >
            Upload Lead File
          </button>
        </div>

        {activeMode === "manual" && (
          <div className="manual-input-section">
            <div className="form-group">
              <input
                type="text"
                name="name"
                placeholder="Name"
                value={formData.name}
                onChange={handleInputChange}
                className="form-input"
              />
            </div>

            <div className="form-group">
              <input
                type="tel"
                name="phoneNumber"
                placeholder="Phone Number"
                value={formData.phoneNumber}
                onChange={handleInputChange}
                className="form-input"
              />
            </div>

            <button className="action-btn" onClick={handleAddToQueue}>
              Add to Queue
            </button>

            <div className="more-fields">+ More Fields</div>
          </div>
        )}

        {activeMode === "upload" && (
          <div className="upload-section">
            <div className="upload-area" onClick={handleFileUploadClick}>
              <div className="upload-icon">📁</div>
              <div className="upload-text">Click here to upload your file</div>
              <div className="upload-subtext">Supported Format: Excel Sheet, csv file</div>
            </div>

            <input
              id="fileInput"
              type="file"
              accept=".xlsx,.xls,.csv,.pdf"
              onChange={handleFileSelect}
              style={{ display: "none" }}
            />

            {selectedFile && (
              <div className="file-selected">
                <div className="file-info">
                  <span className="file-name">{selectedFile.name}</span>
                  <button className="remove-file" onClick={() => setSelectedFile(null)}>
                    🗑️
                  </button>
                </div>
                <div className="file-size">{(selectedFile.size / 1024).toFixed(1)} KB</div>
              </div>
            )}

            <button className="action-btn" onClick={handleViewQueue}>
              View Queue
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default Dialer