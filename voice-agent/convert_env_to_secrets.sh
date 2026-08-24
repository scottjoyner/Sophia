#!/bin/bash

# Convert .env to Docker secrets
# This script reads .env and creates secrets in Docker.

set -e

ENV_FILE=".env"

# Define the secret names that correspond to the environment variables
# Note: Docker secret names use hyphens, while env vars use uppercase with underscores
SECRET_CONFIG=(
  "neo4j-password=NEO4J_PASSWORD"
  "sophia-owner-override-token=SOPHIA_OWNER_OVERRIDE_TOKEN"
  "sophia-app-password=SOPHIA_APP_PASSWORD"
  "tommy-relay-admin-token=TOMMY_RELAY_ADMIN_TOKEN"
  "sophia-session-secret=SOPHIA_SESSION_SECRET"
)

# Check if Docker is available
if ! docker version >/dev/null 2>&1; then
  echo "Error: Docker is not available."
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: $ENV_FILE not found."
  echo "Please create it from .env.template using:
  cp .env.template .env && edit .env"
  exit 1
fi

# First, extract the values we need directly using grep
# This is more reliable than trying to source the .env file which might have comments

echo "Extracting values from $ENV_FILE to create Docker secrets..."

for secret_name in "${SECRET_CONFIG[@]}"; do
  IFS='=' read -r docker_name env_var_name <<< "$secret_name"
  
  # Get the value from .env using grep
  # Match lines with the exact environment variable name (case-sensitive)
  value="$(grep -E "^${env_var_name}=" "$ENV_FILE" | cut -d'=' -f2-)"
  
  if [[ -z "$value" ]]; then
    echo "Warning: $env_var_name not set in $ENV_FILE or is empty, skipping $docker_name"
    continue
  fi
  
  # Remove existing secret if it exists
  if docker secret ls | grep -q "^${docker_name}$"; then
    echo "Removing existing secret: $docker_name"
    docker secret rm "$docker_name" >/dev/null
  fi
  
  # Create temporary file and upload as secret
  tmp_file="$(mktemp)"
  echo -n "$value" > "$tmp_file"
  
  echo "Creating secret: $docker_name"
  docker secret create "$docker_name" "$tmp_file" >/dev/null
  
  rm -f "$tmp_file"
  echo "✓ $docker_name created"
done

echo "\nAll secrets have been created from $ENV_FILE"
echo "To update a secret later:"
echo "  echo \"new_value\" | docker secret create sophia-owner-override-token -"
echo "  docker secret rm sophia-owner-override-token"