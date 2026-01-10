# Supported Spec Formats

Integration Co-Worker supports multiple API specification formats.

## OpenAPI / Swagger

The primary supported format is OpenAPI (formerly Swagger).

### Supported Versions

| Version | Support Level |
|---------|---------------|
| OpenAPI 3.1 | Full |
| OpenAPI 3.0 | Full |
| Swagger 2.0 | Full |

### File Formats

- **YAML**: `.yaml`, `.yml`
- **JSON**: `.json`

### Example

```bash
integration-coworker run \
  -s specs/petstore_v3.json \
  -t "Create a new pet"
```

## URL Sources

Specs can be loaded directly from URLs:

```bash
integration-coworker run \
  -s https://api.example.com/openapi.yaml \
  -t "Fetch user data"
```

## Bundled Specs

The repository includes several reference specs:

| Spec | Provider | Description |
|------|----------|-------------|
| `petstore_v3.json` | Pet Store | Sample CRUD API |
| `stripe_api.json` | Stripe | Payment processing |
| `twilio_messaging_v1.json` | Twilio | SMS messaging |
| `github_api.json` | GitHub | Repository management |
| `openai_api.yaml` | OpenAI | AI/ML API |
| `slack_api.yaml` | Slack | Team communication |
| `spotify_api.yaml` | Spotify | Music streaming |
| `asana_api.yaml` | Asana | Project management |
| `box_api.yaml` | Box | File storage |
| `zoom_api.yaml` | Zoom | Video conferencing |

## Spec Requirements

For best results, specs should include:

### Required

- **Paths**: At least one operation defined
- **Schemas**: Request/response schemas

### Recommended

- **Description**: Clear operation descriptions
- **Parameters**: Documented query/path parameters
- **Security**: Authentication requirements defined
- **Examples**: Request/response examples

## Spec Size Limits

| Metric | Design Target |
|--------|---------------|
| File Size | ~20 MB per document |
| Endpoints | ~1000 per spec |
| Multiple Specs | Unlimited (memory-bound) |

!!! note "Large Specs"
    For very large specs (>500 endpoints), consider:
    
    - Splitting by domain/resource
    - Using streaming persistence
    - Enabling parallel workflow mode

## Future Support

Planned formats (not yet supported):

- AsyncAPI
- GraphQL SDL
- gRPC/Protobuf
- HTML API documentation
- PDF API documentation

---

[Back to Web UI](web-ui.md) | [Workflows →](workflows.md)
