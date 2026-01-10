#!/usr/bin/env python3
"""
Bootstrap Spec Catalog from APIs.guru and Curated Sources

This script populates the local spec catalog (spec_silver.discovery_*) from:
1. APIs.guru directory (bulk import)
2. Curated "golden" providers JSON (high-quality overrides)
3. Computes embeddings for semantic search

Usage:
    # Full bootstrap (first run)
    python scripts/bootstrap_spec_catalog.py
    
    # Refresh from APIs.guru only
    python scripts/bootstrap_spec_catalog.py --source apis_guru
    
    # Apply curated overrides only
    python scripts/bootstrap_spec_catalog.py --source curated
    
    # Skip embedding computation (faster, for testing)
    python scripts/bootstrap_spec_catalog.py --skip-embeddings

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md:
- APIs.guru is used as upstream seed data
- Local catalog enables offline discovery
- Curated entries override APIs.guru for quality
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from integration_coworker.config import get_settings
from integration_coworker.persistence.postgres import get_connection

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

APIS_GURU_DIRECTORY_URL = "https://api.apis.guru/v2/list.json"
APIS_GURU_CACHE_FILE = Path(__file__).parent.parent / "data" / "apis_guru_cache.json"
APIS_GURU_CACHE_TTL_HOURS = 24

# Curated providers file (high-quality overrides)
CURATED_PROVIDERS_FILE = Path(__file__).parent.parent / "data" / "curated_providers.json"

# Batch sizes for database operations
PROVIDER_BATCH_SIZE = 100
EMBEDDING_BATCH_SIZE = 50

# Quality tier mappings
QUALITY_TIERS = {
    "premium": ["stripe.com", "twilio.com", "github.com", "slack.com", "openai.com"],
    "standard": [],  # Everything else
}

# Category mappings (provider domain -> categories)
PROVIDER_CATEGORIES = {
    "stripe.com": ["payments", "ecommerce"],
    "twilio.com": ["messaging", "communication"],
    "sendgrid.com": ["messaging"],
    "github.com": ["infrastructure"],
    "slack.com": ["messaging", "communication"],
    "openai.com": ["ai"],
    "azure.com": ["infrastructure", "ai"],
    "googleapis.com": ["infrastructure", "ai", "storage"],
    "amazonaws.com": ["infrastructure", "storage"],
    "mailchimp.com": ["messaging", "crm"],
    "shopify.com": ["ecommerce"],
    "paypal.com": ["payments"],
    "braintree.com": ["payments"],
}


@dataclass
class ProviderEntry:
    """Parsed provider entry from APIs.guru or curated source."""
    provider_key: str
    domain: str
    slug: str
    display_name: str
    description: Optional[str] = None
    logo_url: Optional[str] = None
    homepage_url: Optional[str] = None
    is_curated: bool = False
    quality_tier: str = "standard"
    specs: List[Dict[str, Any]] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)


# =============================================================================
# APIs.guru Fetching and Parsing
# =============================================================================

async def fetch_apis_guru_directory(
    use_cache: bool = True,
    cache_ttl_hours: int = APIS_GURU_CACHE_TTL_HOURS,
) -> Dict[str, Any]:
    """
    Fetch APIs.guru directory, with disk caching.
    
    Args:
        use_cache: Whether to use cached data if available
        cache_ttl_hours: Cache TTL in hours
        
    Returns:
        APIs.guru directory JSON
    """
    # Check cache first
    if use_cache and APIS_GURU_CACHE_FILE.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(APIS_GURU_CACHE_FILE.stat().st_mtime)
        if cache_age < timedelta(hours=cache_ttl_hours):
            logger.info(f"Using cached APIs.guru data (age: {cache_age})")
            return json.loads(APIS_GURU_CACHE_FILE.read_text())
    
    # Fetch fresh data
    logger.info(f"Fetching APIs.guru directory from {APIS_GURU_DIRECTORY_URL}...")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(APIS_GURU_DIRECTORY_URL)
        response.raise_for_status()
        data = response.json()
    
    # Cache to disk
    APIS_GURU_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    APIS_GURU_CACHE_FILE.write_text(json.dumps(data, indent=2))
    logger.info(f"Cached {len(data)} APIs.guru entries to {APIS_GURU_CACHE_FILE}")
    
    return data


def parse_apis_guru_entry(
    api_key: str,
    api_data: Dict[str, Any],
) -> Optional[ProviderEntry]:
    """
    Parse a single APIs.guru entry into a ProviderEntry.
    
    APIs.guru format:
    {
      "api_key": {
        "added": "2019-01-01",
        "preferred": "1.0",
        "versions": {
          "1.0": {
            "info": { "title": "...", "description": "...", "x-logo": {...} },
            "swaggerUrl": "...",
            "openapi": "3.0.0"
          }
        }
      }
    }
    """
    try:
        # Parse API key (format: "domain:slug" or just "slug")
        if ":" in api_key:
            domain, slug = api_key.split(":", 1)
        else:
            domain = f"{api_key}.com"
            slug = api_key
        
        provider_key = f"{domain}:{slug}"
        
        # Get preferred version info
        preferred_version = api_data.get("preferred", "")
        versions = api_data.get("versions", {})
        
        if not versions:
            logger.debug(f"Skipping {api_key}: no versions")
            return None
        
        # Get info from preferred version (or first available)
        if preferred_version and preferred_version in versions:
            version_info = versions[preferred_version]
        else:
            version_info = next(iter(versions.values()))
        
        info = version_info.get("info", {})
        
        # Extract display name and description
        display_name = info.get("title", slug.replace("-", " ").title())
        description = info.get("description", "")
        if len(description) > 1000:
            description = description[:1000] + "..."
        
        # Extract logo URL
        x_logo = info.get("x-logo", {})
        logo_url = x_logo.get("url") if isinstance(x_logo, dict) else None
        
        # Extract homepage
        homepage_url = info.get("x-origin", [{}])[0].get("url") if isinstance(info.get("x-origin"), list) else None
        
        # Determine quality tier
        quality_tier = "standard"
        for tier, domains in QUALITY_TIERS.items():
            if domain in domains:
                quality_tier = tier
                break
        
        # Build spec entries
        specs = []
        for version, version_data in versions.items():
            spec_url = version_data.get("swaggerUrl")
            if not spec_url:
                continue
            
            openapi_version = version_data.get("openapi", version_data.get("swagger", ""))
            if openapi_version.startswith("3.1"):
                spec_format = "openapi_3.1"
            elif openapi_version.startswith("3."):
                spec_format = "openapi_3.0"
            elif openapi_version.startswith("2."):
                spec_format = "swagger_2"
            else:
                spec_format = "unknown"
            
            specs.append({
                "version": version,
                "spec_url": spec_url,
                "spec_format": spec_format,
                "is_preferred": version == preferred_version,
                "title": version_data.get("info", {}).get("title"),
                "api_version": version_data.get("info", {}).get("version"),
            })
        
        if not specs:
            logger.debug(f"Skipping {api_key}: no valid specs")
            return None
        
        # Determine categories
        categories = PROVIDER_CATEGORIES.get(domain, [])
        
        return ProviderEntry(
            provider_key=provider_key,
            domain=domain,
            slug=slug,
            display_name=display_name,
            description=description,
            logo_url=logo_url,
            homepage_url=homepage_url,
            is_curated=False,
            quality_tier=quality_tier,
            specs=specs,
            categories=categories,
        )
        
    except Exception as e:
        logger.warning(f"Failed to parse APIs.guru entry {api_key}: {e}")
        return None


def load_curated_providers() -> List[ProviderEntry]:
    """
    Load curated provider entries from JSON file.
    
    Curated entries override APIs.guru data for quality.
    """
    if not CURATED_PROVIDERS_FILE.exists():
        logger.info("No curated providers file found, skipping")
        return []
    
    try:
        data = json.loads(CURATED_PROVIDERS_FILE.read_text())
        providers = []
        
        for entry in data.get("providers", []):
            providers.append(ProviderEntry(
                provider_key=entry["provider_key"],
                domain=entry["domain"],
                slug=entry["slug"],
                display_name=entry["display_name"],
                description=entry.get("description"),
                logo_url=entry.get("logo_url"),
                homepage_url=entry.get("homepage_url"),
                is_curated=True,
                quality_tier=entry.get("quality_tier", "premium"),
                specs=entry.get("specs", []),
                categories=entry.get("categories", []),
                aliases=entry.get("aliases", []),
            ))
        
        logger.info(f"Loaded {len(providers)} curated providers")
        return providers
        
    except Exception as e:
        logger.error(f"Failed to load curated providers: {e}")
        return []


# =============================================================================
# Embedding Computation
# =============================================================================

def compute_provider_embedding_text(provider: ProviderEntry) -> str:
    """
    Create text for embedding computation.
    
    Combines display name, description, and categories for semantic search.
    """
    parts = [
        provider.display_name,
        provider.domain,
        provider.description or "",
        " ".join(provider.categories),
        " ".join(provider.aliases),
    ]
    return " ".join(filter(None, parts))


async def compute_embeddings_batch(
    texts: List[str],
    model: str = "text-embedding-3-small",
) -> List[List[float]]:
    """
    Compute embeddings for a batch of texts using OpenAI API.
    
    Args:
        texts: List of texts to embed
        model: Embedding model name
        
    Returns:
        List of embedding vectors
    """
    try:
        import openai
        
        client = openai.AsyncOpenAI()
        response = await client.embeddings.create(
            input=texts,
            model=model,
        )
        
        return [item.embedding for item in response.data]
        
    except ImportError:
        logger.warning("openai package not installed, skipping embeddings")
        return [[] for _ in texts]
    except Exception as e:
        logger.error(f"Embedding computation failed: {e}")
        return [[] for _ in texts]


# =============================================================================
# Database Operations
# =============================================================================

def upsert_providers(
    conn,
    providers: List[ProviderEntry],
    compute_embeddings: bool = True,
) -> Tuple[int, int]:
    """
    Upsert providers into spec_catalog.providers.
    
    Returns:
        Tuple of (added_count, updated_count)
    """
    added = 0
    updated = 0
    
    with conn.cursor() as cur:
        for provider in providers:
            # Check if exists
            cur.execute(
                "SELECT id FROM spec_silver.discovery_providers WHERE provider_key = %s",
                (provider.provider_key,)
            )
            existing = cur.fetchone()
            
            if existing:
                # Update existing
                cur.execute("""
                    UPDATE spec_silver.discovery_providers SET
                        domain = %s,
                        slug = %s,
                        display_name = %s,
                        description = %s,
                        logo_url = %s,
                        homepage_url = %s,
                        is_curated = COALESCE(%s, is_curated),
                        quality_tier = COALESCE(%s, quality_tier),
                        last_refreshed_at = NOW()
                    WHERE provider_key = %s
                    RETURNING id
                """, (
                    provider.domain,
                    provider.slug,
                    provider.display_name,
                    provider.description,
                    provider.logo_url,
                    provider.homepage_url,
                    provider.is_curated if provider.is_curated else None,
                    provider.quality_tier if provider.is_curated else None,
                    provider.provider_key,
                ))
                provider_id = cur.fetchone()[0]
                updated += 1
            else:
                # Insert new
                cur.execute("""
                    INSERT INTO spec_silver.discovery_providers (
                        provider_key, domain, slug, display_name, description,
                        logo_url, homepage_url, is_curated, quality_tier
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    provider.provider_key,
                    provider.domain,
                    provider.slug,
                    provider.display_name,
                    provider.description,
                    provider.logo_url,
                    provider.homepage_url,
                    provider.is_curated,
                    provider.quality_tier,
                ))
                provider_id = cur.fetchone()[0]
                added += 1
            
            # Upsert specs
            _upsert_specs(cur, provider_id, provider.specs)
            
            # Upsert categories
            _upsert_categories(cur, provider_id, provider.categories)
            
            # Upsert aliases
            _upsert_aliases(cur, provider_id, provider.aliases)
    
    conn.commit()
    return added, updated


