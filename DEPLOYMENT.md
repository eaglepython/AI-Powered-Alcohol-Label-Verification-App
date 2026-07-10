# TTB Label Verification System — Deployment Guide

## Quick Start (Local Development)

```bash
# Backend
cd backend
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-key-here"
uvicorn main:app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev

# Open http://localhost:5173
```

---

## Cloud Deployment

### Railway (Recommended - 5 minutes)

1. **Create Railway account** at [railway.app](https://railway.app)

2. **Deploy backend:**
   ```bash
   npm install -g @railway/cli
   railway login
   cd backend
   railway init
   railway link  # Select existing project or create new
   railway up
   ```

3. **Set environment variables:**
   - Go to Railway dashboard
   - Project → Variables tab
   - Add `ANTHROPIC_API_KEY` (or Azure credentials)
   - Redeploy with "Redeploy" button

4. **Get backend URL:**
   - Copy the public URL from Railway dashboard (e.g., `https://ttb-verifier.railway.app`)

5. **Deploy frontend:**
   ```bash
   cd frontend
   npm run build
   ```
   - Upload `dist/` folder to [Netlify](https://netlify.com) (free, drag-and-drop)
   - Set `VITE_API_URL` environment variable to Railway backend URL

**Result:** 
- Frontend: `https://yoursite.netlify.app`
- Backend API: `https://ttb-verifier.railway.app`
- API Docs: `https://ttb-verifier.railway.app/docs`

---

### Azure Container Instances (Federal/FedRAMP Path)

For actual TTB production deployment:

```bash
# Requires Azure CLI: https://learn.microsoft.com/cli/azure/

az login
az account set --subscription "your-subscription-id"

# Create resource group
az group create --name ttb-rg --location eastus

# Create container registry
az acr create --resource-group ttb-rg --name ttbregistry --sku Basic

# Build and push image
az acr build \
  --registry ttbregistry \
  --image ttb-label-verifier:latest \
  --file backend/Dockerfile \
  backend/

# Deploy container instance
az container create \
  --resource-group ttb-rg \
  --name ttb-label-verifier \
  --image ttbregistry.azurecr.io/ttb-label-verifier:latest \
  --registry-login-server ttbregistry.azurecr.io \
  --environment-variables \
    ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT \
    AZURE_OPENAI_KEY=$AZURE_OPENAI_KEY \
    API_KEY=$API_KEY \
  --ports 8000 \
  --cpu 2 \
  --memory 4

# Get public IP
az container show \
  --resource-group ttb-rg \
  --name ttb-label-verifier \
  --query ipAddress.fqdn
```

---

## Network Security for Federal Deployment

### Firewall Considerations (per Marcus Williams)

**Problem:** TTB network blocks outbound traffic to many domains.

**Solution:** The system supports dual LLM providers for this exact scenario.

#### Setup for Firewall-Restricted Networks:

1. **Check what's whitelisted:**
   - Ask IT: "Which external domains can the backend reach on HTTPS?"
   - Likely candidates: `api.openai.com`, `*.openai.azure.com`, `api.anthropic.com`

2. **Configuration based on access:**

   **If `api.anthropic.com` is accessible:**
   ```bash
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   # System will use Anthropic (fastest, most reliable)
   ```

   **If `api.anthropic.com` is blocked but Azure is accessible:**
   ```bash
   AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
   AZURE_OPENAI_KEY=your-key-here
   # System will automatically fall back to Azure
   ```

   **If both are blocked:**
   - Option A: Work with IT to whitelist `api.anthropic.com` (preferred)
   - Option B: Deploy backend on internal network with API access
   - Option C: Use Azure Gov environment (if TTB uses Azure Government)

3. **Testing connection:**
   ```bash
   # Test Anthropic
   curl -I -H "Authorization: Bearer $ANTHROPIC_API_KEY" https://api.anthropic.com/
   
   # Test Azure
   curl -I -H "api-key: $AZURE_OPENAI_KEY" https://your-resource.openai.azure.com/
   ```

4. **Logs will show which provider failed:**
   ```bash
   tail -f ttb_verification_audit.log
   # Look for: "Anthropic extraction failed" or "Azure OpenAI extraction successful"
   ```

---

## Audit Logging & Compliance

All verifications are logged to `ttb_verification_audit.log` for compliance audit trails.

**Log Format:**
```
2024-12-15 10:23:45,123 - TTB_AUDIT - INFO - Starting verification for: bourbon_label.jpg (size: 245678 bytes)
2024-12-15 10:23:47,891 - TTB_AUDIT - INFO - Verification complete - Label: bourbon_label.jpg | Status: APPROVED | Time: 2768ms | Confidence: 0.94 | Issues: 0
```

**Log Retention:**
- Local deployment: `./ttb_verification_audit.log`
- Docker: Mounted to container persistent storage
- Cloud (Railway/Azure): Check platform's logging service

**For Production Compliance:**
- Set up automated backup of audit logs
- Implement retention policy (recommend: 7 years for federal compliance)
- Consider shipping logs to centralized SIEM (Azure Sentinel, Splunk, etc.)

---

## Monitoring & Health Checks

**Health Check Endpoint:**
```bash
curl http://your-backend-url/health
# Returns: {"status": "healthy", "timestamp": 1234567890}
```

**API Documentation:**
```
http://your-backend-url/docs   # Interactive Swagger UI
http://your-backend-url/redoc  # ReDoc alternative
```

**Performance Targets (from Sarah Chen):**
- Single label: < 5 seconds
- Batch (50 labels): < 3-5 minutes
- Typical performance: ~2.8s per label with Claude Vision

---

## Troubleshooting

**Issue: "No LLM provider configured"**
```
Solution: Set either ANTHROPIC_API_KEY or AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_KEY
```

**Issue: "Label extraction failed" in logs**
```
Check: tail -f ttb_verification_audit.log
If Anthropic failed: Check if api.anthropic.com is accessible
If Azure failed: Check AZURE_OPENAI_ENDPOINT is correct and API key is valid
```

**Issue: Network timeouts or connection refused**
```
Cause: Likely firewall blocking outbound connections
Solution: See "Firewall Considerations" section above
Contact IT to test connectivity to LLM provider
```

**Issue: Frontend can't reach backend**
```
Check: Is VITE_API_URL pointing to correct backend URL?
Check: Is backend running and accessible? (curl http://backend-url/health)
Check: CORS enabled? (Backend sets allow_origins=["*"] by default)
```

---

## Post-Deployment Testing

1. **Health check:**
   ```bash
   curl https://your-backend-url/health
   ```

2. **Test with sample label:**
   - Upload image via frontend
   - Check API response in browser DevTools
   - Verify audit log shows entry

3. **Batch test (for Janet's 200+ label scenario):**
   ```bash
   # Upload 50 test images via UI
   # Check: "Batch Results" shows accurate counts
   # Check: Total processing time < 3-4 minutes
   ```

4. **Verify audit logging:**
   - Check `ttb_verification_audit.log` file exists
   - Confirm entries show all verifications
   - Verify timestamps are accurate

---

## Support & Next Steps

For questions or issues:
1. Check logs: `tail -f ttb_verification_audit.log`
2. Check API docs: `https://your-url/docs`
3. Review this guide's troubleshooting section
4. Verify environment variables are set correctly

For federal deployment considerations, consult with:
- IT Security (network access, firewall whitelisting)
- Compliance Officer (audit trail requirements)
- Marcus Williams (COLA integration timeline)
