# Environment Settings
You can also write information into env to skip some options.

## Model keys
```
# Set env BBM_OPENAI_API_KEY to ignore option --openai_key
export BBM_OPENAI_API_KEY=${your_api_key}

# Set env BBM_CAIYUN_API_KEY to ignore option --caiyun_key
export BBM_CAIYUN_API_KEY=${your_api_key}
```

## Google Cloud Translation v3 (`--model googlev3`)

No API key. Auth uses Application Default Credentials (ADC).

### One-time setup

```sh
# 1. Install gcloud CLI (macOS)
brew install --cask google-cloud-sdk

# 2. Log in for ADC — this is what the code uses.
#    Note: `gcloud auth login` alone is NOT enough; ADC is a separate credential.
gcloud auth application-default login
gcloud config set project ${your_project_id}

# 3. Enable the APIs (Storage is only needed for glossary creation)
gcloud services enable translate.googleapis.com storage.googleapis.com

# 4. (glossary only) Create a bucket for term CSVs.
#    Must be us-central1 — the only region that supports glossaries.
gcloud storage buckets create gs://${your_bucket} --location=us-central1
```

Alternatively use a service account instead of step 2:

```sh
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

Verify: `python3 -c "import google.auth; print(google.auth.default())"`

### Env vars

```sh
# Ignore options --google_project_id / --google_glossary_id / --google_location
export BBM_GOOGLE_PROJECT_ID=${your_project_id}   # defaults to the ADC project
export BBM_GOOGLE_GLOSSARY_ID=${your_glossary_id}
export BBM_GOOGLE_LOCATION=us-central1            # default; keep it for glossaries

# Transport defaults to REST; set to grpc only if you don't need a proxy
export BBM_GOOGLE_TRANSPORT=rest
```

### Behind a proxy

- Transport defaults to REST because gRPC ignores SOCKS/HTTP proxies and fails
  with `503 UNAVAILABLE: failed to connect to all addresses`. Keep the REST
  default if you are behind a proxy.
- With a SOCKS proxy (`all_proxy=socks5://...`) you also need:
  `pip install "requests[socks]"`, otherwise requests raises
  `Missing dependencies for SOCKS support`.