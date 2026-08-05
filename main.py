import logging
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
import requests
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, DEFAULT_MODEL, AVAILABLE_MODELS, resolve_model, GOTENBERG_URL, GOTENBERG_USERNAME, GOTENBERG_PASSWORD, CORS_ALLOW_ORIGINS, ANALYSIS_MAX_TOKENS
import tempfile
import os
from utils import convert_html_to_pdf, extract_document_text, DocumentError, parse_model_json
from templates import get_style_template
from openrouter import chat_completion, OpenRouterError
import json as pyjson

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("cv_maker_api")

app = FastAPI(title="CV Maker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. Remove global variables for cv_text_storage, job_desc_text_storage, contact_info_storage
# 2. Remove /upload endpoint
# 3. Refactor /optimize-cv and /generate-cover-letter to accept all required parameters directly as form fields or files
# 4. Update all usages to use request data only

@app.post('/optimize-cv')
def optimize_cv(
    cv_file: Optional[UploadFile] = File(None),
    cv_text: Optional[str] = Form(None),
    job_desc_file: Optional[UploadFile] = File(None),
    job_desc_text: Optional[str] = Form(None),
    # Contact information for cover letter
    full_name: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    city_state_zip: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    generate_pdf: bool = Form(False),
    model: str = Form(None),
    style: str = Form("classic"),
    user_query: str = Form(None) # New optional parameter
):
    # Read the CV and job description from either an upload or raw text
    try:
        cv_extracted_text = extract_document_text(cv_file, cv_text, 'CV')
        job_desc_extracted_text = extract_document_text(
            job_desc_file, job_desc_text, 'job description')
    except DocumentError as e:
        return JSONResponse({'error': e.message}, status_code=e.status_code)
    
    # Use provided model or fall back to default
    selected_model = resolve_model(model)
    
    # Select template based on style
    template_html = get_style_template(style)

    # 1. Run analysis
    analysis_prompt = f"""
You are an advanced CV analysis engine. Analyze the following CV against the provided job description and return a detailed JSON object with:
- ats_score: score out of 100
- keyword_match_percentage: percentage
- missing_keywords: [list of missing keywords]
- strengths: [list of strengths]
- weaknesses: [list of weaknesses]
- skills_gap: [list of missing skills]
- formatting_issues: [list of formatting issues]
- recommendations: [list of improvement recommendations]
- overall_assessment: brief overall assessment

Job Description:
{job_desc_extracted_text}

CV Content:
{cv_extracted_text}

Return ONLY the JSON object, no explanations or additional text.
"""
    try:
        analysis_content = chat_completion(
            selected_model, analysis_prompt, temperature=0.3,
            max_tokens=ANALYSIS_MAX_TOKENS)
    except OpenRouterError as e:
        return JSONResponse({'error': f'API request failed: {e}'}, status_code=500)

    # The analysis is context for the CV prompt below, not the deliverable. If the
    # model returns something unparseable, carry on with an empty analysis rather
    # than failing a request the user is paying for.
    analysis_json = parse_model_json(analysis_content)
    if analysis_json is None:
        logger.warning(
            "analysis JSON unparseable model=%s length=%d; continuing without it",
            selected_model, len(analysis_content or '')
        )
        analysis_json = {'note': 'Analysis unavailable for this request'}

    # 2. Use analysis_json in the optimization prompt
    prompt = f"""
# ATS-Friendly CV Generation Prompt

## IMPORTANT CONTENT INSTRUCTIONS
- You must REWRITE, OPTIMIZE, and TAILOR the CV content for the provided job description and ATS requirements.
- Use the old CV only as a reference for facts and achievements.
- Do NOT copy or reuse the old CV text directly. Instead, summarize, rephrase, and enhance the user's experience, skills, and summary for the target job.
- Integrate relevant keywords and requirements from the job description naturally.
- The output must be concise, impactful, and fit on a single A4 page.
- ONLY include skills, experiences, and claims that are supported by the user's actual CV and the analysis below. Do NOT add anything the user cannot do.

## Styling and Output Requirements
- Use the following CSS and HTML structure as a reference for layout and formatting. Do NOT copy any example content or dummy data from the template. Generate a new CV using the user's information below, following the style and layout of the template.

STYLE REFERENCE: {template_html}

Job Description:
{job_desc_extracted_text}

CV Content:
{cv_extracted_text}

CV Analysis (for context):
{pyjson.dumps(analysis_json, indent=2)}

## Core Requirements

### ATS Compatibility Rules
- Use simple, readable fonts (Arial, Calibri, Times New Roman equivalents)
- Avoid graphics, images, tables, or complex formatting
- Use standard section headers that ATS systems recognize
- Maintain single-column layout structure
- Use consistent bullet points and spacing
- Include relevant keywords naturally within content
- Ensure proper hierarchy with clear headings

### Standard CV Structure
Generate CVs with these sections in order:
1. **Header** (Name, contact information)
2. **Professional Summary** (2-3 sentence overview)
3. **Experience** (Most recent first, with quantified achievements)
4. **Education** (Degree, institution, year)
5. **Skills** (Technical and soft skills relevant to role)
6. **Optional sections** (Certifications, Languages, Projects - if relevant)



## Input Data
- Name: {full_name}
- Email: {email}
- Phone: {phone}
- Address: {address}
- City/State/ZIP: {city_state_zip}


## Output Format
Generate the CV as a complete HTML document with:
- <!DOCTYPE html>, <html>, <head>, and <body> tags
- ALL CSS in a <style> tag in the <head>
- Minimal padding and margin for both screen and print
- Print-friendly, ATS-friendly, and professional design
- Clean, readable layout
- Mobile-responsive structure
- All content must be based on the user's data above
- Do NOT copy any example content or dummy data from the style reference
"""
    
    # Add user_query to the prompt if provided
    if user_query:
        prompt += f"\n\nAdditional user instructions: {user_query}\n"

    try:
        html_content = chat_completion(
            selected_model, prompt, temperature=0.7, max_tokens=4000)
        
        # Clean up the HTML content (remove any markdown formatting if present)
        if html_content.startswith('```html'):
            html_content = html_content[7:]
        if html_content.endswith('```'):
            html_content = html_content[:-3]
        html_content = html_content.strip()
        
        response_data = {
            'message': 'CV optimized successfully',
            'html_content': html_content,
            'model_used': selected_model,
            'analysis': analysis_json
        }
        
        # Generate PDF if requested
        if generate_pdf:
            try:
                pdf_content = convert_html_to_pdf(html_content, "optimized_cv.pdf")
                
                # Create temporary PDF file
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as pdf_file:
                    pdf_file.write(pdf_content)
                    pdf_file_path = pdf_file.name
                
                # Return PDF file
                return FileResponse(
                    pdf_file_path,
                    media_type='application/pdf',
                    filename=f"optimized_cv.pdf",
                    background=None
                )
            except Exception as e:
                response_data['pdf_error'] = f'PDF generation failed: {str(e)}'
        
        return JSONResponse(response_data)
        
    except OpenRouterError as e:
        return JSONResponse({'error': f'API request failed: {str(e)}'}, status_code=500)
    except Exception as e:
        return JSONResponse({'error': f'Optimization failed: {str(e)}'}, status_code=500)

@app.post('/generate-cover-letter')
def generate_cover_letter(
    cv_file: Optional[UploadFile] = File(None),
    cv_text: Optional[str] = Form(None),
    job_desc_file: Optional[UploadFile] = File(None),
    job_desc_text: Optional[str] = Form(None),
    # Contact information for cover letter
    full_name: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    city_state_zip: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    generate_pdf: bool = Form(False),
    model: str = Form(None),
    tone: str = Form("professional")
):
    # Read the CV and job description from either an upload or raw text
    try:
        cv_extracted_text = extract_document_text(cv_file, cv_text, 'CV')
        job_desc_extracted_text = extract_document_text(
            job_desc_file, job_desc_text, 'job description')
    except DocumentError as e:
        return JSONResponse({'error': e.message}, status_code=e.status_code)
    
    # Get contact information with fallbacks
    contact_full_name = full_name
    contact_address = address
    contact_city_state_zip = city_state_zip
    contact_email = email
    contact_phone = phone
    
    # Try to extract name from CV if not provided
    if not contact_full_name:
        cv_lines = cv_extracted_text.split('\n')
        for line in cv_lines[:5]:
            line = line.strip()
            if line and not line.lower().startswith(('email', 'phone', 'address', 'summary', 'experience')):
                contact_full_name = line
                break
    
    # Use provided model or fall back to default
    selected_model = resolve_model(model)
    
    from datetime import datetime
    current_date = datetime.now().strftime('%B %d, %Y')
    
    # Compose the new ATS-friendly cover letter prompt
    prompt = f"""
# ATS-Friendly Cover Letter Generation Prompt

## System Instructions
You are a professional cover letter generator that creates compelling, ATS-friendly cover letters. Your task is to generate personalized cover letters that pass through Applicant Tracking Systems while engaging human recruiters and hiring managers.

## Core Requirements

### ATS Compatibility Rules
- Use simple, readable fonts and formatting
- Include relevant keywords from job descriptions naturally
- Maintain standard business letter structure
- Avoid complex formatting, tables, or graphics
- Use professional language and tone
- Keep length to one page maximum
- Include proper contact information (never use placeholders)
- The entire cover letter must fit on a single A4 page. Trim or summarize content as needed to ensure it does not overflow to a second page.

### Standard Cover Letter Structure
Generate cover letters with these components:
1. **Header** (Applicant contact info, date, recipient info)
2. **Salutation** (Personalized greeting when possible)
3. **Opening Paragraph** (Position, value proposition, hook)
4. **Body Paragraphs** (Experience, achievements, company fit)
5. **Closing Paragraph** (Call to action, gratitude)
6. **Professional Sign-off** (Formal closing and signature)

## Styling and Output Requirements
- Include ALL CSS in a <style> tag in the <head> of the HTML document
- Use minimal padding and margin for both screen and print
- Use print-friendly, ATS-friendly, and professional design
- The <body> and main containers should have padding: 0 and margin: 0
- The design must be clean, readable, and mobile-responsive

## Input Parameters

When generating a cover letter, use these parameters:
- applicant_name: {contact_full_name or ''}
- applicant_contact: {contact_email or ''}, {contact_phone or ''}, {contact_address or ''}, {contact_city_state_zip or ''}
- job_title: [Extract from job description or CV if possible]
- company_name: [Extract from job description or CV if possible]
- hiring_manager: [If known, else use "Hiring Manager"]
- job_requirements: [Extracted from job description]
- applicant_experience: [Extracted from CV]
- company_research: [Extracted from job description or placeholder]
- tone: {tone}

## Content Generation Guidelines

### Opening Paragraph (Hook + Value)
- State the specific position title
- Mention how you learned about the opportunity (if relevant)
- Include a compelling achievement or qualification
- Show immediate value proposition in 2-3 sentences

### Body Paragraph 1 (Experience & Achievements)
- Highlight most relevant experience for the role
- Include 2-3 specific, quantified achievements
- Use action verbs and metrics (percentages, dollar amounts, numbers)
- Connect experience directly to job requirements

### Body Paragraph 2 (Company Fit & Enthusiasm)
- Demonstrate knowledge of the company
- Explain why you're interested in this specific role/company
- Show cultural fit and alignment with company values
- Connect your skills to company goals or recent developments

### Closing Paragraph (Call to Action)
- Reiterate interest and enthusiasm
- Request interview or next steps
- Thank them for their consideration
- Professional and confident tone

## Output Format
Generate the cover letter as a complete HTML document with:
- <!DOCTYPE html>, <html>, <head>, and <body> tags
- ALL CSS in a <style> tag in the <head>
- Minimal padding and margin for both screen and print
- Print-friendly, ATS-friendly, and professional design
- Clean, readable layout
- Mobile-responsive structure

## Example Cover Letter (Reference Only)
Use the following example as a reference for structure, style, and best practices (do not copy content):

<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
    <title>Professional Cover Letter Example</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        html, body {{
            width: 210mm;
            height: 297mm;
            overflow: hidden;
        }}
        body {{
            font-family: 'Georgia', 'Times New Roman', serif;
            line-height: 1.6;
            color: #333;
            background: #f8f9fa;
            padding: 20px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: white;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            border-radius: 8px;
            overflow: hidden;
        }}
        .cover-letter {{
            padding: 60px;
            font-size: 11pt;
            line-height: 1.8;
        }}
        .header {{
            margin-bottom: 40px;
        }}
        .applicant-info {{
            text-align: right;
            margin-bottom: 30px;
        }}
        .name {{
            font-size: 16pt;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 5px;
        }}
        .contact-info {{
            font-size: 10pt;
            color: #666;
            line-height: 1.4;
        }}
        .date {{
            text-align: right;
            margin-bottom: 30px;
            color: #666;
        }}
        .recipient-info {{
            margin-bottom: 30px;
        }}
        .recipient-info div {{
            margin-bottom: 3px;
        }}
        .salutation {{
            margin-bottom: 20px;
            font-weight: 500;
        }}
        .body-paragraph {{
            margin-bottom: 20px;
            text-align: justify;
        }}
        .body-paragraph:first-of-type {{
            margin-bottom: 25px;
        }}
        .closing {{
            margin-top: 30px;
            margin-bottom: 15px;
        }}
        .signature {{
            margin-top: 40px;
            font-weight: 500;
        }}
        .highlight {{
            font-weight: 600;
            color: #2c3e50;
        }}
        .tips-section {{
            background: #e8f4f8;
            padding: 30px;
            border-top: 1px solid #dee2e6;
        }}
        .tips-title {{
            font-size: 18pt;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 20px;
            text-align: center;
        }}
        .tips-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 25px;
            margin-top: 20px;
        }}
        .tip-box {{
            background: white;
            padding: 20px;
            border-radius: 6px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .tip-title {{
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 10px;
            font-size: 12pt;
        }}
        .tip-content {{
            font-size: 10pt;
            color: #555;
            line-height: 1.6;
        }}
        .tip-content ul {{
            margin-left: 15px;
            margin-top: 8px;
        }}
        .tip-content li {{
            margin-bottom: 4px;
        }}
        @media (max-width: 768px) {{
            .cover-letter {{
                padding: 30px;
            }}
            
            .tips-section {{
                padding: 20px;
            }}
            
            .tips-grid {{
                grid-template-columns: 1fr;
            }}
        }}
        @media print {{
            html, body {{
                width: 210mm;
                height: 297mm;
                overflow: hidden;
                font-size: 12px;
            }}
            .cover-letter {{
                page-break-inside: avoid;
            }}
        }}
        @page {{
            size: A4;
            margin: 0.5in;
            overflow: hidden;
        }}
    </style>
</head>
<body>
    <div class=\"container\">
        <div class=\"cover-letter\">
            <div class=\"header\">
                <div class=\"applicant-info\">
                    <div class=\"name\">Sarah Johnson</div>
                    <div class=\"contact-info\">
                        sarah.johnson@email.com<br>
                        (555) 123-4567<br>
                        linkedin.com/in/sarahjohnson<br>
                        New York, NY 10001
                    </div>
                </div>
                
                <div class=\"date\">July 14, 2025</div>
                
                <div class=\"recipient-info\">
                    <div><strong>Ms. Jennifer Chen</strong></div>
                    <div>Hiring Manager</div>
                    <div>Tech Innovations Inc.</div>
                    <div>123 Business Avenue</div>
                    <div>New York, NY 10002</div>
                </div>
            </div>
            
            <div class=\"salutation\">Dear Ms. Chen,</div>
            
            <div class=\"body-paragraph\">
                I am writing to express my strong interest in the <span class=\"highlight\">Senior Marketing Manager</span> position at Tech Innovations Inc. With over five years of progressive marketing experience and a proven track record of developing campaigns that increased brand awareness by 40% and generated $2M in revenue, I am confident that my skills and passion for innovative marketing strategies make me an ideal candidate for your team.
            </div>
            
            <div class=\"body-paragraph\">
                In my current role as Marketing Manager at Tech Solutions Inc., I have successfully led cross-functional teams of eight professionals to develop and execute integrated marketing campaigns that resulted in a 60% increase in qualified leads. My expertise in digital marketing, combined with my ability to analyze market trends and consumer behavior, has enabled me to implement marketing automation platforms that improved conversion rates by 25%. Additionally, I managed a $500K annual marketing budget while achieving a 15% cost reduction and significantly improving ROI.
            </div>
            
            <div class=\"body-paragraph\">
                What particularly excites me about Tech Innovations Inc. is your commitment to cutting-edge technology solutions and your reputation for fostering innovation in the industry. Your recent launch of the AI-powered customer analytics platform aligns perfectly with my experience in data-driven marketing strategies. I am eager to bring my skills in SEO optimization, content strategy, and marketing automation to help drive your company's continued growth and market leadership.
            </div>
            
            <div class=\"body-paragraph\">
                I would welcome the opportunity to discuss how my experience in developing high-impact marketing campaigns and my passion for technological innovation can contribute to Tech Innovations Inc.'s continued success. Thank you for considering my application. I look forward to hearing from you soon.
            </div>
            
            <div class=\"closing\">Sincerely,</div>
            <div class=\"signature\">Sarah Johnson</div>
        </div>
        
        <div class=\"tips-section\">
            <div class=\"tips-title\">Cover Letter Best Practices</div>
            
            <div class=\"tips-grid\">
                <div class=\"tip-box\">
                    <div class=\"tip-title\">Structure & Format</div>
                    <div class=\"tip-content\">
                        <ul>
                            <li>Keep to one page maximum</li>
                            <li>Use professional font (11-12pt)</li>
                            <li>Include proper business letter formatting</li>
                            <li>Address to specific person when possible</li>
                            <li>Use formal but engaging tone</li>
                        </ul>
                    </div>
                </div>
                
                <div class=\"tip-box\">
                    <div class=\"tip-title\">Opening Paragraph</div>
                    <div class=\"tip-content\">
                        <ul>
                            <li>State the specific position you're applying for</li>
                            <li>Mention how you learned about the opportunity</li>
                            <li>Include a compelling hook or achievement</li>
                            <li>Show immediate value proposition</li>
                        </ul>
                    </div>
                </div>
                
                <div class=\"tip-box\">
                    <div class=\"tip-title\">Body Paragraphs</div>
                    <div class=\"tip-content\">
                        <ul>
                            <li>Use specific examples and quantified achievements</li>
                            <li>Connect your experience to job requirements</li>
                            <li>Research the company and show knowledge</li>
                            <li>Demonstrate cultural fit and enthusiasm</li>
                            <li>Focus on what you can contribute, not what you want</li>
                        </ul>
                    </div>
                </div>
                
                <div class=\"tip-box\">
                    <div class=\"tip-title\">Closing</div>
                    <div class=\"tip-content\">
                        <ul>
                            <li>Reiterate interest and enthusiasm</li>
                            <li>Request an interview or next steps</li>
                            <li>Thank them for their consideration</li>
                            <li>Use professional closing (Sincerely, Best regards)</li>
                            <li>Include your typed name</li>
                        </ul>
                    </div>
                </div>
                
                <div class=\"tip-box\">
                    <div class=\"tip-title\">Content Tips</div>
                    <div class=\"tip-content\">
                        <ul>
                            <li>Customize for each application</li>
                            <li>Use keywords from job description</li>
                            <li>Tell a story that complements your resume</li>
                            <li>Show personality while maintaining professionalism</li>
                            <li>Proofread carefully for errors</li>
                        </ul>
                    </div>
                </div>
                
                <div class=\"tip-box\">
                    <div class=\"tip-title\">Common Mistakes to Avoid</div>
                    <div class=\"tip-content\">
                        <ul>
                            <li>Generic, one-size-fits-all letters</li>
                            <li>Repeating resume information exactly</li>
                            <li>Focusing too much on what you want</li>
                            <li>Using overly casual language</li>
                            <li>Exceeding one page length</li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
  
  ## Input Data
Job Description:
{job_desc_extracted_text}

CV Content:
{cv_extracted_text}

## Output Requirements
- Return ONLY the complete HTML document, no explanations or additional text.
- The entire cover letter must fit on a single A4 page. Trim or summarize content as needed to ensure it does not overflow to a second page.
"""
    
    try:
        html_content = chat_completion(
            selected_model, prompt, temperature=0.8, max_tokens=3000)
        
        # Clean up the HTML content
        if html_content.startswith('```html'):
            html_content = html_content[7:]
        if html_content.endswith('```'):
            html_content = html_content[:-3]
        html_content = html_content.strip()
        
        response_data = {
            'message': 'Cover letter generated successfully',
            'html_content': html_content,
            'model_used': selected_model,
            'contact_info_used': {
                'full_name': contact_full_name,
                'address': contact_address,
                'city_state_zip': contact_city_state_zip,
                'email': contact_email,
                'phone': contact_phone
            }
        }
        
        if generate_pdf:
            try:
                from utils import convert_html_to_pdf
                pdf_content = convert_html_to_pdf(html_content, "cover_letter.pdf", pdf_margin="0.4in")
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as pdf_file:
                    pdf_file.write(pdf_content)
                    pdf_file_path = pdf_file.name
                return FileResponse(
                    pdf_file_path,
                    media_type='application/pdf',
                    filename=f"cover_letter.pdf",
                    background=None
                )
            except Exception as e:
                response_data['pdf_error'] = f'PDF generation failed: {str(e)}'
        
        return JSONResponse(response_data)
        
    except OpenRouterError as e:
        return JSONResponse({'error': f'API request failed: {str(e)}'}, status_code=500)
    except Exception as e:
        return JSONResponse({'error': f'Cover letter generation failed: {str(e)}'}, status_code=500)

@app.post('/analyze-cv')
def analyze_cv(
    cv_file: Optional[UploadFile] = File(None),
    cv_text: Optional[str] = Form(None),
    job_desc_file: Optional[UploadFile] = File(None),
    job_desc_text: Optional[str] = Form(None),
    model: str = Form(None)
):
    # Read the CV and job description from either an upload or raw text
    try:
        cv_extracted_text = extract_document_text(cv_file, cv_text, 'CV')
        job_desc_extracted_text = extract_document_text(
            job_desc_file, job_desc_text, 'job description')
    except DocumentError as e:
        return JSONResponse({'error': e.message}, status_code=e.status_code)
    
    # Use provided model or fall back to default
    selected_model = resolve_model(model)
    
    # Create prompt for comprehensive CV analysis with JSON output
    prompt = f"""
    You are an advanced CV analysis engine. Analyze the following CV against the provided job description and return a detailed JSON object with:
    - ats_score: score out of 100
    - keyword_match_percentage: percentage
    - missing_keywords: [list of missing keywords]
    - strengths: [list of strengths]
    - weaknesses: [list of weaknesses]
    - skills_gap: [list of missing skills]
    - formatting_issues: [list of formatting issues]
    - recommendations: [list of improvement recommendations]
    - overall_assessment: brief overall assessment
    
    Job Description:
    {job_desc_extracted_text}
    
    CV Content:
    {cv_extracted_text}
    
    Return ONLY the JSON object, no explanations or additional text.
    """
    
    try:
        analysis_content = chat_completion(
            selected_model, prompt, temperature=0.3,
            max_tokens=ANALYSIS_MAX_TOKENS)

        # Here the analysis IS the deliverable, so an unparseable reply is an error
        # rather than something to continue past.
        analysis_json = parse_model_json(analysis_content)
        if analysis_json is None:
            logger.warning("analysis JSON unparseable model=%s length=%d",
                           selected_model, len(analysis_content or ''))
            return JSONResponse(
                {'error': 'The model did not return a usable analysis. Please retry.'},
                status_code=502
            )

        return JSONResponse({
            'message': 'CV analysis completed successfully',
            'analysis': analysis_json,
            'model_used': selected_model
        })
        
    except OpenRouterError as e:
        return JSONResponse({'error': f'API request failed: {str(e)}'}, status_code=500)
    except Exception as e:
        return JSONResponse({'error': f'Analysis failed: {str(e)}'}, status_code=500)

@app.get('/')
def read_root():
    return {"message": "CV Maker API - AI-Powered Resume and Cover Letter Generator"}

@app.get('/docs')
def get_docs():
    return {"message": "API documentation available at /docs endpoint"}

@app.get('/health', response_class=JSONResponse)
def health_check():
    """Report service health and the state of its two external dependencies."""
    checks = {
        "openrouter": {
            "configured": bool(OPENROUTER_API_KEY),
            "base_url": OPENROUTER_BASE_URL
        },
        "gotenberg": {
            "url": GOTENBERG_URL,
            "reachable": False
        }
    }

    # Gotenberg is optional, so a failure here degrades rather than fails.
    try:
        auth = None
        if GOTENBERG_USERNAME and GOTENBERG_PASSWORD:
            auth = (GOTENBERG_USERNAME, GOTENBERG_PASSWORD)
        gotenberg_response = requests.get(f"{GOTENBERG_URL}/health", auth=auth, timeout=3)
        checks['gotenberg']['reachable'] = gotenberg_response.status_code == 200
    except Exception as e:
        checks['gotenberg']['error'] = str(e)

    # Without an API key no generation endpoint can work, so that alone is fatal.
    if not checks['openrouter']['configured']:
        status = "unhealthy"
        status_code = 503
    elif not checks['gotenberg']['reachable']:
        status = "degraded"  # generation works, PDF conversion does not
        status_code = 200
    else:
        status = "healthy"
        status_code = 200

    return JSONResponse({
        "status": status,
        "service": "cv-maker-api",
        "default_model": DEFAULT_MODEL,
        "available_models": len(AVAILABLE_MODELS),
        "checks": checks
    }, status_code=status_code)

# Add a new endpoint to get available models
@app.get("/available-models", response_class=JSONResponse)
def get_available_models():
    return {
        "models": list(AVAILABLE_MODELS.keys()),
        "default": next(
            (key for key, m in AVAILABLE_MODELS.items() if m['id'] == DEFAULT_MODEL),
            None
        ),
        "details": AVAILABLE_MODELS
    }
 