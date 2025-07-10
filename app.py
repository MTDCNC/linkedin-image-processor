#!/usr/bin/env python3
"""
LinkedIn Image Processor REST Endpoint
"""

from flask import Flask, request, jsonify, send_file
from PIL import Image
import requests
import hashlib
import os
from io import BytesIO

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'processed_images')
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', 2 * 1024 * 1024))  # 2MB
MIN_WIDTH = int(os.environ.get('MIN_WIDTH', 400))
MAX_WIDTH = int(os.environ.get('MAX_WIDTH', 1000))
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def process_image(image_data: bytes) -> tuple:
    """Process image: resize, compress, optimize"""
    try:
        img = Image.open(BytesIO(image_data))
        
        # Convert to RGB if necessary
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        
        # Calculate new dimensions
        original_width, original_height = img.size
        
        # Determine new width within constraints
        if original_width < MIN_WIDTH:
            new_width = MIN_WIDTH
        elif original_width > MAX_WIDTH:
            new_width = MAX_WIDTH
        else:
            new_width = original_width
        
        # Calculate proportional height
        aspect_ratio = original_height / original_width
        new_height = int(new_width * aspect_ratio)
        
        # Resize image
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Compress and optimize
        output = BytesIO()
        quality = 85
        
        # Reduce quality if file is too large
        while True:
            output = BytesIO()
            img.save(output, format='JPEG', quality=quality, optimize=True)
            
            if output.tell() <= MAX_FILE_SIZE or quality <= 50:
                break
                
            quality -= 10
            
            # Resize further if still too large
            if quality <= 50 and output.tell() > MAX_FILE_SIZE:
                new_width = int(new_width * 0.9)
                new_height = int(new_height * 0.9)
                if new_width < MIN_WIDTH:
                    break
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                quality = 75
        
        return output.getvalue(), new_width, new_height
        
    except Exception as e:
        raise Exception(f"Image processing failed: {str(e)}")

def download_linkedin_image(url: str) -> bytes:
    """Download image from LinkedIn URL"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        # Verify it's an image
        content_type = response.headers.get('content-type', '')
        if not content_type.startswith('image/'):
            raise Exception(f"URL does not point to an image. Content-Type: {content_type}")
        
        return response.content
        
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to download image: {str(e)}")

@app.route('/process-linkedin-image', methods=['POST'])
def process_linkedin_image():
    """Main endpoint to process LinkedIn images"""
    try:
        data = request.get_json()
        
        if not data or 'image_url' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing image_url in request body'
            }), 400
        
        linkedin_url = data['image_url']
        custom_filename = data.get('filename', '')
        
        # Download the image
        image_data = download_linkedin_image(linkedin_url)
        
        # Get original dimensions
        original_img = Image.open(BytesIO(image_data))
        original_size = original_img.size
        
        # Process the image
        processed_data, new_width, new_height = process_image(image_data)
        
        # Generate filename
        if custom_filename:
            safe_filename = "".join(c for c in custom_filename if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{safe_filename.replace(' ', '_')}.jpg"
        else:
            url_hash = hashlib.md5(linkedin_url.encode()).hexdigest()[:12]
            filename = f"linkedin_{url_hash}.jpg"
        
        # Save processed image
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        with open(file_path, 'wb') as f:
            f.write(processed_data)
        
        # Get file size
        file_size = os.path.getsize(file_path)
        
        # Build public URL
        public_url = f"{BASE_URL}/images/{filename}"
        
        return jsonify({
            'success': True,
            'processed_url': public_url,
            'local_path': file_path,
            'original_size': original_size,
            'processed_size': [new_width, new_height],
            'file_size': file_size,
            'filename': filename
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/images/<filename>')
def serve_image(filename):
    """Serve processed images"""
    try:
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=False)
        else:
            return jsonify({'error': 'Image not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return j
