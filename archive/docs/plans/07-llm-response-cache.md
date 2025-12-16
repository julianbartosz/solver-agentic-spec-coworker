# Implementation Plan: LLM Response Cache (Redis)

## 1. Overview

**Objective:** Implement production-grade LLM response caching using Redis to reduce API costs, improve latency, and enable deterministic replay for debugging.

**Priority:** Medium  
**Estimated Effort:** 3-4 hours implementation + testing  
**Risk Level:** Medium (new dependency, cache invalidation complexity)

---

## 2. Architecture

### 2.1 Cache Layer Position

```
┌─────────────────────────────────────────────────────────────────────┐
│                          LLM Client                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐ │
│  │ complete()  │ -> │ Cache Check │ -> │ OpenAI/Anthropic API    │ │
│  │             │ <- │ (Redis)     │ <- │                         │ │
│  └─────────────┘    └─────────────┘    └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Cache Key Structure

```
llm:<provider>:<model>:<task_type>:<prompt_hash>
```

Where:
- `provider`: openai, anthropic, etc.
- `model`: gpt-4o-mini, claude-3-sonnet, etc.
- `task_type`: understand_task, plan_flow, codegen, etc.
- `prompt_hash`: SHA-256 of (system_prompt + user_prompt)

Example:
```
llm:openai:gpt-4o-mini:understand_task:a1b2c3d4e5f6...
```

### 2.3 Cache Entry Format

```json
{
  "response": "Generated text response...",
  "model": "gpt-4o-mini",
  "created_at": "2024-12-09T10:30:00Z",
  "tokens": {
    "prompt": 1500,
    "completion": 500
  },
  "cache_version": 1
}
```

---

## 3. File Changes

### 3.1 New Files

| File | Purpose | LOC |
|------|---------|-----|
| `src/integration_coworker/llm/cache.py` | Redis cache implementation | ~150 |
| `tests/test_llm_cache.py` | Cache unit tests | ~100 |

### 3.2 Modified Files

| File | Changes |
|------|---------|
| `src/integration_coworker/llm/client.py` | Add cache lookup before API call |
| `src/integration_coworker/config/__init__.py` | Add Redis connection settings |
| `pyproject.toml` | Add `redis` as optional dependency |
| `docker-compose.yml` | Add Redis service |

---

## 4. Implementation Details

### 4.1 `src/integration_coworker/llm/cache.py`

```python
"""
LLM Response Cache using Redis.

Provides caching for LLM API responses to:
- Reduce API costs
- Improve response latency for repeated queries
- Enable deterministic replay for debugging

Cache Configuration:
- REDIS_URL: Redis connection URL (default: redis://localhost:6379)
- LLM_CACHE_TTL: Cache TTL in seconds (default: 86400 = 24 hours)
- LLM_CACHE_ENABLED: Enable/disable caching (default: true)
"""
import os
import json
import hashlib
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Redis client singleton
_redis_client = None

# Configuration
CACHE_TTL_DEFAULT = 86400  # 24 hours
CACHE_VERSION = 1


def _get_redis_client():
    """Get or create Redis client singleton."""
    global _redis_client
    
    if _redis_client is not None:
        return _redis_client
    
    try:
        import redis
    except ImportError:
        logger.debug("redis package not installed, caching disabled")
        return None
    
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    try:
        _redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        # Test connection
        _redis_client.ping()
        logger.info(f"Connected to Redis at {redis_url}")
        return _redis_client
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}, caching disabled")
        return None


