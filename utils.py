import json
import tempfile
import os
import requests
from datetime import datetime
from PyPDF2 import PdfReader
from docx import Document
from config import (
    GOTENBERG_URL,
    GOTENBERG_USERNAME,
    GOTENBERG_PASSWORD,
    MAX_UPLOAD_BYTES,
)


def _strip_code_fence(text):
    """Remove a leading ```json / ``` fence and any trailing fence."""
    text = text.strip()
    if text.startswith('```'):
        newline = text.find('\n')
        text = text[newline + 1:] if newline != -1 else text[3:]
    if text.endswith('```'):
        text = text[:-3]
    return text.strip()


def _repair_truncated_json(text):
    """Close a JSON object that was cut off mid-generation.

    Models hit their max_tokens partway through and return something like
    '{"a": [1, 2], "b": "half a sen' — valid up to a point. Walk to the last
    safe boundary and close whatever is still open.
    """
    stack = []
    in_string = False
    escaped = False
    last_safe = None

    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
                # end of a string value is a safe place to stop
                if stack:
                    last_safe = i + 1
        elif ch == '"':
            in_string = True
        elif ch in '{[':
            stack.append('}' if ch == '{' else ']')
        elif ch in '}]':
            if stack:
                stack.pop()
            last_safe = i + 1
        elif ch == ',':
            last_safe = i  # drop the trailing comma
        elif ch.isdigit() or ch in 'aeflnorstu':
            # inside a bare literal (number/true/false/null); only safe once closed
            pass

    if last_safe is None:
        return None

    candidate = text[:last_safe].rstrip().rstrip(',')

    # Recount what is still open at the truncation point.
    stack = []
    in_string = False
    escaped = False
    for ch in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in '{[':
            stack.append('}' if ch == '{' else ']')
        elif ch in '}]' and stack:
            stack.pop()

    return candidate + ''.join(reversed(stack))


def parse_model_json(content):
    """Best-effort parse of a JSON object from a model reply.

    Returns the parsed dict, or None if nothing usable could be recovered.
    Handles code fences, surrounding prose, and output truncated by max_tokens.
    """
    if not content or not content.strip():
        return None

    candidates = []
    cleaned = _strip_code_fence(content)
    candidates.append(cleaned)

    # Ignore any prose before the first '{' or after the last '}'.
    start = cleaned.find('{')
    if start != -1:
        end = cleaned.rfind('}')
        if end > start:
            candidates.append(cleaned[start:end + 1])
        candidates.append(cleaned[start:])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            continue

    # Everything failed; the reply was probably cut off mid-object.
    if start != -1:
        repaired = _repair_truncated_json(cleaned[start:])
        if repaired:
            try:
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                pass

    return None


