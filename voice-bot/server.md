steps to run the server :


Terminal 1: Backend (FastAPI Server)

cd voice-bot\backend
.\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 5000



Terminal 2: Frontend (React App)

cd voice-bot\frontend
npm start



Terminal 3: Ngrok Tunnel (External Access)
This is required for Twilio to reach your local backend.

ngrok http 5000