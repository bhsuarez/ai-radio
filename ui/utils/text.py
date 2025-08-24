"""
Text processing utilities
"""
import re

# ANSI escape sequence pattern
ANSI_PATTERN = re.compile(r'\x1B\[[0-9;?]*[ -/]*[@-~]')

def parse_kv_text(text: str) -> dict:
    """
    Parse text with key=value lines into a dictionary.
    
    Args:
        text: Text containing key=value pairs, one per line
        
    Returns:
        Dictionary of parsed key-value pairs
    """
    result = {}
    if not text:
        return result
        
    for line in text.splitlines():
        line = line.strip()
        if '=' in line:
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('"')
    
    return result

def strip_ansi(text: str) -> str:
    """
    Remove ANSI escape sequences from text.
    
    Args:
        text: Text that may contain ANSI sequences
        
    Returns:
        Text with ANSI sequences removed
    """
    if not text:
        return text
    return ANSI_PATTERN.sub('', text)