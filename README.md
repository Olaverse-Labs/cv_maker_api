# CV Maker API - AI-Powered Resume and Cover Letter Generator

[![Olaverse API](https://img.shields.io/badge/Olaverse-API%20Doc-blue?style=flat-square)](https://www.olaverse.co.uk/cv-maker-api)

A FastAPI-based service that uses AI to generate professional resumes and cover letters with three distinct design styles.

## Features

- **AI-Powered Generation**: Uses advanced language models (GPT-4, Claude, Gemini) to optimize CVs and generate cover letters
- **Three Design Styles**: 
  - **Classic Professional**: Traditional, conservative layout for finance, law, government
  - **Modern Minimal**: Clean, contemporary design for tech, creative, startup environments  
  - **Corporate Clean**: Professional design with highlighted header for corporate roles
- **ATS-Friendly**: Optimized for Applicant Tracking Systems
- **PDF Generation**: Convert HTML output to professional PDFs
- **Multiple AI Models**: Support for GPT-4, Claude 3, Gemini Pro, and more

## Quick Start

### 1. Install Dependencies

```bash
pip install fastapi uvicorn python-multipart requests PyPDF2 python-docx python-dotenv
```

### 2. Set Environment Variables

Create a `.env` file:

```env
OPENROUTER_API_KEY=your_openrouter_api_key_here
GOTENBERG_URL=http://localhost:3000  # Optional: for PDF generation
```

### 3. Run the API

```bash
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Access the API

- **API Documentation**: http://localhost:8000/docs
- **Alternative Docs**: http://localhost:8000/redoc

## API Endpoints

### Core Endpoints

#### `POST /upload`
Upload CV and job description documents.

**Parameters:**
- `cv_file` (optional): PDF, DOCX, or TXT file
- `cv_text` (optional): CV text content
- `job_desc_file` (optional): PDF, DOCX, or TXT file  
- `job_desc_text` (optional): Job description text
- `full_name` (optional): Contact information
- `address` (optional): Contact information
- `city_state_zip` (optional): Contact information
- `email` (optional): Contact information
- `phone` (optional): Contact information

#### `POST /optimize-cv`
Generate an optimized, ATS-friendly resume.

**Parameters:**
- `generate_pdf` (default: false): Return PDF instead of JSON
- `model` (optional): AI model to use

#### `POST /generate-cover-letter`
Generate a professional cover letter.

**Parameters:**
- `generate_pdf` (default: false): Return PDF instead of JSON
- `model` (optional): AI model to use

#### `POST /analyze-cv`
Analyze CV against job description.

**Parameters:**
- `generate_pdf` (default: false): Return PDF instead of JSON
- `model` (optional): AI model to use

### Information Endpoints

#### `GET /available-models`
Get list of available AI models.

## Design Styles

### 1. Classic Professional
- **Best for**: Finance, Law, Government, Traditional Corporate
- **Features**: Traditional, conservative layout with clear section dividers and centered header
- **Style**: Formal, structured, professional

## Available AI Models

- **GPT-4**: Most capable model, best for complex tasks
- **GPT-4 Turbo**: Fast and efficient, good for most tasks
- **GPT-3.5 Turbo**: Fast and cost-effective for simple tasks
- **Claude 3 Opus**: Highly capable model, excellent for analysis
- **Claude 3 Sonnet**: Balanced performance and cost
- **Claude 3 Haiku**: Fast and efficient for simple tasks
- **Gemini Pro**: Google's advanced language model

## Usage Examples

### Python Example

```python
import requests

# Upload documents
upload_data = {
    'cv_text': 'Your CV content here...',
    'job_desc_text': 'Job description here...',
    'full_name': 'John Doe',
    'email': 'john@example.com'
}

response = requests.post('http://localhost:8000/upload', data=upload_data)

# Generate optimized CV
cv_data = {
    'model': 'gpt-4',
    'generate_pdf': False
}

response = requests.post('http://localhost:8000/optimize-cv', data=cv_data)
result = response.json()
html_content = result['html_content']
```

### cURL Example

```bash
# Upload documents
curl -X POST "http://localhost:8000/upload" \
  -F "cv_text=Your CV content here..." \
  -F "job_desc_text=Job description here..." \
  -F "full_name=John Doe"

# Generate CV with PDF
curl -X POST "http://localhost:8000/optimize-cv" \
  -F "model=gpt-4" \
  -F "generate_pdf=true" \
  --output optimized_cv.pdf
```

## Response Format

### JSON Response
```json
{
  "message": "CV optimized successfully",
  "html_content": "<!DOCTYPE html>...",
  "model_used": "openai/gpt-4"
}
```

### PDF Response
When `generate_pdf=true`, returns a PDF file directly.

## Testing

Run the test script to verify all functionality:

```bash
python test_api.py
```

This will test:
- Document upload
- CV optimization
- Cover letter generation
- CV analysis
- PDF generation
- All API endpoints

## Configuration

### Environment Variables

- `OPENROUTER_API_KEY`: Your OpenRouter API key (required)
- `GOTENBERG_URL`: Gotenberg server URL for PDF generation (optional)
- `GOTENBERG_USERNAME`: Gotenberg username (optional)
- `GOTENBERG_PASSWORD`: Gotenberg password (optional)

### Customization

You can modify the available models in `config.py`:

```python
# Add new models
AVAILABLE_MODELS["your-model"] = {
    "id": "provider/model-name",
    "name": "Display Name",
    "description": "Model description",
    "max_tokens": 4000,
    "temperature": 0.7
}
```

## Architecture

- **FastAPI**: Modern, fast web framework
- **OpenRouter**: AI model provider (supports multiple providers)
- **Gotenberg**: PDF generation service
- **PyPDF2**: PDF text extraction
- **python-docx**: DOCX text extraction

## License

This project is open source and available under the MIT License.

## Support

For issues and questions, please check the API documentation at `/docs` or create an issue in the repository. 

# Deployment with Docker & Elestio

## Build the Docker Image

```sh
docker build -t cv-maker-app .
```

## Run the Docker Container

```sh
docker run -d -p 8000:8000 --name cv-maker-app cv-maker-app
```

## Environment Variables
- Set any required environment variables using `-e VAR_NAME=value` in your `docker run` command or via Elestio's dashboard.

## Deploying on Elestio
1. Push your code to your Git repository.
2. Connect your repository to Elestio and select the Dockerfile build option.
3. Set any required environment variables in the Elestio dashboard.
4. Elestio will build and deploy your app automatically.

## API Usage
- The API will be available at `http://<your-elestio-domain>:8000` (or the port you configure).
- Swagger docs: `/docs`

---

For troubleshooting, check container logs:
```sh
docker logs cv-maker-app
``` 