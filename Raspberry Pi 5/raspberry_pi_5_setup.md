# 🥧 Raspberry Pi 5 Setup Guide

## 🔐 Autologin Configuration

Configure automatic login for the current user:
```bash
sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $USER --noclear %I \$TERM
EOF
```

## 🎮 Gaming Setup

### RustDesk Remote Desktop

1. **Install Pi-Apps:**
   ```bash
   wget -qO- https://raw.githubusercontent.com/Botspot/pi-apps/master/install | bash
   ```

2. **Install RustDesk:**
   - Open Pi-Apps
   - Navigate to Internet category
   - Install RustDesk

### Moonlight Game Streaming

#### Option 1: Automated Install (May Have Limited HEVC Codec Support)
```bash
# Update system
sudo apt update
sudo apt upgrade -y

# Install audio dependencies for proper audio handling
sudo apt install curl pulseaudio
sudo systemctl --global enable pulseaudio

# Add Moonlight repository (official script)
curl -1sLf 'https://dl.cloudsmith.io/public/moonlight-game-streaming/moonlight-qt/setup.deb.sh' | distro=raspbian codename=$(lsb_release -cs) sudo -E bash

# Install Moonlight
sudo apt install moonlight-qt
```

#### Option 2: Build from Source (Better Codec Support)
```bash
# Install required libraries
sudo apt install libegl1-mesa-dev libgl1-mesa-dev libopus-dev libsdl2-dev \
  libsdl2-ttf-dev libssl-dev libavcodec-dev libavformat-dev libswscale-dev \
  libva-dev libvdpau-dev libxkbcommon-dev wayland-protocols libdrm-dev

# Install Qt6 dependencies
sudo apt install qmake6 qt6-base-dev qt6-declarative-dev libqt6svg6-dev \
  qml6-module-qtquick-controls qml6-module-qtquick-templates \
  qml6-module-qtquick-layouts qml6-module-qtqml-workerscript \
  qml6-module-qtquick-window qml6-module-qtquick

# Clone and build Moonlight
git clone https://github.com/moonlight-stream/moonlight-qt.git
cd moonlight-qt
git submodule update --init --recursive
qmake6 "CONFIG+=embedded" "CONFIG+=gpuslow" moonlight-qt.pro
make -j$(nproc)
sudo make install

# Launch Moonlight
moonlight
```   

### Moonlight Configuration

#### Recommended Quality Settings

For **4K displays**, use **2K streaming quality** for optimal connection stability and performance.
