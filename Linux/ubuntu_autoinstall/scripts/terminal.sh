#!/bin/bash

# terminal.sh - Script to install terminal design (Oh My Zsh, plugins, fonts)

# Install Oh My Zsh
RUNONCE=1 CHSH=no sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# Install plugins and themes
git clone https://github.com/zsh-users/zsh-autosuggestions /home/ubuntu/.oh-my-zsh/custom/plugins/zsh-autosuggestions
git clone https://github.com/zsh-users/zsh-syntax-highlighting /home/ubuntu/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting
git clone --depth=1 https://github.com/romkatv/powerlevel10k.git /home/ubuntu/.oh-my-zsh/custom/themes/powerlevel10k

# Download fonts
mkdir -p /home/ubuntu/.local/share/fonts
wget -P /home/ubuntu/.local/share/fonts https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20Regular.ttf
fc-cache -fv

# Change permissions
chown -R ubuntu:ubuntu /home/ubuntu
