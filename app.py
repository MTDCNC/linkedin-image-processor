#!/usr/bin/env python3
"""
LinkedIn Image Processor REST Endpoint
Updated to handle 1280x720 container constraints
Now enforces a minimum width of 640px (upscales if needed)

Includes:
- Verbose logging around network + processing steps
- /debug-fetch-url endpoint to test remote image URLs
"""
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from PIL import Image
import requests
import hashlib
import os
import time
import logging
from io import BytesIO

app = Flask(__name__)

# ---- Logging config ----
# Flask on Render will send stdout/stderr to the Render logs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = app.logger  # use Flask's logger so it includes request info


# Configuration
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'processed_images')
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', 2 * 1024 * 1024))  # 2MB
MIN_WIDTH = int(os.environ.get('MIN_WIDTH', 640))  # <-- enforce min width 640 by default
MAX_CONTAINER_WIDTH = int(os.environ.get('MAX_CONTAINER_WIDTH', 1280))
MAX_CONTAINER_HEIGHT = int(os.environ.get('MAX_CONTAINER_HEIGHT', 720))
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def calculate_container_fit_dimensions(
    original_width: int,
    original_height: int,
    container_width: int = 1280,
    container_height: int = 720,
    min_width: int = 640
) -> tuple[int, int]:
    """
    Calculate output dimensions while maintaining aspect ratio.

    Behavior:
    - If the original image is narrower than min_width, UPSCALE to exactly min_width
      and compute height from the aspect ratio (this path does not clamp to container).
    - Otherwise, fit within the container (max 1280x720) preserving aspect ratio.
    """
    aspect_ratio = original_width / original_height

    # NEW: hard minimum width upscale branch
    if original_width < min_width:
        new_width = min_width
        new_height = int(round(new_width / aspect_ratio))
        return new_width, new_height

    # Container-fit branch (unchanged behavior for images >= min_width)
    width_constrained_width = container_width
    width_constrained_height = int(round(container_width / aspect_ratio))

    height_constrained_width = int(round(container_height * aspect_ratio))
    height_constrained_height = container_height

    if width_constrained_height <= container_height:
        new_width = width_constrained_width
        new_height = width_constrained_height
    else:
        new_width = height_constrained_width
        new_height = height_constrained_height

    # Ensure minimum width is respected in container path (rare edge)
    if new_width < min_width:
        new_width = min_width
        new_height = int(round(min_width / aspect_ratio))

    return new_width, new_height


def process_image(image_data: bytes) -> tuple:
    """Process image: enforce min width, then container-fit if needed, compress, optimize."""
    try:
        t0 = time.time()
        img = Image.open(BytesIO(image_data))

        logger.info(
            "[process_image] Opened image, original size=%sx%s, mode=%s",
            img.size[0], img.size[1], img.mode
        )

        # Convert to RGB if necessary
        if img.mode in ('RGBA', 'LA', 'P'):
            logger.info("[process_image] Converting image mode %s to RGB", img.mode)
            img = img.convert('RGB')

        # Get original dimensions
        original_width, original_height = img.size

        # Calculate new dimensions
        new_width, new_height = calculate_container_fit_dimensions(
            original_width,
            original_height,
            MAX_CONTAINER_WIDTH,
            MAX_CONTAINER_HEIGHT,
            MIN_WIDTH
        )
        logger.info(
            "[process_image] Calculated dimensions %sx%s from original %sx%s",
            new_width, new_height, original_width, original_height
        )

        # Resize image
        if (new_width, new_height) != (original_width, original_height):
            logger.info("[process_image] Resizing image...")
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Compress and optimize under MAX_FILE_SIZE, without dropping below MIN_WIDTH
        quality = 85
        iteration = 0
        while True:
            iteration += 1
            output = BytesIO()
            img.save(output, format='JPEG', quality=quality, optimize=True)
            size_now = output.tell()
            logger.info(
                "[process_image] Iteration %d: quality=%d size=%d bytes",
                iteration, quality, size_now
            )

            # Stop if size OK or quality hit floor
            if size_now <= MAX_FILE_SIZE or quality <= 50:
                break

            # Reduce quality first
            quality -= 10

            # If still too large at minimum quality, scale down but never below MIN_WIDTH
            if quality <= 50 and size_now > MAX_FILE_SIZE:
                scaled_w = int(new_width * 0.9)
                scaled_h = int(new_height * 0.9)

                if scaled_w < MIN_WIDTH:
                    logger.warning(
                        "[process_image] Cannot downscale below MIN_WIDTH (%d). "
                        "Accepting best effort at size %d bytes.",
                        MIN_WIDTH, size_now
                    )
                    break

                logger.info(
                    "[process_image] Downscaling image to %sx%s and retrying...",
                    scaled_w, scaled_h
                )
                img = img.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
                new_width, new_height = scaled_w, scaled_h
                quality = 75  # bounce quality a bit after a downscale and retry

        logger.info(
            "[process_image] Completed processing in %.2fs; final size=%d bytes, %sx%s",
            time.time() - t0, output.tell(), new_width, new_height
        )
        return output.getvalue(), new_width, new_height

    except Exception as e:
        logger.exception("[process_image] Image processing failed: %s", e)
        raise Exception(f"Image processing failed: {str(e)}")


