#!/usr/bin/env bash
# Shell environment setup (Zsh + Oh My Zsh)

# --- Source color/helpers (module-local) ---
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$MODULE_DIR/utils.sh" ]; then
    # shellcheck source=/dev/null
    source "$MODULE_DIR/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
    # shellcheck source=/dev/null
    source "$MODULE_DIR/../modules/utils.sh"
else
    echo "Unable to locate modules/utils.sh - shell setup requires utilities." >&2
    return 1
fi

configure_shell_environment() {
    section_header "Shell Environment Configuration"

    info_box "Shell Configuration" "$SHELL_DESCRIPTION"

    if ! confirm "Do you want to configure Zsh with Oh My Zsh?"; then
        warning_message "Skipping shell configuration"
        return 0
    fi

    log_info "Starting shell configuration"

    # Install Zsh if not present
    if ! command_exists zsh; then
        info "Installing Zsh..."
        install_packages zsh
    else
        info "Zsh is already installed"
    fi

    # Install Oh My Zsh
    if [ ! -d "$HOME/.oh-my-zsh" ]; then
        info "Installing Oh My Zsh..."
        if confirm "Install Oh My Zsh?"; then
            # Use safe_run to respect DRY_RUN; run installer via shell -c piping curl to bash
            safe_run sh -c "curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh | bash -s -- --unattended"
            info "Oh My Zsh installation attempted"
        fi
    else
        info "Oh My Zsh is already installed"
    fi

    # Install required fonts for Powerlevel10k
    if confirm "Do you want to install Powerline fonts (required for Powerlevel10k theme)?"; then
        install_powerline_fonts
    fi

    # Install Powerlevel10k theme
    if [ ! -d "${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/themes/powerlevel10k" ]; then
        if confirm "Do you want to install Powerlevel10k theme?"; then
            info "Installing Powerlevel10k theme..."
            safe_run git clone --depth=1 https://github.com/romkatv/powerlevel10k.git \
                "${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/themes/powerlevel10k"
            info "Powerlevel10k theme installation attempted"
        fi
    else
        info "Powerlevel10k theme is already installed"
    fi

    # Install Zsh plugins
    if confirm "Do you want to install recommended Zsh plugins?"; then
        install_zsh_plugins
    fi

    # Configure .zshrc
    if [ -f "$HOME/.zshrc" ]; then
        if confirm "Do you want to configure .zshrc with recommended settings?"; then
            configure_zshrc
        fi
    fi

    # Try to configure terminal font automatically where possible
    if confirm "Attempt to configure your terminal to use 'MesloLGS NF' font automatically?" "Y"; then
        configure_terminal_font || warning_message "Automatic terminal font configuration failed; see instructions above for manual steps."
    fi

    echo ""
    success_message "Shell environment configured successfully!"
    info "To start using Zsh, run: chsh -s \$(which zsh)"
    info "Then log out and log back in."
    info "For Powerlevel10k configuration, run: p10k configure"
    echo ""

    log_info "Shell configuration completed"
}

# Attempt to configure common terminals to use the MesloLGS NF font
configure_terminal_font() {
    section_header "Configure Terminal Font"

    local font_name="MesloLGS NF"

    info "Attempting to set terminal font to '$font_name' where possible..."

    # Update font cache first
    if command_exists fc-cache; then
        safe_run fc-cache -f -v >/dev/null 2>&1 || true
    fi

    # 1) GNOME (system monospace) - this will affect many GTK terminals
    if command_exists gsettings; then
        info "Setting GNOME monospace font via gsettings..."
        # Keep a reasonable default size if not set
        local current_font
        current_font=$(gsettings get org.gnome.desktop.interface monospace-font-name 2>/dev/null || true)
        if [ -z "$current_font" ] || [ "$current_font" = "''" ]; then
            safe_run gsettings set org.gnome.desktop.interface monospace-font-name "$font_name 11" 2>/dev/null || true
        else
            # try to preserve existing size if present
            local size
            size=$(echo "$current_font" | awk -F' ' '{print $NF}' | tr -d "'\"")
            if [[ "$size" =~ ^[0-9]+$ ]]; then
                safe_run gsettings set org.gnome.desktop.interface monospace-font-name "$font_name $size" 2>/dev/null || true
            else
                safe_run gsettings set org.gnome.desktop.interface monospace-font-name "$font_name 11" 2>/dev/null || true
            fi
        fi
        info "GNOME monospace font update attempted (if GNOME present)"
    fi

    # 2) GNOME Terminal profiles via dconf (more targeted)
    if command_exists dconf && command_exists gsettings; then
        # Find profile IDs and set font for each profile
        local profile_list
        profile_list=$(dconf read /org/gnome/terminal/legacy/profiles:/list 2>/dev/null || true)
        if [ -n "$profile_list" ]; then
            # profile_list looks like ['<id1>','<id2>']
            info "Configuring GNOME Terminal profiles..."
            # Extract ids
            local ids
            ids=$(echo "$profile_list" | tr -d "[],'\"" | tr -s ' ' '\n')
            for id in $ids; do
                local key="/org/gnome/terminal/legacy/profiles:/:$id/"
                safe_run dconf write "$key"font "'${font_name} 11'" 2>/dev/null || safe_run dconf write "$key"font "'${font_name} 11'" 2>/dev/null || true
            done
            info "GNOME Terminal profile fonts updated (if dconf accessible)"
        fi
    fi

    # 3) Windows Terminal (WSL users) - try to find settings.json in known locations and update defaultProfile fontFace
    if grep -qi Microsoft /proc/version 2>/dev/null || [ -n "$WSL_INTEROP" ]; then
        # Attempt to edit Windows Terminal settings.json in LocalState for the default Windows user
        # This is best-effort; we won't try to guess every Windows package path. Provide a helpful note instead.
        info "WSL detected: Automatic configuration of Windows Terminal is not performed to avoid modifying Windows user settings unexpectedly."
        info "Manual steps for Windows Terminal: open Settings -> Appearance -> Font face and set to '$font_name' or edit your settings.json and add 'fontFace': '$font_name' to your profile(s)."
    fi

    info "If your terminal didn't change, set the font manually to: $font_name (e.g. 'MesloLGS NF') and restart the terminal."
    return 0
}

