# Operations Runbook

This document provides operational guidance for deploying and managing the Integration Coworker service in production environments.

## Table of Contents

1. [Health Endpoints](#health-endpoints)
2. [Environment Variables](#environment-variables)
3. [Failure Modes & Recovery](#failure-modes--recovery)
4. [Emergency Switches](#emergency-switches)
5. [Kubernetes Integration](#kubernetes-integration)
6. [Monitoring & Alerting](#monitoring--alerting)
7. [Troubleshooting](#troubleshooting)

---

## Health Endpoints

The service exposes HTTP health endpoints for container orchestrators.

### Endpoints

| Endpoint | Purpose | Success | Failure |
|----------|---------|---------|---------|
| `GET /healthz` or `/health` | Liveness probe | 200 | 5xx |
| `GET /readyz` or `/readiness` | Readiness probe | 200 | 503 |
| `GET /metrics` | Prometheus metrics | 200 | 5xx |

### Configuration

```bash
# Health server binding (default: localhost only for security)
HEALTH_SERVER_HOST=127.0.0.1
HEALTH_SERVER_PORT=8080

# Timeouts
HEALTH_SERVER_TIMEOUT=5.0        # Request timeout (seconds)
HEALTH_CHECK_TIMEOUT=2.0         # Per-check timeout for readiness

# Concurrency limits
HEALTH_SERVER_MAX_WORKERS=4      # Max concurrent request handlers
```

### Security Warning

The health server binds to `127.0.0.1` by default. **Do NOT expose to public networks** without:
- A reverse proxy (nginx, envoy) handling TLS and auth
- Network isolation (Kubernetes NetworkPolicy, firewall rules)

---

## Environment Variables

### Critical (Fail-Fast)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | When `USE_SQLITE=false` | - | PostgreSQL connection string |
| `OPENAI_API_KEY` | When `USE_MOCK_LLM=false` | - | OpenAI API key |
| `VALIDATION_PROFILE` | No | `offline` | Test mode: `offline`, `record`, `live` |
| `INTEGRATION_COWORKER_FAIL_FAST` | No | `0` | Exit on startup if config is invalid |

**Enabling Fail-Fast Mode:**

Set `INTEGRATION_COWORKER_FAIL_FAST=1` to validate all critical configuration at startup and exit immediately if errors are found. This prevents the service from accepting work with broken config.

```bash
# Recommended for production deployments
export INTEGRATION_COWORKER_FAIL_FAST=1
integration-coworker run ...
```

You can also validate config manually:

```bash
# Check config and print results
integration-coworker validate-config

# Fail if config is invalid (for CI)
integration-coworker validate-config --fail-fast

# Output as JSON (for automation)
integration-coworker validate-config --json
```

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_SQLITE` | `true` | Use SQLite instead of PostgreSQL |
| `DATABASE_URL` | - | PostgreSQL connection URL with credentials |
| `PG_POOL_MIN_SIZE` | `2` | Minimum pool connections |
| `PG_POOL_MAX_SIZE` | `10` | Maximum pool connections |

### LLM Provider

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_MOCK_LLM` | `true` | Use mock LLM responses |
| `OPENAI_API_KEY` | - | OpenAI API key |
| `ANTHROPIC_API_KEY` | - | Anthropic API key |
| `LLM_MODEL` | `gpt-4o` | Default model name |

### Circuit Breaker

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_CIRCUIT_FAILURE_THRESHOLD` | `5` | Failures before circuit opens |
| `LLM_CIRCUIT_RECOVERY_TIMEOUT` | `60` | Seconds before half-open |
| `LLM_CIRCUIT_HALF_OPEN_REQUESTS` | `1` | Test requests in half-open |
| `LLM_CIRCUIT_MAX_ENTRIES` | `100` | Max circuit breakers tracked |
| `LLM_CIRCUIT_ENTRY_TTL` | `3600` | TTL for circuit entries |

### Cache

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_CACHE_ENABLED` | `true` | Enable response caching |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `LLM_CACHE_TTL` | `86400` | Cache TTL in seconds (24h) |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `LOG_FORMAT` | `%(asctime)s...` | Log format string |

---

## Failure Modes & Recovery

### Circuit Breaker Open

**Symptoms:**
- `CircuitOpenError` exceptions in logs
- `/metrics` shows `circuit_breaker_opens_total` increasing
- LLM requests failing immediately

**Cause:** Too many consecutive LLM API failures.

**Recovery:**
1. Check LLM provider status (OpenAI, Anthropic)
2. Verify API keys are valid
3. Wait for recovery timeout (`LLM_CIRCUIT_RECOVERY_TIMEOUT` seconds)
4. Or manually reset: `POST /admin/circuit-reset` (if implemented)

### Database Connection Pool Exhaustion

**Symptoms:**
- Slow response times
- `asyncpg.TooManyConnectionsError` in logs
- Readiness probe failures

**Cause:** All pool connections are in use.

**Recovery:**
1. Check `PG_POOL_MAX_SIZE` vs actual load
2. Look for connection leaks (long-running transactions)
3. Increase pool size or scale horizontally

### Health Server Pool Saturation

**Symptoms:**
- `/metrics` shows `health_server_pool_saturations_total` increasing
- Readiness probes returning 503
- Health checks timing out

**Cause:** Too many concurrent readiness checks, or checks are slow.

⚠️ **Python Thread Limitation:** Python's `ThreadPoolExecutor.shutdown(wait=False)` does NOT stop running tasks. When a readiness check times out, the check function continues running in the background. Repeated timeouts from checks that block indefinitely WILL accumulate background threads until pool saturation.

**The ONLY real solution:** Ensure readiness check functions have inherent time bounds (DB `connect_timeout`, HTTP `timeout` params). The executor timeout is a last-resort backstop, not a cleanup mechanism.

**Recovery:**
1. **Root cause first:** Fix slow readiness checks to have internal timeouts
2. Increase `HEALTH_SERVER_MAX_WORKERS` (temporary, masks the issue)
3. Reduce check timeout (`HEALTH_CHECK_TIMEOUT`) to fail faster
4. Review check implementations for blocking calls without timeouts

### Memory Growth

**Symptoms:**
- Container OOMKilled
- Memory in `/metrics` growing over time
- Soak test failures

**Cause:** Memory leak in application code.

**Recovery:**
1. Check recent code changes
2. Enable memory profiling
3. Review circuit breaker LRU/TTL settings
4. Check for unbounded caches

---

## Emergency Switches

### Disable LLM Calls

```bash
# Switch to mock LLM immediately (requires restart)
export USE_MOCK_LLM=true
```

### Disable Caching

```bash
# Disable LLM response cache
export LLM_CACHE_ENABLED=false
```

### Force SQLite

```bash
# Fall back to SQLite (loses persistence)
export USE_SQLITE=true
```

### Aggressive Circuit Breaker

```bash
# Trip circuit after 1 failure, recover in 5 minutes
export LLM_CIRCUIT_FAILURE_THRESHOLD=1
export LLM_CIRCUIT_RECOVERY_TIMEOUT=300
```

---

## Kubernetes Integration

### Basic Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: integration-coworker
spec:
  replicas: 2
  selector:
    matchLabels:
      app: integration-coworker
  template:
    metadata:
      labels:
        app: integration-coworker
    spec:
      containers:
      - name: app
        image: integration-coworker:latest
        ports:
        - containerPort: 8080
          name: health
        env:
        - name: HEALTH_SERVER_HOST
          value: "0.0.0.0"  # Required for K8s probes
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: db-credentials
              key: url
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: llm-credentials
              key: openai-key
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /healthz
            port: health
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /readyz
            port: health
          initialDelaySeconds: 10
          periodSeconds: 5
          timeoutSeconds: 5
          failureThreshold: 2
```

### Network Policy (Restrict Health Endpoints)

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: integration-coworker-health
spec:
  podSelector:
    matchLabels:
      app: integration-coworker
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
    ports:
    - port: 8080
      protocol: TCP
```

### Prometheus ServiceMonitor

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: integration-coworker
spec:
  selector:
    matchLabels:
      app: integration-coworker
  endpoints:
  - port: health
    path: /metrics
    interval: 30s
```

---

## Monitoring & Alerting

### Key Metrics to Monitor

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| `circuit_breaker_opens_total` | +1/5min | +3/5min | Check LLM provider |
| `circuit_breaker_short_circuits_total` | +10/min | +50/min | Increase timeout |
| `health_server_pool_saturations_total` | +1/hour | +5/hour | Increase workers |
| `health_server_readiness_timeouts_total` | +5/hour | +20/hour | Optimize checks |
| `llm_cache_errors_total` | +5/hour | +20/hour | Check Redis |

### Prometheus Alert Rules

```yaml
groups:
- name: integration-coworker
  rules:
  - alert: CircuitBreakerTripping
    expr: increase(circuit_breaker_opens_total[5m]) > 2
    for: 1m
    labels:
      severity: warning
    annotations:
      summary: "Circuit breaker is tripping frequently"
      
  - alert: HealthServerOverloaded
    expr: increase(health_server_pool_saturations_total[1h]) > 3
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "Health server pool is saturated"
      
  - alert: ReadinessChecksTimingOut
    expr: increase(health_server_readiness_timeouts_total[1h]) > 10
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "Readiness checks are timing out"
```

---

## Troubleshooting

### Health Check Failing

1. **Check logs:**
   ```bash
   kubectl logs -l app=integration-coworker --tail=100
   ```

2. **Check metrics:**
   ```bash
   curl http://localhost:8080/metrics | grep -E "readiness|health"
   ```

3. **Check readiness details:**
   ```bash
   curl -s http://localhost:8080/readyz | jq .
   ```

### Circuit Breaker Issues

1. **Check current state:**
   ```bash
   curl http://localhost:8080/metrics | grep circuit_breaker
   ```

2. **View aggregate metrics:**
   ```python
   from integration_coworker.llm.circuit_breaker import get_circuit_breaker
   cb = get_circuit_breaker()
   print(cb.get_aggregate_metrics())
   ```

### Memory Issues

1. **Run soak test:**
   ```bash
   ./scripts/soak.sh 30  # 30 minute test
   ```

2. **Check for leaks:**
   - Thread count should remain stable
   - FD count should remain stable
   - Memory growth should be bounded

### Configuration Issues

1. **Validate startup config:**
   ```python
   from integration_coworker.config.startup import validate_startup_config
   result = validate_startup_config(fail_fast=False)
   print(result.errors)
   print(result.warnings)
   ```

2. **Check config summary:**
   ```python
   from integration_coworker.config.startup import get_config_summary
   import json
   print(json.dumps(get_config_summary(), indent=2))
   ```

---

## Security Considerations

### HTTP Client Safety

All HTTP clients in the codebase use explicit timeouts to prevent connection hangs:

| Component | Client | Timeout |
|-----------|--------|---------|
| `ingest_spec.py` | `httpx.Client` | 30s |
| `github.py` (repo provider) | `httpx.Client` | 30s |
| `http_client.py` (runtime) | `httpx.Client` | Configurable (default 30s) |
| OAuth templates | `httpx.post` | 30s |

### Probe Endpoint Security

The health server is designed for **internal probes only**:

1. **Default binding**: `127.0.0.1` (localhost only)
2. **No secrets exposed**: `/metrics` only exports operational counters
3. **No config dumps**: Configuration summary redacts all secrets

⚠️ **Never expose health endpoints to public networks without:**
- Reverse proxy (nginx, envoy) for TLS termination
- Authentication layer (API key, mTLS)
- Network isolation (Kubernetes NetworkPolicy)

### SSRF Mitigation

User-provided URLs (e.g., OpenAPI spec URLs) are handled by:
- The pooled HTTP client in `ingest_spec.py`
- Explicit timeout prevents resource exhaustion
- Consider adding URL allowlist for production deployments

### Secrets Handling

| Secret Type | Storage | Rotation |
|-------------|---------|----------|
| `OPENAI_API_KEY` | Env var / K8s Secret | At key rotation |
| `DATABASE_URL` | Env var / K8s Secret | At credential rotation |
| `REDIS_URL` | Env var / K8s Secret | At credential rotation |

**Never log secrets.** The startup config summary automatically redacts:
- API keys
- Database URLs with credentials
- Redis URLs with passwords

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2024-12 | Initial runbook |
