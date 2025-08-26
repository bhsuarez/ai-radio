import React, { useState, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';
import io from 'socket.io-client';
import { motion, AnimatePresence } from 'framer-motion';
import './App.css';

interface Track {
  title: string;
  artist: string;
  album?: string;
  type?: string;
  timestamp?: number;
  artwork_url?: string;
  filename?: string;
  duration?: number;
  track_started_at?: number;
  // Additional metadata fields from backend
  date?: string;
  genre?: string;
  year?: string;
  tracknumber?: string;
  started_at?: string;
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
  const [trackStartTime, setTrackStartTime] = useState<number>(0);
  const [currentTime, setCurrentTime] = useState<number>(Date.now());

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
  const fetchCurrentTrack = useCallback(async () => {
    try {
      console.log('Fetching current track from:', `${API_BASE}/api/now`);
      const response = await axios.get(`${API_BASE}/api/now`);
      console.log('Current track response:', response.data);
      const newTrack = response.data;
      
      setCurrentTrack(prevTrack => {
        // Check if this is a different track than what we currently have
        const isNewTrack = !prevTrack || 
          prevTrack.title !== newTrack.title || 
          prevTrack.artist !== newTrack.artist;
        
        // Always use backend timestamp when available (prevents refresh timer resets)
        if (newTrack.track_started_at) {
          const startTime = newTrack.track_started_at * 1000;
          setTrackStartTime(startTime);
          console.log(`🔄 Using backend start time: ${newTrack.title} - Started at: ${new Date(startTime)} (${isNewTrack ? 'NEW' : 'REFRESH'})`);
        } else if (newTrack.title) {
          // Only set current time if we don't have a backend timestamp
          setTrackStartTime(Date.now());
          console.log(`🎵 No backend timestamp for: ${newTrack.title} - Using current time`);
        }
        
        return newTrack;
      });
    } catch (error) {
      console.error('Failed to fetch current track:', error);
    }
  }, [API_BASE]);

  // Fetch history
  const fetchHistory = useCallback(async () => {
    try {
      console.log('Fetching history from:', `${API_BASE}/api/history`);
      const response = await axios.get(`${API_BASE}/api/history`);
      const historyData = response.data.history || response.data; // Support both new and old API format
      console.log('History response length:', historyData.length);
      console.log('First few history items:', historyData.slice(0, 3));
      setHistory(historyData.slice(0, 20)); // Show last 20 items
    } catch (error) {
      console.error('Failed to fetch history:', error);
    }
  }, [API_BASE]);

  // Fetch next tracks
  const fetchNextTracks = useCallback(async (refresh = false) => {
    try {
      const url = refresh ? `${API_BASE}/api/next?refresh=true` : `${API_BASE}/api/next`;
      console.log('Fetching next tracks from:', url);
      const response = await axios.get(url);
      console.log('Next tracks response:', response.data);
      setNextTracks(response.data || []);
    } catch (error) {
      console.error('Failed to fetch next tracks:', error);
    }
  }, [API_BASE]);

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
      
      setCurrentTrack(prevTrack => {
        // Check if this is actually a new track
        const isNewTrack = !prevTrack || 
          prevTrack.title !== trackInfo.title || 
          prevTrack.artist !== trackInfo.artist;
        
        // Always use backend timestamp when available (prevents refresh timer resets)
        if (trackInfo.track_started_at) {
          const startTime = trackInfo.track_started_at * 1000;
          setTrackStartTime(startTime);
          if (isNewTrack) {
            console.log('🎵 New track detected via socket:', trackInfo.title, 'Started at:', new Date(startTime));
          } else {
            console.log('🔄 Using backend start time via socket:', trackInfo.title, 'Started at:', new Date(startTime));
          }
        } else if (isNewTrack) {
          // Fall back to current time only for new tracks without backend timestamp
          setTrackStartTime(Date.now());
          console.log('🎵 New track detected via socket (no backend timestamp):', trackInfo.title);
        }
        
        return trackInfo;
      });
      
      fetchHistory(); // Refresh history when track changes
      fetchNextTracks(true); // Refresh upcoming tracks from Liquidsoap
    });

    socket.on('history_update', (historyItem: HistoryItem) => {
      console.log('📜 History update received:', historyItem);
      setHistory(prevHistory => {
        // Add new item to the beginning of history (most recent first)
        const newHistory = [historyItem, ...prevHistory];
        // Keep only the last 20 items to prevent memory bloat
        return newHistory.slice(0, 20);
      });
    });

    // Clean up on unmount
    return () => {
      console.log('Closing WebSocket connection');
      socket.close();
    };
  }, [API_BASE, fetchHistory, fetchNextTracks]);

  // Initial data fetch
  useEffect(() => {
    const loadInitialData = async () => {
      setLoading(true);
      await Promise.all([fetchCurrentTrack(), fetchHistory(), fetchNextTracks(true)]); // Refresh next tracks on load
      setLoading(false);
    };
    loadInitialData();
  }, [fetchCurrentTrack, fetchHistory, fetchNextTracks]);

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
  }, [isConnected, fetchCurrentTrack, fetchHistory, fetchNextTracks]);

  // Timer for progress tracking
  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(Date.now());
    }, 1000); // Update every second

    return () => clearInterval(timer);
  }, []);

  // Calculate track progress - memoized to prevent re-calculations
  const trackProgress = useMemo((): { elapsed: number; remaining: number; progress: number } => {
    if (!currentTrack || !trackStartTime) {
      return { elapsed: 0, remaining: 0, progress: 0 };
    }

    const elapsed = Math.floor((currentTime - trackStartTime) / 1000);
    
    // Estimate duration based on track type and title length
    let estimatedDuration = 180; // Default 3 minutes
    
    if (currentTrack.title && currentTrack.artist) {
      // Longer titles often indicate longer songs
      const titleLength = currentTrack.title.length;
      const artistLength = currentTrack.artist.length;
      
      if (titleLength > 50 || artistLength > 20) {
        estimatedDuration = 300; // 5 minutes for long titles
      } else if (titleLength > 30) {
        estimatedDuration = 240; // 4 minutes for medium titles
      } else if (titleLength < 15) {
        estimatedDuration = 150; // 2.5 minutes for short titles
      }
      
      // Adjust for likely genres based on artist
      const artistLower = currentTrack.artist.toLowerCase();
      if (artistLower.includes('dj') || artistLower.includes('electronic') || artistLower.includes('mix')) {
        estimatedDuration = Math.max(estimatedDuration, 300); // Electronic tracks often longer
      }
    }
    
    // For DJ intros, use shorter duration
    if (currentTrack.artist === 'AI DJ' || currentTrack.title === 'DJ Intro') {
      estimatedDuration = 15; // DJ intros are typically short
    }

    const duration = currentTrack.duration || estimatedDuration;
    const remaining = Math.max(0, duration - elapsed);
    const progress = Math.min(100, (elapsed / duration) * 100);

    return { elapsed, remaining, progress };
  }, [currentTrack, trackStartTime, currentTime]);

  const formatTime = (timestamp: number) => {
    return new Date(timestamp).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
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
          <AnimatePresence mode="wait">
            {currentTrack ? (
              <motion.div 
                key={`${currentTrack.title}-${currentTrack.artist}-${currentTrack.timestamp}`}
                className="track-info"
                initial={{ opacity: 0, scale: 0.95, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95, y: -20 }}
                transition={{ 
                  duration: 0.5,
                  ease: [0.25, 0.1, 0.25, 1.0]
                }}
              >
                <motion.div 
                  className="album-art"
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.1, duration: 0.6 }}
                >
                  <img 
                    src={getAlbumArtUrl(currentTrack)}
                    alt={`${currentTrack.album || 'Album'} by ${currentTrack.artist}`}
                    className="cover-image"
                    onError={(e) => {
                      // Fallback to default cover on error
                      (e.target as HTMLImageElement).src = `${API_BASE}/static/station-cover.jpg`;
                    }}
                  />
                </motion.div>
                <motion.div 
                  className="track-details"
                  initial={{ opacity: 0, x: 30 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.2, duration: 0.5 }}
                >
                  <h3 className="title">{currentTrack.title}</h3>
                  <p className="artist">{currentTrack.artist}</p>
                  {currentTrack.album && <p className="album">{currentTrack.album}</p>}
                  
                  {/* Progress Bar */}
                  <motion.div 
                    className="progress-container"
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ delay: 0.4, duration: 0.3 }}
                  >
                    <div className="progress-bar">
                      <motion.div 
                        className="progress-fill"
                        initial={{ width: 0 }}
                        animate={{ width: `${trackProgress.progress}%` }}
                        transition={{ duration: 1, ease: "easeOut" }}
                      />
                    </div>
                    <div className="progress-times">
                      <span className="elapsed">{formatDuration(trackProgress.elapsed)}</span>
                      <span className="remaining">
                        {trackProgress.remaining > 0 
                          ? `${formatDuration(trackProgress.remaining)} remaining`
                          : 'Next track coming up...'
                        }
                      </span>
                    </div>
                  </motion.div>
                </motion.div>
                <motion.div 
                  className="controls"
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.3, duration: 0.4 }}
                >
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
                </motion.div>
              </motion.div>
            ) : (
              <motion.p 
                key="no-track"
                className="no-track"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
              >
                No track information available
              </motion.p>
            )}
          </AnimatePresence>
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
              {history.map((item, index) => (
                item.type === 'dj' ? (
                  <div 
                    key={`${item.time}-${index}`}
                    className="history-item dj-item"
                  >
                    <div className="dj-icon">🎙️</div>
                    <div className="track-info">
                      <span className="dj-text">{item.title || item.text || 'DJ Commentary'}</span>
                      <span className="dj-label">AI DJ</span>
                      {item.text && (
                        <div className="dj-transcript">
                          <span className="transcript-label">Transcript:</span>
                          <p className="transcript-text">{item.text}</p>
                        </div>
                      )}
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
                ) : (
                  <div 
                    key={`${item.time}-${index}`}
                    className="history-item"
                  >
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
                )
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
