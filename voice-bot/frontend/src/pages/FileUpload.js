"use client"

import { useState } from "react"
import "./FileUpload.css"

const FileUpload = () => {
  const [file, setFile] = useState(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const backendUrl = process.env.REACT_APP_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:5000"

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0])
      setError(null)
    }
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = () => {
    setIsDragging(false)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setIsDragging(false)

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0])
      setError(null)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()

    if (!file) {
      setError("Please select a file to upload")
      return
    }

    setIsLoading(true)
    setError(null)
    setResult(null)

    const formData = new FormData()
    formData.append("file", file)

    try {
      const response = await fetch(`${backendUrl}/api/upload-file`, {
        method: "POST",
        body: formData,
      })

      const data = await response.json()

      if (data.success) {
        setResult(data.data)
        setFile(null)
      } else {
        setError(data.message || "File upload failed")
      }
    } catch (err) {
      setError("An error occurred while uploading the file")
      console.error(err)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="file-upload fade-in">
      <h1 className="page-title">File Upload</h1>

      <div className="card upload-card">
        <h2>Upload File</h2>
        <p className="upload-description">
          Upload audio files, documents, or other supported files to be processed by the AI assistant.
        </p>

        {error && (
          <div className="alert alert-error">
            <p>{error}</p>
          </div>
        )}

        {result && (
          <div className="alert alert-success">
            <p>File uploaded successfully!</p>
            {result.id && <p>File ID: {result.id}</p>}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div
            className={`drop-zone ${isDragging ? "dragging" : ""} ${file ? "has-file" : ""}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <input type="file" id="file" onChange={handleFileChange} className="file-input" />

            {file ? (
              <div className="file-info">
                <div className="file-icon">📄</div>
                <div className="file-name">{file.name}</div>
                <div className="file-size">{(file.size / 1024).toFixed(2)} KB</div>
                <button type="button" className="remove-file" onClick={() => setFile(null)}>
                  ✕
                </button>
              </div>
            ) : (
              <div className="drop-content">
                <div className="upload-icon">📁</div>
                <p>Drag & drop your file here or</p>
                <label htmlFor="file" className="select-file-btn">
                  Select File
                </label>
              </div>
            )}
          </div>

          <button
            type="submit"
            className={`btn btn-primary upload-btn ${isLoading ? "loading" : ""}`}
            disabled={!file || isLoading}
          >
            {isLoading ? (
              <>
                <span className="spinner spin"></span>
                <span>Uploading...</span>
              </>
            ) : (
              "Upload File"
            )}
          </button>
        </form>
      </div>

      {result && (
        <div className="card result-card">
          <h2>Upload Result</h2>
          <pre className="result-json">{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  )
}

export default FileUpload
