# CORS Security Configuration

## Fixed Critical Security Issue

The CORS bypass vulnerability has been fixed with environment-based configuration.

## Environment Settings

### Development (Default)
```bash
export ENVIRONMENT=development
# OR
ENVIRONMENT=development python main.py
```
- **CORS Origins**: `localhost:3000`, `localhost:8080`, `localhost:8000`, `127.0.0.1:3000`, `127.0.0.1:8080`, `127.0.0.1:8000`
- **CORS Methods**: All methods (`*`)
- **Trusted Hosts**: All hosts (`*`)

### Staging
```bash
export ENVIRONMENT=staging
```
- **CORS Origins**: `staging.yourdomain.com`, `localhost:3000`, `localhost:8080`
- **CORS Methods**: All methods (`*`)
- **Trusted Hosts**: `staging.yourdomain.com`, `localhost`, `127.0.0.1`

### Production
```bash
export ENVIRONMENT=production
```
- **CORS Origins**: `yourdomain.com`, `www.yourdomain.com`, `api.yourdomain.com`
- **CORS Methods**: `GET`, `POST`, `DELETE`, `OPTIONS` only
- **Trusted Hosts**: `yourdomain.com`, `www.yourdomain.com`, `api.yourdomain.com`

## Customizing for Your Domain

Before deploying to production, update the domain names in `api/main.py`:

1. Replace `yourdomain.com` with your actual domain
2. Update the production origins list with your frontend URLs
3. Ensure HTTPS is used in production

## Testing CORS

### Local Development
```bash
# Test with curl
curl -H "Origin: http://localhost:3000" \
     -H "Access-Control-Request-Method: POST" \
     -X OPTIONS \
     http://localhost:8000/translate

# Should return CORS headers allowing the request
```

### Production
```bash
# This should be blocked
curl -H "Origin: http://malicious-site.com" \
     -H "Access-Control-Request-Method: POST" \
     -X OPTIONS \
     https://api.yourdomain.com/translate

# This should be allowed
curl -H "Origin: https://yourdomain.com" \
     -H "Access-Control-Request-Method: POST" \
     -X OPTIONS \
     https://api.yourdomain.com/translate
```

## Security Benefits

1. **Development**: Allows local testing without security restrictions
2. **Staging**: Controlled testing environment with some restrictions
3. **Production**: Strict CORS policy preventing unauthorized cross-origin requests

## Deployment Commands

### Development
```bash
ENVIRONMENT=development uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Production
```bash
ENVIRONMENT=production uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### Docker Production
```dockerfile
ENV ENVIRONMENT=production
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```