def download_linkedin_image(url: str) -> bytes:
    """Download image from LinkedIn URL"""
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/91.0.4472.124 Safari/537.36'
        ),
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    logger.info("[download_linkedin_image] Start download url=%s", url)
    t0 = time.time()

    try:
        # Split timeout: 5s to connect, 25s to read
        response = requests.get(url, headers=headers, timeout=(5, 25))
        elapsed = time.time() - t0
        logger.info(
            "[download_linkedin_image] HTTP %s in %.2fs",
            response.status_code, elapsed
        )
        response.raise_for_status()

        # Verify it's an image
        content_type = response.headers.get('content-type', '')
        logger.info(
            "[download_linkedin_image] Content-Type=%s, length=%s",
            content_type, response.headers.get('content-length')
        )
        if not content_type.startswith('image/'):
            raise Exception(f"URL does not point to an image. Content-Type: {content_type}")

        return response.content

    except requests.exceptions.RequestException as e:
        elapsed = time.time() - t0
        logger.exception(
            "[download_linkedin_image] Failed after %.2fs: %s", elapsed, e
        )
        raise Exception(f"Failed to download image: {str(e)}")

@app.route("/health", methods=["GET"])
def health_check():
    return {
        "status": "ok",
        "service": "linkedin-image-processor",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }, 200


@app.route('/')
def home():
    """Home page with API info"""
    return {
        'service': 'LinkedIn Image Processor',
        'status': 'running',
        'version': '2.2.0',
        'container_constraints': {
            'max_width': MAX_CONTAINER_WIDTH,
            'max_height': MAX_CONTAINER_HEIGHT,
            'min_width': MIN_WIDTH
        },
        'endpoints': {
            'POST /process-linkedin-image': 'Process a LinkedIn image URL',
            'POST /debug-fetch-url': 'Debug: fetch a remote URL and report timing',
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
            'Images >= min width follow 1280x720 container-fit while maintaining aspect ratio',
            'Images < min width are upscaled to min width (aspect ratio preserved)',
            'Minimum width default is 640px (configurable via MIN_WIDTH)',
            'File size kept under 2MB'
        ]
    }


