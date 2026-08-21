import json
import logging
import re
import tempfile
import os
import requests
import pytz
from html import escape, unescape
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from PyPDF2 import PdfReader
from docx import Document
from config import (
    GOTENBERG_URL,
    GOTENBERG_TIMEOUT,
    GOTENBERG_USERNAME,
    GOTENBERG_PASSWORD,
    MAX_UPLOAD_BYTES,
)

logger = logging.getLogger(__name__)


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

    Whatever the source, the result has to contain something. Empty input used to
    be passed on to the model, which answered by inventing a whole CV — two paid
    calls to produce a document about nobody.
    """
    if upload_file:
        _check_size(upload_file, label)
        filename = upload_file.filename or ''
        # Case-folded: phones and Windows both hand over "CV.PDF" routinely, and
        # a straight endswith rejected those as an unsupported type.
        extension = os.path.splitext(filename)[1].lower()
        if extension == '.pdf':
            pdf_reader = PdfReader(upload_file.file)
            text = " ".join(page.extract_text() or '' for page in pdf_reader.pages)
            if not text.strip():
                raise DocumentError(
                    f'No text could be read from the {label} PDF. If it is a scan '
                    f'or an image, send a text-based PDF, DOCX or TXT instead'
                )
            return text
        elif extension == '.docx':
            doc = Document(upload_file.file)
            text = " ".join([para.text for para in doc.paragraphs])
        elif extension == '.txt':
            text = upload_file.file.read().decode('utf-8', errors='replace')
        else:
            raise DocumentError(
                f'Unsupported {label} file type. Use PDF, DOCX, or TXT'
            )
        if not text.strip():
            raise DocumentError(f'The {label} file is empty')
        return text
    elif fallback_text and fallback_text.strip():
        return fallback_text
    else:
        raise DocumentError(f'No {label} provided (file or text)')

# "+05:30", "-08:00", "+0530", "UTC+13", "GMT-5" — the shapes a browser or a
# hand-written client is likely to send when it does not know its IANA name.
_UTC_OFFSET_RE = re.compile(
    r'^(?:UTC|GMT)?(?P<sign>[+-])(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?$',
    re.IGNORECASE,
)


def resolve_timezone(name):
    """Return a tzinfo for an IANA name or a UTC offset, or None if unusable.

    Anything unrecognised returns None rather than raising: a mistyped timezone
    should cost the caller a slightly wrong date, not a failed generation.
    """
    name = ' '.join((name or '').split())
    if not name:
        return None

    offset = _UTC_OFFSET_RE.match(name)
    if offset:
        minutes = int(offset.group('hours')) * 60 + int(offset.group('minutes') or 0)
        if minutes > 18 * 60:  # no real zone is further out than UTC+14
            return None
        if offset.group('sign') == '-':
            minutes = -minutes
        return timezone(timedelta(minutes=minutes))

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


# Headers that carry a timezone outright. Every major edge sets one of these, so
# a deployment behind a CDN gets the applicant's zone without the client helping.
_TIMEZONE_HEADERS = (
    'x-timezone',                    # our own, if the frontend sets it
    'x-vercel-ip-timezone',
    'cloudfront-viewer-time-zone',
    'cf-timezone',                   # Cloudflare, Enterprise plans
)

# Headers carrying an ISO country code, the next best thing.
_COUNTRY_HEADERS = (
    'cf-ipcountry',
    'x-vercel-ip-country',
    'cloudfront-viewer-country',
    'x-appengine-country',
    'x-country-code',
)

# Cloudflare's stand-ins for "no idea" and "Tor exit node".
_UNKNOWN_COUNTRIES = {'XX', 'T1'}

# pytz lists a country's zones in zone.tab order, which is not population order:
# Australia comes out as Lord Howe Island and Brazil as Fernando de Noronha. For
# countries wide enough for that to matter, name the zone most applicants live in.
_COUNTRY_OVERRIDES = {
    'AU': 'Australia/Sydney',
    'BR': 'America/Sao_Paulo',
    'CA': 'America/Toronto',
    'CD': 'Africa/Kinshasa',
    'CL': 'America/Santiago',
    'EC': 'America/Guayaquil',
    'ES': 'Europe/Madrid',
    'ID': 'Asia/Jakarta',
    'KZ': 'Asia/Almaty',
    'MX': 'America/Mexico_City',
    'MY': 'Asia/Kuala_Lumpur',
    'PT': 'Europe/Lisbon',
    'RU': 'Europe/Moscow',
    'UA': 'Europe/Kyiv',
    'US': 'America/New_York',
    'ZA': 'Africa/Johannesburg',
}


def timezone_from_country(code):
    """Best-effort zone for an ISO 3166-1 alpha-2 code, or None.

    Most countries have exactly one zone, so this is exact for them. For the rest
    it is an approximation, which still beats UTC: the date only differs from a
    neighbouring zone's for the few hours either side of midnight.
    """
    code = (code or '').strip().upper()
    if len(code) != 2 or code in _UNKNOWN_COUNTRIES:
        return None
    if code in _COUNTRY_OVERRIDES:
        return _COUNTRY_OVERRIDES[code]
    try:
        zones = pytz.country_timezones(code)
    except KeyError:
        return None
    return zones[0] if zones else None


def timezone_from_accept_language(value):
    """Zone implied by the region subtag of an Accept-Language header, or None.

    "en-NG,en;q=0.9" means the browser is set to Nigerian English, which is a
    weaker signal than an IP country — a Nigerian in London still sends it — but
    it is the last thing available before falling back to a fixed default.
    """
    for tag in (value or '').split(','):
        tag = tag.split(';')[0].strip()
        parts = tag.replace('_', '-').split('-')
        for part in parts[1:]:
            if len(part) == 2 and part.isalpha():
                zone = timezone_from_country(part)
                if zone:
                    return zone
    return None


def infer_timezone(headers=None, explicit=None):
    """Work out the applicant's timezone from whatever the request reveals.

    Returns (zone_name_or_None, source). Ordered most to least trustworthy: what
    the caller stated, what the CDN observed, the country it observed, then the
    browser's language region. None means nothing usable was found and the
    configured default should stand.
    """
    if resolve_timezone(explicit):
        return ' '.join(explicit.split()), 'request'

    lookup = {}
    if headers:
        items = headers.items() if hasattr(headers, 'items') else headers
        lookup = {str(k).lower(): v for k, v in items}

    for header in _TIMEZONE_HEADERS:
        if resolve_timezone(lookup.get(header)):
            return ' '.join(lookup[header].split()), header

    for header in _COUNTRY_HEADERS:
        zone = timezone_from_country(lookup.get(header))
        if zone:
            return zone, header

    zone = timezone_from_accept_language(lookup.get('accept-language'))
    if zone:
        return zone, 'accept-language'

    return None, 'default'


def today_in_timezone(name, fallback=None):
    """Today's date where the applicant is, formatted for a letter head.

    `name` wins, `fallback` (the configured default) is tried next, and UTC is
    the last resort. The day is composed rather than strftime'd because the
    padding-free directive for it differs between platforms.
    """
    tz = resolve_timezone(name) or resolve_timezone(fallback) or timezone.utc
    today = datetime.now(tz)
    return f"{today:%B} {today.day}, {today:%Y}"


# An element carrying a class attribute, plus its contents up to its own closing
# tag. The body rules out a nested open or close of the same tag, so the match
# stays on the innermost element rather than swallowing a sibling's markup.
_CLASSED_ELEMENT_RE = re.compile(
    r'(?P<open><(?P<tag>[a-zA-Z][\w:-]*)\b[^>]*?'
    r'class\s*=\s*(?P<q>["\'])(?P<cls>[^"\']*)(?P=q)[^>]*>)'
    r'(?P<body>(?:(?!</?(?P=tag)\b).)*?)'
    r'(?P<close></(?P=tag)\s*>)',
    re.IGNORECASE | re.DOTALL,
)


def enforce_letter_date(html_content: str, date_text: str):
    """Overwrite the letter's date element with `date_text`.

    The prompt pins the date too, but that only makes the right answer likely —
    the model still owns the output. Rewriting it here makes it certain, which
    matters because a wrong date on a cover letter is the kind of error a
    recruiter notices and the applicant never sees.

    Returns (html, replacements). Zero replacements means the model laid the
    header out without a `class="date"` element and the text is left untouched;
    there is no way to tell a date apart from the rest of the prose without
    guessing, and a bad guess would corrupt the letter.
    """
    if not html_content or not date_text:
        return html_content, 0

    replaced = 0

    def rewrite(match):
        nonlocal replaced
        # Exact class token, so `date-location` and `update` are left alone.
        if 'date' not in match.group('cls').split():
            return match.group(0)
        replaced += 1
        return match.group('open') + escape(date_text) + match.group('close')

    return _CLASSED_ELEMENT_RE.sub(rewrite, html_content), replaced


# Contact details as they appear in generated markup. An address or a city line
# has no shape to match on, so only these two are machine-checkable — the rest
# stay the prompt's job.
_EMAIL_RE = re.compile(r'[\w.+%-]+@[\w-]+(?:\.[\w-]+)+')
# A phone number as people write them, brackets and country code included. The
# separator class is deliberately narrow, and the digit count is checked after
# matching, so a year range or a street number does not pass for a number.
_PHONE_RE = re.compile(r'[+(]?\d[\d\s().+/–—-]{5,20}\d')
_MIN_PHONE_DIGITS, _MAX_PHONE_DIGITS = 7, 15
# "2019 - 2021" clears the digit count but is a date; headers that put a span of
# years next to the contact line should not have it overwritten.
_YEAR_RANGE_RE = re.compile(r'^\s*(?:19|20)\d{2}\s*[/–—-]\s*(?:19|20)\d{2}\s*$')

# The pieces a contact line is built from, and the "•" or "|" spans that some
# styles put between them.
_SPAN_RE = re.compile(r'<span\b[^>]*>(?:(?!</?span\b).)*?</span>',
                      re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r'<[^>]+>')
_SEPARATOR_ONLY_RE = re.compile(r'^[\s·•–—|/,;.\-]*$')


def _phone_digits(text):
    return ''.join(ch for ch in text if ch.isdigit())


def _find_phones(text):
    """Phone-shaped runs in `text`, minus the ones that only look like one."""
    return [m.group(0) for m in _PHONE_RE.finditer(text)
            if _MIN_PHONE_DIGITS <= len(_phone_digits(m.group(0))) <= _MAX_PHONE_DIGITS
            and not _YEAR_RANGE_RE.match(m.group(0))]


def _find_emails(text):
    return [m.group(0) for m in _EMAIL_RE.finditer(text)]


def _same_phone(a, b):
    """True if two written forms plausibly denote the same number.

    Compared on digits alone and from the right, because the same number is
    written "+44 7700 900123" in one field and "07700 900123" in the other.
    """
    a, b = _phone_digits(a), _phone_digits(b)
    if not a or not b:
        return False
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= _MIN_PHONE_DIGITS and longer.endswith(shorter)


def _same_email(a, b):
    return a.strip().lower() == b.strip().lower()


_CONTACT_KINDS = {
    'email': (_find_emails, _same_email),
    'phone': (_find_phones, _same_phone),
}


def _element_text(html):
    """Visible text of a markup fragment, for shape-matching against it."""
    return unescape(_TAG_RE.sub(' ', html))


def _contact_items(body):
    """The contact line split into its parts: spans if it uses them, else all."""
    spans = list(_SPAN_RE.finditer(body))
    if spans:
        return [(m.start(), m.end()) for m in spans]
    return [(0, len(body))]


def _tidy_separators(body):
    """Drop "•" spans left stranded by a removal, at either end or doubled up."""
    spans = list(_SPAN_RE.finditer(body))
    if not spans:
        return body
    is_sep = [bool(_SEPARATOR_ONLY_RE.match(_element_text(m.group(0))))
              for m in spans]
    drop = set()
    seen_value = False
    for i, sep in enumerate(is_sep):
        if not sep:
            seen_value = True
            continue
        if not seen_value or (i - 1) in drop or (i > 0 and is_sep[i - 1]):
            drop.add(i)
    for i in range(len(is_sep) - 1, -1, -1):  # a trailing run of separators
        if not is_sep[i]:
            break
        drop.add(i)
    if not drop:
        return body
    out, cursor = [], 0
    for i, match in enumerate(spans):
        if i in drop:
            out.append(body[cursor:match.start()])
            cursor = match.end()
    out.append(body[cursor:])
    return ''.join(out)


def _append_contact(body, value):
    """Add a missing detail to the end of the contact line, in its own style."""
    spans = list(_SPAN_RE.finditer(body))
    addition = f'<span>{escape(value)}</span>'
    if not spans:
        return body + addition
    separator = next(
        (m.group(0) for m in spans
         if _SEPARATOR_ONLY_RE.match(_element_text(m.group(0)))
         and _element_text(m.group(0)).strip()),
        '')
    end = spans[-1].end()
    return body[:end] + separator + addition + body[end:]


def _rewrite_contact_line(body, supplied, from_source, report):
    """Correct one contact element in place. See enforce_contact_details."""
    for kind, (find_all, same) in _CONTACT_KINDS.items():
        value = supplied.get(kind)
        if not value:
            # Nothing to check against. Whatever the model wrote came from the
            # CV or from nowhere, and deleting it on that suspicion would be a
            # worse failure than leaving it: the evidence is text extracted from
            # a PDF, which mangles a number often enough not to trust.
            continue

        found = []  # (start, end, written form) per part of the line
        for start, end in _contact_items(body):
            written = find_all(_element_text(body[start:end]))
            if written:
                found.append((start, end, written[0].strip()))

        if not found:
            body = _append_contact(body, value)
            report[kind] = 'added'
            continue
        if any(same(written, value) for _, _, written in found):
            report[kind] = 'unchanged'  # already correct; leave the markup be
            continue

        # Correct the detail the model was least likely to have got from the CV,
        # which is where a placeholder shows up. Other parts of the line — a
        # LinkedIn URL, a city, a second real number — are never touched.
        stale = [item for item in found
                 if not any(same(item[2], known) for known in from_source[kind])]
        target = (stale or found)[0]
        # Repeats of the very value being corrected are the same mistake twice.
        duplicates = [item for item in found
                      if item is not target and same(item[2], target[2])]

        rebuilt, cursor = [], 0
        for item in found:
            if item is not target and item not in duplicates:
                continue
            start, end, written = item
            rebuilt.append(body[cursor:start])
            if item is target:
                rebuilt.append(body[start:end].replace(written, escape(value), 1))
            cursor = end
        rebuilt.append(body[cursor:])
        body = ''.join(rebuilt)
        if duplicates:
            body = _tidy_separators(body)
        report[kind] = 'corrected'
    return body


def enforce_contact_details(html_content: str, supplied: dict, source_text: str = ''):
    """Make the CV header's email and phone say what the applicant said.

    The prompt asks for this too, but the same prompt tells the model to rewrite
    rather than copy the source, and the header gets caught by that: a supplied
    phone number comes back as a stock "0123456789" often enough that callers
    reported it as the field being ignored. Rewriting the value here makes it
    certain, the way enforce_letter_date does for the date.

    Only `supplied` details with a recognisable shape are touched — an address
    or a city has nothing to match on and stays the prompt's job. A supplied
    value replaces what the model wrote and is appended if it wrote none.
    `source_text` is the applicant's own CV, used only to tell a placeholder
    apart from a detail the CV really carries when the header holds more than
    one of a kind.

    Returns (html, report), where report maps 'email'/'phone' to what happened
    ('unchanged', 'corrected' or 'added') and omits a kind the caller left
    blank. An empty report means there was nothing to enforce, or the model
    produced no `class="contact-info"` element to enforce it in.
    """
    supplied = {k: v.strip() for k, v in (supplied or {}).items() if v and v.strip()}
    if not html_content or not supplied:
        return html_content, {}

    from_source = {
        kind: find_all(source_text or '')
        for kind, (find_all, _) in _CONTACT_KINDS.items()
    }
    report = {}

    def rewrite(match):
        if 'contact-info' not in match.group('cls').split():
            return match.group(0)
        body = _rewrite_contact_line(
            match.group('body'), supplied, from_source, report)
        return match.group('open') + body + match.group('close')

    return _CLASSED_ELEMENT_RE.sub(rewrite, html_content), report


def convert_html_to_pdf(html_content: str, filename: str = "document.pdf", pdf_margin: str = "0in") -> bytes:
    """Convert HTML content to PDF using Gotenberg API.

    `pdf_margin` is a CSS length applied on all four sides. It reaches the page
    through the injected @page rule, since preferCssPageSize makes Chromium take
    the CSS over the form fields; the form fields are sent to match so the two
    cannot disagree.
    """
    html_file_path = None
    try:
        html_content = html_content.strip()
        # Only inject @page for margins/size, do not override any other CSS
        page_css = f"""
        @page {{
            margin: {pdf_margin};
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
            data = {
                'marginTop': pdf_margin,
                'marginBottom': pdf_margin,
                'marginLeft': pdf_margin,
                'marginRight': pdf_margin,
                'format': 'A4',
                'preferCssPageSize': 'true'
            }
            # Without a timeout a hung Gotenberg holds this worker thread for the
            # life of the process, the same way the model calls used to.
            gotenberg_response = requests.post(
                f"{GOTENBERG_URL}/forms/chromium/convert/html",
                files=files,
                data=data,
                auth=auth,
                timeout=GOTENBERG_TIMEOUT
            )
        if gotenberg_response.status_code == 200:
            return gotenberg_response.content
        else:
            raise Exception(f"PDF generation failed: {gotenberg_response.text}")
    except requests.exceptions.Timeout:
        raise Exception(f"PDF conversion timed out after {GOTENBERG_TIMEOUT}s")
    except Exception as e:
        raise Exception(f"PDF conversion failed: {str(e)}")
    finally:
        # The unlink used to sit on the success path only, so every failed
        # conversion left its HTML behind in the container.
        if html_file_path:
            try:
                os.unlink(html_file_path)
            except OSError:
                logger.warning("could not remove temporary file %s", html_file_path)
