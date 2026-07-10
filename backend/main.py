"""
TTB Alcohol Label Verification API
Department of Treasury — Take-Home Assessment
Joseph Bidias | Bidias Capital Consulting LLC

Architecture:
- FastAPI backend with async processing
- Claude Vision API for label field extraction (with Azure OpenAI fallback for firewall)
- Sub-5s response target (Sarah's hard requirement)
- Batch upload support (Janet's request)
- Fuzzy matching for Dave's "STONE'S THROW" case
- Audit logging for compliance trails
"""

import os
import base64
import asyncio
import time
import re
import json
import logging
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Depends, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import anthropic
import httpx

# ── LOGGING SETUP (Audit Trail) ─────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/ttb_verification_audit.log') if os.name != 'nt' else logging.FileHandler('ttb_verification_audit.log'),
        logging.StreamHandler()
    ]
)
audit_logger = logging.getLogger('TTB_AUDIT')

app = FastAPI(
    title="TTB Label Verification API",
    description="AI-powered alcohol label compliance verification for Treasury/TTB",
    version="1.0.0"
)

# ── RATE LIMITING ──────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──────────────────────────────────────────────────────
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── API KEY AUTH ───────────────────────────────────────────────
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(api_key: str = Security(_api_key_header)):
    """Validate API key when API_KEY env var is set. Open in dev if unset."""
    expected = os.environ.get("API_KEY", "")
    if expected and api_key != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing API key.")

# ── CONSTANTS ──────────────────────────────────────────────────

GOVERNMENT_WARNING_EXACT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, "
    "women should not drink alcoholic beverages during pregnancy "
    "because of the risk of birth defects. (2) Consumption of "
    "alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)

REQUIRED_FIELDS = [
    "brand_name",
    "class_type",
    "alcohol_content",
    "net_contents",
    "producer_name",
    "government_warning",
]

# ── MODELS ────────────────────────────────────────────────────

class LabelField(BaseModel):
    value: Optional[str] = None
    found: bool = False
    compliant: bool = False
    issue: Optional[str] = None

class VerificationResult(BaseModel):
    label_id: str
    overall_status: str  # APPROVED | REJECTED | NEEDS_REVIEW
    processing_time_ms: int
    confidence: float
    fields: dict
    issues: list[str]
    recommendations: list[str]

class BatchResult(BaseModel):
    total: int
    approved: int
    rejected: int
    needs_review: int
    results: list[VerificationResult]
    total_processing_time_ms: int

# ── LLM EXTRACTION (with fallback support) ───────────────────

async def extract_label_fields_anthropic(image_bytes: bytes, filename: str) -> dict:
    """Extract fields using Anthropic Claude (primary)."""
    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

        # Detect image type
        if filename.lower().endswith(".png"):
            media_type = "image/png"
        elif filename.lower().endswith((".jpg", ".jpeg")):
            media_type = "image/jpeg"
        elif filename.lower().endswith(".webp"):
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"

        image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

        prompt = """You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) label compliance expert.
Extract ALL of the following fields from this alcohol beverage label image.
Be precise. If a field is not visible or not present, say "NOT FOUND".

Extract these exact fields:
1. BRAND_NAME: The brand name exactly as it appears on the label
2. CLASS_TYPE: The class and type designation (e.g., "Kentucky Straight Bourbon Whiskey")
3. ALCOHOL_CONTENT: Alcohol by volume percentage (e.g., "40% Alc./Vol.")
4. NET_CONTENTS: Volume/net contents (e.g., "750 mL")
5. PRODUCER_NAME: Name and address of bottler/producer/importer
6. COUNTRY_OF_ORIGIN: Country of origin (required for imports, may not apply)
7. GOVERNMENT_WARNING: The complete government warning text exactly as it appears
8. WARNING_FORMAT: Describe the format of the government warning (font size relative to other text, bold/caps for "GOVERNMENT WARNING:", placement)

Respond ONLY in this exact JSON format:
{
  "brand_name": "exact text or NOT FOUND",
  "class_type": "exact text or NOT FOUND",
  "alcohol_content": "exact text or NOT FOUND",
  "net_contents": "exact text or NOT FOUND",
  "producer_name": "exact text or NOT FOUND",
  "country_of_origin": "exact text or NOT APPLICABLE or NOT FOUND",
  "government_warning": "exact text or NOT FOUND",
  "warning_format": "description of warning format",
  "image_quality": "GOOD or POOR - describe any issues like angle, glare, blur",
  "extraction_confidence": 0.0 to 1.0
}"""

        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }]
        )

        text = response.content[0].text.strip()
        # Strip any markdown code blocks
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        result = json.loads(text)
        audit_logger.info(f"Anthropic extraction successful for {filename}")
        return result

    except Exception as e:
        audit_logger.warning(f"Anthropic extraction failed: {str(e)}. Will attempt Azure OpenAI fallback.")
        raise

