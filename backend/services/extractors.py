from . import log, POPPLER_PATH
from .utils import clean_text
import os

EXTRACTORS_INFO = []

try:
    import fitz  # PyMuPDF
    EXTRACTORS_INFO.append("PyMuPDF")
except Exception:
    fitz = None

try:
    import pdfplumber
    EXTRACTORS_INFO.append("pdfplumber")
except Exception:
    pdfplumber = None

try:
    from PyPDF2 import PdfReader
    EXTRACTORS_INFO.append("PyPDF2")
except Exception:
    PdfReader = None

try:
    from PIL import Image
    import pytesseract
except Exception:
    Image = None
    pytesseract = None

try:
    from pdf2image import convert_from_bytes
except Exception:
    convert_from_bytes = None

try:
    import docx
except Exception:
    docx = None


def extract_text_from_pdf(path: str) -> str:
    text = ""
    if fitz:
        try:
            with fitz.open(path) as doc:
                text = clean_text("\n".join([p.get_text() or "" for p in doc]))
        except Exception as e:
            log.exception("PyMuPDF extract failed: %s", e)
    if not text and pdfplumber:
        try:
            with pdfplumber.open(path) as pdf:
                text = clean_text("\n".join([p.extract_text() or "" for p in pdf.pages]))
        except Exception as e:
            log.exception("pdfplumber extract failed: %s", e)
    if not text and PdfReader:
        try:
            reader = PdfReader(path)
            text = clean_text("\n".join([(p.extract_text() or "") for p in reader.pages]))
        except Exception as e:
            log.exception("PyPDF2 extract failed: %s", e)
    if text and len(text) >= 40:
        return text
    if convert_from_bytes and pytesseract and Image:
        try:
            with open(path, "rb") as fh:
                pdf_bytes = fh.read()
            pages = convert_from_bytes(pdf_bytes, dpi=300, poppler_path=POPPLER_PATH) \
                    if POPPLER_PATH else convert_from_bytes(pdf_bytes, dpi=300)
            chunks = []
            for im in pages:
                try:
                    chunks.append(pytesseract.image_to_string(im, config="--psm 6") or "")
                except Exception as e:
                    log.exception("OCR page failed: %s", e)
            ocr_text = clean_text("\n".join(chunks))
            if ocr_text: return ocr_text
        except Exception as e:
            log.exception("pdf2image OCR failed: %s", e)
    return text or ""

def extract_text_from_image(path: str) -> str:
    if not (Image and pytesseract): return ""
    try:
        img = Image.open(path).convert("RGB")
        return clean_text(pytesseract.image_to_string(img, config="--psm 6"))
    except Exception as e:
        log.exception("Image OCR failed: %s", e); return ""

def extract_text_from_docx(path: str) -> str:
    if not docx: return ""
    try:
        d = docx.Document(path)
        return clean_text("\n".join(p.text for p in d.paragraphs))
    except Exception as e:
        log.exception("DOCX extract failed: %s", e); return ""

def extract_text_from_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":  return extract_text_from_pdf(path)
    if ext in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        return extract_text_from_image(path)
    if ext == ".docx": return extract_text_from_docx(path)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return clean_text(f.read())
    except Exception:
        return ""
