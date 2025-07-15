#!/usr/bin/env python3
"""
LinkedIn Image Processor REST Endpoint
Updated to handle 1280x720 container constraints
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
MAX_CONTAINER_WIDTH = int(os.environ.get('MAX_CONTAINER_WIDTH', 1280))
MAX_CONTAINER_HEIGHT = int(os.environ.get('MAX_CONTAINER_HEIGHT', 720))
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def calculate_container_fit_dimensions(original_width, original_height, container_width=1280, container_height=720, min_width=400):
    """
    Calculate dimensions to fit image within container while maintaining aspect ratio
    
    Args:
        original_width: Original image width
        original_height: Original image height
        container_width: Maximum container width (default 1280)
        container_height: Maximum container height (default 720)
        min_width: Minimum allowed width (default 400)
    
    Returns:
        tuple: (new_width, new_height)
    """
    # Calculate aspect ratio
    aspect_ratio = original_width / original_height
    
    # Calculate dimensions if we fit by width
    width_constrained_width = container_width
    width_constrained_height = int(container_width / aspect_ratio)
    
    # Calculate dimensions if we fit by height  
    height_constrained_width = int(container_height * aspect_ratio)
    height_constrained_height = container_height
    
    # Choose the constraint that keeps image within both bounds
    if width_constrained_height <= container_height:
        # Width is the limiting factor
        new_width = width_constrained_width
        new_height = width_constrained_height
    else:
        # Height is the limiting factor
        new_width = height_constrained_width
        new_height = height_constrained_height
    
    # Ensure minimum width is respected
    if new_width < min_width:
        new_width = min_width
        new_height = int(min_width / aspect_ratio)
        
        # If minimum width causes height to exceed container, crop height
        if new_height > container_height:
            new_height = container_height
            # Recalculate width to maintain aspect ratio
            new_width = int(container_height * aspect_ratio)
    
    return new_width, new_height

def process_image(image_data: bytes) -> tuple:
    """Process image: resize to fit 1280x720 container, compress, optimize"""
    try:
        img = Image.open(BytesIO(image_data))
        
        # Convert to RGB if necessary
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        
        # Get original dimensions
        original_width, original_height = img.size
        
        # Calculate new dimensions to fit in 1280x720 container
        new_width, new_height = calculate_container_fit_dimensions(
            original_width, 
            original_height,
            MAX_CONTAINER_WIDTH,
            MAX_CONTAINER_HEIGHT,
            MIN_WIDTH
        )
        
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
            
            # If still too large at minimum quality, resize further
            if quality <= 50 and output.tell() > MAX_FILE_SIZE:
                # Reduce size by 10% each iteration
                new_width = int(new_width * 0.9)
                new_height = int(new_height * 0.9)
                
                # Don't go below minimum width
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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
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

@app.route('/')
def home():
    """Home page with API info"""
    return {
        'service': 'LinkedIn Image Processor',
        'status': 'running',
        'version': '2.0.0',
        'container_constraints': {
            'max_width': MAX_CONTAINER_WIDTH,
            'max_height': MAX_CONTAINER_HEIGHT,
            'min_width': MIN_WIDTH
        },
        'endpoints': {
            'POST /process-linkedin-image': 'Process a LinkedIn image URL',
            'GET /images/<filename>': 'Serve processed images', 
            'GET /health': 'Health check'
        },
        'usage_example': {
            'url': BASE_URL + '/process-linkedin-image',
            'method': 'POST',
            'body': {
                'image_url': 'https://media.licdn.com/dms/image/...',
                'filename': 'optional-name'
            }
        },
        'notes': [
            'Images are resized to fit within 1280x720 container while maintaining aspect ratio',
            'Portrait images (1000x2000) will be max 360x720',
            'Landscape images (2000x1000) will be max 1280x640',
            'All images maintain minimum 400px width',
            'File size kept under 2MB'
        ]
    }

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
        
        # Calculate what the final dimensions will be
        calculated_width, calculated_height = calculate_container_fit_dimensions(
            original_size[0], 
            original_size[1],
            MAX_CONTAINER_WIDTH,
            MAX_CONTAINER_HEIGHT,
            MIN_WIDTH
        )
        
        # Process the image
        processed_data, final_width, final_height = process_image(image_data)
        
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
        
        # Determine fit type for debugging
        aspect_ratio = original_size[0] / original_size[1]
        container_aspect = MAX_CONTAINER_WIDTH / MAX_CONTAINER_HEIGHT
        fit_type = "width-constrained" if aspect_ratio > container_aspect else "height-constrained"
        
        return jsonify({
            'success': True,
            'processed_url': public_url,
            'local_path': file_path,
            'original_size': original_size,
            'processed_size': [final_width, final_height],
            'file_size': file_size,
            'filename': filename,
            'container_info': {
                'max_container': [MAX_CONTAINER_WIDTH, MAX_CONTAINER_HEIGHT],
                'fit_type': fit_type,
                'aspect_ratio': round(aspect_ratio, 2),
                'container_aspect': round(container_aspect, 2)
            }
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
    return jsonify({
        'status': 'healthy',
        'upload_folder': UPLOAD_FOLDER,
        'max_file_size': MAX_FILE_SIZE,
        'container_constraints': {
            'max_width': MAX_CONTAINER_WIDTH,
            'max_height': MAX_CONTAINER_HEIGHT,
            'min_width': MIN_WIDTH
        },
        'base_url': BASE_URL
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