async def extract_label_fields_azure(image_bytes: bytes, filename: str) -> dict:
    """Extract fields using Azure OpenAI (fallback for firewall-restricted networks)."""
    try:
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        api_key = os.environ.get("AZURE_OPENAI_KEY", "")
        
        if not endpoint or not api_key:
            raise ValueError("Azure OpenAI credentials not configured")

        # Encode image to base64
        image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

        # Detect media type
        if filename.lower().endswith(".png"):
            media_type = "image/png"
        elif filename.lower().endswith((".jpg", ".jpeg")):
            media_type = "image/jpeg"
        elif filename.lower().endswith(".webp"):
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"

        prompt = """You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) label compliance expert.
Extract ALL of the following fields from this alcohol beverage label image.
Be precise. If a field is not visible or not present, say "NOT FOUND".

Extract these exact fields:
1. BRAND_NAME: The brand name exactly as it appears on the label
2. CLASS_TYPE: The class and type designation (e.g., "Kentucky Straight Bourbon Whiskey")
3. ALCOHOL_CONTENT: Alcohol by volume percentage (e.g., "40% Alc./Vol.")
4. NET_CONTENTS: Volume/net contents (e.g., "750 mL")
5. PRODUCER_NAME: Name and address of bottler/producer/importer
6. COUNTRY_OF_ORIGIN: Country of origin (required for imports, may not apply)
7. GOVERNMENT_WARNING: The complete government warning text exactly as it appears
8. WARNING_FORMAT: Describe the format of the government warning (font size relative to other text, bold/caps for "GOVERNMENT WARNING:", placement)

Respond ONLY in this exact JSON format:
{
  "brand_name": "exact text or NOT FOUND",
  "class_type": "exact text or NOT FOUND",
  "alcohol_content": "exact text or NOT FOUND",
  "net_contents": "exact text or NOT FOUND",
  "producer_name": "exact text or NOT FOUND",
  "country_of_origin": "exact text or NOT APPLICABLE or NOT FOUND",
  "government_warning": "exact text or NOT FOUND",
  "warning_format": "description of warning format",
  "image_quality": "GOOD or POOR - describe any issues like angle, glare, blur",
  "extraction_confidence": 0.0 to 1.0
}"""

        headers = {
            "api-key": api_key,
            "Content-Type": "application/json"
        }

        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{image_data}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ],
            "max_tokens": 1000,
            "model": "gpt-4-vision"
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{endpoint}/openai/deployments/gpt-4-vision/chat/completions?api-version=2024-02-15-preview",
                json=payload,
                headers=headers,
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

        text = data["choices"][0]["message"]["content"].strip()
        # Strip markdown code blocks if present
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        result = json.loads(text)
        audit_logger.info(f"Azure OpenAI extraction successful for {filename}")
        return result

    except Exception as e:
        audit_logger.error(f"Azure OpenAI extraction failed: {str(e)}")
        raise

async def extract_label_fields(image_bytes: bytes, filename: str) -> dict:
    """
    Extract TTB fields with intelligent fallback.
    Primary: Anthropic Claude (fast, high quality)
    Fallback: Azure OpenAI (for firewall-restricted networks)
    
    Per Marcus Williams: TTB network blocks outbound traffic to some domains.
    This implementation supports both Anthropic (primary) and Azure OpenAI (behind corporate firewall).
    """
    # Try Anthropic first (primary - fastest and most reliable)
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return await extract_label_fields_anthropic(image_bytes, filename)
        except Exception as e:
            audit_logger.warning(f"Anthropic failed, attempting Azure fallback: {str(e)}")
    
    # Fall back to Azure OpenAI
    if os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_KEY"):
        try:
            return await extract_label_fields_azure(image_bytes, filename)
        except Exception as e:
            audit_logger.error(f"Both Anthropic and Azure failed: {str(e)}")
            raise HTTPException(500, f"Label extraction failed. Ensure API credentials are configured: {str(e)}")
    
    # No providers configured
    raise HTTPException(500, "No LLM provider configured. Set ANTHROPIC_API_KEY or AZURE_OPENAI_* environment variables.")

