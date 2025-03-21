#!/bin/bash
# Shell environment configuration

configure_shell_environment() {
    # Install Zsh
    if ! command_exists zsh; then
        install_packages zsh
    fi

    # Oh My Zsh
    if [ ! -d "$HOME/.oh-my-zsh" ]; then
        sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
    fi

    # Install plugins
    install_zsh_plugins() {
        local plugin_dir="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}"
        for plugin in "${ZSH_PLUGINS[@]}"; do
            case $plugin in
                zsh-autosuggestions)
                    git clone https://github.com/zsh-users/zsh-autosuggestions "$plugin_dir/plugins/zsh-autosuggestions"
                    ;;
                zsh-syntax-highlighting)
                    git clone https://github.com/zsh-users/zsh-syntax-highlighting "$plugin_dir/plugins/zsh-syntax-highlighting"
                    ;;
            esac
        done
    }

    # Configure .zshrc
    configure_zshrc() {
        sed -i "s/^ZSH_THEME=.*/ZSH_THEME=\"$ZSH_THEME\"/" ~/.zshrc
        sed -i "s/^plugins=.*/plugins=(${ZSH_PLUGINS[*]})/" ~/.zshrc
        echo "export HISTSIZE=500000" >> ~/.zshrc
        echo "export SAVEHIST=100000" >> ~/.zshrc
    }

    install_zsh_plugins
    configure_zshrc
}