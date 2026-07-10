#!/bin/bash
echo 'HISTSIZE=50000' >> /home/ubuntu/.zshrc
echo 'SAVEHIST=10000' >> /home/ubuntu/.zshrc
echo 'ZSH_THEME="powerlevel10k/powerlevel10k"' >> /home/ubuntu/.zshrc
echo 'plugins=(z git zsh-autosuggestions zsh-syntax-highlighting colored-man-pages)' >> /home/ubuntu/.zshrc
chown -R ubuntu:ubuntu /home/ubuntu
