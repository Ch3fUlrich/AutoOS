#!/bin/bash

# install languages
## C++
sudo apt install -y clang

## python 3
# Install Python and required packages
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-setuptools \
    python3-wheel

# Verify Python installation
python3 --version
if [ $? -eq 0 ]; then
    echo "Python installed successfully"
    # Verify installations
else
    echo "Python installation failed"
fi
python3 -c "import pip" && echo "Pip is working"

# install git
sudo apt install -y git 

# windowmanager
sudo apt Install initx openbox

# virtual Terminals
sudo apt Install tmux

# Browser
sudo apt Install w3m firefox-esr

# text editor
sudo apt install -y nano neovim
git clone https://github.com/LazyVim/starter ~/.config/nvim
rm -rf ~/.config/nvim/.git

# download tools
sudo apt install wget curl 

# install monitoring tools 
sudo apt install -y htop nmon neofetch

# install file commander
sudo apt install -y mc

# automation
sudo apt install -y ansible
sudo apt install -y cron

# add nfs support
sudo apt install -y nfs-common

# file movement
sudo apt install -y rsync

# install docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
groupadd docker
usermod -aG docker $USER
# check if docker is running
#service docker status
# start if not running
systemctl start docker
# enable docker autostarting at reboot
systemctl enable docker.service
systemctl enable containerd.service

# install portainer agent
docker run -d -p 9001:9001 --name portainer_agent --restart=always -v /var/run/docker.sock:/var/run/docker.sock -v /var/lib/docker/volumes:/var/lib/docker/volumes portainer/agent:latest   # create directory for apps
mkdir apps




# install shell tools
sudo apt install -y zsh
# install oh-my-zsh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# zsh plugins
## add zsh-autosuggestions
git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions
## add zsh-syntax-highlighting
git clone https://github.com/zsh-users/zsh-syntax-highlighting ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting

HISTSIZE=50000         # Number of commands to keep in memory
SAVEHIST=10000         # Number of commands to save in the history file

# change theme

## Download good fonts
# Create fonts directory if it doesn't exist
mkdir -p ~/.local/share/fonts

## Download single font directly to fonts directory
wget -P ~/.local/share/fonts https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20Regular.ttf
## Update font cache after installation
fc-cache -fv

## Create a temporary directory for downloading
#mkdir -p ~/nerd-fonts-temp
#cd ~/nerd-fonts-temp
## Clone the complete Nerd Fonts repository
#git clone --depth 1 https://github.com/ryanoasis/nerd-fonts.git
## Go into the directory
#cd nerd-fonts
## Install all fonts
### install all available fonts
#./install.sh
#./install.sh FiraCode    # Great for coding, has ligatures
#./install.sh Hack        # Clean and readable
#./install.sh JetBrainsMono # Popular for IDEs
#./install.sh UbuntuMono  # Good terminal font
#./install.sh SourceCodePro # Adobe's coding font
#./install.sh Meslo
# The script will install to ~/.local/share/fonts/
# Update font cache after installation
#fc-cache -fv
## Clean up
#cd ~
#rm -rf ~/nerd-fonts-temp


## Powerlevel10k theme (best theme)
git clone --depth=1 https://github.com/romkatv/powerlevel10k.git ${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/themes/powerlevel10k
NEW_THEME="powerlevel10k/powerlevel10k"

## Alternative theme if you don't like Powerlevel10k
### NEW_THEME="jonathan"
# Update ZSH_THEME line in ~/.zshrc
sed -i "s|^ZSH_THEME=\".*\"|ZSH_THEME=\"$NEW_THEME\"|" ~/.zshrc
## add plugins to .zshrc
sed -i "s/^plugins=.*/plugins=(z git zsh-autosuggestions zsh-syntax-highlighting colored-man-pages)/" ~/.zshrc

# start p10k configuration
exec zsh
# if the configuration wizard doesn't start automatically.
# p10k configure 

## update changes
source  ~/.zshrc
