# Azure Deployment Guide

This guide outlines how to deploy the Agentic Integration Co-Worker to Microsoft Azure using **Azure Container Apps**, **Azure Database for PostgreSQL**, and **Azure Cache for Redis**.

## Prerequisites

- Azure CLI installed (`az`)
- Docker installed
- An active Azure subscription

## 1. Setup Environment Variables

Set these variables for use in subsequent commands:

```bash
export RESOURCE_GROUP="rg-integration-coworker"
export LOCATION="eastus"
export ACR_NAME="acrintegrationcoworker$RANDOM"
export APP_NAME="integration-coworker"
export POSTGRES_SERVER="psql-integration-coworker$RANDOM"
export REDIS_NAME="redis-integration-coworker$RANDOM"
```

## 2. Create Resource Group

```bash
az group create --name $RESOURCE_GROUP --location $LOCATION
```

## 3. Azure Container Registry (ACR)

Create a registry to store your Docker images.

```bash
# Create ACR
az acr create --resource-group $RESOURCE_GROUP --name $ACR_NAME --sku Basic --admin-enabled true

# Login to ACR
az acr login --name $ACR_NAME

# Get ACR Login Server
export ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --query loginServer --output tsv)
```

## 4. Build and Push Image

Build the Docker image and push it to your Azure Container Registry.

```bash
# Build image
docker build -t $APP_NAME:latest .

# Tag image
docker tag $APP_NAME:latest $ACR_LOGIN_SERVER/$APP_NAME:latest

# Push image
docker push $ACR_LOGIN_SERVER/$APP_NAME:latest
```

## 5. Create Managed Services

### PostgreSQL (Flexible Server)

We need a Postgres server with the `vector` extension enabled.

```bash
# Create Postgres Server
az postgres flexible-server create \
    --resource-group $RESOURCE_GROUP \
    --name $POSTGRES_SERVER \
    --location $LOCATION \
    --admin-user integration \
    --admin-password "YourStrongPassword123!" \
    --sku-name Standard_B1ms \
    --tier Burstable \
    --version 16 \
    --storage-size 32 \
    --yes

# Allow access from Azure services
az postgres flexible-server firewall-rule create \
    --resource-group $RESOURCE_GROUP \
    --name $POSTGRES_SERVER \
    --rule-name AllowAzureIPs \
    --start-ip-address 0.0.0.0 \
    --end-ip-address 0.0.0.0

# Enable pgvector extension
az postgres flexible-server parameter set \
    --resource-group $RESOURCE_GROUP \
    --server-name $POSTGRES_SERVER \
    --name azure.extensions \
    --value vector
```

**Connection String Construction:**
`postgresql://integration:YourStrongPassword123!@$POSTGRES_SERVER.postgres.database.azure.com:5432/integration_coworker`

### Redis Cache

```bash
az redis create \
    --resource-group $RESOURCE_GROUP \
    --name $REDIS_NAME \
    --location $LOCATION \
    --sku Basic \
    --vm-size c0
```

Get the Redis connection string/key from the Azure Portal or CLI.

## 6. Deploy to Azure Container Apps

Azure Container Apps is a serverless container service ideal for this application.

```bash
# Create Container Apps Environment
az containerapp env create \
    --name "$APP_NAME-env" \
    --resource-group $RESOURCE_GROUP \
    --location $LOCATION

# Get Registry Credentials
export ACR_USERNAME=$(az acr credential show --name $ACR_NAME --query "username" -o tsv)
export ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

# Deploy the App
az containerapp create \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --environment "$APP_NAME-env" \
    --image "$ACR_LOGIN_SERVER/$APP_NAME:latest" \
    --target-port 8501 \
    --ingress 'external' \
    --registry-server $ACR_LOGIN_SERVER \
    --registry-username $ACR_USERNAME \
    --registry-password $ACR_PASSWORD \
    --env-vars \
        DATABASE_URL="postgresql://integration:YourStrongPassword123!@$POSTGRES_SERVER.postgres.database.azure.com:5432/postgres" \
        REDIS_URL="redis://:$REDIS_KEY@$REDIS_NAME.redis.cache.windows.net:6380" \
        OPENAI_API_KEY="sk-..." \
        ANTHROPIC_API_KEY="sk-ant-..." \
        STREAMING_PERSISTENCE="auto"
```

*Note: Replace `YourStrongPassword123!`, `$REDIS_KEY`, and API keys with actual values.*

## 7. Post-Deployment Initialization

Once the app is running, you need to initialize the database schema. You can do this by connecting to the container console or running a job.

**Option A: Console via Portal**
1. Go to your Container App in Azure Portal.
2. Select **Console**.
3. Run: `integration-coworker init-db --seed-kg`

**Option B: Azure CLI Exec**
```bash
az containerapp exec \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --command "integration-coworker init-db --seed-kg"
```

## 8. Verification

Get the public URL of your container app:

```bash
az containerapp show --name $APP_NAME --resource-group $RESOURCE_GROUP --query properties.configuration.ingress.fqdn
```

Visit the URL to see the Streamlit UI.
