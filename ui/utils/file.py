"""
File I/O utilities with error handling
"""
import json
import fcntl
from pathlib import Path
from typing import Any, Dict, List, Optional

def safe_json_read(filepath: Path, default=None) -> Any:
    """
    Safely read JSON file with proper error handling.
    
    Args:
        filepath: Path to JSON file
        default: Default value if file doesn't exist or is invalid
        
    Returns:
        Parsed JSON data or default value
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default

def safe_json_write(filepath: Path, data: Any) -> bool:
    """
    Safely write JSON data to file.
    
    Args:
        filepath: Path to write to
        data: Data to serialize as JSON
        
    Returns:
        True if successful, False otherwise
    """
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except (OSError, TypeError):
        return False

def atomic_write(filepath: Path, data: Any) -> bool:
    """
    Atomically write JSON data using temporary file and rename.
    
    Args:
        filepath: Target file path
        data: Data to write
        
    Returns:
        True if successful, False otherwise
    """
    try:
        temp_path = filepath.with_suffix(filepath.suffix + '.tmp')
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        temp_path.rename(filepath)
        return True
    except (OSError, TypeError):
        return False

def locked_json_read(filepath: Path, default=None) -> Any:
    """
    Read JSON file with file locking.
    
    Args:
        filepath: Path to JSON file
        default: Default value if file doesn't exist
        
    Returns:
        Parsed JSON data or default value
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default

def locked_json_append(filepath: Path, new_data: Dict, max_entries: int = 500) -> bool:
    """
    Append data to JSON array file with locking.
    
    Args:
        filepath: Path to JSON array file
        new_data: Data to append
        max_entries: Maximum entries to keep
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Read existing data with shared lock
        existing_data = []
        if filepath.exists():
            with open(filepath, 'r', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                    if isinstance(data, list):
                        existing_data = data
                except json.JSONDecodeError:
                    pass
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        # Append new data and limit size
        existing_data.append(new_data)
        existing_data = existing_data[-max_entries:]
        
        # Write atomically
        return atomic_write(filepath, existing_data)
    except (OSError, TypeError):
        return False