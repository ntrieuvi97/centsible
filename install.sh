#!/usr/bin/env bash
# ==============================================================================
# Centsible Universal Installer for Linux & macOS 🪙⚡
#
# Quick install:
#   curl -fsSL https://raw.githubusercontent.com/ntrieuvi97/centsible/main/install.sh | bash
#
# Or run from a local clone:
#   ./install.sh
# ==============================================================================
set -e

REPO_DIR="${CENTSIBLE_DIR:-$HOME/.gemini/config/plugins/centsible}"
LOCAL_BIN="$HOME/.local/bin"

echo "=== Installing Centsible 🪙⚡ ==="

# 1. Determine repository location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/bin/centsible" ]; then
    REPO_DIR="$SCRIPT_DIR"
    echo "• Using local repository: $REPO_DIR"
elif [ ! -d "$REPO_DIR" ]; then
    echo "• Cloning centsible into $REPO_DIR..."
    mkdir -p "$(dirname "$REPO_DIR")"
    git clone https://github.com/ntrieuvi97/centsible.git "$REPO_DIR"
else
    echo "• Found existing repository: $REPO_DIR"
fi

# 2. Ensure executable permissions
chmod +x "$REPO_DIR/bin/centsible"
echo "✓ Ensured launcher is executable"

# 3. Ensure ~/.local/bin exists
mkdir -p "$LOCAL_BIN"
echo "✓ Target directory ready: $LOCAL_BIN"

# 4. Create or update symlink
ln -sf "$REPO_DIR/bin/centsible" "$LOCAL_BIN/centsible"
echo "✓ Created symlink: $LOCAL_BIN/centsible -> $REPO_DIR/bin/centsible"

# 5. Check if ~/.local/bin is in PATH
IS_IN_PATH=0
case ":$PATH:" in
    *":$LOCAL_BIN:"*) IS_IN_PATH=1 ;;
esac

if [ "$IS_IN_PATH" -eq 1 ]; then
    echo "✓ $LOCAL_BIN is already in your PATH"
else
    echo "⚠ $LOCAL_BIN is NOT in your current PATH"

    # Detect user's shell configuration file
    RC_FILE=""
    if [ -n "$ZSH_VERSION" ] || [ "$(basename "$SHELL" 2>/dev/null)" = "zsh" ]; then
        RC_FILE="$HOME/.zshrc"
    elif [ -n "$BASH_VERSION" ] || [ "$(basename "$SHELL" 2>/dev/null)" = "bash" ]; then
        RC_FILE="$HOME/.bashrc"
    elif [ -f "$HOME/.profile" ]; then
        RC_FILE="$HOME/.profile"
    fi

    EXPORT_CMD='export PATH="$HOME/.local/bin:$PATH"'
    if [ -n "$RC_FILE" ]; then
        if ! grep -q '\.local/bin' "$RC_FILE" 2>/dev/null; then
            printf '\n# Added by centsible installer\n%s\n' "$EXPORT_CMD" >> "$RC_FILE"
            echo "✓ Added PATH export to $RC_FILE"
        else
            echo "• PATH entry already present in $RC_FILE"
        fi
        echo ""
        echo "👉 To activate in your current terminal session, run:"
        echo "   $EXPORT_CMD"
        echo "   (or open a new terminal window)"
    fi
fi

# 6. Verify installation
export PATH="$LOCAL_BIN:$PATH"
echo ""
"$LOCAL_BIN/centsible" status || true
echo ""
echo "🎉 Centsible installation complete!"
echo "Run 'centsible stats' or 'centsible status' to check metrics."
