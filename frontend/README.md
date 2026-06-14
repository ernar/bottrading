# MT4 Trading Bot Dashboard

React frontend dashboard for the MT4 Ollama Trading Bot.

## Requirements

- Node.js 18+ and npm
- Python backend running on `http://localhost:5000`

## Installation

```bash
# Install dependencies
npm install

# Create .env file (optional, defaults to localhost:5000)
cp .env.example .env
```

## Running the Dashboard

### Development Mode

```bash
npm run dev
```

The dashboard will be available at `http://localhost:3000`

### Production Build

```bash
npm run build
npm run preview
```

## Project Structure

```
frontend/
├── src/
│   ├── pages/         # Main page components (Signals, Positions, History)
│   ├── components/    # Reusable UI components
│   ├── hooks/         # Custom React hooks (useWebSocket, useApi)
│   ├── types/         # TypeScript type definitions
│   ├── App.tsx        # Main app component
│   ├── main.tsx       # Entry point
│   └── index.css      # Global styles
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.js
└── index.html
```

## Features

- **Real-time Signals**: View latest AI trading signals with confidence levels
- **Position Monitoring**: Track open positions with live P&L updates
- **Trade History**: View all closed trades and performance metrics
- **Account Overview**: Monitor balance, equity, margin, and leverage
- **WebSocket Updates**: Real-time data streaming from the bot
- **Responsive Design**: Works on desktop and tablet screens

## Styling

This project uses Tailwind CSS for styling. All colors and themes are defined in `tailwind.config.js`.

## Troubleshooting

### Dashboard won't connect to API
- Ensure Flask server is running on port 5000
- Check that CORS is enabled in `api_server.py`
- Verify your `.env` file has the correct `VITE_API_URL`

### WebSocket connection fails
- Check browser console for error messages
- Verify Flask server is running and accessible
- Ensure firewall allows localhost connections

## Tech Stack

- **React 18** - UI framework
- **TypeScript** - Type safety
- **Vite** - Build tool and dev server
- **Tailwind CSS** - Styling
- **React Router** - Navigation
- **Socket.io Client** - WebSocket communication
- **Axios** - HTTP client
- **TanStack Table** - Table component (optional, currently using native tables)