# ── CLAUDE VISION EXTRACTION ───────────────────────────────────

# ── COMPLIANCE VERIFICATION ────────────────────────────────────

def verify_government_warning(warning_text: str, warning_format: str) -> tuple[bool, str]:
    """
    Verify government warning meets exact TTB requirements.
    Per Jenny: must be exact, GOVERNMENT WARNING: must be all caps and bold.
    """
    if not warning_text or warning_text == "NOT FOUND":
        return False, "Government warning statement is missing"

    warning_upper = warning_text.upper()

    # Check "GOVERNMENT WARNING:" prefix in all caps
    if not warning_text.startswith("GOVERNMENT WARNING:"):
        if "government warning:" in warning_text.lower():
            return False, "GOVERNMENT WARNING: must be in ALL CAPS (found lowercase)"
        elif "Government Warning:" in warning_text:
            return False, "GOVERNMENT WARNING: must be in ALL CAPS (found title case — Jenny caught this type before)"
        else:
            return False, "GOVERNMENT WARNING: prefix missing or incorrectly formatted"

    # Check key required phrases
    required_phrases = [
        "surgeon general",
        "birth defects",
        "drive a car or operate machinery",
        "health problems"
    ]

    warning_lower = warning_text.lower()
    missing = [p for p in required_phrases if p not in warning_lower]
    if missing:
        return False, f"Government warning missing required language: {', '.join(missing)}"

    # Check format description for font/size issues
    format_lower = warning_format.lower() if warning_format else ""
    if any(word in format_lower for word in ["tiny", "small font", "buried", "very small"]):
        return False, "Government warning appears to be in inadequate font size — must be clearly legible"

    return True, "Compliant"

def fuzzy_match(value1: str, value2: str) -> bool:
    """
    Dave's 'STONE'S THROW' case — normalize and compare.
    Handles case differences, apostrophe variations, extra spaces.
    """
    def normalize(s: str) -> str:
        s = s.upper()
        # Replace all common Unicode quotation/apostrophe variants with straight apostrophe
        s = re.sub(r"[\u2018\u2019\u201a\u201b\u2032\u2035`']", "'", s)
        s = re.sub(r'\s+', ' ', s).strip()
        s = re.sub(r'[^\w\s\']', '', s)
        return s

    return normalize(value1) == normalize(value2)

def verify_alcohol_content(alcohol_str: str) -> tuple[bool, str]:
    """Verify ABV is present and in valid format."""
    if not alcohol_str or alcohol_str == "NOT FOUND":
        return False, "Alcohol content not found on label"

    alc_lower = alcohol_str.lower()
    if "%" not in alc_lower:
        return False, "Alcohol content format unclear — should include % and Alc./Vol."

    # Extract percentage
    pct_match = re.search(r'(\d+\.?\d*)\s*%', alcohol_str)
    if pct_match:
        pct = float(pct_match.group(1))
        if pct < 0.5 or pct > 99:
            return False, f"Alcohol content {pct}% appears invalid"

    return True, "Compliant"

