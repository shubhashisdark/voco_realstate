"use client"

import { useState, useEffect } from "react"
import { useParams, useLocation, useNavigate } from "react-router-dom"
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from "recharts"
import { getBackendUrl } from "../utils/backendUrl"
import "./CallDetail.css"

const CallDetail = () => {
  const { callId } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const [callData, setCallData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [isPlaying, setIsPlaying] = useState(false)
  // eslint-disable-next-line no-unused-vars
  const [currentTime, setCurrentTime] = useState(0)

  const [sentimentData, setSentimentData] = useState([])
  const [overallSentiment, setOverallSentiment] = useState(null)
  const [aiRecommendation, setAiRecommendation] = useState("")
  const [loadingRecommendation, setLoadingRecommendation] = useState(false)
  const [showAnalytics, setShowAnalytics] = useState(true)
  const [customerDetailsForm, setCustomerDetailsForm] = useState({
    contactName: "",
    phoneNumber: "",
    interest: "",
    sentiment: "neutral",
    sentimentDescription: "",
    notes: "",
  })
  const [appointmentForm, setAppointmentForm] = useState({
    appointmentType: "site_visit",
    preferredDate: "",
    preferredTime: "",
    propertyOfInterest: "",
    notes: "",
  })
  const [savingCustomerDetails, setSavingCustomerDetails] = useState(false)
  const [bookingAppointment, setBookingAppointment] = useState(false)

  // Get contact info from navigation state
  const contactInfo = location.state?.contact || {}
  const phoneNumber = location.state?.phoneNumber || "Unknown"
  const sentiment = location.state?.sentiment || "neutral"

  const backendUrl = getBackendUrl()

  const callStorageKey = (id) => `voco_call_${id}`
  const callIndexKey = "voco_call_index"

  const loadCachedCall = (id) => {
    try {
      const raw = localStorage.getItem(callStorageKey(id))
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  }

  const saveCachedCall = (callSnapshot) => {
    if (!callSnapshot?.id) return

    try {
      localStorage.setItem(callStorageKey(callSnapshot.id), JSON.stringify(callSnapshot))

      const rawIndex = localStorage.getItem(callIndexKey)
      const index = rawIndex ? JSON.parse(rawIndex) : []
      const nextIndex = [callSnapshot.id, ...index.filter((item) => item !== callSnapshot.id)].slice(0, 200)
      localStorage.setItem(callIndexKey, JSON.stringify(nextIndex))
    } catch (error) {
      console.warn("Unable to persist call snapshot:", error)
    }
  }

  const buildCallSnapshot = (data) => ({
    id: data.id,
    status: data.status,
    startedAt: data.startedAt,
    endedAt: data.endedAt,
    recordingUrl: data.recordingUrl || "",
    transcript: data.transcript || "",
    summary: data.summary || "",
    sentiment: data?.sentiment || overallSentiment?.label || sentiment || "neutral",
    customer: data.customer,
    phoneNumber: data.phoneNumber,
    duration: data.duration,
    totalCost: data.totalCost,
    updatedAt: new Date().toISOString(),
  })

  useEffect(() => {
    if (callId) {
      const cachedCall = loadCachedCall(callId)
      if (cachedCall) {
        setCallData(cachedCall)
        setLoading(false)
      }

      fetchCallDetails()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callId])

  useEffect(() => {
    if (callData) {
      analyzeSentimentProgression()
      generateAIRecommendation()

      setCustomerDetailsForm((prev) => ({
        ...prev,
        contactName: callData?.customer?.name || prev.contactName || "",
        phoneNumber: callData?.phoneNumber?.phoneNumber || prev.phoneNumber || "",
        sentiment: (callData?.sentiment && callData?.sentiment !== "neutral")
          ? callData.sentiment
          : (overallSentiment?.label || callData?.sentiment || prev.sentiment || "neutral"),
        sentimentDescription: callData?.sentimentDescription || prev.sentimentDescription || "",
      }))

      setAppointmentForm((prev) => ({
        ...prev,
        propertyOfInterest: callData?.rawData?.property_of_interest || prev.propertyOfInterest || "",
        notes: callData?.summary || prev.notes || "",
      }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callData])

  const bookAppointment = async () => {
    if (!callData) return

    const customerName = customerDetailsForm.contactName || callData?.customer?.name || ""
    const customerPhone = customerDetailsForm.phoneNumber || callData?.phoneNumber?.phoneNumber || ""

    if (!customerName.trim() || !customerPhone.trim()) {
      alert("Please enter customer name and phone")
      return
    }

    if (!appointmentForm.preferredDate || !appointmentForm.preferredTime) {
      alert("Please choose preferred date and time")
      return
    }

    try {
      setBookingAppointment(true)
      const response = await fetch(`${backendUrl}/api/appointments`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "1",
          ...(process.env.REACT_APP_API_KEY ? { "X-API-Key": process.env.REACT_APP_API_KEY } : {}),
        },
        body: JSON.stringify({
          customer_name: customerName,
          customer_phone: customerPhone,
          appointment_type: appointmentForm.appointmentType,
          property_of_interest: appointmentForm.propertyOfInterest,
          preferred_date: appointmentForm.preferredDate,
          preferred_time: appointmentForm.preferredTime,
          notes: appointmentForm.notes,
          call_sid: callData.call_sid || callData.id || callId,
        }),
      })

      const result = await response.json()
      if (result.success) {
        alert("Appointment booked successfully")
        setAppointmentForm((prev) => ({
          ...prev,
          preferredDate: "",
          preferredTime: "",
          notes: "",
        }))
      } else {
        alert(result.message || "Failed to book appointment")
      }
    } catch (e) {
      console.error("bookAppointment error:", e)
      alert("Error booking appointment")
    } finally {
      setBookingAppointment(false)
    }
  }

  const saveCustomerDetails = async () => {
    if (!callData) return

    const payload = {
      ...customerDetailsForm,
      callId: callData.id || callId,
      callSid: callData.call_sid || callData.id || callId,
      twilioCallSid: callData.twilio_call_sid || "",
      recordingSid: callData.recording_sid || callData.recordingSid || "",
      recordingUrl: callData.recordingUrl || "",
      callDetails: {
        status: callData.status || "",
        summary: callData.summary || "",
        duration: callData.duration || 0,
        startedAt: callData.startedAt || "",
        endedAt: callData.endedAt || "",
        transcript: callData.transcript || "",
      },
    }

    try {
      setSavingCustomerDetails(true)
      const response = await fetch(`${backendUrl}/api/customer-details`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "1",
        },
        body: JSON.stringify(payload),
      })
      const result = await response.json()
      if (result.success) {
        alert("Customer details saved successfully")
      } else {
        alert(result.message || "Failed to save customer details")
      }
    } catch (e) {
      console.error("saveCustomerDetails error:", e)
      alert("Error saving customer details")
    } finally {
      setSavingCustomerDetails(false)
    }
  }

  const fetchCallDetails = async () => {
    try {
      setLoading(true)
      setError(null)
      const response = await fetch(`${backendUrl}/api/call/${callId}`, {
        headers: { "ngrok-skip-browser-warning": "1" },
      })
      const result = await response.json()

      if (result.success) {
        // Accept either `call` (our API) or legacy `callData` payload
        const callDoc = result.call || result.callData || null
        if (!callDoc) {
          setError("No call data returned from server")
        } else {
          // Extract phone number from call doc
          const phone = callDoc.phone || callDoc.customer_phone || callDoc.telephony_data?.to_number || ""
          let matchedContact = null
          if (phone) {
            try {
              const contactRes = await fetch(`${backendUrl}/api/contacts?search=${encodeURIComponent(phone)}`, {
                headers: { "ngrok-skip-browser-warning": "1" },
              })
              const contactData = await contactRes.json()
              if (contactData.success && Array.isArray(contactData.contacts) && contactData.contacts.length > 0) {
                const cleanedPhone = phone.replace(/[+\s-]/g, "")
                matchedContact = contactData.contacts.find(c => {
                  const cPhone = (c.phoneNumber || c.phone || "").replace(/[+\s-]/g, "")
                  return cPhone && (cPhone.includes(cleanedPhone) || cleanedPhone.includes(cPhone))
                })
              }
            } catch (contactErr) {
              console.error("Failed to fetch contact for name matching:", contactErr)
            }
          }
          const transformedData = transformCallData(callDoc, matchedContact)
          setCallData(transformedData)
          saveCachedCall(buildCallSnapshot(transformedData))
        }
      } else {
        setError(result.message || "Failed to fetch call details")
      }
    } catch (err) {
      console.error("Error fetching call details:", err)
      const cachedCall = callId ? loadCachedCall(callId) : null
      if (cachedCall) {
        setCallData(cachedCall)
        setError(null)
      } else {
        setError("Network error occurred while fetching call details")
      }
    } finally {
      setLoading(false)
    }
  }

  // Transform API call data to match the expected structure
  const transformCallData = (rawCallData, contact = null) => {
    // Safely parse date strings as UTC if they lack a timezone offset to prevent timezone offset mismatches
    const parseUtc = (dateStr) => {
      if (!dateStr) return null
      const parsedStr = typeof dateStr === 'string' && !dateStr.endsWith('Z') && !dateStr.includes('+') ? `${dateStr}Z` : dateStr
      return new Date(parsedStr)
    }

    // Normalize timestamps
    const startRaw = rawCallData.started_at || rawCallData.created_at || rawCallData.startedAt || rawCallData.createdAt
    const endRaw = rawCallData.ended_at || rawCallData.updated_at || rawCallData.endedAt || rawCallData.updatedAt
    const startTime = startRaw ? parseUtc(startRaw) : new Date()
    const endTime = rawCallData.conversation_duration
      ? new Date(startTime.getTime() + rawCallData.conversation_duration * 1000)
      : endRaw
        ? parseUtc(endRaw)
        : new Date()

    const detectedPhone =
      rawCallData.phone ||
      rawCallData.customer_phone ||
      rawCallData.telephony_data?.to_number ||
      rawCallData.context_details?.recipient_phone_number ||
      phoneNumber

    return {
      // Basic call info
      id: rawCallData.id || rawCallData.call_sid || rawCallData.twilio_call_sid,
      mongoId: rawCallData._id,
      call_sid: rawCallData.call_sid || rawCallData.id || "",
      twilio_call_sid: rawCallData.twilio_call_sid || "",
      status: rawCallData.status,
      createdAt: rawCallData.created_at || rawCallData.createdAt || "",
      updatedAt: rawCallData.updated_at || rawCallData.updatedAt || "",
      startedAt: rawCallData.started_at || rawCallData.created_at || rawCallData.startedAt || "",
      endedAt: endTime.toISOString(),

      // Customer info - extract from telephony data or use contact info
      customer: {
        name:
          rawCallData.customer_name ||
          contact?.contactName ||
          contact?.name ||
          contactInfo.contactName ||
          location.state?.contactName ||
          `Contact-${(detectedPhone || "").slice(-4) || "0000"}`,
      },

      // Phone number info
      phoneNumber: {
        phoneNumber: detectedPhone,
      },

      // Assistant info
      assistant: {
        name: "AI Assistant",
        id: rawCallData.agent_id || rawCallData.agentId || null,
      },

      // Call content
      transcript: (() => {
        if (Array.isArray(rawCallData.transcripts) && rawCallData.transcripts.length > 0) {
          return rawCallData.transcripts
            .map((t) => `${t.speaker || t.role || "User"}: ${t.text || t.msg || t.content || ""}`)
            .join("\n")
        }
        return rawCallData.transcript || rawCallData.transcripts || ""
      })(),
      summary: rawCallData.summary || rawCallData.sentiment_description || rawCallData.summaryText || "",

      // Recording
      recordingUrl:
        rawCallData.recording_url || rawCallData.recordingUrl || rawCallData.telephony_data?.recording_url || "",
      recording_sid: rawCallData.recording_sid || rawCallData.recordingSid || rawCallData.recordingSid || "",
      recordingSid: rawCallData.recording_sid || rawCallData.recordingSid || rawCallData.recordingSid || "",

      // Duration and cost
      duration:
        rawCallData.conversation_duration ||
        (rawCallData.started_at && rawCallData.ended_at ? (new Date(rawCallData.ended_at) - new Date(rawCallData.started_at)) / 1000 : rawCallData.duration || 0),
      totalCost: rawCallData.total_cost || 0,

      // Usage breakdown for analytics
      usageBreakdown: rawCallData.usage_breakdown,
      costBreakdown: rawCallData.cost_breakdown,

      // Raw transcripts array (if present) and parsed messages
      transcriptArray: Array.isArray(rawCallData.transcripts) ? rawCallData.transcripts : [],
      messages: Array.isArray(rawCallData.transcripts) && rawCallData.transcripts.length > 0
        ? rawCallData.transcripts.map((t) => ({ role: (t.speaker || "User").toLowerCase().includes("ai") || (t.speaker || "").toLowerCase().includes("assistant") ? "assistant" : "user", content: t.text || t.msg || t.text || "", timestamp: t.timestamp }))
        : parseTranscriptToMessages(rawCallData.transcript || ""),

      // Additional Gemini-specific data
      telephonyData: rawCallData.telephony_data,
      extractedData: rawCallData.extracted_data,
      contextDetails: rawCallData.context_details,
      errorMessage: rawCallData.error_message,
      sentiment: (() => {
        const primarySentiment = rawCallData.sentiment || rawCallData.sentiment_label;
        if (primarySentiment && primarySentiment !== "neutral") {
          return primarySentiment;
        }
        return contact?.sentiment || contactInfo.sentiment || primarySentiment || "neutral";
      })(),
      sentimentDescription: rawCallData.sentiment_description || rawCallData.sentimentDescription || contact?.sentimentDescription || contact?.sentiment_description || contactInfo.sentimentDescription || "",

      // Keep complete API payload for full details rendering
      rawData: rawCallData,
    }
  }

  const toDisplayValue = (value) => {
    if (value === null || value === undefined || value === "") return "N/A"
    if (typeof value === "object") {
      try {
        return JSON.stringify(value)
      } catch {
        return "[Unserializable Object]"
      }
    }
    return String(value)
  }

  // Parse transcript string into messages array for sentiment analysis
  const parseTranscriptToMessages = (transcriptString) => {
    if (!transcriptString) return []

    const messages = []
    const lines = transcriptString.split("\n").filter((line) => line.trim())

    lines.forEach((line, index) => {
      const trimmedLine = line.trim()
      if (trimmedLine.includes("assistant:")) {
        const content = trimmedLine.replace(/^assistant:\s*"?|"?$/g, "").trim()
        if (content) {
          messages.push({
            role: "assistant",
            message: content,
            secondsFromStart: index * 10, // Estimate timing
          })
        }
      } else if (trimmedLine.includes("user:")) {
        const content = trimmedLine.replace(/^user:\s*"?|"?$/g, "").trim()
        if (content) {
          messages.push({
            role: "user",
            message: content,
            secondsFromStart: index * 10, // Estimate timing
          })
        }
      }
    })

    return messages
  }

  // Generate AI-powered recommendation with minimized response
  const generateAIRecommendation = async () => {
    if (!callData?.transcript || !callData?.summary) return

    setLoadingRecommendation(true)
    try {
      const response = await fetch(`${backendUrl}/api/generate-recommendation`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "1",
        },
        body: JSON.stringify({
          transcript: callData.transcript,
          summary: callData.summary,
          sentiment: overallSentiment?.label || sentiment,
          callDuration: callData.duration || 0,
          minimized: true, // Flag to request minimized response
        }),
      })

      const result = await response.json()
      if (result.success) {
        setAiRecommendation(result.recommendation)
      } else {
        setAiRecommendation("Unable to generate recommendation at this time.")
      }
    } catch (error) {
      console.error("Error generating recommendation:", error)
      setAiRecommendation("Unable to generate recommendation due to network error.")
    } finally {
      setLoadingRecommendation(false)
    }
  }

  // Analyze sentiment progression throughout the call
  const analyzeSentimentProgression = () => {
    if (!callData?.messages) return

    const userMessages = callData.messages.filter((msg) => msg.role === "user" && getMessageText(msg).trim())
    const sentimentProgression = []
    let cumulativeSentiment = 0
    const sentimentCounts = { positive: 0, negative: 0, neutral: 0 }


    userMessages.forEach((message, index) => {
      const messageText = getMessageText(message)
      const sentiment = analyzeSentimentFromText(messageText)
      const timeFromStart = message.secondsFromStart || index * 30
      cumulativeSentiment += sentiment.score
      sentimentCounts[sentiment.label]++

      sentimentProgression.push({
        time: Math.round(timeFromStart),
        timeFormatted: formatTime(Math.round(timeFromStart)),
        sentiment: sentiment.score,
        sentimentLabel: sentiment.label,
        message: messageText.substring(0, 50) + (messageText.length > 50 ? "..." : ""),
        cumulativeSentiment: cumulativeSentiment / (index + 1),
        responseNumber: index + 1,
      })
    })

    setSentimentData(sentimentProgression)

    // Calculate overall sentiment
    const totalResponses = sentimentCounts.positive + sentimentCounts.negative + sentimentCounts.neutral
    const overallScore = totalResponses > 0 ? (sentimentCounts.positive - sentimentCounts.negative) / totalResponses : 0

    setOverallSentiment({
      score: overallScore,
      label: overallScore > 0.2 ? "positive" : overallScore < -0.2 ? "negative" : "neutral",
      distribution: sentimentCounts,
      totalResponses,
    })
  }

  // Simple sentiment analysis function
  const analyzeSentimentFromText = (text) => {
    const positiveWords = [
      "అవును",
      "బాగుంది",
      "మంచి",
      "సంతోషం",
      "ధన్యవాదాలు",
      "బెటర్",
      "good",
      "yes",
      "happy",
      "satisfied",
      "thank",
      "great",
      "excellent",
      "wonderful",
    ]
    const negativeWords = [
      "కోపం",
      "చెడు",
      "లేదు",
      "కాదు",
      "వెయిట్",
      "రూట్",
      "bad",
      "no",
      "angry",
      "rude",
      "wait",
      "problem",
      "terrible",
      "awful",
    ]

    const lowerText = text.toLowerCase()
    let score = 0

    positiveWords.forEach((word) => {
      if (lowerText.includes(word)) score += 1
    })

    negativeWords.forEach((word) => {
      if (lowerText.includes(word)) score -= 1
    })

    const normalizedScore = Math.max(-1, Math.min(1, score))
    let label = "neutral"
    if (normalizedScore > 0.3) label = "positive"
    else if (normalizedScore < -0.3) label = "negative"

    return { score: normalizedScore, label }
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
        return "#6b7280"
    }
  }

  const getSentimentDistributionData = () => {
    if (!overallSentiment) return []
    const totalResponses = overallSentiment.totalResponses || 0
    return [
      {
        name: "Positive",
        value: overallSentiment.distribution.positive,
        percentage: totalResponses > 0 ? Math.round((overallSentiment.distribution.positive / totalResponses) * 100) : 0,
        color: "#A7D477",
      },
      {
        name: "Negative",
        value: overallSentiment.distribution.negative,
        percentage: totalResponses > 0 ? Math.round((overallSentiment.distribution.negative / totalResponses) * 100) : 0,
        color: "#EF5A6F",
      },
      {
        name: "Neutral",
        value: overallSentiment.distribution.neutral,
        percentage: totalResponses > 0 ? Math.round((overallSentiment.distribution.neutral / totalResponses) * 100) : 0,
        color: "#F8ED8C",
      },
    ]
  }

  const getSentimentTrendData = () => {
    return sentimentData.map((item, index) => ({
      time: item.timeFormatted,
      sentiment: (item.sentiment * 100).toFixed(0), // Convert to percentage for better readability
      cumulative: (item.cumulativeSentiment * 100).toFixed(0),
      response: item.responseNumber,
      label: item.sentimentLabel,
    }))
  }

  // Calculate speaking time distribution from usage breakdown
  const calculateSpeakingTime = () => {
    if (!callData?.usageBreakdown) {
      // Fallback calculation based on messages
      let aiTime = 0
      let userTime = 0

      if (callData?.messages) {
        callData.messages.forEach((message) => {
          // Estimate duration based on message length
          const estimatedDuration = getMessageText(message).length * 0.1
          if (message.role === "assistant") {
            aiTime += estimatedDuration
          } else if (message.role === "user") {
            userTime += estimatedDuration
          }
        })
      }

      return { aiTime, userTime, totalTime: aiTime + userTime }
    }

    // Use actual duration data from Gemini Voice Agent
    const totalDuration = callData.duration || 0
    // Estimate AI vs User speaking time (this is an approximation)
    const aiTime = totalDuration * 0.6 // Assume AI speaks 60% of the time
    const userTime = totalDuration * 0.4 // User speaks 40% of the time

    return { aiTime, userTime, totalTime: totalDuration }
  }

  const getSpeakingTimeData = () => {
    const { aiTime, userTime } = calculateSpeakingTime()
    const total = aiTime + userTime
    return [
      {
        name: "AI Assistant",
        value: Math.round(aiTime),
        percentage: total > 0 ? Math.round((aiTime / total) * 100) : 0,
        color: "#60D0F4",
      },
      {
        name: "Customer",
        value: Math.round(userTime),
        percentage: total > 0 ? Math.round((userTime / total) * 100) : 0,
        color: "#A7D477",
      },
    ]
  }

  const formatTime = (seconds) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, "0")}`
  }

  const handlePlayPause = () => {
    setIsPlaying(!isPlaying)
  }

  const getRecordingSid = (data = callData) => data?.recordingSid || data?.recording_sid || ""

  const getRecordingPlaybackUrl = (data = callData) => {
    const recordingSid = getRecordingSid(data)
    if (recordingSid) {
      return `${backendUrl}/api/recording/media/${recordingSid}`
    }
    return data?.recordingUrl || ""
  }



  const getMessageText = (message) => message?.message || message?.content || message?.text || ""

  const fetchRecordingFromTwilio = async () => {
    if (!callData) return
    const recSid = getRecordingSid(callData)
    if (!recSid) {
      alert('No recording SID available for this call')
      return
    }

    try {
      const res = await fetch(`${backendUrl}/api/recording/fetch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify({ recording_sid: recSid, call_sid: callData.id || callData.call_sid }),
      })
      const body = await res.json()
      if (body.success) {
        const updated = { ...callData, recordingUrl: body.recording_url || body.recordingUrl || getRecordingPlaybackUrl(callData) }
        setCallData(updated)
        saveCachedCall(buildCallSnapshot(updated))
        alert('Recording URL fetched and updated')
      } else {
        alert('Failed to fetch recording: ' + (body.error || 'unknown'))
      }
    } catch (e) {
      console.error(e)
      alert('Error fetching recording')
    }
  }



  if (loading) {
    return (
      <div className="loading-container">
        <div className="loading-spinner"></div>
        <p>Loading call details...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="error-container">
        <h2>Error Loading Call Details</h2>
        <p>{error}</p>
        <button onClick={() => navigate(-1)} className="back-button">
          Go Back
        </button>
      </div>
    )
  }

  const calculateDuration = () => {
    if (callData?.duration) return callData.duration
    if (callData?.startedAt && callData?.endedAt) {
      const start = new Date(callData.startedAt + (callData.startedAt.includes('Z') ? '' : 'Z'))
      const end = new Date(callData.endedAt + (callData.endedAt.includes('Z') ? '' : 'Z'))
      if (!isNaN(start) && !isNaN(end)) {
        return Math.floor((end - start) / 1000)
      }
    }
    return 0
  }
  const callDuration = calculateDuration()
  const sentimentDistributionData = getSentimentDistributionData()
  const sentimentTrendData = getSentimentTrendData()
  const speakingTimeData = getSpeakingTimeData()
  const hasSentimentDistributionData = sentimentDistributionData.some((item) => item.value > 0)
  const hasSpeakingTimeData = speakingTimeData.some((item) => item.value > 0)
  const hasTrendData = sentimentTrendData.length > 0
  const detailRows = [
    ["Mongo ID", callData?.mongoId],
    ["Call SID", callData?.call_sid || callData?.id],
    ["Twilio Call SID", callData?.twilio_call_sid],
    ["Recording SID", callData?.recording_sid || callData?.recordingSid],
    ["Recording URL", getRecordingPlaybackUrl()],
    ["Customer Name", callData?.customer?.name],
    ["Customer Phone", callData?.phoneNumber?.phoneNumber],
    ["Status", callData?.status],
    ["Sentiment", callData?.sentiment],
    ["Sentiment Description", callData?.sentimentDescription],
    ["Duration (seconds)", callData?.duration],
    ["Total Cost", callData?.totalCost],
    ["Assistant Name", callData?.assistant?.name],
    ["Assistant ID", callData?.assistant?.id],
    ["Started At", callData?.startedAt],
    ["Ended At", callData?.endedAt],
    ["Created At", callData?.createdAt],
    ["Updated At", callData?.updatedAt],
    ["Transcript Entries", Array.isArray(callData?.transcriptArray) ? callData.transcriptArray.length : 0],
    ["Messages Count", Array.isArray(callData?.messages) ? callData.messages.length : 0],
    ["Error Message", callData?.errorMessage],
  ]

  return (
    <div className="call-detail-container">
      <div className="call-detail-card">
        <div className="header-section">
          <div className="call-info-header">
            <h2>📞 Call Information</h2>
          </div>
        </div>

        {/* Call Information Section */}
        <div className="call-info-section">
          <div className="call-info-cards">
            <div className="info-card">
              <div className="info-icon">👤</div>
              <div className="info-content">
                <span className="info-label">Customer Name</span>
                <span className="info-value">{callData?.customer?.name || contactInfo.contactName || "Unknown"}</span>
              </div>
            </div>

            <div className="info-card">
              <div className="info-icon">📱</div>
              <div className="info-content">
                <span className="info-label">Contact Number</span>
                <span className="info-value">{callData?.phoneNumber?.phoneNumber || phoneNumber || "Unknown"}</span>
              </div>
            </div>

            <div className="info-card">
              <div className="info-icon">🆔</div>
              <div className="info-content">
                <span className="info-label">Call ID</span>
                <span className="info-value">#{callId}</span>
              </div>
            </div>

            <div className="info-card">
              <div className="info-icon">😊</div>
              <div className="info-content">
                <span className="info-label">Sentiment</span>
                <span
                  className="info-value sentiment-value"
                  style={{ color: getSentimentColor(callData?.sentiment || overallSentiment?.label || sentiment) }}
                >
                  {callData?.sentiment || overallSentiment?.label || sentiment}
                </span>
              </div>
            </div>

            <div className="info-card">
              <div className="info-icon">📊</div>
              <div className="info-content">
                <span className="info-label">Status</span>
                <span className="info-value">{callData?.status || "Unknown"}</span>
              </div>
            </div>

            <div className="info-card">
              <div className="info-icon">⏱️</div>
              <div className="info-content">
                <span className="info-label">Call Duration</span>
                <span className="info-value">{formatTime(callDuration)}</span>
              </div>
            </div>

            <div className="info-card">
              <div className="info-icon">🕐</div>
              <div className="info-content">
                <span className="info-label">Started At</span>
                <span className="info-value">
                  {callData?.startedAt ? new Date(callData.startedAt + (callData.startedAt.includes('Z') ? '' : 'Z')).toLocaleString('en-GB', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true }).toUpperCase() : "Unknown"}
                </span>
              </div>
            </div>

            <div className="info-card">
              <div className="info-icon">🕑</div>
              <div className="info-content">
                <span className="info-label">Ended At</span>
                <span className="info-value">
                  {callData?.endedAt ? new Date(callData.endedAt + (callData.endedAt.includes('Z') ? '' : 'Z')).toLocaleString('en-GB', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true }).toUpperCase() : "Ongoing"}
                </span>
              </div>
            </div>

            {/* New Cost Information Card */}
            {callData?.totalCost && (
              <div className="info-card">
                <div className="info-icon">💰</div>
                <div className="info-content">
                  <span className="info-label">Total Cost</span>
                  <span className="info-value">${callData.totalCost.toFixed(2)}</span>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="appointment-booking-section">
          <h2>Book Appointment on Call</h2>
          <div className="appointment-form-grid">
            <div className="appointment-form-item">
              <label>Customer Name</label>
              <input
                type="text"
                value={customerDetailsForm.contactName}
                onChange={(e) => setCustomerDetailsForm((prev) => ({ ...prev, contactName: e.target.value }))}
                placeholder="Customer name"
              />
            </div>
            <div className="appointment-form-item">
              <label>Phone</label>
              <input
                type="text"
                value={customerDetailsForm.phoneNumber}
                onChange={(e) => setCustomerDetailsForm((prev) => ({ ...prev, phoneNumber: e.target.value }))}
                placeholder="Phone number"
              />
            </div>
            <div className="appointment-form-item">
              <label>Appointment Type</label>
              <select
                value={appointmentForm.appointmentType}
                onChange={(e) => setAppointmentForm((prev) => ({ ...prev, appointmentType: e.target.value }))}
              >
                <option value="site_visit">Site Visit</option>
                <option value="callback">Callback</option>
              </select>
            </div>
            <div className="appointment-form-item">
              <label>Preferred Date</label>
              <input
                type="date"
                value={appointmentForm.preferredDate}
                onChange={(e) => setAppointmentForm((prev) => ({ ...prev, preferredDate: e.target.value }))}
              />
            </div>
            <div className="appointment-form-item">
              <label>Preferred Time</label>
              <input
                type="time"
                value={appointmentForm.preferredTime}
                onChange={(e) => setAppointmentForm((prev) => ({ ...prev, preferredTime: e.target.value }))}
              />
            </div>
            <div className="appointment-form-item full-width">
              <label>Property / Interest</label>
              <input
                type="text"
                value={appointmentForm.propertyOfInterest}
                onChange={(e) => setAppointmentForm((prev) => ({ ...prev, propertyOfInterest: e.target.value }))}
                placeholder="Property name or interest"
              />
            </div>
            <div className="appointment-form-item full-width">
              <label>Notes</label>
              <textarea
                rows={3}
                value={appointmentForm.notes}
                onChange={(e) => setAppointmentForm((prev) => ({ ...prev, notes: e.target.value }))}
                placeholder="Add booking notes"
              />
            </div>
          </div>
          <button className="save-customer-btn" onClick={bookAppointment} disabled={bookingAppointment}>
            {bookingAppointment ? "Booking..." : "Book Appointment"}
          </button>
        </div>

        {/* All Details Section */}
        <div className="all-details-section">
          <h2>All Call Details</h2>

          <div className="customer-save-section">
            <h3>Save Customer Details</h3>
            <div className="customer-form-grid">
              <div className="customer-form-item">
                <label>Name</label>
                <input
                  type="text"
                  value={customerDetailsForm.contactName}
                  onChange={(e) => setCustomerDetailsForm((prev) => ({ ...prev, contactName: e.target.value }))}
                  placeholder="Customer name"
                />
              </div>
              <div className="customer-form-item">
                <label>Phone</label>
                <input
                  type="text"
                  value={customerDetailsForm.phoneNumber}
                  onChange={(e) => setCustomerDetailsForm((prev) => ({ ...prev, phoneNumber: e.target.value }))}
                  placeholder="Phone number"
                />
              </div>
              <div className="customer-form-item">
                <label>Interest</label>
                <input
                  type="text"
                  value={customerDetailsForm.interest}
                  onChange={(e) => setCustomerDetailsForm((prev) => ({ ...prev, interest: e.target.value }))}
                  placeholder="Property interest / requirement"
                />
              </div>
              <div className="customer-form-item">
                <label>Sentiment</label>
                <select
                  value={customerDetailsForm.sentiment}
                  onChange={(e) => setCustomerDetailsForm((prev) => ({ ...prev, sentiment: e.target.value }))}
                >
                  <option value="positive">Positive</option>
                  <option value="neutral">Neutral</option>
                  <option value="negative">Negative</option>
                </select>
              </div>
              <div className="customer-form-item full-width">
                <label>Sentiment Description</label>
                <input
                  type="text"
                  value={customerDetailsForm.sentimentDescription}
                  onChange={(e) =>
                    setCustomerDetailsForm((prev) => ({ ...prev, sentimentDescription: e.target.value }))
                  }
                  placeholder="Sentiment notes"
                />
              </div>
              <div className="customer-form-item full-width">
                <label>Notes</label>
                <textarea
                  value={customerDetailsForm.notes}
                  onChange={(e) => setCustomerDetailsForm((prev) => ({ ...prev, notes: e.target.value }))}
                  placeholder="Additional customer details"
                  rows={3}
                />
              </div>
            </div>

            <button className="save-customer-btn" onClick={saveCustomerDetails} disabled={savingCustomerDetails}>
              {savingCustomerDetails ? "Saving..." : "Save Customer Details"}
            </button>
          </div>

          <div className="details-grid">
            {detailRows.map(([label, value]) => (
              <div className="detail-row" key={label}>
                <span className="detail-label">{label}</span>
                <span className="detail-value">{toDisplayValue(value)}</span>
              </div>
            ))}
          </div>

          <div className="json-block-wrap">
            <h3>Telephony Data</h3>
            <pre className="json-block">{JSON.stringify(callData?.telephonyData || {}, null, 2)}</pre>
          </div>

          <div className="json-block-wrap">
            <h3>Context Details</h3>
            <pre className="json-block">{JSON.stringify(callData?.contextDetails || {}, null, 2)}</pre>
          </div>

          <div className="json-block-wrap">
            <h3>Extracted Data</h3>
            <pre className="json-block">{JSON.stringify(callData?.extractedData || {}, null, 2)}</pre>
          </div>

          <div className="json-block-wrap">
            <h3>Raw Call Document</h3>
            <pre className="json-block">{JSON.stringify(callData?.rawData || {}, null, 2)}</pre>
          </div>
        </div>

        {/* Sentiment Analytics Section */}
        <div className="analytics-section">
          <div className="analytics-header">
            <h2>😊 Sentiment Analysis</h2>
            <button className="analytics-toggle-btn" onClick={() => setShowAnalytics(!showAnalytics)}>
              {showAnalytics ? "Hide Analytics" : "Show Analytics"}
            </button>
          </div>

          {showAnalytics && (
            <div className="analytics-content">
              <div className="analytics-grid">
                {/* Sentiment Distribution */}
                <div className="analytics-chart">
                  <h4>📊 Sentiment Distribution</h4>
                  {hasSentimentDistributionData ? (
                    <ResponsiveContainer width="100%" height={300}>
                      <PieChart>
                        <Pie
                          data={sentimentDistributionData}
                          cx="50%"
                          cy="50%"
                          labelLine={true}
                          label={({ name, percentage, value }) => value > 0 ? `${name}: ${percentage}%` : null}
                          outerRadius={70}
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
                  ) : (
                    <div className="chart-empty-state">No sentiment data available yet for this call.</div>
                  )}
                  <div className="analytics-legend">
                    {sentimentDistributionData.map((item, index) => (
                      <div key={index} className="legend-item">
                        <div className="legend-color" style={{ backgroundColor: item.color }}></div>
                        <span>
                          {item.name}: {item.value} responses ({item.percentage}%)
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Speaking Time Distribution */}
                <div className="analytics-chart">
                  <h4>🕐 Speaking Time Distribution</h4>
                  {hasSpeakingTimeData ? (
                    <ResponsiveContainer width="100%" height={300}>
                      <PieChart>
                        <Pie
                          data={speakingTimeData}
                          cx="50%"
                          cy="50%"
                          labelLine={true}
                          label={({ name, percentage, value }) => value > 0 ? `${name}: ${percentage}%` : null}
                          outerRadius={70}
                          fill="#8884d8"
                          dataKey="value"
                        >
                          {speakingTimeData.map((entry, index) => (
                            <Cell key={`cell-${index}`} fill={entry.color} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(value) => [`${value}s`, "Duration"]} />
                      </PieChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="chart-empty-state">No speaking-time data available yet for this call.</div>
                  )}
                  <div className="analytics-legend">
                    {speakingTimeData.map((item, index) => (
                      <div key={index} className="legend-item">
                        <div className="legend-color" style={{ backgroundColor: item.color }}></div>
                        <span>
                          {item.name}: {item.value}s ({item.percentage}%)
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Sentiment Progression Over Time - CENTERED */}
                <div className="analytics-chart centered-chart">
                  <h4>📈 Sentiment Progression Throughout Call</h4>
                  {hasTrendData ? (
                    <ResponsiveContainer width="100%" height={350}>
                      <LineChart data={sentimentTrendData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="time" label={{ value: "Time in Call", position: "insideBottom", offset: -20 }} />
                        <YAxis
                          domain={[-100, 100]}
                          label={{
                            value: "Sentiment Score (%)",
                            angle: -90,
                            position: "insideLeft",
                            offset: 10,
                            style: { textAnchor: "middle" },
                          }}
                        />
                        <Tooltip
                          formatter={(value, name) => [
                            name === "sentiment"
                              ? `${value}% (${sentimentTrendData.find((d) => d.sentiment === value)?.label || "neutral"})`
                              : `${value}%`,
                            name === "sentiment" ? "Response Sentiment" : "Average Sentiment",
                          ]}
                          labelFormatter={(label) => `Time: ${label}`}
                        />
                        <Line
                          type="monotone"
                          dataKey="sentiment"
                          stroke="#A7D477"
                          strokeWidth={4}
                          dot={{ fill: "#A7D477", strokeWidth: 2, r: 6 }}
                          name="sentiment"
                        />
                        <Line
                          type="monotone"
                          dataKey="cumulative"
                          stroke="#60D0F4"
                          strokeWidth={2}
                          strokeDasharray="5 5"
                          dot={false}
                          name="cumulative"
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="chart-empty-state">No transcript messages were available to analyze sentiment over time.</div>
                  )}
                </div>
              </div>

              {/* Sentiment Insights */}
              <div className="sentiment-insights">
                <h4>🔍 Sentiment Insights</h4>
                <div className="insights-grid">
                  <div className="insight-card">
                    <h5>Overall Trend</h5>
                    <p>
                      {sentimentData.length === 0
                        ? "No transcript data was available, so sentiment could not be analyzed yet."
                        : overallSentiment?.score > 0.2
                          ? "Customer showed mostly positive sentiment throughout the call"
                          : overallSentiment?.score < -0.2
                            ? "Customer expressed negative sentiment, indicating dissatisfaction"
                            : "Customer maintained neutral sentiment during the interaction"}
                    </p>
                  </div>

                  <div className="insight-card">
                    <h5>Key Moments</h5>
                    <p>
                      {sentimentData.length > 0 &&
                        (() => {
                          const mostNegative = sentimentData.reduce((prev, current) =>
                            prev.sentiment < current.sentiment ? prev : current,
                          )
                          const mostPositive = sentimentData.reduce((prev, current) =>
                            prev.sentiment > current.sentiment ? prev : current,
                          )

                          if (mostNegative.sentiment < -0.3) {
                            return `Lowest sentiment at ${mostNegative.timeFormatted}: "${mostNegative.message}"`
                          } else if (mostPositive.sentiment > 0.3) {
                            return `Highest sentiment at ${mostPositive.timeFormatted}: "${mostPositive.message}"`
                          } else {
                            return "No significant sentiment peaks detected during the call"
                          }
                        })()}
                      {sentimentData.length === 0 && "No key moments detected because there is no transcript data yet."}
                    </p>
                  </div>

                  <div className="insight-card">
                    <h5>AI Recommendation</h5>
                    {loadingRecommendation ? (
                      <p>Generating personalized recommendation...</p>
                    ) : (
                      <p>{aiRecommendation || "No recommendation available"}</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Summary Section with Translation */}
        <div className="charts-section">
          <div className="summary-container full-width">
            <div className="summary-header">
              <h3>Call Summary</h3>
            </div>

            <p>
              {callData?.summary || callData?.sentimentDescription || contactInfo.sentimentDescription || "No summary available for this call."}
            </p>
          </div>
        </div>

        {/* Call Record Details */}
        <div className="call-record-section">
          <h2>Call Record Details</h2>
          <div className="recording-header">
            <span>Call Recording</span>
            <span className="date-time">
              {callData?.startedAt ? new Date(callData.startedAt + (callData.startedAt.includes('Z') ? '' : 'Z')).toLocaleString('en-GB', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true }).toUpperCase() : "Unknown Date"}
            </span>
          </div>

          <div className="recording-meta">
            {(callData?.recording_sid || callData?.recordingSid) && (
              <div className="recording-id">Recording SID: {callData.recording_sid || callData.recordingSid}</div>
            )}
            {(callData?.recording_sid || callData?.recordingSid) && (
              <button className="fetch-recording-btn" onClick={fetchRecordingFromTwilio}>Refresh Recording Link</button>
            )}
            {getRecordingPlaybackUrl() && (
              <a className="recording-link" href={getRecordingPlaybackUrl()} target="_blank" rel="noreferrer">Play Recording</a>
            )}
          </div>

          {/* Audio Player */}
          {getRecordingPlaybackUrl() ? (
            <div className="audio-player">
              <audio controls style={{ width: "100%" }}>
                <source src={getRecordingPlaybackUrl()} type="audio/wav" />
                Your browser does not support the audio element.
              </audio>
            </div>
          ) : (
            <div className="audio-player">
              <button className="play-button" onClick={handlePlayPause}>
                {isPlaying ? "⏸" : "▶"}
              </button>
              <div className="progress-container">
                <div className="progress-bar">
                  <div className="progress-fill" style={{ width: `${(currentTime / callDuration) * 100}%` }}></div>
                </div>
              </div>
              <span className="time-display">
                {formatTime(currentTime)} / {formatTime(callDuration)}
              </span>
            </div>
          )}


          {/* Status and Recommendation */}
          <div className="status-section">
            <div className="status-item">
              <span className="label">Status:</span>
              <span className="value">
                {callData?.status === "completed" ? "Call Completed" : callData?.status || "Unknown"}
              </span>
            </div>
            <div className="status-item">
              <span className="label">Recommended Action:</span>
              <span className="value">
                {loadingRecommendation
                  ? "Generating recommendation..."
                  : aiRecommendation || "No specific recommendation available"}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default CallDetail
