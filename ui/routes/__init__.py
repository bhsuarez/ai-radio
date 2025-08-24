"""
Route modules for AI Radio Flask application
"""
from .main import main_bp
from .api import api_bp
from .websocket import register_websocket_handlers

def register_routes(app):
    """Register all route blueprints with the Flask app"""
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')

__all__ = ['register_routes', 'register_websocket_handlers']