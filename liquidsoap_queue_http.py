#!/usr/bin/env python3
"""
HTTP wrapper for Liquidsoap queue metadata queries
Provides HTTP endpoints to get current/next track info without direct telnet usage
"""

import socket
import time
import json
import re
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler

class LiquidsoapTelnetClient:
    """Wrapper for Liquidsoap telnet commands"""
    
    def __init__(self, host='127.0.0.1', port=1234):
        self.host = host
        self.port = port
    
    def execute_command(self, command: str, timeout: float = 2.0) -> str:
        """Execute a telnet command and return the response"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((self.host, self.port))
                
                # Send command
                sock.sendall(f"{command}\nquit\n".encode())
                
                # Read response
                response = ""
                while True:
                    try:
                        data = sock.recv(4096).decode('utf-8', errors='replace')
                        if not data or "Bye!" in data:
                            break
                        response += data
                    except socket.timeout:
                        break
                
                return response.strip()
        except Exception as e:
            return f"ERROR: {str(e)}"
    
    def get_request_queue(self) -> List[str]:
        """Get list of request IDs in the queue"""
        response = self.execute_command("request.all")
        if response.startswith("ERROR"):
            return []
        
        lines = response.split('\n')
        for line in lines:
            if line and not line.startswith("END") and not line.startswith("Bye!"):
                # Response should be space-separated request IDs
                return line.split()
        return []
    
    def get_request_metadata(self, request_id: str) -> Dict:
        """Get metadata for a specific request ID"""
        response = self.execute_command(f"request.metadata {request_id}")
        
        if response.startswith("ERROR") or "No such request" in response:
            return {}
        
        metadata = {}
        lines = response.split('\n')
        
        for line in lines:
            if '=' in line and not line.startswith("END"):
                try:
                    key, value = line.split('=', 1)
                    # Clean up value (remove quotes and escape sequences)
                    value = value.strip('"').replace('\\u0000', '').replace('\u0000', '')
                    metadata[key] = value
                except ValueError:
                    continue
        
        return metadata
    
    def get_queue_with_metadata(self) -> Dict:
        """Get current queue with metadata for each track"""
        queue_ids = self.get_request_queue()
        
        result = {
            "queue_ids": queue_ids,
            "current": None,
            "next": [],
            "timestamp": int(time.time())
        }
        
        if not queue_ids:
            return result
        
        # First ID is usually current playing
        if len(queue_ids) > 0:
            current_metadata = self.get_request_metadata(queue_ids[0])
            if current_metadata:
                result["current"] = {
                    "rid": queue_ids[0],
                    "metadata": current_metadata
                }
        
        # Subsequent IDs are upcoming tracks
        for queue_id in queue_ids[1:]:
            metadata = self.get_request_metadata(queue_id)
            if metadata:
                result["next"].append({
                    "rid": queue_id,
                    "metadata": metadata
                })
        
        return result

class QueueHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for queue metadata requests"""
    
    def __init__(self, *args, **kwargs):
        self.telnet_client = LiquidsoapTelnetClient()
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        try:
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            
            if path == '/':
                self.send_info_page()
            elif path == '/queue':
                self.send_queue_metadata()
            elif path == '/current':
                self.send_current_track()
            elif path == '/next':
                self.send_next_tracks()
            else:
                self.send_error(404, "Endpoint not found")
        except Exception as e:
            self.send_error(500, str(e))
    
    def send_json_response(self, data: Dict, status: int = 200):
        """Send JSON response"""
        response = json.dumps(data, indent=2)
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.send_header('Content-length', str(len(response)))
        self.end_headers()
        self.wfile.write(response.encode())
    
    def send_info_page(self):
        """Send info page with available endpoints"""
        info = {
            "service": "Liquidsoap Queue HTTP API",
            "endpoints": {
                "/queue": "Full queue with metadata",
                "/current": "Current playing track",
                "/next": "Upcoming tracks"
            },
            "timestamp": int(time.time())
        }
        self.send_json_response(info)
    
    def send_queue_metadata(self):
        """Send full queue metadata"""
        data = self.telnet_client.get_queue_with_metadata()
        self.send_json_response(data)
    
    def send_current_track(self):
        """Send current playing track"""
        queue_data = self.telnet_client.get_queue_with_metadata()
        current = queue_data.get("current")
        
        if current:
            # Format for compatibility with existing API
            metadata = current["metadata"]
            track_info = {
                "title": metadata.get("title", metadata.get("tt2", "Unknown Title")),
                "artist": metadata.get("artist", metadata.get("tp1", "Unknown Artist")),
                "album": metadata.get("album", metadata.get("tal", "Unknown Album")),
                "filename": metadata.get("filename", ""),
                "rid": current["rid"],
                "source": "liquidsoap_queue"
            }
        else:
            track_info = {"error": "No current track"}
        
        self.send_json_response(track_info)
    
    def send_next_tracks(self):
        """Send upcoming tracks"""
        queue_data = self.telnet_client.get_queue_with_metadata()
        next_tracks = []
        
        for track in queue_data.get("next", []):
            metadata = track["metadata"]
            track_info = {
                "title": metadata.get("title", metadata.get("tt2", "Unknown Title")),
                "artist": metadata.get("artist", metadata.get("tp1", "Unknown Artist")),
                "album": metadata.get("album", metadata.get("tal", "Unknown Album")),
                "filename": metadata.get("filename", ""),
                "rid": track["rid"]
            }
            next_tracks.append(track_info)
        
        self.send_json_response(next_tracks)
    
    def log_message(self, format, *args):
        """Override to reduce logging noise"""
        pass

def main():
    """Start the HTTP server"""
    server_address = ('127.0.0.1', 8003)
    httpd = HTTPServer(server_address, QueueHTTPHandler)
    
    print(f"Starting Liquidsoap Queue HTTP API on http://{server_address[0]}:{server_address[1]}")
    print("Available endpoints:")
    print("  /queue - Full queue with metadata")
    print("  /current - Current playing track")
    print("  /next - Upcoming tracks")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.shutdown()

if __name__ == '__main__':
    main()