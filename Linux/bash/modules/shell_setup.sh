#!/bin/bash
# Shell environment configuration

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
        echo "Installing Zsh..."
        install_packages zsh
    else
        echo "✅ Zsh is already installed"
    fi
    
    # Install Oh My Zsh
    if [ ! -d "$HOME/.oh-my-zsh" ]; then
        echo "Installing Oh My Zsh..."
        if confirm "Install Oh My Zsh?"; then
            sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
            echo "✅ Oh My Zsh installed"
        fi
    else
        echo "✅ Oh My Zsh is already installed"
    fi
    
    # Install required fonts for Powerlevel10k
    if confirm "Do you want to install Powerline fonts (required for Powerlevel10k theme)?"; then
        install_powerline_fonts
    fi
    
    # Install Powerlevel10k theme
    if [ ! -d "${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/themes/powerlevel10k" ]; then
        if confirm "Do you want to install Powerlevel10k theme?"; then
            echo "Installing Powerlevel10k theme..."
            git clone --depth=1 https://github.com/romkatv/powerlevel10k.git \
                "${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/themes/powerlevel10k"
            echo "✅ Powerlevel10k theme installed"
        fi
    else
        echo "✅ Powerlevel10k theme is already installed"
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
    
    echo ""
    success_message "Shell environment configured successfully!"
    echo "To start using Zsh, run: chsh -s \$(which zsh)"
    echo "Then log out and log back in."
    echo ""
    echo "For Powerlevel10k configuration, run: p10k configure"
    echo ""
    
    log_info "Shell configuration completed"
}

# Install Powerline fonts
install_powerline_fonts() {
    echo "Installing Powerline fonts..."
    
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
        local font_name=$(echo "$font" | sed 's/%20/ /g')
        if [ ! -f "$font_dir/$font_name" ]; then
            echo "  Downloading $font_name..."
            wget -q -P "$font_dir" "$font_base_url/$font" || {
                warning_message "Failed to download $font_name from $font_base_url/$font. Please check your network connection or try downloading the font manually from the URL above."
            }
        else
            echo "  ✅ $font_name already exists"
        fi
    done
    
    # Update font cache
    echo "Updating font cache..."
    fc-cache -fv >/dev/null 2>&1
    
    echo "✅ Powerline fonts installed"
    echo ""
    echo "⚠️  Please configure your terminal to use 'MesloLGS NF' font"
    echo ""
}

# Install Zsh plugins
install_zsh_plugins() {
    echo "Installing Zsh plugins..."
    
    local plugin_dir="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins"
    
    # zsh-autosuggestions
    if [ ! -d "$plugin_dir/zsh-autosuggestions" ]; then
        echo "  Installing zsh-autosuggestions..."
        git clone https://github.com/zsh-users/zsh-autosuggestions \
            "$plugin_dir/zsh-autosuggestions" 2>/dev/null
        echo "  ✅ zsh-autosuggestions installed"
    else
        echo "  ✅ zsh-autosuggestions already installed"
    fi
    
    # zsh-syntax-highlighting
    if [ ! -d "$plugin_dir/zsh-syntax-highlighting" ]; then
        echo "  Installing zsh-syntax-highlighting..."
        git clone https://github.com/zsh-users/zsh-syntax-highlighting \
            "$plugin_dir/zsh-syntax-highlighting" 2>/dev/null
        echo "  ✅ zsh-syntax-highlighting installed"
    else
        echo "  ✅ zsh-syntax-highlighting already installed"
    fi
    
    echo "✅ All plugins installed"
}

# Configure .zshrc
configure_zshrc() {
    echo "Configuring .zshrc..."
    
    backup_file "$HOME/.zshrc"
    
    # Update theme
    if grep -q "^ZSH_THEME=" "$HOME/.zshrc"; then
        sed -i "s|^ZSH_THEME=.*|ZSH_THEME=\"$ZSH_THEME\"|" "$HOME/.zshrc"
        echo "  ✅ Theme updated to $ZSH_THEME"
    fi
    
    # Update plugins
    local plugins_string="${ZSH_PLUGINS[*]}"
    if grep -q "^plugins=" "$HOME/.zshrc"; then
        sed -i "s|^plugins=.*|plugins=($plugins_string)|" "$HOME/.zshrc"
        echo "  ✅ Plugins updated: $plugins_string"
    fi
    
    # Add history settings if not present
    if ! grep -q "HISTSIZE=" "$HOME/.zshrc"; then
        echo "" >> "$HOME/.zshrc"
        echo "# History settings" >> "$HOME/.zshrc"
        echo "export HISTSIZE=500000" >> "$HOME/.zshrc"
        echo "export SAVEHIST=100000" >> "$HOME/.zshrc"
        echo "  ✅ History settings added"
    fi
    
    # Add helpful aliases if not present
    if ! grep -q "# AutoOS aliases" "$HOME/.zshrc"; then
        echo "" >> "$HOME/.zshrc"
        echo "# AutoOS aliases" >> "$HOME/.zshrc"
        echo "alias update='sudo apt update && sudo apt upgrade'" >> "$HOME/.zshrc"
        echo "alias ll='ls -lah'" >> "$HOME/.zshrc"
        echo "alias grep='grep --color=auto'" >> "$HOME/.zshrc"
        echo "  ✅ Helpful aliases added"
    fi
    
    echo "✅ .zshrc configured"
}