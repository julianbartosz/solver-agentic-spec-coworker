# Deployment Plan

This document outlines the steps to deploy the Agentic Integration Co-Worker to a production environment.

## 1. Prerequisites

- **Docker & Docker Compose**: Ensure Docker Engine and Docker Compose are installed on the target host.
- **API Keys**: You will need valid API keys for:
  - OpenAI (`OPENAI_API_KEY`)
  - Anthropic (`ANTHROPIC_API_KEY`)
  - LangSmith (Optional, for tracing) (`LANGCHAIN_API_KEY`)

## 2. Containerization

The application is containerized using Docker.

- **Dockerfile**: Located in the project root. Builds a Python 3.11 image with all dependencies installed.
- **docker-compose.prod.yml**: Defines the production stack including:
  - `app`: The main application (Streamlit UI + CLI).
  - `db`: Postgres 16 with `pgvector` extension.
  - `redis`: Redis 7 for caching.

## 3. Deployment Steps

### Step 1: Clone Repository
Clone the repository to the production server.

```bash
git clone <repo-url>
cd solver-agentic-spec-coworker
```

### Step 2: Configure Environment
Create a `.env` file or export environment variables.

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
# Optional: LangSmith Tracing
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY="ls-..."
```

### Step 3: Build and Start Services
Use the production Docker Compose file to build and start the stack.

```bash
docker-compose -f docker-compose.prod.yml up -d --build
```

### Step 4: Initialize Database
Once the services are up and healthy, initialize the database schema and seed the Knowledge Graph.

```bash
docker-compose -f docker-compose.prod.yml exec app integration-coworker init-db --seed-kg
```

### Step 5: Verify Deployment
Check the health of the application.

```bash
docker-compose -f docker-compose.prod.yml exec app integration-coworker health
```

Access the UI at `http://<server-ip>:8501`.

## 4. Data Persistence

- **Postgres Data**: Persisted in the `pgdata_prod` Docker volume.
- **Redis Data**: Persisted in the `redisdata_prod` Docker volume.

**Backup Strategy:**
Regularly backup the Postgres database.
```bash
docker-compose -f docker-compose.prod.yml exec db pg_dump -U integration integration_coworker > backup.sql
```

## 5. Scaling Considerations

- **Database**: The `pgvector` database is the primary state store. For high availability, consider using a managed Postgres service (e.g., AWS RDS, Google Cloud SQL) instead of the containerized DB. Update `DATABASE_URL` in `docker-compose.prod.yml` accordingly.
- **Application**: The application is stateless (state is in DB/Redis). You can run multiple replicas of the `app` container behind a load balancer if needed, provided they share the same DB and Redis.

## 6. Troubleshooting

View logs:
```bash
docker-compose -f docker-compose.prod.yml logs -f app
```

Restart services:
```bash
docker-compose -f docker-compose.prod.yml restart
```
