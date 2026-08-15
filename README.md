# CV Maker API - AI-Powered Resume and Cover Letter Generator

[![Olaverse API](https://img.shields.io/badge/Olaverse-API%20Doc-blue?style=flat-square)](https://www.olaverse.co.uk/cv-maker-api)

A FastAPI-based service that uses AI to generate professional resumes and cover letters with three distinct design styles.

## Features

- **AI-Powered Generation**: Uses advanced language models (Claude, GPT-5, Gemini, Grok, DeepSeek) to optimize CVs and generate cover letters
- **Three Design Styles**: 
  - **Classic Professional**: Traditional, conservative layout for finance, law, government
  - **Modern Minimal**: Clean, contemporary design for tech, creative, startup environments  
  - **Corporate Clean**: Professional design with highlighted header for corporate roles
- **ATS-Friendly**: Optimized for Applicant Tracking Systems
- **PDF Generation**: Convert HTML output to professional PDFs
- **Multiple AI Models**: Support for Claude Sonnet 5, GPT-5.6, Grok 4.5, Gemini, DeepSeek and more

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
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

All generation endpoints take the CV and job description directly — there is no
separate upload step. For each of the two documents, send **either** a file or
raw text:

- `cv_file` / `cv_text` — PDF, DOCX or TXT file, or the text itself
- `job_desc_file` / `job_desc_text` — same, for the job description

Files are capped at `MAX_UPLOAD_MB` (10MB by default); a larger one returns
`413`. A missing document returns `400`.

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
- `tone` (default: `professional`): Tone of the letter
- `full_name`, `email`, `phone`, `address`, `city_state_zip` (optional): Applicant contact details
- `company_name` (optional): Company being applied to
- `hiring_manager` (optional): Named recipient; falls back to "Hiring Manager"
- `job_title` (optional): Role being applied for
- `letter_date` (optional): Date printed in the letter head, used verbatim.
  Overrides `timezone`. Use it to control the wording, e.g. `15 August 2026`.
- `timezone` (optional): IANA name (`Pacific/Auckland`) or UTC offset (`+13:00`,
  `-0800`, `UTC+13`) the default date is computed in. May also be sent as an
  `X-Timezone` header, which is usually one line in a shared HTTP client.

##### How the date gets its timezone

"Today" is not the same date everywhere at once and the container clock is UTC,
so an applicant in Auckland filing at 9am local would otherwise date the letter
yesterday. Nothing has to be passed for this to work: the zone is resolved from
the request itself, most to least trustworthy, and the first hit wins.

| # | Source | Accuracy |
| --- | --- | --- |
| 1 | `timezone` form field | Exact |
| 2 | `X-Timezone` header | Exact |
| 3 | CDN timezone headers — `X-Vercel-IP-Timezone`, `CloudFront-Viewer-Time-Zone`, `CF-Timezone` | Exact, set by the edge from the viewer's IP |
| 4 | CDN country headers — `CF-IPCountry`, `X-Vercel-IP-Country`, `CloudFront-Viewer-Country`, `X-Appengine-Country` | Exact for single-zone countries, approximate for wide ones |
| 5 | `Accept-Language` region subtag (`en-NZ` → New Zealand) | Weak: a Nigerian in London still sends `en-NG` |
| 6 | `DEFAULT_TIMEZONE`, else UTC | Fixed |

Rows 3–5 need no client changes at all — a deployment behind Cloudflare,
Vercel, CloudFront or App Engine already gets them. Row 3 is the one to aim for;
enable your CDN's geolocation headers and worldwide accuracy comes free. Failing
that, browsers know their own zone, so `Intl.DateTimeFormat().resolvedOptions().timeZone`
sent as `X-Timezone` gets you row 2 in one line.

Where a country spans many zones the most populous one is used (`US` →
`America/New_York`, `AU` → `Australia/Sydney`), which is approximate: it is only
ever wrong by a day, and only for applicants in another zone of that country
within a few hours of midnight. An unresolvable value falls through to the next
source rather than failing the request.

Supplying `company_name`, `hiring_manager` and `job_title` is strongly
recommended — without them the model has to infer each one from the job
description, which is where generic-sounding letters come from.

The JSON response echoes the date used as `letter_date`, the zone it was computed
in as `letter_timezone`, and which of the sources above supplied it as
`letter_timezone_source` (both `null` when `letter_date` was passed). Watching
how often `letter_timezone_source` comes back `default` tells you what share of
real traffic is falling all the way through. The date is pinned in
the prompt *and* the returned HTML's `class="date"` element is rewritten
afterwards, so the printed date does not depend on the model getting it right;
`letter_date_enforced` reports whether that rewrite found an element to act on.

#### `POST /analyze-cv`
Analyze CV against job description.

**Parameters:**
- `generate_pdf` (default: false): Return PDF instead of JSON
- `model` (optional): AI model to use

### Information Endpoints

#### `GET /available-models`
Get list of available AI models, the default key, and full per-model metadata.

#### `GET /health`
Service health plus the state of both external dependencies.

- `healthy` (200) — OpenRouter key configured and Gotenberg reachable
- `degraded` (200) — generation works, PDF conversion unavailable
- `unhealthy` (503) — no OpenRouter API key, nothing can run

```json
{
  "status": "healthy",
  "service": "cv-maker-api",
  "default_model": "anthropic/claude-sonnet-5",
  "available_models": 7,
  "checks": {
    "openrouter": {"configured": true, "base_url": "https://openrouter.ai/api/v1"},
    "gotenberg": {"url": "http://localhost:3000", "reachable": true}
  }
}
```

## Design Styles

### 1. Classic Professional
- **Best for**: Finance, Law, Government, Traditional Corporate
- **Features**: Traditional, conservative layout with clear section dividers and centered header
- **Style**: Formal, structured, professional

## Available AI Models

Pass the key in the `model` form field. Defaults to `claude-sonnet-5`.
Prices are USD per million tokens (input / output).

| Key | Model | Best for | Price |
| --- | --- | --- | --- |
| `claude-sonnet-5` *(default)* | Claude Sonnet 5 | Best all-round writing quality and reliable HTML/JSON output | $2 / $10 |
| `gpt-5.6-terra` | GPT-5.6 Terra | Tailoring CVs to job descriptions | $1 / $6 |
| `grok-4.5` | Grok 4.5 | Punchier, less formulaic writing | $2 / $6 |
| `claude-haiku-4.5` | Claude Haiku 4.5 | Fast extraction and short cover letters | $1 / $5 |
| `gpt-5.4-mini` | GPT-5.4 Mini | Low-cost routine rewrites | $0.75 / $4.50 |
| `gemini-3.5-flash-lite` | Gemini 3.5 Flash Lite | Bulk generation and quick drafts | $0.30 / $2.50 |
| `deepseek-v4-pro` | DeepSeek V4 Pro | Cheapest option, high-volume use | $0.44 / $0.87 |

`gpt-5.6-terra` doubles as the legacy model: every retired key (`gpt-4`,
`gpt-4-turbo`, `gpt-3.5-turbo`, `claude-3-opus`, `claude-3-sonnet`, `claude-3-haiku`,
`gemini-pro`, `llama-3`, `mixtral-8x7b`) routes to it, so existing clients keep
working. Unknown keys fall back to the default.

Call `GET /available-models` for the live list with full metadata.

## Usage Examples

### Python Example

```python
import requests

# Everything goes in a single call
cv_data = {
    'cv_text': 'Your CV content here...',
    'job_desc_text': 'Job description here...',
    'full_name': 'John Doe',
    'email': 'john@example.com',
    'model': 'claude-sonnet-5',
    'style': 'classic',
    'generate_pdf': False
}

response = requests.post('http://localhost:8000/optimize-cv', data=cv_data)
result = response.json()
html_content = result['html_content']
analysis = result['analysis']
```

Or send the CV as a file:

```python
with open('cv.pdf', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/optimize-cv',
        files={'cv_file': f},
        data={'job_desc_text': 'Job description here...'}
    )
```

### cURL Example

```bash
# Generate CV with PDF
curl -X POST "http://localhost:8000/optimize-cv" \
  -F "cv_text=Your CV content here..." \
  -F "job_desc_text=Job description here..." \
  -F "full_name=John Doe" \
  -F "model=claude-sonnet-5" \
  -F "generate_pdf=true" \
  --output optimized_cv.pdf
```

## Response Format

### JSON Response
```json
{
  "message": "CV optimized successfully",
  "html_content": "<!DOCTYPE html>...",
  "model_used": "anthropic/claude-sonnet-5"
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
- `OPENROUTER_TIMEOUT`: Seconds to wait for a model response (default `120`)
- `ANALYSIS_MAX_TOKENS`: Token ceiling for the analysis step (default `6000`)
- `MAX_UPLOAD_MB`: Largest accepted upload per file (default `10`)
- `CORS_ALLOW_ORIGINS`: Comma-separated origins, or `*` for any (default `*`)
- `APP_URL` / `APP_TITLE`: Optional OpenRouter dashboard attribution
- `DEFAULT_TIMEZONE`: Zone the cover letter date falls back to when the caller
  names none (default `UTC`). Set it to your audience's zone if they share one
- `LOG_LEVEL`: Logging level (default `INFO`)

### Running the tests

```bash
pip install pytest
pytest
```

Tests stub OpenRouter, so they never make a network call or spend credits.

### Customization

You can modify the available models in `config.py`:

```python
# Add new models
AVAILABLE_MODELS["your-model"] = {
    "id": "provider/model-name",
    "name": "Display Name",
    "description": "Model description",
    "context_length": 200000,
    "tier": "balanced",
    "price_per_m": {"input": 1.00, "output": 5.00},
    "max_tokens": 8000,
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