# Install Powerline fonts
install_powerline_fonts() {
    info "Installing Powerline fonts..."

    local font_dir="$HOME/.local/share/fonts"
    ensure_directory "$font_dir"

    local font_base_url="https://github.com/romkatv/powerlevel10k-media/raw/master"
    local fonts=(
        "MesloLGS%20NF%20Regular.ttf"
        "MesloLGS%20NF%20Bold.ttf"
        "MesloLGS%20NF%20Italic.ttf"
        "MesloLGS%20NF%20Bold%20Italic.ttf"
    )

    for font in "${fonts[@]}"; do
        local font_name
        font_name=$(echo "$font" | sed 's/%20/ /g')
        if [ ! -f "$font_dir/$font_name" ]; then
            info "Downloading $font_name..."
            safe_run wget -q -P "$font_dir" "$font_base_url/$font" || {
                warning_message "Failed to download $font_name from $font_base_url/$font. Please check your network connection or try downloading the font manually from the URL above."
            }
        else
            info "MesloLGS NF $font_name already exists"
        fi
    done

    # Update font cache
    info "Updating font cache..."
    safe_run fc-cache -f -v >/dev/null 2>&1 || true

    success_message "Powerline fonts installation attempted"
}

# Install Zsh plugins
install_zsh_plugins() {
    info "Installing Zsh plugins..."

    local plugin_dir="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins"

    # zsh-autosuggestions
    if [ ! -d "$plugin_dir/zsh-autosuggestions" ]; then
        info "Installing zsh-autosuggestions..."
        safe_run git clone https://github.com/zsh-users/zsh-autosuggestions \
            "$plugin_dir/zsh-autosuggestions" 2>/dev/null
        info "zsh-autosuggestions installation attempted"
    else
        info "zsh-autosuggestions already installed"
    fi

    # zsh-syntax-highlighting
    if [ ! -d "$plugin_dir/zsh-syntax-highlighting" ]; then
        info "Installing zsh-syntax-highlighting..."
        safe_run git clone https://github.com/zsh-users/zsh-syntax-highlighting \
            "$plugin_dir/zsh-syntax-highlighting" 2>/dev/null
        info "zsh-syntax-highlighting installation attempted"
    else
        info "zsh-syntax-highlighting already installed"
    fi

    info "All plugins installation attempted"
}

# Configure .zshrc
configure_zshrc() {
    info "Configuring .zshrc..."

    backup_file "$HOME/.zshrc"

    # Update theme
    if grep -q "^ZSH_THEME=" "$HOME/.zshrc"; then
        sed -i "s|^ZSH_THEME=.*|ZSH_THEME=\"$ZSH_THEME\"|" "$HOME/.zshrc"
        info "Theme updated to $ZSH_THEME"
    fi

    # Update plugins
    local plugins_string="${ZSH_PLUGINS[*]}"
    if grep -q "^plugins=" "$HOME/.zshrc"; then
        sed -i "s|^plugins=.*|plugins=($plugins_string)|" "$HOME/.zshrc"
        info "Plugins updated: $plugins_string"
    fi

    # Add history settings if not present
    if ! grep -q "HISTSIZE=" "$HOME/.zshrc"; then
        echo "" >> "$HOME/.zshrc"
        echo "# History settings" >> "$HOME/.zshrc"
        echo "export HISTSIZE=500000" >> "$HOME/.zshrc"
        echo "export SAVEHIST=100000" >> "$HOME/.zshrc"
        info "History settings added"
    fi

    # Add helpful aliases if not present
    if ! grep -q "# AutoOS aliases" "$HOME/.zshrc"; then
        echo "" >> "$HOME/.zshrc"
        echo "# AutoOS aliases" >> "$HOME/.zshrc"
        echo "alias update='sudo apt update && sudo apt upgrade'" >> "$HOME/.zshrc"
        echo "alias ll='ls -lah'" >> "$HOME/.zshrc"
        echo "alias grep='grep --color=auto'" >> "$HOME/.zshrc"
        info "Helpful aliases added"
    fi

    info ".zshrc configured"
}