@app.post("/debug-fetch-url")
def debug_fetch_url():
    """
    Debug endpoint:
    POST JSON: { "url": "<any image URL>" }
    Returns status, timing, and content length.

    Use this from Zapier or a REST client to see how fast LinkedIn responds
    from inside Render, without running the full processing pipeline.
    """
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")

    if not url:
        return jsonify({"ok": False, "error": "missing 'url'"}), 400

    logger.info("[debug_fetch_url] Testing URL=%s", url)
    t0 = time.time()

    try:
        resp = requests.get(url, timeout=(5, 25))
        elapsed = time.time() - t0
        logger.info(
            "[debug_fetch_url] DONE HTTP %s in %.2fs (len=%d)",
            resp.status_code, elapsed, len(resp.content)
        )
        return jsonify({
            "ok": True,
            "status_code": resp.status_code,
            "elapsed_seconds": round(elapsed, 3),
            "content_length": len(resp.content),
            "headers": {
                "content_type": resp.headers.get("content-type"),
                "content_length": resp.headers.get("content-length"),
            }
        }), 200

    except Exception as e:
        elapsed = time.time() - t0
        logger.exception(
            "[debug_fetch_url] ERROR after %.2fs url=%s err=%s",
            elapsed, url, e
        )
        return jsonify({
            "ok": False,
            "error": str(e),
            "elapsed_seconds": round(elapsed, 3),
        }), 500


@app.route('/process-linkedin-image', methods=['POST'])
def process_linkedin_image():
    """Main endpoint to process LinkedIn images"""
    req_start = time.time()
    logger.info("[process_linkedin_image] Incoming request from %s", request.remote_addr)

    try:
        data = request.get_json(force=True, silent=True)

        if not data or 'image_url' not in data:
            logger.warning("[process_linkedin_image] Missing image_url in body: %s", data)
            return jsonify({
                'success': False,
                'error': 'Missing image_url in request body'
            }), 400

        linkedin_url = data['image_url']
        custom_filename = data.get('filename', '')

        logger.info(
            "[process_linkedin_image] Start for url=%s filename=%s",
            linkedin_url, custom_filename
        )

        # Download the image
        image_data = download_linkedin_image(linkedin_url)

        # Calculate what the final dimensions will be
        with Image.open(BytesIO(image_data)) as original_img:
            original_size = original_img.size
            calculated_width, calculated_height = calculate_container_fit_dimensions(
                original_size[0],
                original_size[1],
                MAX_CONTAINER_WIDTH,
                MAX_CONTAINER_HEIGHT,
                MIN_WIDTH
            )

        logger.info(
            "[process_linkedin_image] Original size=%s, calculated_size=%sx%s",
            original_size, calculated_width, calculated_height
        )

        # Process the image
        processed_data, final_width, final_height = process_image(image_data)

        # Generate filename
        if custom_filename:
            safe_filename = "".join(
                c for c in custom_filename if c.isalnum() or c in (' ', '-', '_')
            ).rstrip()
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
        fit_type = (
            "min-width-upscale" if original_size[0] < MIN_WIDTH
            else ("width-constrained" if aspect_ratio > container_aspect else "height-constrained")
        )

        total_elapsed = time.time() - req_start
        logger.info(
            "[process_linkedin_image] SUCCESS filename=%s size=%d bytes "
            "processed_size=%sx%s total_time=%.2fs",
            filename, file_size, final_width, final_height, total_elapsed
        )

        return jsonify({
            'success': True,
            'processed_url': public_url,
            'local_path': file_path,
            'original_size': list(original_size),
            'calculated_size': [calculated_width, calculated_height],
            'processed_size': [final_width, final_height],
            'file_size': file_size,
            'filename': filename,
            'container_info': {
                'max_container': [MAX_CONTAINER_WIDTH, MAX_CONTAINER_HEIGHT],
                'fit_type': fit_type,
                'aspect_ratio': round(aspect_ratio, 4),
                'container_aspect': round(container_aspect, 4),
                'min_width': MIN_WIDTH
            },
            'timing': {
                'total_seconds': round(total_elapsed, 3)
            }
        })

    except Exception as e:
        total_elapsed = time.time() - req_start
        logger.exception(
            "[process_linkedin_image] ERROR after %.2fs: %s",
            total_elapsed, e
        )
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
            logger.info("[serve_image] Serving %s", file_path)
            return send_file(file_path, as_attachment=False)
        else:
            logger.warning("[serve_image] Image not found: %s", file_path)
            return jsonify({'error': 'Image not found'}), 404
    except Exception as e:
        logger.exception("[serve_image] Error: %s", e)
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
    # debug=True will also give stack traces locally; on Render it's usually ignored
    app.run(host='0.0.0.0', port=port, debug=True)
