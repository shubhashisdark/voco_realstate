import "./Home.css"
import aiBrainHand from "../assets/ai-brain-hand.jpeg"

const Home = () => {

  const stats = [
    { value: "99.9%", label: "Uptime Services" },
    { value: "50ms", label: "Avg Response Time" },
    { value: "10M+", label: "Calls Processed" },
    { value: "500+", label: "Happy Customers" },
  ]

  const industries = [
    {
      icon: "🏦",
      title: "Finance",
      description: "Automated customer service, loan processing, and financial consultations",
    },
    {
      icon: "💼",
      title: "Sales",
      description: "Lead qualification, appointment setting, and follow-up campaigns",
    },
    {
      icon: "🍽️",
      title: "Restaurants",
      description: "Reservation management, order taking, and customer support",
    },
    {
      icon: "🏠",
      title: "Real Estate",
      description: "Property inquiries, showing scheduling, and client screening",
    },
    {
      icon: "🏥",
      title: "Healthcare",
      description: "Appointment scheduling, patient reminders, and basic consultations",
    },
    {
      icon: "🛒",
      title: "E-commerce",
      description: "Order support, product inquiries, and customer service automation",
    },
  ]


  return (
    <div className="home fade-in">
      <section className="hero">
        <div className="hero-background">
          <div className="hero-gradient"></div>
          <div className="hero-pattern"></div>
        </div>
        <div className="container">
          <div className="hero-content">
            <div className="hero-text">
              <h1 className="hero-title">
                Introducing Autonomous, Self-learning AI Agents
              </h1>
              <p className="hero-description">
                Deployable on your private cloud
              </p>
              <div className="hero-image-container">
                <img
                  src={aiBrainHand}
                  alt="AI Brain in Hand - Autonomous Intelligence"
                  width={400}
                  height={300}
                  className="hero-image"
                />
              </div>
              <div className="hero-actions">
                <a href="https://calendly.com/mitali-scankart/new-meeting" target="_blank" rel="noopener noreferrer">
                  <button className="btn btn-primary btn-lg">
                    Book a Demo
                  </button>
                </a>
              </div>
              <div className="hero-stats">
                {stats.map((stat, index) => (
                  <div key={index} className="stat-item">
                    <div className="stat-value">{stat.value}</div>
                    <div className="stat-label">{stat.label}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>
      <section className="features">
        <div className="container">
          <div className="section-header">
            <h2 className="section-title">Trusted Across Industries</h2>
            <p className="section-description">
              Our AI voice agents are transforming customer interactions across multiple sectors
            </p>
          </div>
          <div className="features-grid">
            {industries.map((industry, index) => (
              <div key={index} className="feature-card slide-up" style={{ animationDelay: `${index * 0.1}s` }}>
                <div className="feature-icon">{industry.icon}</div>
                <h3 className="feature-title">{industry.title}</h3>
                <p className="feature-description">{industry.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="cta">
        <div className="container">
          <div className="cta-content">
            <h2 className="cta-title">Ready to transform your customer interactions?</h2>
            <p className="cta-description">
              Join thousands of businesses using VOCO AI to automate their voice communications.
            </p>
          </div>
        </div>
      </section>
    </div>
  )
}

export default Home