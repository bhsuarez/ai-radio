#!/usr/bin/env python3
"""
Liquidsoap Connection Pool Manager

Provides a managed connection pool to Liquidsoap telnet interface with:
- Connection pooling and reuse
- Rate limiting to prevent spam
- Health monitoring and auto-reconnection
- Graceful error handling
"""

import time
import socket
import threading
from typing import Dict, Optional, Any
from contextlib import contextmanager
import queue

class LiquidsoapConnectionPool:
    """Thread-safe connection pool for Liquidsoap telnet interface"""
    
    def __init__(self, host="127.0.0.1", port=1234, max_connections=2, rate_limit_seconds=5):
        self.host = host
        self.port = port
        self.max_connections = max_connections
        self.rate_limit = rate_limit_seconds
        
        # Connection pool
        self._pool = queue.Queue(maxsize=max_connections)
        self._active_connections = 0
        self._pool_lock = threading.Lock()
        
        # Rate limiting
        self._last_query_time = 0
        self._rate_lock = threading.Lock()
        
        # Health monitoring
        self._connection_failures = 0
        self._max_failures = 3
        
    def _create_connection(self) -> socket.socket:
        """Create a new connection to Liquidsoap"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)  # 10 second timeout
            sock.connect((self.host, self.port))
            
            # Consume any welcome message
            try:
                sock.recv(1024)
            except socket.timeout:
                pass
                
            self._connection_failures = 0  # Reset failure counter on success
            return sock
            
        except Exception as e:
            self._connection_failures += 1
            raise ConnectionError(f"Failed to connect to Liquidsoap: {e}")
    
    def _is_connection_healthy(self, sock: socket.socket) -> bool:
        """Check if a connection is still healthy"""
        try:
            # Send a simple command to test
            sock.send(b"uptime\n")
            response = sock.recv(1024)
            return b"END" in response or len(response) > 0
        except:
            return False
    
    @contextmanager
    def get_connection(self):
        """Get a connection from the pool (context manager)"""
        connection = None
        
        try:
            # Rate limiting check
            with self._rate_lock:
                current_time = time.time()
                time_since_last = current_time - self._last_query_time
                
                if time_since_last < self.rate_limit:
                    sleep_time = self.rate_limit - time_since_last
                    print(f"Rate limiting: sleeping {sleep_time:.1f}s")
                    time.sleep(sleep_time)
                
                self._last_query_time = time.time()
            
            # Get connection from pool or create new one
            try:
                connection = self._pool.get_nowait()
                
                # Test if connection is still healthy
                if not self._is_connection_healthy(connection):
                    connection.close()
                    connection = self._create_connection()
                    
            except queue.Empty:
                # Pool is empty, create new connection if under limit
                with self._pool_lock:
                    if self._active_connections < self.max_connections:
                        connection = self._create_connection()
                        self._active_connections += 1
                    else:
                        # Wait for a connection to become available
                        connection = self._pool.get(timeout=30)
            
            yield connection
            
        except Exception as e:
            if connection:
                connection.close()
                with self._pool_lock:
                    self._active_connections -= 1
            raise e
            
        finally:
            # Return connection to pool if still healthy
            if connection:
                try:
                    if self._is_connection_healthy(connection):
                        self._pool.put_nowait(connection)
                    else:
                        connection.close()
                        with self._pool_lock:
                            self._active_connections -= 1
                except queue.Full:
                    # Pool is full, close connection
                    connection.close()
                    with self._pool_lock:
                        self._active_connections -= 1
    
    def execute_command(self, command: str, timeout: float = 5.0) -> str:
        """Execute a command and return the response"""
        if self._connection_failures >= self._max_failures:
            raise ConnectionError("Too many connection failures, service may be down")
        
        try:
            with self.get_connection() as conn:
                # Send command
                conn.send(f"{command}\n".encode('utf-8'))
                
                # Read response
                response = b""
                start_time = time.time()
                
                while time.time() - start_time < timeout:
                    try:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        response += chunk
                        
                        # Check for END marker
                        if b"END" in response:
                            break
                            
                    except socket.timeout:
                        break
                
                # Clean and return response
                result = response.decode('utf-8', errors='ignore')
                result = result.replace('\r\n', '\n').replace('\r', '\n')
                
                # Remove END marker and clean up
                lines = result.split('\n')
                clean_lines = []
                
                for line in lines:
                    line = line.strip()
                    if line == "END" or line == command:
                        continue
                    if line:
                        clean_lines.append(line)
                
                return '\n'.join(clean_lines)
                
        except Exception as e:
            print(f"Command '{command}' failed: {e}")
            raise
    
    def batch_execute(self, commands: list) -> Dict[str, str]:
        """Execute multiple commands in one connection session"""
        results = {}
        
        try:
            with self.get_connection() as conn:
                for cmd in commands:
                    try:
                        # Send command
                        conn.send(f"{cmd}\n".encode('utf-8'))
                        
                        # Read response
                        response = b""
                        start_time = time.time()
                        
                        while time.time() - start_time < 5.0:
                            try:
                                chunk = conn.recv(4096)
                                if not chunk:
                                    break
                                response += chunk
                                
                                if b"END" in response:
                                    break
                                    
                            except socket.timeout:
                                break
                        
                        # Clean response
                        result = response.decode('utf-8', errors='ignore')
                        result = result.replace('\r\n', '\n').replace('\r', '\n')
                        
                        lines = result.split('\n')
                        clean_lines = []
                        
                        for line in lines:
                            line = line.strip()
                            if line == "END" or line == cmd:
                                continue
                            if line:
                                clean_lines.append(line)
                        
                        results[cmd] = '\n'.join(clean_lines)
                        
                    except Exception as e:
                        results[cmd] = f"ERROR: {e}"
                        
            return results
            
        except Exception as e:
            return {cmd: f"CONNECTION_ERROR: {e}" for cmd in commands}
    
    def close_all(self):
        """Close all connections in the pool"""
        with self._pool_lock:
            while not self._pool.empty():
                try:
                    conn = self._pool.get_nowait()
                    conn.close()
                except:
                    pass
            self._active_connections = 0

# Global connection pool instance
_connection_pool = None
_pool_lock = threading.Lock()

def get_liquidsoap_pool() -> LiquidsoapConnectionPool:
    """Get the global connection pool instance"""
    global _connection_pool
    
    with _pool_lock:
        if _connection_pool is None:
            _connection_pool = LiquidsoapConnectionPool()
        return _connection_pool

def liquidsoap_query(command: str) -> str:
    """Simple interface for single commands"""
    pool = get_liquidsoap_pool()
    return pool.execute_command(command)

def liquidsoap_batch_query(commands: list) -> Dict[str, str]:
    """Simple interface for batch commands"""
    pool = get_liquidsoap_pool()
    return pool.batch_execute(commands)

# Test function
if __name__ == "__main__":
    print("Testing Liquidsoap connection pool...")
    
    try:
        # Test single query
        uptime = liquidsoap_query("uptime")
        print(f"Uptime: {uptime}")
        
        # Test batch query
        results = liquidsoap_batch_query(["request.all", "output.icecast.remaining"])
        print(f"Batch results: {results}")
        
    except Exception as e:
        print(f"Test failed: {e}")