#!/bin/bash
# Cross-platform migration runner for Unix/Linux/macOS
# Usage: ./scripts/migrate.sh [upgrade|downgrade|current|history]

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

COMMAND="${1:-upgrade}"
REVISION="${2:-head}"

# Ensure we're in the project root
if [ ! -f "alembic.ini" ]; then
    echo -e "${RED}Error: alembic.ini not found. Run this script from the project root.${NC}"
    exit 1
fi

echo -e "${YELLOW}Running alembic ${COMMAND} ${REVISION}...${NC}"

case "$COMMAND" in
    upgrade)
        alembic upgrade "$REVISION"
        echo -e "${GREEN}✓ Database upgraded to ${REVISION}${NC}"
        ;;
    downgrade)
        alembic downgrade "$REVISION"
        echo -e "${GREEN}✓ Database downgraded to ${REVISION}${NC}"
        ;;
    current)
        alembic current
        ;;
    history)
        alembic history
        ;;
    *)
        echo -e "${RED}Unknown command: ${COMMAND}${NC}"
        echo "Usage: $0 [upgrade|downgrade|current|history] [revision|head]"
        exit 1
        ;;
esac

echo -e "${GREEN}✓ Migration complete${NC}"
