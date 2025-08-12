# Ubuntu Autoinstall Configuration

This directory contains automated installation configurations for Ubuntu Server and Desktop, allowing for completely unattended OS installations with pre-configured settings.

## 🎯 Purpose

Ubuntu Autoinstall provides:
- **Unattended Installation**: Complete OS setup without manual intervention
- **Reproducible Deployments**: Consistent system configurations across multiple machines
- **Customizable Settings**: Pre-configured users, networking, and software
- **Time Savings**: Eliminate manual installation steps

## 📁 Directory Contents

```
ubuntu_autoinstall/
├── autoinstall.yml      # Main autoinstall configuration file
└── README.md            # This file
```

## 🚀 Usage

### Using with Ubuntu Installation Media

#### Method 1: USB/ISO Integration
1. **Download Ubuntu Server/Desktop ISO**
2. **Modify ISO** to include autoinstall.yml
3. **Boot from modified media**
4. **Installation proceeds automatically**

#### Method 2: Network Boot (PXE)
```bash
# Place autoinstall.yml on web server
# Configure PXE boot with autoinstall parameter
# Example kernel parameters:
autoinstall ds=nocloud-net;s=http://192.168.1.100/autoinstall/
```

#### Method 3: Cloud-Init Integration
```bash
# For cloud deployments
# Include autoinstall.yml in cloud-init user-data
# Supported on AWS, Azure, GCP, etc.
```

## ⚙️ Configuration Details

### Current Configuration (`autoinstall.yml`)

**System Settings**:
- **Hostname**: ubuntu-server (customizable)
- **User**: ubuntu with encrypted password
- **Locale**: en_US.UTF-8
- **Keyboard**: US layout
- **Storage**: LVM with 2GB swap

**Network Configuration**:
- **DHCP**: Automatic IP configuration
- **SSH**: OpenSSH server enabled
- **Firewall**: Basic UFW configuration

**Storage Layout**:
```yaml
storage:
  layout:
    name: lvm
  swap:
    size: 2GB
```

### Customization Options

#### User Account Configuration
```yaml
identity:
  hostname: your-hostname
  username: your-username
  password: "$6$rounds=4096$salt$hashedpassword"
  ssh_authorized_keys:
    - "ssh-rsa AAAA... your-key"
```

#### Network Configuration
```yaml
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
  # Or static configuration:
    eth0:
      addresses: [192.168.1.100/24]
      gateway4: 192.168.1.1
      nameservers:
        addresses: [8.8.8.8, 1.1.1.1]
```

#### Software Packages
```yaml
packages:
  - curl
  - wget
  - git
  - vim
  - htop
  - docker.io
```

#### Post-Installation Commands
```yaml
late-commands:
  - curtin in-target --target=/target -- systemctl enable ssh
  - curtin in-target --target=/target -- apt update
  - curtin in-target --target=/target -- snap install code --classic
```

## 🔧 Advanced Features

### Integration with AutoOS Scripts
**Planned Enhancement**: Automatic integration with AutoOS bash scripts
```yaml
late-commands:
  - curtin in-target --target=/target -- wget -O /home/ubuntu/setup.sh https://raw.githubusercontent.com/Ch3fUlrich/AutoOS/main/Linux/bash/install.sh
  - curtin in-target --target=/target -- chmod +x /home/ubuntu/setup.sh
  - curtin in-target --target=/target -- sudo -u ubuntu /home/ubuntu/setup.sh
```

### Multiple Environment Configurations
Create different configurations for various use cases:
- `autoinstall-server.yml` - Minimal server installation
- `autoinstall-desktop.yml` - Desktop environment with GUI
- `autoinstall-dev.yml` - Development workstation setup

### Password Generation
```bash
# Generate encrypted password for autoinstall
openssl passwd -6 -salt xyz your-password
# Or use mkpasswd
mkpasswd --method=SHA-512 --rounds=4096 your-password
```

## 🔍 Troubleshooting

### Common Issues

**Installation Fails to Start**:
- Verify autoinstall.yml syntax with YAML validator
- Check file accessibility via HTTP/network
- Ensure proper kernel parameters

**User Creation Fails**:
```bash
# Test password hash generation
python3 -c "import crypt; print(crypt.crypt('password', crypt.mksalt(crypt.METHOD_SHA512)))"
```

**Network Configuration Issues**:
- Verify interface names match target hardware
- Test network configuration syntax
- Check DHCP server availability

**Storage Configuration Problems**:
- Ensure target disk size meets requirements
- Verify partition layout matches hardware
- Check LVM configuration syntax

### Validation
```bash
# Validate YAML syntax
python3 -c "import yaml; yaml.safe_load(open('autoinstall.yml'))"

# Test with cloud-init
cloud-init schema --config-file autoinstall.yml
```

## 📊 Testing

### Virtual Machine Testing
```bash
# Test with QEMU/KVM
qemu-system-x86_64 -m 2048 -cdrom ubuntu-server.iso -hda test-disk.qcow2 -netdev user,id=net0 -device e1000,netdev=net0

# Test with VirtualBox
VBoxManage createvm --name "Ubuntu-Test" --ostype Ubuntu_64
VBoxManage modifyvm "Ubuntu-Test" --memory 2048 --dvd ubuntu-server.iso
```

### Container Testing
```bash
# Test autoinstall syntax in container
docker run --rm -v $(pwd):/workspace ubuntu:latest bash -c "
  apt update && apt install -y cloud-init
  cloud-init schema --config-file /workspace/autoinstall.yml
"
```

## 🔗 Future Enhancements

### Planned Improvements
- [ ] **Modular Software Installation**: Small bash scripts for specific software categories
- [ ] **AutoOS Integration**: Automatic download and execution of AutoOS bash scripts
- [ ] **Multiple Profiles**: Different configurations for server, desktop, and development setups
- [ ] **Security Hardening**: Enhanced security configurations and policies
- [ ] **Hardware Detection**: Automatic hardware-specific optimizations

### Integration Roadmap
```yaml
# Future autoinstall.yml structure
late-commands:
  - wget -O /tmp/autoos.tar.gz https://github.com/Ch3fUlrich/AutoOS/archive/main.tar.gz
  - tar -xzf /tmp/autoos.tar.gz -C /home/ubuntu/
  - sudo -u ubuntu /home/ubuntu/AutoOS-main/Linux/bash/install.sh --auto
```

## 🔗 Related Documentation

- [Ubuntu Autoinstall Documentation](https://ubuntu.com/server/docs/install/autoinstall)
- [Cloud-Init Documentation](https://cloudinit.readthedocs.io/)
- [Linux Bash Scripts](../bash/README.md)
- [AutoOS Main Documentation](../../README.md)

## 💡 Best Practices

1. **Security**: Use encrypted passwords and SSH keys
2. **Testing**: Always test configurations in virtual environments
3. **Backup**: Keep backup configurations for different scenarios
4. **Documentation**: Document custom configurations and requirements
5. **Version Control**: Track autoinstall configurations in Git
6. **Validation**: Validate YAML syntax before deployment

---

**Security Note**: Autoinstall files may contain sensitive information. Secure properly and use encrypted passwords. Never commit plain text passwords to version control.

