"use client"

import { useState } from "react"
import { Link } from "react-router-dom"
import "./Navbar.css"
import logo from '../assets/logo.png';

const Navbar = ({ theme, toggleTheme, assistantInfo }) => {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <nav className="navbar">
      <div className="navbar-container">
        <Link to="/dashboard" className="navbar-logo">
          <img src={logo} alt="VOCO Logo" className="logo-image" />
          <span className="logo-text">VOCO</span>
          <span className="logo-accent"></span>
        </Link>

        {/*{assistantInfo && (
          <div className="assistant-info">
            <div className="assistant-status pulse"></div>
            <span>{assistantInfo.name}</span>
          </div>
        )}*/}

        <div className={`navbar-menu ${menuOpen ? "active" : ""}`}>
          {/*<a href="https://calendly.com/mitali-scankart/new-meeting" target="_blank" rel="noopener noreferrer">
        <button className="nav-btn">
            Book a Demo
        </button>
    </a>*/}
          <Link to="/dashboard" className="nav-item" onClick={() => setMenuOpen(false)}>
            Home
          </Link>
          <Link to="/call-center" className="nav-item" onClick={() => setMenuOpen(false)}>
            Call Dialer
          </Link>
          <Link to="/properties" className="nav-item" onClick={() => setMenuOpen(false)}>
            Properties
          </Link>
          <Link to="/appointments" className="nav-item" onClick={() => setMenuOpen(false)}>
            Appointments
          </Link>
          <Link to="/file-upload" className="nav-item" onClick={() => setMenuOpen(false)}>
            File Upload
          </Link>
          {/*<Link to="/call-dialer" className="nav-item" onClick={() => setMenuOpen(false)}>
      Call Dialer
    </Link>*/}
          {/*<Link to="/webhook-data" className="nav-item" onClick={() => setMenuOpen(false)}>
      Webhook Data
    </Link>*/}
        </div>

        {/*<div className="navbar-actions">
          <button className="theme-toggle" onClick={toggleTheme}>
            {theme === "dark" ? "☀️" : "🌙"}
          </button>
          <button className="menu-toggle" onClick={toggleMenu}>
            <span></span>
            <span></span>
            <span></span>
          </button>
        </div>*/}
      </div>
    </nav>
  )
}

export default Navbar
