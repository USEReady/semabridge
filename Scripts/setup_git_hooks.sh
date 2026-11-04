#!/usr/bin/env bash
# ==============================================================================
# Installs a Git pre-push hook to run automated pre-deploy checks before pushing
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_FILE="$ROOT_DIR/.git/hooks/pre-push"

if [ ! -d "$ROOT_DIR/.git" ]; then
    echo "❌ Error: Not a git repository."
    exit 1
fi

cat << 'EOF' > "$HOOK_FILE"
#!/usr/bin/env bash
echo "🛡️  Running SemaBridge Pre-Deploy Quality Gate before push..."
make pre-deploy
EOF

chmod +x "$HOOK_FILE"
echo "✅ Git pre-push hook successfully installed at: $HOOK_FILE"
echo "Every 'git push' will now automatically validate compilation, tests, secrets, and frontend build."
