#!/bin/bash
set -e

ENV_FILE=".env"
EXPECTED_URI="bolt://localhost:7687"
EXPECTED_USER="neo4j"
EXPECTED_PASSWORD="${NEO4J_PASSWORD}"
EXPECTED_DATABASE="${NEO4J_PASSWORD}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo "=== Neo4j Configuration Verification ==="
echo ""
echo "Expected values:"
echo "  NEO4J_URI: $EXPECTED_URI"
echo "  NEO4J_USER: $EXPECTED_USER"
echo "  NEO4J_PASSWORD: $EXPECTED_PASSWORD"
echo "  NEO4J_DATABASE: $EXPECTED_DATABASE"
echo ""
echo ".env file contents:"
grep -E "^(NEO4J_|SOPHIA_)" "$ENV_FILE" | grep -E "^(NEO4J_URI|NEO4J_USER|NEO4J_PASSWORD|NEO4J_DATABASE)" | sed 's/^/  /'

echo ""
echo "=== Docker Compose YAML defaults ==="
grep -A 5 "x-sophia-env:" docker-compose.yml | grep -E "^\s*(NEO4J_|SOPHIA_):" | grep -E "\$\{NEO4J_|SOPHIA_:-" | sed 's/^/  /'

echo ""
echo "=== Verification ==="

current_uri=$(grep -E "^  NEO4J_URI:.*\$\{NEO4J_URI:-" docker-compose.yml | head -1 | sed "s/.*\$\{NEO4J_URI:-\([^}]*\)}/\1/")
current_user=$(grep -E "^  NEO4J_USER:.*\$\{NEO4J_USER:-" docker-compose.yml | head -1 | sed "s/.*\$\{NEO4J_USER:-\([^}]*\)}/\1/")
current_password=$(grep -E "^  NEO4J_PASSWORD:.*\$\{NEO4J_PASSWORD:-" docker-compose.yml | head -1 | sed "s/.*\$\{NEO4J_PASSWORD:-\([^}]*\)}/\1/")
current_database=$(grep -E "^  NEO4J_DATABASE:.*\$\{NEO4J_DATABASE:-" docker-compose.yml | head -1 | sed "s/.*\$\{NEO4J_DATABASE:-\([^}]*\)}/\1/")

pass_count=0
fail_count=0

if [[ "$current_uri" == "$EXPECTED_URI" ]]; then
    log_info "✓ URI matches: $current_uri"
    pass_count=$((pass_count + 1))
else
    log_error "✗ URI does NOT match!"
    log_error "  Expected: $EXPECTED_URI"
    log_error "  Got: $current_uri"
    fail_count=$((fail_count + 1))
fi

if [[ "$current_user" == "$EXPECTED_USER" ]]; then
    log_info "✓ USER matches: $current_user"
    pass_count=$((pass_count + 1))
else
    log_error "✗ USER does NOT match!"
    log_error "  Expected: $EXPECTED_USER"
    log_error "  Got: $current_user"
    fail_count=$((fail_count + 1))
fi

if [[ "$current_password" == "$EXPECTED_PASSWORD" ]]; then
    log_info "✓ PASSWORD matches: $current_password"
    pass_count=$((pass_count + 1))
else
    log_error "✗ PASSWORD does NOT match!"
    log_error "  Expected: $EXPECTED_PASSWORD"
    log_error "  Got: $current_password"
    fail_count=$((fail_count + 1))
fi

if [[ "$current_database" == "$EXPECTED_DATABASE" ]]; then
    log_info "✓ DATABASE matches: $current_database"
    pass_count=$((pass_count + 1))
else
    log_error "✗ DATABASE does NOT match!"
    log_error "  Expected: $EXPECTED_DATABASE"
    log_error "  Got: $current_database"
    fail_count=$((fail_count + 1))
fi

echo ""
echo "=== Summary ==="
echo "Passed: $pass_count, Failed: $fail_count"

if [[ $fail_count -eq 0 ]]; then
    echo "✓ SUCCESS: All Neo4j config values match expected!"
    exit 0
else
    echo "✗ FAILURE: $fail_count config values do NOT match!"
    exit 1
fi
