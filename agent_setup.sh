#!/bin/bash
#
#  Compact installer for OpenAI, Gemini, and Claude command-line tools.
#
#  Usage:
#    chmod +x install_agents.sh
#    ./install_agents.sh
#

# Helper function to check if a command is available
command_exists() {
  command -v "$1" >/dev/null 2>&1
}

echo "--- Checking Prerequisites ---"
if ! command_exists node || ! command_exists npm; then
  echo "Error: Node.js and npm are required. Please install them and rerun."
  exit 1
fi
echo "Prerequisites met."

# --- OpenAI Codex CLI ---
echo "--- Installing OpenAI Codex CLI ---"
if command_exists codex; then
  echo "OpenAI Codex CLI is already installed."
else
  echo "Installing via npm..."
  if npm install -g @openai/codex; then
    echo "Success. Configure with: export OPENAI_API_KEY='your-key'"
  else
    echo "Failed to install OpenAI Codex CLI." >&2
  fi
fi

# --- Google Gemini CLI ---
echo "--- Installing Google Gemini CLI ---"
if command_exists gemini; then
  echo "Google Gemini CLI is already installed."
else
  echo "Installing via npm..."
  # Installs the official Gemini CLI agent
  if npm install -g @google/gemini-cli; then
    echo "Success. Run 'gemini auth login' or just 'gemini' to authenticate."
  else
    echo "Failed to install Google Gemini CLI." >&2
  fi
fi

# --- Anthropic Claude Code CLI ---
echo "--- Installing Anthropic Claude Code CLI ---"
if command_exists claude; then
  echo "Anthropic Claude Code CLI is already installed."
else
  echo "Installing via npm..."
  if npm install -g @anthropic-ai/claude-code; then
    echo "Success. Run 'claude' in your project directory to start."
  else
    echo "Failed to install Anthropic Claude Code CLI." >&2
  fi
fi

echo "Installing context 7 into claude code"
npm install -g @anthropic-ai/claude-code
claude mcp add screenshot-website-fast -s user -- npx -y @just-every/mcp-screenshot-website-fast
claude mcp add --transport http context7 https://mcp.context7.com/mcp
echo "--- Installation Complete ---"
echo "Remember to configure API keys and restart your shell for changes to take effect."

# --- Setting up CLI aliases ---
echo "--- Setting up CLI aliases ---"
ALIAS_COMMAND="alias dclaude='claude --dangerously-skip-permissions'"

# Add alias to common shell configuration files
for shell_config in ~/.bashrc ~/.zshrc ~/.profile; do
  if [ -f "$shell_config" ]; then
    if ! grep -q "alias dclaude=" "$shell_config"; then
      echo "" >> "$shell_config"
      echo "# Claude CLI shortcut" >> "$shell_config"
      echo "$ALIAS_COMMAND" >> "$shell_config"
      echo "Added dclaude alias to $shell_config"
    else
      echo "dclaude alias already exists in $shell_config"
    fi
  fi
done

echo "Alias 'dclaude' has been added. Restart your terminal or run 'source ~/.bashrc' (or appropriate shell config) to use it."


