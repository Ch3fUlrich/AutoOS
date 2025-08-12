# Windows Automation Tools

This directory contains comprehensive automation tools for Windows 10/11 systems using Ansible and PowerShell. The tools provide post-installation setup, software management, and terminal customization capabilities.

## 📁 Directory Structure

```
Windows/
├── ansible/              # Ansible playbooks for Windows automation
│   ├── playbooks/        # Individual automation playbooks
│   ├── installs/         # Software installation files
│   ├── inventory.yml     # Ansible inventory configuration
│   ├── main_playbook.yml # Main orchestration playbook
│   └── *.yml            # Configuration files
├── powershell/           # PowerShell scripts and configurations
│   └── oh-my-posh/      # Terminal customization setup
└── README.md             # This file
```

## 🚀 Quick Start

### Prerequisites Setup
1. **Enable WSL** (Windows Subsystem for Linux):
```powershell
# Run as Administrator
wsl --install
# Restart computer when prompted
```

2. **Setup SSH Server** on Windows:
```powershell
# Run as Administrator
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Protocol TCP -Action Allow -LocalPort 22
```

3. **Install Ansible** in WSL Ubuntu:
```bash
sudo apt update && sudo apt install ansible git sshpass -y
```

### Running Automation
```bash
git clone https://github.com/Ch3fUlrich/AutoOS.git
cd AutoOS/Windows/ansible
# Configure inventory.yml with your Windows credentials
ansible-playbook -i inventory.yml main_playbook.yml
```

## 📦 Available Tools

### 1. Ansible Playbooks (`ansible/`)
**Purpose**: Comprehensive Windows system automation using Ansible

**Capabilities**:
- Software installation via Chocolatey
- Python/Miniconda environment setup
- Network drive mapping
- Terminal and development environment configuration
- Automated software downloads and installations

**Key Playbooks**:
- `chocolatey_software.yml` - Install software packages via Chocolatey
- `python_miniconda_suite2p.yml` - Python development environment
- `add_drives.yml` - Network drive mapping
- `down_install_software.yml` - Download and install custom software
- `terminal_setup.yml` - Terminal and shell configuration

### 2. PowerShell Scripts (`powershell/`)
**Purpose**: Windows-native automation scripts

**Features**:
- Oh My Posh terminal theme installation
- Font management (Nerd Fonts)
- PowerShell profile configuration
- Windows-specific customizations

## ⚙️ Configuration

### Setting Up Ansible Inventory
Edit `ansible/inventory.yml`:
```yaml
[windows]
your-windows-pc ansible_host=192.168.1.100 ansible_user=your-username ansible_password=your-password ansible_connection=ssh
```

### Get Windows Connection Details
```powershell
# Get username
$Env:UserName

# Get IP address
ipconfig | Select-String "IPv4"
```

### Ansible Configuration
Create `~/.ansible.cfg` in WSL:
```ini
[defaults]
interpreter_python = auto_silent
host_key_checking = False
```

## 📋 Software Installation Categories

### Development Tools
- **Languages**: Python (Miniconda), Node.js, Git
- **Editors**: Visual Studio Code, Notepad++
- **Tools**: Docker Desktop, Postman, GitHub Desktop

### System Utilities
- **Browsers**: Firefox, Chrome
- **Media**: VLC, OBS Studio
- **Productivity**: 7-Zip, Adobe Reader
- **Communication**: Discord, Slack

### Terminal Enhancement
- **Oh My Posh**: Modern terminal theming
- **Nerd Fonts**: Icon-rich fonts for terminal
- **PowerShell**: Enhanced shell experience

## 🔧 Usage Examples

### Install Everything
```bash
cd Windows/ansible
ansible-playbook -i inventory.yml main_playbook.yml
```

### Install Specific Components
```bash
# Only install software via Chocolatey
ansible-playbook -i inventory.yml playbooks/chocolatey_software.yml

# Setup Python environment only
ansible-playbook -i inventory.yml playbooks/python_miniconda_suite2p.yml

# Configure terminal only
ansible-playbook -i inventory.yml playbooks/terminal_setup.yml
```

### PowerShell Terminal Setup
```powershell
# Run from Windows PowerShell
cd Windows/powershell/oh-my-posh
.\setup_ohmypsh.ps1
```

## 🎨 Customization

### Adding Software Packages
Edit `ansible/software_links.yml` to add new software:
```yaml
software_links:
  new_software:
    name: "Your Software"
    url: "https://download-url.com/software.exe"
    destination: "C:\\temp\\software.exe"
```

### Network Drives Configuration
Edit `ansible/network_drives.yml`:
```yaml
network_drives:
  - letter: "Z"
    path: "\\\\server\\share"
    username: "domain\\user"
    password: "password"
```

### Chocolatey Packages
Edit the chocolatey packages list in `playbooks/chocolatey_software.yml`:
```yaml
chocolatey_packages:
  - firefox
  - googlechrome
  - vscode
  - git
  - your-new-package
```

## 🔍 Troubleshooting

### Common Issues

**SSH Connection Failed**:
```bash
# Test SSH connection
ssh your-username@your-windows-ip

# Check SSH service status (Windows)
Get-Service -Name sshd
```

**Ansible Connection Issues**:
```bash
# Test Ansible connectivity
ansible windows -i inventory.yml -m win_ping

# Verbose output for debugging
ansible-playbook -i inventory.yml main_playbook.yml -vvv
```

**PowerShell Execution Policy**:
```powershell
# Set execution policy
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**WSL/Ubuntu Issues**:
```bash
# Update WSL
wsl --update

# Reset Ubuntu if needed
wsl --unregister Ubuntu
wsl --install
```

### Debug Mode
Run playbooks with increased verbosity:
```bash
ansible-playbook -i inventory.yml main_playbook.yml -vvv
```

## 🧪 Testing

### Pre-Production Testing
1. **Virtual Machine**: Test in Windows VM first
2. **Subset Installation**: Run individual playbooks
3. **Backup**: Create system restore point

```powershell
# Create restore point before automation
Checkpoint-Computer -Description "Before AutoOS Setup"
```

### Validation
```bash
# Check if all software installed correctly
ansible windows -i inventory.yml -m win_shell -a "choco list --local-only"
```

## 🔗 Related Documentation

- [Ansible Playbooks Guide](ansible/README.md)
- [PowerShell Scripts Guide](powershell/README.md)  
- [Main Project Documentation](../README.md)
- [Official Ansible Windows Guide](https://docs.ansible.com/ansible/latest/user_guide/windows.html)

## 💡 Tips

1. **Always run PowerShell as Administrator** for system-level changes
2. **Test network connectivity** before running Ansible playbooks
3. **Review software_links.yml** to understand what will be installed
4. **Monitor antivirus** - may interfere with installations
5. **Use WSL 2** for better performance with Ansible

---

**Security Note**: Be careful with credentials in inventory files. Consider using Ansible Vault for sensitive information in production environments.