class DocumentError(Exception):
    """Raised when an upload cannot be read. The message goes straight to the client."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _check_size(upload_file, label):
    """Reject oversized uploads before parsing them into memory."""
    stream = upload_file.file
    try:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(0)
    except (AttributeError, OSError):
        return  # non-seekable stream; let the parser handle it
    if size > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise DocumentError(
            f'{label} file is too large. Maximum size is {limit_mb}MB',
            status_code=413
        )


def extract_document_text(upload_file, fallback_text, label):
    """Return text from an upload, or the supplied text if no file was sent.

    `label` is "CV" or "job description" and appears in error messages, which are
    unchanged from when this logic was inlined in each endpoint.
    """
    if upload_file:
        _check_size(upload_file, label)
        filename = upload_file.filename or ''
        if filename.endswith('.pdf'):
            pdf_reader = PdfReader(upload_file.file)
            return " ".join(page.extract_text() or '' for page in pdf_reader.pages)
        elif filename.endswith('.docx'):
            doc = Document(upload_file.file)
            return " ".join([para.text for para in doc.paragraphs])
        elif filename.endswith('.txt'):
            return upload_file.file.read().decode('utf-8')
        else:
            raise DocumentError(
                f'Unsupported {label} file type. Use PDF, DOCX, or TXT'
            )
    elif fallback_text:
        return fallback_text
    else:
        raise DocumentError(f'No {label} provided (file or text)')

def get_cv_html_template(style: str, content: str, title: str = "CV", custom_style: dict = None, custom_colors: dict = None) -> str:
    """Generate HTML with different CV styles and custom colors"""
    
    # Use custom preferences if provided, otherwise use defaults
    if custom_style:
        font_size = custom_style.get('font_size', '12px')
        line_spacing = custom_style.get('line_spacing', '1.5')
    else:
        font_size = '12px'
        line_spacing = '1.5'
    
    if custom_colors:
        primary_color = custom_colors.get('primary', '#2c3e50')
        secondary_color = custom_colors.get('secondary', '#3498db')
        accent_color = custom_colors.get('accent', '#34495e')
    else:
        primary_color = '#2c3e50'
        secondary_color = '#3498db'
        accent_color = '#34495e'
    
    # Base CSS for all styles
    base_css = f"""
        @page {{ margin: 0; }}
        body {{ 
            font-family: Arial, sans-serif; 
            line-height: {line_spacing}; 
            margin: 0; 
            padding: 0; 
            color: #333; 
            font-size: {font_size};
        }}
        h1 {{ margin: 0 0 20px 0; }}
        h2 {{ margin: 30px 0 15px 0; }}
        h3 {{ margin: 20px 0 10px 0; }}
        ul {{ margin: 10px 0 10px 20px; }}
        li {{ margin-bottom: 5px; }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .content {{ 
            white-space: pre-wrap; 
            font-size: {font_size}; 
            line-height: {line_spacing};
        }}
        p {{ margin: 0 0 12px 0; }}
    """
    
    # Style-specific CSS with custom colors
    style_css = {
        "professional": f"""
            body {{ 
                font-family: 'Times New Roman', serif; 
                font-size: {font_size};
            }}
            h1 {{ 
                color: {primary_color}; 
                border-bottom: 2px solid {accent_color}; 
                padding-bottom: 8px; 
                font-size: 18px;
            }}
            h2 {{ 
                color: {accent_color}; 
                border-left: 3px solid {secondary_color}; 
                padding-left: 12px; 
                font-size: 14px;
            }}
            h3 {{ color: #7f8c8d; font-size: 13px; }}
            .header {{ 
                border-bottom: 1px solid #bdc3c7; 
                padding-bottom: 15px; 
                margin-bottom: 20px;
            }}
            .content {{ 
                font-size: {font_size}; 
                line-height: {line_spacing};
            }}
        """,
        "modern": f"""
            body {{ 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                font-size: {font_size};
            }}
            h1 {{ 
                color: {primary_color}; 
                font-size: 16px; 
                font-weight: 600; 
                margin-bottom: 15px;
            }}
            h2 {{ 
                color: {secondary_color}; 
                font-size: 13px; 
                font-weight: 500; 
                margin-top: 20px;
            }}
            h3 {{ 
                color: #7f8c8d; 
                font-weight: 600; 
                font-size: 12px;
            }}
            .header {{ 
                background: linear-gradient(135deg, {secondary_color} 0%, {accent_color} 100%); 
                color: white; 
                padding: 20px; 
                border-radius: 8px; 
                margin-bottom: 20px;
            }}
            .content {{ 
                background: #f8f9fa; 
                padding: 15px; 
                border-radius: 6px; 
                font-size: {font_size};
                line-height: {line_spacing};
            }}
        """,
        "creative": f"""
            body {{ 
                font-family: 'Helvetica Neue', Arial, sans-serif; 
                font-size: {font_size};
            }}
            h1 {{ 
                color: {primary_color}; 
                font-size: 18px; 
                text-transform: uppercase; 
                letter-spacing: 1px; 
                font-weight: 600;
            }}
            h2 {{ 
                color: {accent_color}; 
                border-bottom: 2px solid {primary_color}; 
                padding-bottom: 6px; 
                font-size: 13px;
            }}
            h3 {{ 
                color: {accent_color}; 
                font-style: italic; 
                font-size: 12px;
            }}
            .header {{ 
                background: #ecf0f1; 
                padding: 18px; 
                border-left: 4px solid {primary_color}; 
                margin-bottom: 20px;
            }}
            .content {{ 
                border: 1px solid #bdc3c7; 
                padding: 18px; 
                border-radius: 4px; 
                font-size: {font_size};
                line-height: {line_spacing};
            }}
        """,
        "minimal": f"""
            body {{ 
                font-family: 'Arial', sans-serif; 
                max-width: 100%; 
                margin: 0; 
                font-size: {font_size};
            }}
            h1 {{ 
                color: {primary_color}; 
                font-size: 16px; 
                font-weight: normal; 
                margin-bottom: 15px;
            }}
            h2 {{ 
                color: {accent_color}; 
                font-size: 13px; 
                font-weight: normal; 
                border-bottom: 1px solid #bdc3c7; 
                padding-bottom: 5px;
            }}
            h3 {{ 
                color: #7f8c8d; 
                font-weight: normal; 
                font-size: 12px;
            }}
            .header {{ 
                margin-bottom: 25px; 
                text-align: left;
            }}
            .content {{ 
                line-height: {line_spacing}; 
                font-size: {font_size};
            }}
        """,
        "elegant": f"""
            body {{ 
                font-family: 'Georgia', serif; 
                font-size: {font_size};
            }}
            h1 {{ 
                color: {primary_color}; 
                font-size: 18px; 
                font-weight: 600; 
                margin-bottom: 15px;
                border-bottom: 2px solid {secondary_color};
            }}
            h2 {{ 
                color: {accent_color}; 
                font-size: 14px; 
                font-weight: 500; 
                margin-top: 20px;
            }}
            h3 {{ 
                color: {secondary_color}; 
                font-style: italic; 
                font-size: 12px;
            }}
            .header {{ 
                background: linear-gradient(135deg, {primary_color} 0%, {accent_color} 100%); 
                color: white; 
                padding: 20px; 
                border-radius: 8px; 
                margin-bottom: 20px;
            }}
            .content {{ 
                background: #fafafa; 
                padding: 20px; 
                border-radius: 6px; 
                font-size: {font_size};
                line-height: {line_spacing};
                border-left: 3px solid {secondary_color};
            }}
        """,
        "bold": f"""
            body {{ 
                font-family: 'Impact', 'Arial Black', sans-serif; 
                font-size: {font_size};
            }}
            h1 {{ 
                color: {primary_color}; 
                font-size: 20px; 
                font-weight: 900; 
                margin-bottom: 15px;
                text-transform: uppercase;
            }}
            h2 {{ 
                color: {secondary_color}; 
                font-size: 16px; 
                font-weight: 700; 
                margin-top: 20px;
                border-bottom: 3px solid {accent_color};
            }}
            h3 {{ 
                color: {accent_color}; 
                font-weight: 700; 
                font-size: 14px;
            }}
            .header {{ 
                background: {primary_color}; 
                color: white; 
                padding: 25px; 
                margin-bottom: 20px;
                text-align: center;
            }}
            .content {{ 
                background: #f8f9fa; 
                padding: 20px; 
                font-size: {font_size};
                line-height: {line_spacing};
                border: 2px solid {secondary_color};
            }}
        """
    }
    
    # Get the selected style CSS, default to professional if not found
    selected_css = style_css.get(style, style_css["professional"])
    
    # No generation date for any documents
    generation_date_html = ""
    
    # Only show header if title is provided
    header_html = ""
    if title and title.strip():
        header_html = f"""
        <div class="header">
            <h1>{title}</h1>
            {generation_date_html}
        </div>
        """
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{title or 'Document'}</title>
        <style>
            {base_css}
            {selected_css}
        </style>
    </head>
    <body>
        {header_html}
        <div class="content">{content}</div>
    </body>
    </html>
    """
    
    return html_content

def convert_html_to_pdf(html_content: str, filename: str = "document.pdf", pdf_margin: str = "0in") -> bytes:
    """Convert HTML content to PDF using Gotenberg API"""
    try:
        html_content = html_content.strip()
        # Only inject @page for margins/size, do not override any other CSS
        page_css = f"""
        @page {{
            margin: 0;
            size: A4;
        }}
        """
        if '<style>' in html_content:
            html_content = html_content.replace('<style>', f'<style>{page_css}')
        elif '<head>' in html_content:
            html_content = html_content.replace('<head>', f'<head><style>{page_css}</style>')
        else:
            html_content = html_content.replace('<body>', f'<head><style>{page_css}</style></head><body>')

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as html_file:
            html_file.write(html_content)
            html_file_path = html_file.name
        with open(html_file_path, 'rb') as html_file:
            files = {
                'index.html': ('index.html', html_file, 'text/html')
            }
            auth = None
            if GOTENBERG_USERNAME and GOTENBERG_PASSWORD:
                auth = (GOTENBERG_USERNAME, GOTENBERG_PASSWORD)
            margin_val = '0'
            data = {
                'marginTop': margin_val,
                'marginBottom': margin_val,
                'marginLeft': margin_val,
                'marginRight': margin_val,
                'format': 'A4',
                'preferCssPageSize': 'true'
            }
            gotenberg_response = requests.post(
                f"{GOTENBERG_URL}/forms/chromium/convert/html",
                files=files,
                data=data,
                auth=auth
            )
        os.unlink(html_file_path)
        if gotenberg_response.status_code == 200:
            return gotenberg_response.content
        else:
            raise Exception(f"PDF generation failed: {gotenberg_response.text}")
    except Exception as e:
        raise Exception(f"PDF conversion failed: {str(e)}")

def get_available_styles() -> dict:
    """Return available CV styles"""
    return {} 