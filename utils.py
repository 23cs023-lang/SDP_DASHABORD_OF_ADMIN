# utils.py - Simple token-based authentication without JWT
import secrets
from functools import wraps
from flask import request, jsonify, redirect  # ADDED redirect here
import datetime

# Simple in-memory token storage (use Redis or database in production)
tokens = {}

def generate_token(username):
    """Generate a random token"""
    token = secrets.token_hex(32)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=24)
    tokens[token] = {
        'username': username,
        'expiry': expiry
    }
    return token

def verify_token(token):
    """Verify token validity"""
    if token in tokens:
        token_data = tokens[token]
        if datetime.datetime.now() < token_data['expiry']:
            return token_data['username']
        else:
            # Remove expired token
            del tokens[token]
    return None

def revoke_token(token):
    """Remove token"""
    if token in tokens:
        del tokens[token]

def token_required(f):
    """Decorator to protect routes with token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Check for token in Authorization header
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        # Check for token in cookies (for web pages)
        if not token:
            token = request.cookies.get('admin_token')
        
        if not token:
            if request.path.startswith('/api/'):
                return jsonify({
                    'success': False,
                    'error': 'Authentication required',
                    'redirect': '/login'
                }), 401
            else:
                return redirect('/login')  # This uses redirect
        
        username = verify_token(token)
        if not username:
            if request.path.startswith('/api/'):
                return jsonify({
                    'success': False,
                    'error': 'Invalid or expired token',
                    'redirect': '/login'
                }), 401
            else:
                return redirect('/login')  # This uses redirect
        
        # Add username to request context
        request.username = username
        request.token = token
        return f(*args, **kwargs)
    
    return decorated