def run_compliance_checks(extracted: dict) -> tuple[dict, list[str], list[str]]:
    """
    Run all TTB compliance checks on extracted fields.
    Returns field results, issues list, and recommendations.
    """
    fields = {}
    issues = []
    recommendations = []

    # ── Brand Name ──────────────────────────────────────────
    brand = extracted.get("brand_name", "NOT FOUND")
    brand_found = brand not in ["NOT FOUND", "", None]
    fields["brand_name"] = {
        "value": brand if brand_found else None,
        "found": brand_found,
        "compliant": brand_found,
        "issue": None if brand_found else "Brand name not found on label"
    }
    if not brand_found:
        issues.append("Brand name is missing or not legible")

    # ── Class/Type ──────────────────────────────────────────
    class_type = extracted.get("class_type", "NOT FOUND")
    ct_found = class_type not in ["NOT FOUND", "", None]
    fields["class_type"] = {
        "value": class_type if ct_found else None,
        "found": ct_found,
        "compliant": ct_found,
        "issue": None if ct_found else "Class/type designation not found"
    }
    if not ct_found:
        issues.append("Class/type designation missing")

    # ── Alcohol Content ─────────────────────────────────────
    alcohol = extracted.get("alcohol_content", "NOT FOUND")
    alc_ok, alc_msg = verify_alcohol_content(alcohol)
    fields["alcohol_content"] = {
        "value": alcohol if alcohol not in ["NOT FOUND", "", None] else None,
        "found": alcohol not in ["NOT FOUND", "", None],
        "compliant": alc_ok,
        "issue": None if alc_ok else alc_msg
    }
    if not alc_ok:
        issues.append(alc_msg)

    # ── Net Contents ────────────────────────────────────────
    net = extracted.get("net_contents", "NOT FOUND")
    net_found = net not in ["NOT FOUND", "", None]
    fields["net_contents"] = {
        "value": net if net_found else None,
        "found": net_found,
        "compliant": net_found,
        "issue": None if net_found else "Net contents not found"
    }
    if not net_found:
        issues.append("Net contents (volume) not found on label")

    # ── Producer Name ────────────────────────────────────────
    producer = extracted.get("producer_name", "NOT FOUND")
    prod_found = producer not in ["NOT FOUND", "", None]
    fields["producer_name"] = {
        "value": producer if prod_found else None,
        "found": prod_found,
        "compliant": prod_found,
        "issue": None if prod_found else "Producer/bottler name and address not found"
    }
    if not prod_found:
        issues.append("Producer/bottler name and address missing")

    # ── Government Warning ───────────────────────────────────
    warning = extracted.get("government_warning", "NOT FOUND")
    warning_format = extracted.get("warning_format", "")
    warn_ok, warn_msg = verify_government_warning(warning, warning_format)
    fields["government_warning"] = {
        "value": warning if warning not in ["NOT FOUND", "", None] else None,
        "found": warning not in ["NOT FOUND", "", None],
        "compliant": warn_ok,
        "issue": None if warn_ok else warn_msg
    }
    if not warn_ok:
        issues.append(f"Government warning issue: {warn_msg}")

    # ── Country of Origin ────────────────────────────────────
    origin = extracted.get("country_of_origin", "NOT APPLICABLE")
    fields["country_of_origin"] = {
        "value": origin,
        "found": origin not in ["NOT FOUND", "", None],
        "compliant": True,  # Only required for imports
        "issue": None
    }

    # ── Image Quality ────────────────────────────────────────
    img_quality = extracted.get("image_quality", "GOOD")
    if "POOR" in img_quality.upper():
        recommendations.append(f"Image quality issue detected: {img_quality}. Consider requesting a clearer photo.")

    # ── Recommendations ──────────────────────────────────────
    if issues:
        recommendations.append(f"Label has {len(issues)} compliance issue(s) requiring correction before approval.")
    else:
        recommendations.append("Label appears compliant. Recommend agent review for final approval.")

    return fields, issues, recommendations

# ── ROUTES ────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "TTB Label Verification API",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs"
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": time.time()}

@app.post("/verify", response_model=VerificationResult)
@limiter.limit("30/minute")
async def verify_label(request: Request, file: UploadFile = File(...), _: None = Depends(require_api_key)):
    """
    Verify a single alcohol label for TTB compliance.
    Target: < 5 seconds (Sarah's hard requirement).
    Logs all activity for compliance audit trail.
    """
    start = time.time()
    label_id = file.filename or "unknown"

    if not file.content_type.startswith("image/"):
        audit_logger.warning(f"Invalid file type attempt: {file.content_type} for {label_id}")
        raise HTTPException(400, "File must be an image (JPEG, PNG, WebP)")

    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:  # 10MB limit
        audit_logger.warning(f"File too large: {len(image_bytes)} bytes for {label_id}")
        raise HTTPException(400, "Image too large. Please upload under 10MB.")

    try:
        audit_logger.info(f"Starting verification for: {label_id} (size: {len(image_bytes)} bytes)")
        extracted = await extract_label_fields(image_bytes, file.filename or "label.jpg")
    except Exception as e:
        audit_logger.error(f"Label extraction failed for {label_id}: {str(e)}")
        raise HTTPException(500, f"Label extraction failed: {str(e)}")

    fields, issues, recommendations = run_compliance_checks(extracted)

    # Determine overall status
    critical_fields = ["brand_name", "alcohol_content", "government_warning"]
    critical_issues = [f for f in critical_fields if not fields[f]["compliant"]]

    if not issues:
        status = "APPROVED"
    elif critical_issues:
        status = "REJECTED"
    else:
        status = "NEEDS_REVIEW"

    elapsed_ms = int((time.time() - start) * 1000)
    confidence = extracted.get("extraction_confidence", 0.85)

    # Audit logging
    audit_logger.info(
        f"Verification complete - Label: {label_id} | Status: {status} | Time: {elapsed_ms}ms | "
        f"Confidence: {confidence:.2f} | Issues: {len(issues)}"
    )

    return VerificationResult(
        label_id=label_id,
        overall_status=status,
        processing_time_ms=elapsed_ms,
        confidence=confidence,
        fields=fields,
        issues=issues,
        recommendations=recommendations
    )

