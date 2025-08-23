import React, { useState, useEffect } from 'react';
import axios from 'axios';
import io from 'socket.io-client';
import './App.css';

interface Track {
  title: string;
  artist: string;
  album?: string;
  type?: string;
  timestamp?: number;
}

interface HistoryItem {
  title: string;
  artist: string;
  time: number;
  type: string;
}

function App() {
  const [currentTrack, setCurrentTrack] = useState<Track | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [loading, setLoading] = useState(true);

  const API_BASE = 'http://192.168.1.146:5055';
  
  // Fetch current track
  const fetchCurrentTrack = async () => {
    try {
      const response = await axios.get(`${API_BASE}/api/now`);
      setCurrentTrack(response.data);
    } catch (error) {
      console.error('Failed to fetch current track:', error);
    }
  };

  // Fetch history
  const fetchHistory = async () => {
    try {
      const response = await axios.get(`${API_BASE}/api/history`);
      setHistory(response.data.slice(0, 10)); // Show last 10 tracks
    } catch (error) {
      console.error('Failed to fetch history:', error);
    }
  };

  // Skip track
  const skipTrack = async () => {
    try {
      await axios.post(`${API_BASE}/api/skip`, {});
      fetchCurrentTrack(); // Refresh after skip
    } catch (error) {
      console.error('Failed to skip track:', error);
    }
  };

  // Initialize WebSocket connection
  useEffect(() => {
    const socket = io(API_BASE);
    
    socket.on('connect', () => {
      console.log('Connected to WebSocket');
      setIsConnected(true);
    });

    socket.on('disconnect', () => {
      console.log('Disconnected from WebSocket');
      setIsConnected(false);
    });

    socket.on('track_update', (trackInfo: Track) => {
      console.log('Track update received:', trackInfo);
      setCurrentTrack(trackInfo);
      fetchHistory(); // Refresh history when track changes
    });

    return () => {
      socket.close();
    };
  }, [API_BASE]);

  // Initial data fetch
  useEffect(() => {
    const loadInitialData = async () => {
      setLoading(true);
      await Promise.all([fetchCurrentTrack(), fetchHistory()]);
      setLoading(false);
    };
    loadInitialData();
  }, []);

  const formatTime = (timestamp: number) => {
    return new Date(timestamp).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  if (loading) {
    return (
      <div className="app">
        <div className="loading">
          <h2>🎧 AI Radio</h2>
          <p>Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <h1>🎧 AI Radio</h1>
        <div className="status">
          <span className={`connection ${isConnected ? 'connected' : 'disconnected'}`}>
            {isConnected ? '🟢 Live' : '🔴 Disconnected'}
          </span>
        </div>
      </header>

      <main className="main">
        {/* Now Playing Section */}
        <section className="now-playing">
          <h2>📻 Now Playing</h2>
          {currentTrack ? (
            <div className="track-info">
              <div className="track-details">
                <h3 className="title">{currentTrack.title}</h3>
                <p className="artist">{currentTrack.artist}</p>
                {currentTrack.album && <p className="album">{currentTrack.album}</p>}
              </div>
              <div className="controls">
                <button 
                  className="skip-button"
                  onClick={skipTrack}
                  disabled={!isConnected}
                >
                  ⏭️ Skip Track
                </button>
                <a 
                  href="http://192.168.1.146:8000/stream.mp3" 
                  target="_blank"
                  rel="noopener noreferrer"
                  className="listen-button"
                >
                  🎧 Listen Live
                </a>
              </div>
            </div>
          ) : (
            <p className="no-track">No track information available</p>
          )}
        </section>

        {/* History Section */}
        <section className="history">
          <h2>📜 Recently Played</h2>
          {history.length > 0 ? (
            <div className="history-list">
              {history.map((item, index) => (
                <div key={`${item.time}-${index}`} className="history-item">
                  <div className="track-info">
                    <span className="title">{item.title}</span>
                    <span className="artist">{item.artist}</span>
                  </div>
                  <span className="time">{formatTime(item.time)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="no-history">No history available</p>
          )}
        </section>
      </main>
    </div>
  );
}

export default App;
