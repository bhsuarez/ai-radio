"""
Main routes (home page, static files)
"""
from flask import Blueprint, send_from_directory, current_app
from pathlib import Path

main_bp = Blueprint('main', __name__)

@main_bp.route("/")
def index():
    """Serve the main application page"""
    base_dir = Path(current_app.root_path)
    response = send_from_directory(base_dir, "index.html")
    
    # Add cache-busting headers for mobile browsers
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response