"""
Security utilities for path validation
"""
import os
from config import config

def is_allowed_path(path: str) -> bool:
    """
    Check if a path is within allowed music directories to prevent path traversal attacks.
    
    Args:
        path: File path to validate
        
    Returns:
        True if path is safe, False otherwise
    """
    if not path:
        return False
        
    try:
        normalized_path = os.path.abspath(path)
        return any(
            os.path.commonpath([normalized_path, root]) == os.path.abspath(root)
            for root in config.MUSIC_ROOTS
        )
    except (ValueError, TypeError):
        return False