import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { motion } from 'framer-motion';
import './SpeakerModal.css';

interface Speaker {
  name: string;
  display_name: string;
  sample_url: string;
}

interface SpeakerModalProps {
  isOpen: boolean;
  onClose: () => void;
  apiBase: string;
}

const SpeakerModal: React.FC<SpeakerModalProps> = ({ isOpen, onClose, apiBase }) => {
  const [speakers, setSpeakers] = useState<Speaker[]>([]);
  const [currentSpeaker, setCurrentSpeaker] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [playingPreview, setPlayingPreview] = useState<string | null>(null);
  const [selectedSpeaker, setSelectedSpeaker] = useState<string>('');

  // Fetch speakers when modal opens
  useEffect(() => {
    if (isOpen) {
      fetchSpeakers();
    }
  }, [isOpen]);

  const fetchSpeakers = async () => {
    setLoading(true);
    try {
      const response = await axios.get(`${apiBase}/api/tts/speakers`);
      setSpeakers(response.data.speakers || []);
      setCurrentSpeaker(response.data.current_speaker || '');
      setSelectedSpeaker(response.data.current_speaker || '');
    } catch (error) {
      console.error('Failed to fetch speakers:', error);
    } finally {
      setLoading(false);
    }
  };

  const selectSpeaker = async (speakerName: string) => {
    setSaving(true);
    try {
      await axios.post(`${apiBase}/api/tts/speakers/current`, {
        speaker: speakerName
      });
      setCurrentSpeaker(speakerName);
      setSelectedSpeaker(speakerName);
      
      // Show success feedback
      setTimeout(() => {
        onClose();
      }, 1000);
    } catch (error) {
      console.error('Failed to set speaker:', error);
      alert('Failed to change speaker. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const playPreview = async (speakerName: string) => {
    setPlayingPreview(speakerName);
    
    try {
      // Generate a preview with custom text
      await axios.post(`${apiBase}/api/tts/speakers/${speakerName}/preview`, {
        text: `Hello! This is ${speakerName.replace('_', ' ')}, your AI Radio DJ. Thanks for listening!`
      });
      
      // Wait a moment for generation, then stop the preview indicator
      setTimeout(() => {
        setPlayingPreview(null);
      }, 3000);
    } catch (error) {
      console.error('Failed to generate preview:', error);
      setPlayingPreview(null);
    }
  };

  const playSample = (sampleUrl: string) => {
    const audio = new Audio(`${apiBase}${sampleUrl}`);
    audio.play().catch(error => {
      console.error('Failed to play sample:', error);
    });
  };

  if (!isOpen) return null;

  return (
    <motion.div 
      className="speaker-modal-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onClose}
    >
      <motion.div 
        className="speaker-modal"
        initial={{ scale: 0.8, opacity: 0, y: 50 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.8, opacity: 0, y: 50 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2>🎙️ Choose DJ Voice</h2>
          <button className="close-button" onClick={onClose}>×</button>
        </div>

        <div className="modal-content">
          {loading ? (
            <div className="loading-state">
              <div className="spinner"></div>
              <p>Loading available voices...</p>
            </div>
          ) : (
            <>
              <div className="current-speaker">
                <p>Current DJ Voice: <strong>{currentSpeaker.replace('_', ' ')}</strong></p>
              </div>

              <div className="speakers-grid">
                {speakers.map((speaker) => (
                  <motion.div 
                    key={speaker.name}
                    className={`speaker-card ${selectedSpeaker === speaker.name ? 'selected' : ''} ${currentSpeaker === speaker.name ? 'current' : ''}`}
                    whileHover={{ scale: 1.02 }}
                    whileTap={{ scale: 0.98 }}
                    layout
                  >
                    <div className="speaker-info">
                      <h3>{speaker.display_name}</h3>
                      {currentSpeaker === speaker.name && (
                        <span className="current-badge">Current</span>
                      )}
                    </div>

                    <div className="speaker-actions">
                      <button 
                        className="sample-button"
                        onClick={() => playSample(speaker.sample_url)}
                        disabled={playingPreview !== null}
                      >
                        🔊 Sample
                      </button>

                      <button 
                        className="preview-button"
                        onClick={() => playPreview(speaker.name)}
                        disabled={playingPreview !== null}
                      >
                        {playingPreview === speaker.name ? (
                          <>
                            <div className="mini-spinner"></div>
                            Generating...
                          </>
                        ) : (
                          '🎤 Preview'
                        )}
                      </button>

                      <button 
                        className={`select-button ${currentSpeaker === speaker.name ? 'current' : ''}`}
                        onClick={() => selectSpeaker(speaker.name)}
                        disabled={saving || currentSpeaker === speaker.name}
                      >
                        {saving && selectedSpeaker === speaker.name ? (
                          <>
                            <div className="mini-spinner"></div>
                            Setting...
                          </>
                        ) : currentSpeaker === speaker.name ? (
                          '✓ Active'
                        ) : (
                          'Select'
                        )}
                      </button>
                    </div>
                  </motion.div>
                ))}
              </div>

              <div className="modal-footer">
                <p className="help-text">
                  • <strong>Sample:</strong> Play the original voice sample<br/>
                  • <strong>Preview:</strong> Generate a custom DJ intro with this voice<br/>
                  • <strong>Select:</strong> Set this voice as your active DJ
                </p>
              </div>
            </>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
};

export default SpeakerModal;