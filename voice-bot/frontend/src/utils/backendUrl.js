const DEFAULT_BACKEND_URL = "http://localhost:5000"

export function getBackendUrl() {
  const rawUrl = process.env.REACT_APP_BACKEND_URL || process.env.BACKEND_URL || DEFAULT_BACKEND_URL
  const trimmedUrl = rawUrl.replace(/\/$/, "")

  if (/^https?:\/\//i.test(trimmedUrl)) {
    return trimmedUrl
  }

  return `https://${trimmedUrl}`
}