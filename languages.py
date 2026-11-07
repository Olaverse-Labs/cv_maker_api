"""The `language` request parameter: which language a generated document is in.

A caller passes a BCP 47 tag ("fr", "de-CH", "ar"). Babel's copy of the CLDR
locale data is what decides whether the tag is real, what the language is
called, which way it is written and how a date is spelled in it, so a new market
needs no code change here — only a tag CLDR knows.
"""
import re
from datetime import date

from babel import Locale, UnknownLocaleError
from babel.dates import format_date

# Letters, digits and separators only, and short: the tag's English name ends up
# in the prompt, but the raw value is never trusted beyond this shape check.
_TAG_RE = re.compile(r'^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8}){0,3}$')

# Regional conventions CLDR's display name alone does not convey to a model.
# Swiss Standard German is the case that bites: it never uses "ß".
_VARIANT_NOTES = {
    'de_CH': 'Use Swiss Standard German spelling: write "ss", never "ß".',
    'de_LI': 'Use Swiss Standard German spelling: write "ss", never "ß".',
    'pt_BR': 'Use Brazilian Portuguese spelling and vocabulary.',
    'pt_PT': 'Use European Portuguese spelling and vocabulary.',
    'en_GB': 'Use British English spelling (e.g. "optimise", "organisation").',
    'en_US': 'Use American English spelling (e.g. "optimize", "organization").',
}


class LanguageError(ValueError):
    """The caller asked for a language we cannot produce. Maps to a 400."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class Language:
    """A resolved output language."""

    def __init__(self, locale: Locale):
        self.locale = locale
        # Canonical BCP 47 form, e.g. "de-CH", for the response and <html lang>.
        self.tag = str(locale).replace('_', '-')
        self.name = locale.get_display_name('en')
        self.rtl = locale.character_order == 'right-to-left'

    def format_date(self, day: date) -> str:
        """The day as a letter head in this language writes it."""
        return format_date(day, 'long', locale=self.locale)

    def prompt_instructions(self, document: str) -> str:
        """The prompt section that puts `document` in this language."""
        lines = [
            '',
            '## Output Language',
            f'Write the entire {document} in {self.name} (language tag `{self.tag}`), '
            'regardless of the language the CV or job description is written in.',
            '- Translate every section heading, label, greeting and sign-off into '
            f'{self.name}, using the headings conventionally used in {self.name}.',
            '- Keep proper nouns as they are: personal names, employer and institution '
            'names, product names, email addresses, phone numbers and URLs.',
            '- Keep job titles and qualifications accurate rather than literal; where a '
            'title has no established equivalent, keep the original.',
            f'- Set `lang="{self.tag}"` on the <html> element.',
        ]
        if self.rtl:
            lines += [
                f'- {self.name} is written right to left. Set `dir="rtl"` on the <html> '
                'element, and lay the page out so it reads right to left: prefer '
                'logical CSS properties (margin-inline-start, text-align: start) over '
                'left/right, and mirror any asymmetric layout.',
            ]
        note = _VARIANT_NOTES.get(str(self.locale))
        if note:
            lines.append(f'- {note}')
        return '\n'.join(lines) + '\n'

    def analysis_instructions(self) -> str:
        """Ask for analysis prose in this language while keys stay machine-readable."""
        return (
            f'\nWrite every string value in {self.name} (language tag `{self.tag}`). '
            'Keep the JSON keys exactly as listed above, in English.\n'
        )


def resolve_language(value):
    """Turn a caller's `language` field into a Language, or None if it is blank.

    Raises LanguageError for a malformed tag or one CLDR does not know, rather
    than quietly generating English for a market that asked for something else.
    """
    if value is None or not value.strip():
        return None
    raw = value.strip()
    if not _TAG_RE.match(raw):
        raise LanguageError(
            f'Invalid language {raw[:20]!r}: use a BCP 47 tag such as "fr", '
            '"de-CH" or "ar".')
    try:
        locale = Locale.parse(raw.replace('-', '_'))
    except (UnknownLocaleError, ValueError):
        raise LanguageError(
            f'Unsupported language {raw!r}: use a BCP 47 tag such as "fr", '
            '"de-CH" or "ar".')
    return Language(locale)
