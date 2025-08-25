import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import axios from 'axios';
import './DJSettingsModal.css';

interface DJPrompt {
  name: string;
  prompt: string;
}

interface DJSettings {
  ai_prompts: {
    intro_prompts: DJPrompt[];
    outro_prompts: DJPrompt[];
    active_intro_prompt: string;
    active_outro_prompt: string;
  };
  auto_dj_enabled: boolean;
  ai_dj_probability: number;
  min_interval_minutes: number;
  max_interval_minutes: number;
}

interface DJSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  apiBase: string;
}

const DJSettingsModal: React.FC<DJSettingsModalProps> = ({ isOpen, onClose, apiBase }) => {
  const [settings, setSettings] = useState<DJSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState<'prompts' | 'config' | 'openai'>('prompts');
  const [customPrompt, setCustomPrompt] = useState({ name: '', prompt: '' });
  const [openaiKey, setOpenaiKey] = useState('');

  useEffect(() => {
    if (isOpen) {
      fetchSettings();
    }
  }, [isOpen]);

  const fetchSettings = async () => {
    try {
      setLoading(true);
      const response = await axios.get(`${apiBase}/api/dj-prompts`);
      setSettings(response.data);
    } catch (error) {
      console.error('Failed to fetch DJ settings:', error);
    } finally {
      setLoading(false);
    }
  };

  const saveActivePrompts = async (introPrompt: string, outroPrompt: string) => {
    try {
      setSaving(true);
      await axios.post(`${apiBase}/api/dj-prompts/active`, {
        intro_prompt: introPrompt,
        outro_prompt: outroPrompt
      });
      
      // Update local state
      if (settings) {
        setSettings({
          ...settings,
          ai_prompts: {
            ...settings.ai_prompts,
            active_intro_prompt: introPrompt,
            active_outro_prompt: outroPrompt
          }
        });
      }
    } catch (error) {
      console.error('Failed to save active prompts:', error);
    } finally {
      setSaving(false);
    }
  };

  const addCustomPrompt = async (type: 'intro' | 'outro') => {
    if (!customPrompt.name.trim() || !customPrompt.prompt.trim()) {
      return;
    }

    try {
      setSaving(true);
      await axios.post(`${apiBase}/api/dj-prompts/custom`, {
        name: customPrompt.name,
        prompt: customPrompt.prompt,
        type: type
      });
      
      setCustomPrompt({ name: '', prompt: '' });
      await fetchSettings(); // Refresh settings
    } catch (error) {
      console.error('Failed to add custom prompt:', error);
    } finally {
      setSaving(false);
    }
  };

  const saveOpenAIKey = async () => {
    if (!openaiKey.trim()) {
      return;
    }

    try {
      setSaving(true);
      await axios.post(`${apiBase}/api/dj-prompts/openai-key`, {
        api_key: openaiKey
      });
      
      // Clear the key from state for security
      setOpenaiKey('');
      alert('OpenAI API key saved successfully!');
    } catch (error) {
      console.error('Failed to save OpenAI key:', error);
      alert('Failed to save OpenAI API key. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const updateConfig = async (key: string, value: any) => {
    if (!settings) return;

    try {
      setSaving(true);
      await axios.post(`${apiBase}/api/dj-prompts/config`, {
        [key]: value
      });
      
      // Update local state
      setSettings({
        ...settings,
        [key]: value
      });
    } catch (error) {
      console.error('Failed to update config:', error);
      alert('Failed to update configuration. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <motion.div 
        className="modal-overlay"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      >
        <motion.div 
          className="dj-settings-modal"
          initial={{ opacity: 0, scale: 0.9, y: 50 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.9, y: 50 }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="modal-header">
            <h2>🎙️ DJ Settings</h2>
            <button className="close-button" onClick={onClose}>×</button>
          </div>

          <div className="modal-tabs">
            <button 
              className={`tab ${activeTab === 'prompts' ? 'active' : ''}`}
              onClick={() => setActiveTab('prompts')}
            >
              Prompts
            </button>
            <button 
              className={`tab ${activeTab === 'config' ? 'active' : ''}`}
              onClick={() => setActiveTab('config')}
            >
              Config
            </button>
            <button 
              className={`tab ${activeTab === 'openai' ? 'active' : ''}`}
              onClick={() => setActiveTab('openai')}
            >
              OpenAI
            </button>
          </div>

          <div className="modal-content">
            {loading ? (
              <div className="loading">Loading DJ settings...</div>
            ) : settings ? (
              <>
                {activeTab === 'prompts' && (
                  <div className="prompts-tab">
                    <div className="prompt-section">
                      <h3>Intro Prompts</h3>
                      <div className="prompt-list">
                        {settings.ai_prompts.intro_prompts.map((prompt, index) => (
                          <div key={index} className={`prompt-item ${settings.ai_prompts.active_intro_prompt === prompt.name ? 'active' : ''}`}>
                            <div className="prompt-header">
                              <span className="prompt-name">{prompt.name}</span>
                              <button 
                                className="select-button"
                                onClick={() => saveActivePrompts(prompt.name, settings.ai_prompts.active_outro_prompt)}
                                disabled={saving}
                              >
                                {settings.ai_prompts.active_intro_prompt === prompt.name ? '✓ Active' : 'Select'}
                              </button>
                            </div>
                            <div className="prompt-text">{prompt.prompt}</div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="prompt-section">
                      <h3>Outro Prompts</h3>
                      <div className="prompt-list">
                        {settings.ai_prompts.outro_prompts.map((prompt, index) => (
                          <div key={index} className={`prompt-item ${settings.ai_prompts.active_outro_prompt === prompt.name ? 'active' : ''}`}>
                            <div className="prompt-header">
                              <span className="prompt-name">{prompt.name}</span>
                              <button 
                                className="select-button"
                                onClick={() => saveActivePrompts(settings.ai_prompts.active_intro_prompt, prompt.name)}
                                disabled={saving}
                              >
                                {settings.ai_prompts.active_outro_prompt === prompt.name ? '✓ Active' : 'Select'}
                              </button>
                            </div>
                            <div className="prompt-text">{prompt.prompt}</div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="custom-prompt-section">
                      <h3>Add Custom Prompt</h3>
                      <div className="custom-prompt-form">
                        <input
                          type="text"
                          placeholder="Prompt name"
                          value={customPrompt.name}
                          onChange={(e) => setCustomPrompt({...customPrompt, name: e.target.value})}
                        />
                        <textarea
                          placeholder="Enter your custom DJ prompt here... Use {title} and {artist} placeholders."
                          value={customPrompt.prompt}
                          onChange={(e) => setCustomPrompt({...customPrompt, prompt: e.target.value})}
                          rows={4}
                        />
                        <div className="custom-prompt-buttons">
                          <button 
                            onClick={() => addCustomPrompt('intro')}
                            disabled={saving || !customPrompt.name.trim() || !customPrompt.prompt.trim()}
                          >
                            Add as Intro
                          </button>
                          <button 
                            onClick={() => addCustomPrompt('outro')}
                            disabled={saving || !customPrompt.name.trim() || !customPrompt.prompt.trim()}
                          >
                            Add as Outro
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {activeTab === 'config' && (
                  <div className="config-tab">
                    <div className="config-section">
                      <h3>DJ Generation Settings</h3>
                      <div className="config-item">
                        <label>
                          <input 
                            type="checkbox" 
                            checked={settings.auto_dj_enabled}
                            onChange={(e) => updateConfig('auto_dj_enabled', e.target.checked)}
                            disabled={saving}
                          />
                          Enable Auto DJ
                        </label>
                      </div>
                      <div className="config-item">
                        <label>DJ Generation Probability: {settings.ai_dj_probability}%</label>
                        <input 
                          type="range" 
                          min="0" 
                          max="100" 
                          value={settings.ai_dj_probability}
                          onChange={(e) => updateConfig('ai_dj_probability', parseInt(e.target.value))}
                          disabled={saving}
                        />
                      </div>
                      <div className="config-item">
                        <label>Min Interval (minutes): {settings.min_interval_minutes}</label>
                        <input 
                          type="range" 
                          min="0" 
                          max="30" 
                          value={settings.min_interval_minutes}
                          onChange={(e) => updateConfig('min_interval_minutes', parseInt(e.target.value))}
                          disabled={saving}
                        />
                      </div>
                      <div className="config-item">
                        <label>Max Interval (minutes): {settings.max_interval_minutes}</label>
                        <input 
                          type="range" 
                          min="1" 
                          max="60" 
                          value={settings.max_interval_minutes}
                          onChange={(e) => updateConfig('max_interval_minutes', parseInt(e.target.value))}
                          disabled={saving}
                        />
                      </div>
                    </div>
                  </div>
                )}

                {activeTab === 'openai' && (
                  <div className="openai-tab">
                    <div className="config-section">
                      <h3>OpenAI Configuration</h3>
                      <p>Add your OpenAI API key for faster and more reliable DJ generation.</p>
                      
                      <div className="config-item">
                        <label>OpenAI API Key:</label>
                        <input 
                          type="password" 
                          placeholder="sk-..."
                          value={openaiKey}
                          onChange={(e) => setOpenaiKey(e.target.value)}
                        />
                      </div>
                      
                      <div className="openai-info">
                        <h4>Benefits of OpenAI:</h4>
                        <ul>
                          <li>5-10x faster generation (5-10 seconds vs 30-60 seconds)</li>
                          <li>More reliable and consistent results</li>
                          <li>Better quality control and fewer retries</li>
                          <li>Uses GPT-4 and GPT-3.5 models</li>
                        </ul>
                        
                        <h4>Current Fallback System:</h4>
                        <ul>
                          <li>Tier 1: OpenAI (disabled - no API key)</li>
                          <li>Tier 2: Ollama local models (currently used)</li>
                          <li>Tier 3: Alternative Ollama models</li>
                          <li>Tier 4: Template-based fallback</li>
                        </ul>
                      </div>
                      
                      <button 
                        className="save-button"
                        onClick={saveOpenAIKey}
                        disabled={!openaiKey.trim() || saving}
                      >
                        {saving ? 'Saving...' : 'Save OpenAI Key'}
                      </button>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="error">Failed to load DJ settings</div>
            )}
          </div>

          <div className="modal-footer">
            <button className="close-button" onClick={onClose}>
              Close
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
};

export default DJSettingsModal;