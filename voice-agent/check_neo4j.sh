#!/bin/bash
set -e

ENV_FILE=".env"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo "=== Neo4j Configuration Check ==="
echo ""
echo "Expected (from user request):"
echo "  NEO4J_URI: bolt://localhost:7687"
echo "  NEO4J_USER: neo4j"
echo "  NEO4J_PASSWORD: knowledge_graph_2026"
echo "  NEO4J_DATABASE: knowledge_graph_2026"
echo ""
echo ".env file contents:"
grep -E "^NEO4J_" "$ENV_FILE"

echo ""
echo "=== Verification ==="

# Get .env values
actual_uri=$(grep "^NEO4J_URI=" "$ENV_FILE" | cut -d'=' -f2)
actual_user=$(grep "^NEO4J_USER=" "$ENV_FILE" | cut -d'=' -f2)
actual_password=$(grep "^NEO4J_PASSWORD=" "$ENV_FILE" | cut -d'=' -f2)
actual_database=$(grep "^NEO4J_DATABASE=" "$ENV_FILE" | cut -d'=' -f2)
expected_uri="bolt://localhost:7687"
expected_user="neo4j"
expected_password="knowledge_graph_2026"
expected_database="knowledge_graph_2026"

pass_count=0
fail_count=0

if [[ "$actual_uri" == "$expected_uri" ]]; then
    log_info "✓ URI matches: $actual_uri"
    pass_count=$((pass_count + 1))
else
    log_error "✗ URI mismatch!"
    log_error "  Expected: $expected_uri"
    log_error "  Actual: $actual_uri"
    fail_count=$((fail_count + 1))
fi

if [[ "$actual_user" == "$expected_user" ]]; then
    log_info "✓ USER matches: $actual_user"
    pass_count=$((pass_count + 1))
else
    log_error "✗ USER mismatch!"
    log_error "  Expected: $expected_user"
    log_error "  Actual: $actual_user"
    fail_count=$((fail_count + 1))
fi

if [[ "$actual_password" == "$expected_password" ]]; then
    log_info "✓ PASSWORD matches: $actual_password"
    pass_count=$((pass_count + 1))
else
    log_error "✗ PASSWORD mismatch!"
    log_error "  Expected: $expected_password"
    log_error "  Actual: $actual_password"
    fail_count=$((fail_count + 1))
fi

if [[ "$actual_database" == "$expected_database" ]]; then
    log_info "✓ DATABASE matches: $actual_database"
    pass_count=$((pass_count + 1))
else
    log_error "✗ DATABASE mismatch!"
    log_error "  Expected: $expected_database"
    log_error "  Actual: $actual_database"
    fail_count=$((fail_count + 1))
fi

echo ""
echo "=== Summary ==="
echo "Passed: $pass_count"
echo "Failed: $fail_count"

if [[ $fail_count -eq 0 ]]; then
    echo "✓ SUCCESS: All Neo4j config matches expected values!"
    exit 0
else
    echo "✗ FAILURE: $fail_count config mismatches"
    exit 1
fi
