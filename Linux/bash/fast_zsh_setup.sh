#!/usr/bin/env bash
# Fast installation script for Zsh, Oh My Zsh, Powerlevel10k theme, and custom plugins.
# Based on AutoOS Linux bash setup configuration.

set -e

echo "Starting Fast Zsh & Oh My Zsh Setup..."

# 1. Install Zsh and prerequisites
echo "Installing Zsh, Git, Curl, and Wget..."
sudo apt update && sudo apt install -y zsh git curl wget

# 2. Install Oh My Zsh (unattended)
if [ ! -d "$HOME/.oh-my-zsh" ]; then
  echo "Installing Oh My Zsh..."
  sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
else
  echo "Oh My Zsh is already installed."
fi

# 3. Download and install MesloLGS NF fonts
echo "Installing MesloLGS NF fonts..."
mkdir -p ~/.local/share/fonts
for font in Regular Bold Italic "Bold Italic"; do
  if [ ! -f "$HOME/.local/share/fonts/MesloLGS NF ${font}.ttf" ]; then
    wget -q -O "$HOME/.local/share/fonts/MesloLGS NF ${font}.ttf" \
      "https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20${font// /%20}.ttf"
  fi
done
if command -v fc-cache >/dev/null 2>&1; then
  fc-cache -f -v >/dev/null 2>&1 || true
fi

# 4. Clone or update Powerlevel10k theme and custom plugins
ZSH_CUSTOM="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}"

setup_repo() {
  local repo_url=$1
  local dest_dir=$2
  if [ -d "$dest_dir" ]; then
    echo "Updating existing repository in $dest_dir..."
    git -C "$dest_dir" pull --ff-only || true
  else
    echo "Cloning into $dest_dir..."
    git clone --depth=1 "$repo_url" "$dest_dir"
  fi
}

setup_repo "https://github.com/romkatv/powerlevel10k.git" "$ZSH_CUSTOM/themes/powerlevel10k"
setup_repo "https://github.com/zsh-users/zsh-autosuggestions" "$ZSH_CUSTOM/plugins/zsh-autosuggestions"
setup_repo "https://github.com/zsh-users/zsh-syntax-highlighting" "$ZSH_CUSTOM/plugins/zsh-syntax-highlighting"
setup_repo "https://github.com/zsh-users/zsh-completions" "$ZSH_CUSTOM/plugins/zsh-completions"

# 5. Configure ~/.zshrc with P10k theme and default AutoOS plugins
echo "Configuring ~/.zshrc..."
if [ -f "$HOME/.zshrc" ]; then
  # Backup existing .zshrc
  cp "$HOME/.zshrc" "$HOME/.zshrc.bak.$(date +%F_%T)"
  
  # Replace theme and plugins
  sed -i 's|^ZSH_THEME=.*|ZSH_THEME="powerlevel10k/powerlevel10k"|' "$HOME/.zshrc"
  sed -i 's|^plugins=.*|plugins=(git zsh-autosuggestions zsh-syntax-highlighting z colored-man-pages zsh-completions extract sudo command-not-found history)|' "$HOME/.zshrc"
else
  # Fallback if no .zshrc exists
  echo 'export ZSH="$HOME/.oh-my-zsh"' > "$HOME/.zshrc"
  echo 'ZSH_THEME="powerlevel10k/powerlevel10k"' >> "$HOME/.zshrc"
  echo 'plugins=(git zsh-autosuggestions zsh-syntax-highlighting z colored-man-pages zsh-completions extract sudo command-not-found history)' >> "$HOME/.zshrc"
  echo 'source $ZSH/oh-my-zsh.sh' >> "$HOME/.zshrc"
fi

# 6. Change default shell to Zsh
echo "Setting Zsh as the default shell..."
chsh -s "$(which zsh)"

echo "Setup completed successfully!"
echo "Please set your terminal font to 'MesloLGS NF' and restart the terminal."
