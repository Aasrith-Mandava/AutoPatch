#!/bin/bash
# AutoPatch — Single command to start everything
# Usage: ./start.sh

echo "⚡ Starting AutoPatch..."
echo ""

# Start FastAPI backend in background
echo "🔧 Starting API backend (port 8000)..."
cd "$(dirname "$0")"
python api.py &
BACKEND_PID=$!

# Wait a moment for backend to boot
sleep 2

# Start Vite React dev server
echo "🎨 Starting React UI (port 5173)..."
cd ui
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✅ AutoPatch is running!"
echo "   → UI:      http://localhost:5173"
echo "   → API:     http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop everything."

# Trap Ctrl+C to kill both processes
trap "echo ''; echo '🛑 Shutting down...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" SIGINT SIGTERM

# Wait for either process to exit
wait