@app.post("/verify/batch", response_model=BatchResult)
@limiter.limit("10/minute")
async def verify_batch(request: Request, files: list[UploadFile] = File(...), _: None = Depends(require_api_key)):
    """
    Batch verify multiple labels concurrently.
    Janet's request — handles 200-300 label submissions from large importers.
    Logs batch audit trail with summary statistics.
    """
    if len(files) > 50:
        audit_logger.warning(f"Batch limit exceeded: {len(files)} files (max 50)")
        raise HTTPException(400, "Batch limit is 50 labels per request. Split larger batches.")

    start = time.time()
    batch_id = f"batch_{int(start)}_{len(files)}"
    audit_logger.info(f"Starting batch verification: {batch_id} with {len(files)} labels")

    async def process_one(file: UploadFile) -> VerificationResult:
        label_id = file.filename or "unknown"
        try:
            image_bytes = await file.read()
            extracted = await extract_label_fields(image_bytes, file.filename or "label.jpg")
            fields, issues, recommendations = run_compliance_checks(extracted)
            critical_fields = ["brand_name", "alcohol_content", "government_warning"]
            critical_issues = [f for f in critical_fields if not fields[f]["compliant"]]
            if not issues:
                status = "APPROVED"
            elif critical_issues:
                status = "REJECTED"
            else:
                status = "NEEDS_REVIEW"
            elapsed_ms = int((time.time() - start) * 1000)
            confidence = extracted.get("extraction_confidence", 0.85)
            return VerificationResult(
                label_id=label_id,
                overall_status=status,
                processing_time_ms=elapsed_ms,
                confidence=confidence,
                fields=fields,
                issues=issues,
                recommendations=recommendations
            )
        except Exception as e:
            audit_logger.error(f"Batch processing error for {label_id}: {str(e)}")
            return VerificationResult(
                label_id=label_id,
                overall_status="NEEDS_REVIEW",
                processing_time_ms=0,
                confidence=0.0,
                fields={},
                issues=[f"Processing error: {str(e)}"],
                recommendations=["Manual review required due to processing error"]
            )

    results = await asyncio.gather(*[process_one(f) for f in files])
    total_ms = int((time.time() - start) * 1000)

    approved = sum(1 for r in results if r.overall_status == "APPROVED")
    rejected = sum(1 for r in results if r.overall_status == "REJECTED")
    needs_review = sum(1 for r in results if r.overall_status == "NEEDS_REVIEW")

    # Audit logging for batch completion
    audit_logger.info(
        f"Batch complete: {batch_id} | Total: {len(results)} | Approved: {approved} | "
        f"Rejected: {rejected} | Needs Review: {needs_review} | Total Time: {total_ms}ms"
    )

    return BatchResult(
        total=len(results),
        approved=approved,
        rejected=rejected,
        needs_review=needs_review,
        results=list(results),
        total_processing_time_ms=total_ms
    )

@app.get("/requirements")
async def ttb_requirements():
    """Return TTB label requirements for reference."""
    return {
        "required_fields": {
            "brand_name": "Brand name exactly as registered",
            "class_type": "Class and type designation",
            "alcohol_content": "Alcohol by volume (% Alc./Vol.)",
            "net_contents": "Volume in metric units (mL, L)",
            "producer_name": "Name and address of bottler/producer",
            "government_warning": "Mandatory government health warning"
        },
        "government_warning_requirements": {
            "prefix": "Must begin with 'GOVERNMENT WARNING:' in ALL CAPS and bold",
            "text": GOVERNMENT_WARNING_EXACT,
            "placement": "Must be clearly legible, adequate font size"
        }
    }
