# AutoOS - Automated Operating System Setup

AutoOS is a comprehensive collection of automation scripts and configurations designed to streamline the post-installation setup of operating systems. This repository provides ready-to-use solutions for configuring fresh OS installations with essential tools, applications, and custom configurations.

## 🎯 Purpose

AutoOS eliminates the tedious manual setup process after a fresh OS installation by providing:
- **Automated software installation** and configuration
- **Consistent development environments** across different machines
- **Modular setup scripts** for different use cases
- **Cross-platform support** for Linux and Windows

## 📁 Repository Structure

```
AutoOS/
├── Linux/                    # Linux automation tools
│   ├── bash/                 # Bash scripts for Ubuntu/Debian setup
│   ├── ubuntu_autoinstall/   # Ubuntu automated installation configs
│   └── Network/              # Network configuration tools
├── Windows/                  # Windows automation tools
│   ├── ansible/              # Ansible playbooks for Windows
│   └── powershell/           # PowerShell scripts and configurations
├── Fonts/                    # Font resources and installations
└── README.md                 # This file
```

## 🚀 Quick Start

### For Linux (Ubuntu/Debian)
```bash
git clone https://github.com/Ch3fUlrich/AutoOS.git
cd AutoOS/Linux/bash
./install.sh
```

### For Windows
1. Set up WSL and SSH (see [Windows Setup Guide](Windows/README.md))
2. Use Ansible playbooks:
```bash
cd AutoOS/Windows/ansible
ansible-playbook -i inventory.yml main_playbook.yml
```

## ✅ Completed Features

- **Linux Setup Scripts**: Modular bash scripts for Ubuntu/Debian configuration
- **Windows Automation**: Ansible playbooks for Windows software installation
- **Terminal Customization**: Oh My Posh setup for Windows PowerShell
- **Network Configuration**: Automated eduroam setup for Linux
- **Font Management**: Nerd Font installation and configuration
- **Development Tools**: Programming language and tool installation

## 🔄 Platform Support

| Platform | Status | Automation Tool | Documentation |
|----------|--------|-----------------|---------------|
| Ubuntu 24.04 LTS | ✅ Complete | Bash Scripts | [Linux/README.md](Linux/README.md) |
| Windows 10/11 | ✅ Complete | Ansible + PowerShell | [Windows/README.md](Windows/README.md) |
| macOS | 🔄 Planned | TBD | Coming Soon |

## 🛠️ Automation Technologies

- **[Ansible](https://docs.ansible.com/ansible/latest/getting_started/index.html)**: Primary automation framework
- **Bash Scripts**: Linux system configuration
- **PowerShell**: Windows-specific automation
- **WSL**: Windows Subsystem for Linux integration

## 📋 Detailed Setup Instructions

For platform-specific setup instructions, see the respective directories:
- **Linux Setup**: [Linux/README.md](Linux/README.md)
- **Windows Setup**: [Windows/README.md](Windows/README.md)

## 🔮 Proposed Future Steps

### Near-term Improvements
- [ ] **Enhanced Setup Instructions**: Add detailed, step-by-step setup guides for each OS
- [ ] **Automation Expansion**: Automate more installation and configuration tasks
- [ ] **Troubleshooting Documentation**: Add comprehensive FAQ and troubleshooting sections
- [ ] **macOS Support**: Extend automation to macOS platforms

### Development & Quality
- [ ] **CI/CD Integration**: Implement continuous integration for script linting and validation
- [ ] **Testing Framework**: Add automated testing for all scripts and playbooks
- [ ] **Code Quality**: Implement linting and code quality checks
- [ ] **Documentation**: Expand documentation for each module and script

### Advanced Features
- [ ] **Configuration Management**: Add dotfiles and configuration synchronization
- [ ] **Cloud Integration**: Support for cloud-based development environments
- [ ] **Container Support**: Docker and container development environment setup
- [ ] **Security Hardening**: Add security configuration and hardening scripts

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/new-automation`
3. Make your changes and test thoroughly
4. Submit a pull request with a clear description

## 📖 Documentation

- [Linux Documentation](Linux/README.md) - Linux automation scripts and tools
- [Windows Documentation](Windows/README.md) - Windows automation with Ansible and PowerShell
- [Network Setup](Linux/Network/README.md) - Network configuration tools
- [Font Resources](Fonts/README.md) - Font installation and management

## 🔧 Why Ansible?

AutoOS leverages Ansible as the primary automation framework because it offers:

- **Agentless Architecture**: No agent installation required on target machines
- **Simple YAML Syntax**: Easy-to-read and maintain automation scripts
- **Cross-Platform Support**: Manages Linux, Windows, and network devices
- **Idempotent Operations**: Safe to run multiple times with consistent results
- **Extensive Module Library**: Rich ecosystem of pre-built automation modules
- **Enterprise Integration**: Supports LDAP, Active Directory, and Kerberos authentication

## 📄 License

This project is open source. Please check individual scripts for specific licensing information.

---

**Note**: Always review and understand the scripts before running them on your system. Test in a virtual machine or non-production environment first.