def _build_cache_key(
    provider: str,
    model: str,
    task_type: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Build cache key from request parameters."""
    # Hash the prompts to keep key size reasonable
    combined = f"{system_prompt or ''}|{user_prompt}"
    prompt_hash = hashlib.sha256(combined.encode()).hexdigest()[:32]
    
    return f"llm:{provider}:{model}:{task_type}:{prompt_hash}"


def cache_enabled() -> bool:
    """Check if caching is enabled and available."""
    if os.getenv("LLM_CACHE_ENABLED", "true").lower() == "false":
        return False
    
    return _get_redis_client() is not None


def get_cached_response(
    provider: str,
    model: str,
    task_type: str,
    system_prompt: str,
    user_prompt: str,
) -> Optional[str]:
    """
    Retrieve cached LLM response if available.
    
    Returns None if not cached or caching is disabled.
    """
    if not cache_enabled():
        return None
    
    client = _get_redis_client()
    if not client:
        return None
    
    cache_key = _build_cache_key(provider, model, task_type, system_prompt, user_prompt)
    
    try:
        cached = client.get(cache_key)
        if cached:
            entry = json.loads(cached)
            # Validate cache version
            if entry.get("cache_version") == CACHE_VERSION:
                logger.debug(f"Cache HIT for {task_type}")
                return entry.get("response")
            else:
                logger.debug(f"Cache version mismatch, ignoring")
    except Exception as e:
        logger.warning(f"Cache read error: {e}")
    
    return None


def set_cached_response(
    provider: str,
    model: str,
    task_type: str,
    system_prompt: str,
    user_prompt: str,
    response: str,
    tokens: Optional[Dict[str, int]] = None,
) -> None:
    """
    Store LLM response in cache.
    
    Args:
        provider: LLM provider (openai, anthropic)
        model: Model name
        task_type: Task type for metrics
        system_prompt: System prompt used
        user_prompt: User prompt used
        response: Generated response to cache
        tokens: Optional token usage stats
    """
    if not cache_enabled():
        return
    
    client = _get_redis_client()
    if not client:
        return
    
    cache_key = _build_cache_key(provider, model, task_type, system_prompt, user_prompt)
    ttl = int(os.getenv("LLM_CACHE_TTL", CACHE_TTL_DEFAULT))
    
    entry = {
        "response": response,
        "model": model,
        "provider": provider,
        "task_type": task_type,
        "created_at": datetime.utcnow().isoformat(),
        "tokens": tokens or {},
        "cache_version": CACHE_VERSION,
    }
    
    try:
        client.setex(cache_key, ttl, json.dumps(entry))
        logger.debug(f"Cache SET for {task_type} (TTL: {ttl}s)")
    except Exception as e:
        logger.warning(f"Cache write error: {e}")


def clear_cache(pattern: str = "llm:*") -> int:
    """
    Clear cached entries matching pattern.
    
    Returns number of keys deleted.
    """
    client = _get_redis_client()
    if not client:
        return 0
    
    try:
        keys = list(client.scan_iter(match=pattern, count=1000))
        if keys:
            return client.delete(*keys)
        return 0
    except Exception as e:
        logger.warning(f"Cache clear error: {e}")
        return 0


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics."""
    client = _get_redis_client()
    if not client:
        return {"enabled": False}
    
    try:
        # Count keys by task type
        stats = {
            "enabled": True,
            "total_keys": 0,
            "by_task_type": {},
        }
        
        for key in client.scan_iter(match="llm:*", count=1000):
            stats["total_keys"] += 1
            parts = key.split(":")
            if len(parts) >= 4:
                task_type = parts[3]
                stats["by_task_type"][task_type] = stats["by_task_type"].get(task_type, 0) + 1
        
        return stats
    except Exception as e:
        return {"enabled": True, "error": str(e)}
```

### 4.2 Integration into `llm/client.py`

Add to `LangChainOpenAIClient.complete()`:

```python
def complete(self, prompt: str, system_prompt: Optional[str] = None, ...):
    # Check cache first
    from integration_coworker.llm.cache import get_cached_response, set_cached_response
    
    cached = get_cached_response(
        provider="openai",
        model=self.model,
        task_type=self.task_type,
        system_prompt=system_prompt or "",
        user_prompt=prompt,
    )
    if cached is not None:
        return cached
    
    # ... existing API call logic ...
    
    # Cache successful response
    set_cached_response(
        provider="openai",
        model=self.model,
        task_type=self.task_type,
        system_prompt=system_prompt or "",
        user_prompt=prompt,
        response=result,
        tokens=token_usage,
    )
    
    return result
```

### 4.3 Config Changes

Add to `config/__init__.py`:

```python
@dataclass
class CacheConfig:
    """LLM cache configuration."""
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    enabled: bool = field(default_factory=lambda: os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true")
    ttl_seconds: int = field(default_factory=lambda: int(os.getenv("LLM_CACHE_TTL", "86400")))
```

### 4.4 Docker Compose Addition

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

volumes:
  redis_data:
```

---

## 5. Configuration Options

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `LLM_CACHE_ENABLED` | `true` | Enable/disable caching |
| `LLM_CACHE_TTL` | `86400` | Cache TTL in seconds (24 hours) |

---

## 6. CLI Commands

Add to CLI:

```python
@app.command("cache-stats")
def cache_stats():
    """Show LLM cache statistics."""
    from integration_coworker.llm.cache import get_cache_stats
    stats = get_cache_stats()
    # ... display stats ...

@app.command("cache-clear")
def cache_clear(pattern: str = "llm:*"):
    """Clear LLM cache entries."""
    from integration_coworker.llm.cache import clear_cache
    count = clear_cache(pattern)
    typer.echo(f"Cleared {count} cache entries")
```

---

## 7. Testing Strategy

### 7.1 Unit Tests

```python
# tests/test_llm_cache.py

def test_cache_key_generation():
    """Test cache key format and uniqueness."""
    
def test_cache_hit():
    """Test cache retrieval for identical prompts."""
    
def test_cache_miss():
    """Test cache miss for different prompts."""
    
def test_cache_ttl_expiry():
    """Test cache entry expires after TTL."""
    
def test_cache_version_mismatch():
    """Test old cache versions are ignored."""
    
def test_cache_disabled():
    """Test caching disabled via environment."""
```

### 7.2 Integration Tests

- Test with real Redis container
- Test fallback when Redis unavailable
- Test concurrent cache access

---

## 8. Rollout Plan

1. **Phase 1:** Add optional Redis dependency and cache module
2. **Phase 2:** Integrate with LLM client (disabled by default)
3. **Phase 3:** Add CLI commands and documentation
4. **Phase 4:** Enable by default with monitoring

---

## 9. Monitoring & Observability

### Metrics to Track:
- Cache hit rate by task type
- Cache miss rate
- Cache latency
- Redis connection errors

### Logging:
- DEBUG: Cache hits/misses
- WARNING: Redis connection failures
- INFO: Cache clear operations

---

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Redis unavailable | High | Graceful fallback to uncached mode |
| Stale cache | Medium | Version field + TTL + manual clear CLI |
| Memory pressure | Low | LRU eviction policy in Redis |
| Prompt collisions | Low | SHA-256 hash (collision probability ~0) |

---

## 11. Dependencies

### New:
- `redis>=4.5.0` (optional, in `[cache]` extra)

### Docker:
- `redis:7-alpine` service

---

## 12. Files to Create/Modify

```
CREATE:
  src/integration_coworker/llm/cache.py         (~150 LOC)
  tests/test_llm_cache.py                       (~100 LOC)
  docs/features/llm-cache.md                    (~50 LOC)

MODIFY:
  src/integration_coworker/llm/client.py        (+20 LOC)
  src/integration_coworker/config/__init__.py   (+15 LOC)
  src/integration_coworker/cli.py               (+30 LOC)
  pyproject.toml                                (+3 LOC)
  docker-compose.yml                            (+10 LOC)
```

**Total New Code:** ~250 LOC
**Total Modified Code:** ~80 LOC
