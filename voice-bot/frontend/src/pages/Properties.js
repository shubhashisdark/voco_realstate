"use client"

import { useState, useEffect } from "react"
import "./Properties.css"

const Properties = () => {
  const [properties, setProperties] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showModal, setShowModal] = useState(false)
  const [editingProperty, setEditingProperty] = useState(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [filterType, setFilterType] = useState("")

  const [formData, setFormData] = useState({
    project_name: "",
    developer: "",
    location: "",
    city: "",
    type: "2BHK",
    size_sqft: "",
    price: "",
    price_value: "",
    amenities: "",
    status: "Available",
    floors: "",
    possession_date: "",
    description: "",
  })

  const backendUrl = process.env.REACT_APP_BACKEND_URL || "http://localhost:5000"

  const propertyTypes = ["1BHK", "2BHK", "3BHK", "3BHK+Villa", "Plot", "Commercial"]

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      fetchProperties()
    }, 500); // Waits 500ms after the last keystroke before fetching

    return () => clearTimeout(timeoutId); // Cancels the previous timer if user keeps typing
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, filterType])

  const fetchProperties = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (searchQuery) params.set("location", searchQuery)
      if (filterType) params.set("property_type", filterType)

      const response = await fetch(`${backendUrl}/api/properties?${params}`)
      const data = await response.json()

      if (data.success) {
        setProperties(data.properties)
      } else {
        setError("Failed to fetch properties")
      }
    } catch (err) {
      setError("Connection error")
      console.error(err)
    }
    setLoading(false)
  }

  const handleInputChange = (e) => {
    const { name, value } = e.target
    setFormData(prev => ({
      ...prev,
      [name]: value
    }))
  }

  const openAddModal = () => {
    setEditingProperty(null)
    setFormData({
      project_name: "",
      developer: "",
      location: "",
      city: "",
      type: "2BHK",
      size_sqft: "",
      price: "",
      price_value: "",
      amenities: "",
      status: "Available",
      floors: "",
      possession_date: "",
      description: "",
    })
    setShowModal(true)
  }

  const openEditModal = (property) => {
    setEditingProperty(property)
    setFormData({
      project_name: property.project_name || "",
      developer: property.developer || "",
      location: property.location || "",
      city: property.city || "",
      type: property.type || "2BHK",
      size_sqft: property.size_sqft || "",
      price: property.price || "",
      price_value: property.price_value || "",
      amenities: property.amenities || "",
      status: property.status || "Available",
      floors: property.floors || "",
      possession_date: property.possession_date || "",
      description: property.description || "",
    })
    setShowModal(true)
  }

  const handleSubmit = async (e) => {
    e.preventDefault()

    try {
      const url = editingProperty
        ? `${backendUrl}/api/properties/${editingProperty._id}`
        : `${backendUrl}/api/properties`

      const method = editingProperty ? "PUT" : "POST"

      const response = await fetch(url, {
        method,
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(formData),
      })

      const data = await response.json()

      if (data.success) {
        alert(data.message)
        setShowModal(false)
        fetchProperties()
      } else {
        alert("Error: " + data.message)
      }
    } catch (err) {
      alert("Connection error")
      console.error(err)
    }
  }

  const handleDelete = async (id, projectName) => {
    if (!window.confirm(`Are you sure you want to delete "${projectName}"?`)) {
      return
    }

    try {
      const response = await fetch(`${backendUrl}/api/properties/${id}`, {
        method: "DELETE",
      })

      const data = await response.json()

      if (data.success) {
        alert("Property deleted successfully")
        fetchProperties()
      } else {
        alert("Error: " + data.message)
      }
    } catch (err) {
      alert("Connection error")
      console.error(err)
    }
  }

  return (
    <div className="properties-page">
      <div className="properties-header">
        <h1>Property Management</h1>
        <button className="btn-add" onClick={openAddModal}>
          + Add Property
        </button>
      </div>

      <div className="properties-filters">
        <input
          type="text"
          placeholder="Search by location..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="search-input"
        />
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="filter-select"
        >
          <option value="">All Types</option>
          {propertyTypes.map(type => (
            <option key={type} value={type}>{type}</option>
          ))}
        </select>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div className="properties-grid">
        {loading ? (
          <div className="loading" style={{ gridColumn: "1 / -1", textAlign: "center", padding: "2rem" }}>Loading properties...</div>
        ) : properties.length === 0 ? (
          <div className="no-data" style={{ gridColumn: "1 / -1", textAlign: "center" }}>
            <p>No properties found. Click "Add Property" to get started.</p>
          </div>
        ) : (
          properties.map((property) => (
            <div key={property._id} className="property-card">
              <div className="property-status">{property.status || "Available"}</div>
              <h3>{property.project_name}</h3>
              <div className="property-details">
                <p className="property-location">📍 {property.location}</p>
                <p className="property-type">🏠 {property.type} • {property.size_sqft?.toLocaleString()} sq.ft</p>
                <p className="property-price">💰 {property.price}</p>
                {property.amenities && (
                  <p className="property-amenities">🏋️ {property.amenities}</p>
                )}
                {property.possession_date && (
                  <p className="property-possession">📅 Possession: {property.possession_date}</p>
                )}
              </div>
              <div className="property-actions">
                <button onClick={() => openEditModal(property)} className="btn-edit">
                  Edit
                </button>
                <button onClick={() => handleDelete(property._id, property.project_name)} className="btn-delete">
                  Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Add/Edit Modal */}
      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{editingProperty ? "Edit Property" : "Add New Property"}</h2>
              <button className="modal-close" onClick={() => setShowModal(false)}>×</button>
            </div>

            <form onSubmit={handleSubmit} className="property-form">
              <div className="form-grid">
                <div className="form-group">
                  <label>Project Name *</label>
                  <input
                    type="text"
                    name="project_name"
                    value={formData.project_name}
                    onChange={handleInputChange}
                    required
                  />
                </div>

                <div className="form-group">
                  <label>Developer</label>
                  <input
                    type="text"
                    name="developer"
                    value={formData.developer}
                    onChange={handleInputChange}
                  />
                </div>

                <div className="form-group">
                  <label>Location *</label>
                  <input
                    type="text"
                    name="location"
                    value={formData.location}
                    onChange={handleInputChange}
                    required
                  />
                </div>

                <div className="form-group">
                  <label>City *</label>
                  <input
                    type="text"
                    name="city"
                    value={formData.city}
                    onChange={handleInputChange}
                    required
                  />
                </div>

                <div className="form-group">
                  <label>Property Type *</label>
                  <select
                    name="type"
                    value={formData.type}
                    onChange={handleInputChange}
                    required
                  >
                    {propertyTypes.map(type => (
                      <option key={type} value={type}>{type}</option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label>Size (sq.ft)</label>
                  <input
                    type="number"
                    name="size_sqft"
                    value={formData.size_sqft}
                    onChange={handleInputChange}
                  />
                </div>

                <div className="form-group">
                  <label>Price *</label>
                  <input
                    type="text"
                    name="price"
                    value={formData.price}
                    onChange={handleInputChange}
                    placeholder="e.g., 65 Lakhs"
                    required
                  />
                </div>

                <div className="form-group">
                  <label>Price (numeric)</label>
                  <input
                    type="number"
                    name="price_value"
                    value={formData.price_value}
                    onChange={handleInputChange}
                    placeholder="e.g., 6500000"
                  />
                </div>

                <div className="form-group full-width">
                  <label>Amenities</label>
                  <input
                    type="text"
                    name="amenities"
                    value={formData.amenities}
                    onChange={handleInputChange}
                    placeholder="Gym, Pool, Garden, Parking"
                  />
                </div>

                <div className="form-group">
                  <label>Status</label>
                  <select
                    name="status"
                    value={formData.status}
                    onChange={handleInputChange}
                  >
                    <option value="Available">Available</option>
                    <option value="Reserved">Reserved</option>
                    <option value="Sold">Sold</option>
                  </select>
                </div>

                <div className="form-group">
                  <label>Floors</label>
                  <input
                    type="number"
                    name="floors"
                    value={formData.floors}
                    onChange={handleInputChange}
                  />
                </div>

                <div className="form-group">
                  <label>Possession Date</label>
                  <input
                    type="date"
                    name="possession_date"
                    value={formData.possession_date}
                    onChange={handleInputChange}
                  />
                </div>

                <div className="form-group full-width">
                  <label>Description</label>
                  <textarea
                    name="description"
                    value={formData.description}
                    onChange={handleInputChange}
                    rows="3"
                  />
                </div>
              </div>

              <div className="form-actions">
                <button type="button" className="btn-cancel" onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn-submit">
                  {editingProperty ? "Update Property" : "Add Property"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

export default Properties