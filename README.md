# LinkedIn Image Processor

REST API that downloads LinkedIn images, processes them to WordPress-compatible format, and serves them with clean URLs.

## Usage

POST to `/process-linkedin-image` with:
```json
{
  "image_url": "https://media.licdn.com/dms/image/...",
  "filename": "optional-name"
}
