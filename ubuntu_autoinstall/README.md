# Ubuntu Autoinstall (AutoOS)

This folder contains configuration files and documentation for automating Ubuntu installation using autoinstall.

## Files
- **autoinstall.yml**: Main autoinstall configuration file (located in `../Linux/ubuntu_autoinstall/autoinstall.yml`)

## What is Ubuntu Autoinstall?

Ubuntu Autoinstall is a feature that allows you to perform unattended installations of Ubuntu Server (20.04+). It uses a YAML configuration file to define all installation parameters, eliminating the need for manual interaction during the installation process.

## Prerequisites

Before you begin, ensure you have:
- Ubuntu Server ISO (20.04 LTS or newer)
- A USB drive (4GB or larger) or virtual machine
- Basic understanding of YAML syntax
- Knowledge of your target system's hardware specifications

## Step-by-Step Guide

### 1. Prepare the Autoinstall Configuration

The `autoinstall.yml` file in this repository contains a pre-configured setup. Review and customize it according to your needs:

```bash
# Clone this repository
git clone https://github.com/Ch3fUlrich/AutoOS.git
cd AutoOS/Linux/ubuntu_autoinstall

# Edit the autoinstall.yml file
nano autoinstall.yml
```

Key sections to customize:
- **identity**: Set your desired hostname, username, and password
- **network**: Configure network settings (DHCP or static IP)
- **storage**: Define disk partitioning and LVM setup
- **packages**: Add or remove packages to install
- **user-data**: Customize post-installation commands

### 2. Generate Password Hash

The autoinstall file requires a hashed password. Generate one using:

```bash
# Generate a password hash
mkpasswd --method=SHA-512 --rounds=4096

# Or use openssl
openssl passwd -6 -salt somesalt yourpassword
```

Replace the password value in the `identity` section with your generated hash.

### 3. Create Bootable Media

#### Option A: Using Ubuntu ISO with Cloud-Init

1. Download the Ubuntu Server ISO from [ubuntu.com](https://ubuntu.com/download/server)

2. Create a custom ISO with your autoinstall file:

```bash
# Install required tools
sudo apt install xorriso p7zip-full

# Extract the ISO
7z x ubuntu-*.iso -oiso_extract

# Create autoinstall directory structure
mkdir -p iso_extract/nocloud

# Copy your autoinstall file
cp autoinstall.yml iso_extract/nocloud/user-data

# Create empty meta-data file
touch iso_extract/nocloud/meta-data

# Recreate the ISO
xorriso -as mkisofs \
  -r -V "Ubuntu Autoinstall" \
  -o ubuntu-autoinstall.iso \
  -J -l -b isolinux/isolinux.bin \
  -c isolinux/boot.cat \
  -no-emul-boot -boot-load-size 4 -boot-info-table \
  -eltorito-alt-boot -e boot/grub/efi.img \
  -no-emul-boot -isohybrid-gpt-basdat \
  iso_extract/
```

#### Option B: Using HTTP Server (Network Install)

1. Host the autoinstall.yml file on a web server:

```bash
# Using Python's built-in HTTP server
python3 -m http.server 8000
```

2. Boot from standard Ubuntu Server ISO and at the installer prompt, press 'e' to edit boot parameters

3. Add the following to the kernel boot line:
```
autoinstall ds=nocloud-net;s=http://YOUR_SERVER_IP:8000/
```

### 4. Boot and Install

1. Boot your target system from the prepared media
2. The installation will proceed automatically
3. The system will reboot once installation is complete
4. Log in with the credentials you specified in the autoinstall.yml file

### 5. Post-Installation

After installation, the autoinstall configuration in this repository will:
- Install Oh My Zsh with Powerlevel10k theme
- Set up zsh plugins (autosuggestions, syntax highlighting)
- Install and configure various development tools
- Configure terminal fonts and appearance

You can verify the installation by checking:
```bash
# Check installed packages
dpkg -l | grep -E "ansible|python3|git"

# Verify zsh installation
zsh --version
echo $SHELL
```

## Configuration Options

### Identity Section
```yaml
identity:
  hostname: ubuntu-server    # Change to your desired hostname
  username: ubuntu           # Your username
  password: "$6$..."         # Hashed password (use mkpasswd)
```

### Network Section
```yaml
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true           # Or configure static IP
```

For static IP:
```yaml
network:
  version: 2
  ethernets:
    eth0:
      addresses: [192.168.1.100/24]
      gateway4: 192.168.1.1
      nameservers:
        addresses: [8.8.8.8, 8.8.4.4]
```

### Storage Section
The provided configuration uses LVM with:
- 1GB boot partition
- 20GB root partition (18GB usable, 2GB swap)
- Automatic disk selection (largest disk)

### Packages Section
Add or remove packages as needed:
```yaml
packages:
  - package-name
  - another-package
```

## Troubleshooting

### Installation Hangs or Fails

**Problem**: Installation stops at a specific step
**Solution**: 
- Press Ctrl+Alt+F2 to access a TTY console
- Check `/var/log/installer/autoinstall-user-data` for errors
- Verify your autoinstall.yml syntax using a YAML validator

### Password Authentication Fails

**Problem**: Cannot log in after installation
**Solution**: 
- Ensure your password hash was generated correctly
- Try generating a new hash with `mkpasswd --method=SHA-512 --rounds=4096`
- Verify there are no extra spaces or quotes in the password field

### Network Configuration Issues

**Problem**: Network not working after installation
**Solution**: 
- Check network interface names with `ip link show`
- Update the interface name in autoinstall.yml (may be `ens33` instead of `eth0`)
- Verify network settings in `/etc/netplan/`

### Storage/Partitioning Errors

**Problem**: Disk partitioning fails
**Solution**: 
- Ensure the disk is large enough for the defined partitions
- Verify disk paths match your hardware
- Try using `layout: lvm` for simpler configurations

### Custom Commands Fail

**Problem**: Commands in `runcmd` section fail
**Solution**: 
- Commands run as root, ensure paths and permissions are correct
- Add error checking: `- command || true` to continue on errors
- Check logs in `/var/log/cloud-init-output.log`

## Advanced Usage

### Using Variables and Templates

You can create multiple autoinstall files for different environments:
- `autoinstall-dev.yml` - Development environment
- `autoinstall-prod.yml` - Production environment
- `autoinstall-minimal.yml` - Minimal installation

### Integrating with AutoOS Scripts

After installation, you can run the AutoOS Linux setup scripts:

```bash
# Clone the repository
git clone https://github.com/Ch3fUlrich/AutoOS.git
cd AutoOS/Linux/bash

# Run the main setup script
bash main.sh
```

This will configure additional software and settings beyond the base autoinstall.

## Additional Resources

- [Official Ubuntu Autoinstall Documentation](https://ubuntu.com/server/docs/install/autoinstall)
- [Cloud-Init Documentation](https://cloudinit.readthedocs.io/)
- [Ubuntu Server Guide](https://ubuntu.com/server/docs)

---
See the main [README.md](../README.md) for repository overview.
