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
  artwork_url?: string;
  filename?: string;
}

interface HistoryItem {
  title: string;
  artist: string;
  time: number;
  type: string;
  artwork_url?: string;
  filename?: string;
  album?: string;
  audio_url?: string;
  text?: string;
}

function App() {
  const [currentTrack, setCurrentTrack] = useState<Track | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [nextTracks, setNextTracks] = useState<Track[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [loading, setLoading] = useState(true);

  const API_BASE = `${window.location.protocol}//${window.location.hostname}:5055`;
  
  // Get album art URL with fallback
  const getAlbumArtUrl = (track: Track): string => {
    // If track has artwork_url, use it
    if (track.artwork_url) {
      return track.artwork_url.startsWith('http') ? track.artwork_url : `${API_BASE}${track.artwork_url}`;
    }
    
    // Try file-based cover if filename exists
    if (track.filename) {
      return `${API_BASE}/api/cover?file=${encodeURIComponent(track.filename)}`;
    }
    
    // Try online cover if artist/album exist - this is the main path for tracks without filenames
    if (track.artist && track.album) {
      return `${API_BASE}/api/cover/online?artist=${encodeURIComponent(track.artist)}&album=${encodeURIComponent(track.album)}`;
    }
    
    // If only artist available, try with artist name as album
    if (track.artist) {
      return `${API_BASE}/api/cover/online?artist=${encodeURIComponent(track.artist)}&album=${encodeURIComponent(track.artist)}`;
    }
    
    // Fallback to default station cover
    return `${API_BASE}/static/station-cover.jpg`;
  };
  
  // Fetch current track
  const fetchCurrentTrack = async () => {
    try {
      console.log('Fetching current track from:', `${API_BASE}/api/now`);
      const response = await axios.get(`${API_BASE}/api/now`);
      console.log('Current track response:', response.data);
      setCurrentTrack(response.data);
    } catch (error) {
      console.error('Failed to fetch current track:', error);
    }
  };

  // Fetch history
  const fetchHistory = async () => {
    try {
      console.log('Fetching history from:', `${API_BASE}/api/history`);
      const response = await axios.get(`${API_BASE}/api/history`);
      console.log('History response length:', response.data.length);
      console.log('First few history items:', response.data.slice(0, 3));
      setHistory(response.data.slice(0, 20)); // Show last 20 items
    } catch (error) {
      console.error('Failed to fetch history:', error);
    }
  };

  // Fetch next tracks
  const fetchNextTracks = async () => {
    try {
      console.log('Fetching next tracks from:', `${API_BASE}/api/next`);
      const response = await axios.get(`${API_BASE}/api/next`);
      console.log('Next tracks response:', response.data);
      setNextTracks(response.data || []);
    } catch (error) {
      console.error('Failed to fetch next tracks:', error);
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
    console.log('Initializing WebSocket connection to:', API_BASE);
    const socket = io(API_BASE, {
      transports: ['websocket', 'polling'],
      timeout: 20000,
      forceNew: true
    });
    
    socket.on('connect', () => {
      console.log('✅ Connected to WebSocket');
      setIsConnected(true);
    });

    socket.on('disconnect', (reason) => {
      console.log('❌ Disconnected from WebSocket:', reason);
      setIsConnected(false);
    });

    socket.on('connect_error', (error) => {
      console.error('WebSocket connection error:', error);
      setIsConnected(false);
    });

    socket.on('track_update', (trackInfo: Track) => {
      console.log('🎵 Track update received:', trackInfo);
      setCurrentTrack(trackInfo);
      fetchHistory(); // Refresh history when track changes
      fetchNextTracks(); // Refresh upcoming tracks
    });

    socket.on('history_update', (historyItem: HistoryItem) => {
      console.log('📜 History update received:', historyItem);
      setHistory(prevHistory => {
        // Add new item to the beginning of history (most recent first)
        const newHistory = [historyItem, ...prevHistory];
        // Keep only the last 50 items to prevent unlimited growth
        return newHistory.slice(0, 50);
      });
    });

    // Clean up on unmount
    return () => {
      console.log('Closing WebSocket connection');
      socket.close();
    };
  }, []);

  // Initial data fetch
  useEffect(() => {
    const loadInitialData = async () => {
      setLoading(true);
      await Promise.all([fetchCurrentTrack(), fetchHistory(), fetchNextTracks()]);
      setLoading(false);
    };
    loadInitialData();
  }, []);

  // Fallback polling when WebSocket is disconnected
  useEffect(() => {
    if (!isConnected) {
      const fallbackInterval = setInterval(() => {
        console.log('🔄 Fallback polling (WebSocket disconnected)');
        fetchCurrentTrack();
        fetchHistory();
        fetchNextTracks();
      }, 10000); // Poll every 10 seconds when disconnected

      return () => clearInterval(fallbackInterval);
    }
  }, [isConnected]);

  const formatTime = (timestamp: number) => {
    return new Date(timestamp).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  // Debug logging
  console.log('App render - currentTrack:', currentTrack);
  console.log('App render - history length:', history.length);
  console.log('App render - nextTracks length:', nextTracks.length);
  console.log('App render - loading:', loading);

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
              <div className="album-art">
                <img 
                  src={getAlbumArtUrl(currentTrack)}
                  alt={`${currentTrack.album || 'Album'} by ${currentTrack.artist}`}
                  className="cover-image"
                  onError={(e) => {
                    // Fallback to default cover on error
                    (e.target as HTMLImageElement).src = `${API_BASE}/static/station-cover.jpg`;
                  }}
                />
              </div>
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
                  href={`http://${window.location.hostname}:8000/stream.mp3`}
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

        {/* Upcoming Section */}
        <section className="upcoming">
          <h2>🔜 Coming Up</h2>
          {nextTracks.length > 0 ? (
            <div className="upcoming-list">
              {nextTracks.slice(0, 5).map((track, index) => (
                <div key={`${track.title}-${track.artist}-${index}`} className="upcoming-item">
                  <img 
                    src={getAlbumArtUrl(track)}
                    alt={`${track.title} by ${track.artist}`}
                    className="upcoming-cover"
                    onError={(e) => {
                      (e.target as HTMLImageElement).src = `${API_BASE}/static/station-cover.jpg`;
                    }}
                  />
                  <div className="track-info">
                    <span className="title">{track.title}</span>
                    <span className="artist">{track.artist}</span>
                    {track.album && <span className="album">{track.album}</span>}
                  </div>
                  <span className="queue-position">#{index + 1}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="no-upcoming">No upcoming tracks available</p>
          )}
        </section>

        {/* History Section */}
        <section className="history">
          <h2>📜 Recently Played</h2>
          {history.length > 0 ? (
            <div className="history-list">
              {history.map((item, index) => {
                if (item.type === 'dj') {
                  return (
                    <div key={`${item.time}-${index}`} className="history-item dj-item">
                      <div className="dj-icon">🎙️</div>
                      <div className="track-info">
                        <span className="dj-text">{item.text || 'DJ Commentary'}</span>
                        <span className="dj-label">AI DJ</span>
                        {item.audio_url && (
                          <audio 
                            controls 
                            preload="none"
                            className="dj-audio-player"
                            src={item.audio_url.startsWith('http') ? item.audio_url : `${API_BASE}${item.audio_url}`}
                          >
                            Your browser does not support the audio element.
                          </audio>
                        )}
                      </div>
                      <span className="time">{formatTime(item.time)}</span>
                    </div>
                  );
                }
                return (
                  <div key={`${item.time}-${index}`} className="history-item">
                    <img 
                      src={getAlbumArtUrl(item as Track)}
                      alt={`${item.title} by ${item.artist}`}
                      className="history-cover"
                      onError={(e) => {
                        (e.target as HTMLImageElement).src = `${API_BASE}/static/station-cover.jpg`;
                      }}
                    />
                    <div className="track-info">
                      <span className="title">{item.title}</span>
                      <span className="artist">{item.artist}</span>
                      {item.album && <span className="album">{item.album}</span>}
                    </div>
                    <span className="time">{formatTime(item.time)}</span>
                  </div>
                );
              })}
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
