#!/bin/bash

# Deploy Caddy HTTPS config for Sophia Voice Agent
# This creates a reverse proxy with Let's Encrypt SSL for the voice-agent service

set -e

# Configuration
CADDY_CONFIG_DIR="/etc/caddy"
REPO_DIR="/home/scott/git/Sophia/voice-agent"
CADDY_IMAGE="caddy:2-alpine"
CONTAINER_NAME="sophia-caddy-nginx"
BACKUP_DIR="$REPO_DIR/backups/$(date +%Y%m%d_%H%M%S)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Docker is available
if ! command -v docker >/dev/null 2>&1; then
    log_error "Docker is not installed or not in PATH"
    exit 1
fi

# Check if we have the Caddy config file
CADDYFILE="$REPO_DIR/caddy/SophiaCaddyfile"
if [[ ! -f "$CADDYFILE" ]]; then
    log_error "Caddy config file not found at $CADDYFILE"
    echo "Please ensure the SophiaCaddyfile exists in the voice-agent repository"
    exit 1
fi

log_info "Checking for existing Caddy container..."

if docker ps -q --filter "name=${CONTAINER_NAME}" | grep -q .; then
    log_info "Stopping existing Caddy container..."
    docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    
    log_info "Removing existing Caddy container..."
    docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

if docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^$CADDY_IMAGE$"; then
    log_info "Caddy image already exists, pulling latest..."
    docker pull "$CADDY_IMAGE" >/dev/null 2>&1
else
    log_info "Pulling Caddy image..."
    docker pull "$CADDY_IMAGE" >/dev/null 2>&1
fi

log_info "Creating backup of Caddy config..."
mkdir -p "$BACKUP_DIR"
cp -r "$CADDY_CONFIG_DIR" "$(dirname "$CADDYFILE")" "$BACKUP_DIR/" 2>/dev/null || true

log_info "Creating Caddy container with HTTPS configuration..."

# Get the external IP for certbot
alias_ip=$(curl -s https://api.ipify.org || echo "")
if [[ -z "$alias_ip" ]]; then
    alias_ip="1.2.3.4"  # Fallback - will need manual configuration
    log_warn "Could not determine public IP. Caddy will use placeholder and need manual DNS/ssl setup."
fi

# Create a temporary Caddyfile with the correct domain
TEMP_CADDYFILE="$(mktemp)"
cat "$CADDYFILE" | sed "s|YOUR_DOMAIN_HERE|$alias_ip|g" > "$TEMP_CADDYFILE"

log_info "Starting Caddy container with HTTPS..."
docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p "8443:8443" \
    -p "80:80" \
    -v "$(dirname "$TEMP_CADDYFILE"):/etc/caddy" \
    -v "$CONTAINER_NAME-caddy_data":/data \
    -v "$CONTAINER_NAME-caddy_config":/config \
    "$CADDY_IMAGE" \
    caddy run --config /etc/caddy/SophiaCaddyfile

# Clean up temp file
rm -f "$TEMP_CADDYFILE"

# Wait a moment for Caddy to start
log_info "Waiting for Caddy to start..."
sleep 5

if docker ps -q --filter "name=${CONTAINER_NAME}" | grep -q .; then
    log_info "✓ Caddy container is running on https://$alias_ip:8443"
    log_info ""
    log_info "Next Steps:"
    log_info "1. Update your DNS to point $alias_ip to this machine/server"
    log_info "2. Access Sophia via: https://$alias_ip:8443/"
    log_info "3. Configure your firewall to allow ports 80 and 8443"
    log_info ""
    log_info "To check Caddy logs:"
    log_info "  docker logs $CONTAINER_NAME -f"
    log_info ""
    log_info "To stop Caddy:"
    log_info "  docker stop $CONTAINER_NAME"
else
    log_error "Caddy container failed to start. Check logs:"
    docker logs "$CONTAINER_NAME" 2>/dev/null || echo "Could not retrieve logs"
    exit 1
fi