def _upsert_specs(cur, provider_id: int, specs: List[Dict[str, Any]]) -> None:
    """Upsert spec entries for a provider."""
    for spec in specs:
        cur.execute("""
            INSERT INTO spec_silver.discovery_specs (
                provider_id, version, is_preferred, spec_url,
                spec_format, title, api_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider_id, version) DO UPDATE SET
                is_preferred = EXCLUDED.is_preferred,
                spec_url = EXCLUDED.spec_url,
                spec_format = EXCLUDED.spec_format,
                title = EXCLUDED.title,
                api_version = EXCLUDED.api_version,
                updated_at = NOW()
        """, (
            provider_id,
            spec.get("version", "default"),
            spec.get("is_preferred", False),
            spec["spec_url"],
            spec.get("spec_format", "unknown"),
            spec.get("title"),
            spec.get("api_version"),
        ))


def _upsert_categories(cur, provider_id: int, categories: List[str]) -> None:
    """Upsert category associations for a provider."""
    # Delete existing associations
    cur.execute(
        "DELETE FROM spec_silver.discovery_provider_categories WHERE provider_id = %s",
        (provider_id,)
    )
    
    for category_name in categories:
        # Get or create category
        cur.execute(
            "SELECT id FROM spec_silver.discovery_categories WHERE name = %s",
            (category_name,)
        )
        row = cur.fetchone()
        if row:
            category_id = row[0]
        else:
            cur.execute(
                "INSERT INTO spec_silver.discovery_categories (name) VALUES (%s) RETURNING id",
                (category_name,)
            )
            category_id = cur.fetchone()[0]
        
        # Create association
        cur.execute("""
            INSERT INTO spec_silver.discovery_provider_categories (provider_id, category_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (provider_id, category_id))


def _upsert_aliases(cur, provider_id: int, aliases: List[str]) -> None:
    """Upsert aliases for a provider."""
    for i, alias in enumerate(aliases):
        cur.execute("""
            INSERT INTO spec_silver.discovery_aliases (provider_id, alias, alias_type, priority)
            VALUES (%s, %s, 'name', %s)
            ON CONFLICT (provider_id, alias) DO UPDATE SET
                priority = EXCLUDED.priority
        """, (provider_id, alias, i))


def update_provider_embeddings(
    conn,
    embeddings: Dict[str, List[float]],
) -> int:
    """
    Update embeddings for providers.
    
    Args:
        conn: Database connection
        embeddings: Dict of provider_key -> embedding vector
        
    Returns:
        Number of providers updated
    """
    updated = 0
    
    with conn.cursor() as cur:
        for provider_key, embedding in embeddings.items():
            if not embedding:
                continue
            
            cur.execute("""
                UPDATE spec_silver.discovery_providers 
                SET embedding = %s::vector
                WHERE provider_key = %s
            """, (embedding, provider_key))
            updated += cur.rowcount
    
    conn.commit()
    return updated


def log_refresh(
    conn,
    source: str,
    status: str,
    providers_added: int,
    providers_updated: int,
    specs_added: int,
    specs_updated: int,
    error_message: Optional[str] = None,
) -> None:
    """Log a catalog refresh operation."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO spec_silver.discovery_refresh_log (
                source, status, providers_added, providers_updated,
                specs_added, specs_updated, error_message, completed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """, (
            source,
            status,
            providers_added,
            providers_updated,
            specs_added,
            specs_updated,
            error_message,
        ))
    conn.commit()


# =============================================================================
# Main Bootstrap Flow
# =============================================================================

async def bootstrap_from_apis_guru(
    conn,
    skip_embeddings: bool = False,
) -> Tuple[int, int]:
    """
    Bootstrap catalog from APIs.guru directory.
    
    Returns:
        Tuple of (providers_added, providers_updated)
    """
    logger.info("Starting APIs.guru bootstrap...")
    
    # Fetch directory
    directory = await fetch_apis_guru_directory()
    logger.info(f"Fetched {len(directory)} APIs from APIs.guru")
    
    # Parse entries
    providers = []
    for api_key, api_data in directory.items():
        entry = parse_apis_guru_entry(api_key, api_data)
        if entry:
            providers.append(entry)
    
    logger.info(f"Parsed {len(providers)} valid provider entries")
    
    # Upsert to database
    added, updated = upsert_providers(conn, providers, compute_embeddings=False)
    logger.info(f"Database: {added} added, {updated} updated")
    
    # Compute embeddings
    if not skip_embeddings:
        logger.info("Computing embeddings...")
        embeddings = {}
        
        for i in range(0, len(providers), EMBEDDING_BATCH_SIZE):
            batch = providers[i:i + EMBEDDING_BATCH_SIZE]
            texts = [compute_provider_embedding_text(p) for p in batch]
            
            batch_embeddings = await compute_embeddings_batch(texts)
            
            for provider, embedding in zip(batch, batch_embeddings):
                if embedding:
                    embeddings[provider.provider_key] = embedding
            
            logger.info(f"Computed embeddings for {min(i + EMBEDDING_BATCH_SIZE, len(providers))}/{len(providers)} providers")
        
        if embeddings:
            embedded_count = update_provider_embeddings(conn, embeddings)
            logger.info(f"Updated {embedded_count} provider embeddings")
    
    return added, updated


async def bootstrap_from_curated(conn) -> Tuple[int, int]:
    """
    Apply curated provider overrides.
    
    Returns:
        Tuple of (providers_added, providers_updated)
    """
    logger.info("Applying curated provider overrides...")
    
    providers = load_curated_providers()
    if not providers:
        return 0, 0
    
    added, updated = upsert_providers(conn, providers, compute_embeddings=False)
    logger.info(f"Curated: {added} added, {updated} updated")
    
    return added, updated


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Bootstrap spec catalog")
    parser.add_argument(
        "--source",
        choices=["all", "apis_guru", "curated"],
        default="all",
        help="Data source to bootstrap from",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip embedding computation (faster, for testing)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    # Get database connection (context manager)
    try:
        with get_connection() as conn:
            total_added = 0
            total_updated = 0
            
            if args.source in ("all", "apis_guru"):
                added, updated = await bootstrap_from_apis_guru(
                    conn, skip_embeddings=args.skip_embeddings
                )
                total_added += added
                total_updated += updated
                log_refresh(conn, "apis_guru", "completed", added, updated, 0, 0)
            
            if args.source in ("all", "curated"):
                added, updated = await bootstrap_from_curated(conn)
                total_added += added
                total_updated += updated
                log_refresh(conn, "curated_json", "completed", added, updated, 0, 0)
            
            logger.info(f"Bootstrap complete: {total_added} added, {total_updated} updated")
            
    except Exception as e:
        logger.error(f"Bootstrap failed: {e}")
        # Try to log failure if we have a connection
        try:
            with get_connection() as conn:
                log_refresh(conn, args.source, "failed", 0, 0, 0, 0, str(e))
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
