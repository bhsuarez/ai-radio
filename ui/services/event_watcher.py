"""
Event file watcher for handling Liquidsoap fallback events
Processes track_events.jsonl when HTTP calls fail
"""
import json
import os
import time
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class EventFileHandler(FileSystemEventHandler):
    """Handler for track event file changes"""
    
    def __init__(self, push_event_callback):
        self.push_event = push_event_callback
        self.last_processed = 0
        
    def on_modified(self, event):
        """Process new events when file is modified"""
        if event.is_directory:
            return
            
        if event.src_path.endswith("track_events.jsonl"):
            print(f"Event file modified: {event.src_path}")
            self.process_pending_events()
    
    def process_pending_events(self):
        """Read and process new events from file"""
        event_file = Path("/opt/ai-radio/cache/track_events.jsonl")
        
        if not event_file.exists():
            return
            
        try:
            # Read all lines
            with open(event_file, "r") as f:
                lines = f.readlines()
            
            processed_count = 0
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                try:
                    event_data = json.loads(line)
                    print(f"Processing fallback event: {event_data.get('artist')} - {event_data.get('title')}")
                    
                    # Use existing Flask push_event function
                    self.push_event(event_data)
                    processed_count += 1
                    
                except json.JSONDecodeError as e:
                    print(f"Invalid JSON in event file: {line[:50]}... Error: {e}")
                    continue
            
            if processed_count > 0:
                print(f"Processed {processed_count} fallback events")
                
                # Clear the file after processing
                with open(event_file, "w") as f:
                    pass  # Clear file
                    
        except Exception as e:
            print(f"Error processing event file: {e}")

class EventWatcher:
    """File watcher service for track events"""
    
    def __init__(self, push_event_callback):
        self.push_event = push_event_callback
        self.observer = None
        self.handler = EventFileHandler(push_event_callback)
        
    def start(self):
        """Start watching for event file changes"""
        cache_dir = Path("/opt/ai-radio/cache")
        cache_dir.mkdir(exist_ok=True)
        
        # Create empty event file if it doesn't exist
        event_file = cache_dir / "track_events.jsonl"
        if not event_file.exists():
            event_file.touch()
        
        self.observer = Observer()
        self.observer.schedule(self.handler, str(cache_dir), recursive=False)
        self.observer.start()
        
        print("Event file watcher started")
        
    def stop(self):
        """Stop the file watcher"""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            print("Event file watcher stopped")