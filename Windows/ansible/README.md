# Windows Ansible Automation

This directory contains Ansible playbooks and configurations for comprehensive Windows system automation. The playbooks handle software installation, system configuration, and development environment setup using Infrastructure as Code principles.

## 📁 Directory Structure

```
ansible/
├── playbooks/                    # Individual automation playbooks
│   ├── chocolatey_software.yml   # Software installation via Chocolatey
│   ├── python_miniconda_suite2p.yml # Python development environment
│   ├── add_drives.yml            # Network drive mapping
│   ├── down_install_software.yml # Custom software downloads
│   └── terminal_setup.yml        # Terminal configuration
├── installs/                     # Downloaded software installers
├── inventory.yml                 # Ansible inventory file
├── main_playbook.yml             # Main orchestration playbook
├── software_links.yml            # Software download links
├── network_drives.yml            # Network drive configurations
└── README.md                     # This file
```

## 🎯 Purpose

This Ansible automation suite provides:
- **Idempotent Operations**: Safe to run multiple times
- **Declarative Configuration**: Define desired state, not steps
- **Modular Design**: Individual playbooks for specific tasks
- **Scalable Deployment**: Manage multiple Windows machines
- **Version Control**: Track configuration changes over time

## 🚀 Quick Start

### 1. Prerequisites Setup
Ensure WSL, SSH, and Ansible are configured (see [Windows README](../README.md))

### 2. Configure Inventory
Edit `inventory.yml` with your Windows machine details:
```yaml
[windows]
desktop-pc ansible_host=192.168.1.100 ansible_user=john ansible_password=secure_password ansible_connection=ssh

[windows:vars]
ansible_shell_type=cmd
ansible_python_interpreter=auto_silent
```

### 3. Test Connection
```bash
ansible windows -i inventory.yml -m win_ping
```

### 4. Run Main Playbook
```bash
ansible-playbook -i inventory.yml main_playbook.yml
```

## 📦 Playbook Details

### 1. Chocolatey Software (`chocolatey_software.yml`)
**Purpose**: Install and manage software packages using Chocolatey package manager

**Features**:
- Chocolatey installation and setup
- Bulk software installation
- Package version management
- Dependency resolution

**Example Packages**:
```yaml
chocolatey_packages:
  - firefox
  - googlechrome
  - vscode
  - git
  - nodejs
  - python
  - docker-desktop
```

**Usage**:
```bash
ansible-playbook -i inventory.yml playbooks/chocolatey_software.yml
```

### 2. Python Environment (`python_miniconda_suite2p.yml`)
**Purpose**: Set up comprehensive Python development environment

**Features**:
- Miniconda installation
- Python package management
- Virtual environment creation
- Development tools setup
- Suite2P neuroscience toolkit

**Includes**:
- Miniconda Python distribution
- Essential Python packages (numpy, pandas, matplotlib)
- Jupyter Notebook environment
- Scientific computing libraries

**Usage**:
```bash
ansible-playbook -i inventory.yml playbooks/python_miniconda_suite2p.yml
```

### 3. Network Drives (`add_drives.yml`)
**Purpose**: Map network drives and shared folders

**Features**:
- Persistent drive mapping
- Credential management
- Share accessibility testing
- Drive letter assignment

**Configuration**: Edit `network_drives.yml`
```yaml
network_drives:
  - letter: "Z"
    path: "\\\\fileserver\\shared"
    username: "domain\\user"
    password: "{{ vault_password }}"
```

### 4. Software Downloads (`down_install_software.yml`)
**Purpose**: Download and install software not available via Chocolatey

**Features**:
- Direct download from URLs
- Checksum verification
- Silent installation
- Custom installer arguments

**Configuration**: Edit `software_links.yml`
```yaml
software_links:
  adobe_reader:
    name: "Adobe Acrobat Reader"
    url: "https://get.adobe.com/reader/download/"
    destination: "C:\\temp\\AdobeReader.exe"
    install_args: "/S"
```

### 5. Terminal Setup (`terminal_setup.yml`)
**Purpose**: Configure and enhance terminal experience

**Features**:
- PowerShell profile creation
- Oh My Posh installation
- Font installation (Nerd Fonts)
- Terminal theme configuration

## ⚙️ Configuration Management

### Inventory Variables
Customize behavior with inventory variables:
```yaml
[windows:vars]
chocolatey_packages_state: present
python_version: "3.11"
install_dev_tools: true
create_desktop_shortcuts: false
```

### Variable Files
- `software_links.yml`: Download URLs and installation settings
- `network_drives.yml`: Network share configurations
- Custom variables can be added per playbook

### Ansible Vault for Secrets
Protect sensitive information:
```bash
# Create encrypted file
ansible-vault create secrets.yml

# Edit encrypted file
ansible-vault edit secrets.yml

# Run playbook with vault
ansible-playbook -i inventory.yml main_playbook.yml --ask-vault-pass
```

## 🔧 Customization

### Adding New Software
1. **Via Chocolatey**: Add package name to chocolatey packages list
2. **Direct Download**: Add entry to `software_links.yml`
3. **Custom Installation**: Create new playbook in `playbooks/`

### Example Custom Playbook
```yaml
---
- name: Install Custom Software
  hosts: windows
  tasks:
    - name: Download custom software
      win_get_url:
        url: "{{ custom_software_url }}"
        dest: "C:\\temp\\custom_software.exe"
    
    - name: Install custom software
      win_package:
        path: "C:\\temp\\custom_software.exe"
        arguments: "/S /VERYSILENT"
        state: present
```

### Module Configuration
Individual modules can be enabled/disabled:
```yaml
# In main_playbook.yml
- import_playbook: playbooks/chocolatey_software.yml
  when: install_chocolatey | default(true)

- import_playbook: playbooks/python_miniconda_suite2p.yml
  when: install_python | default(true)
```

## 🔍 Troubleshooting

### Common Issues

**Ansible Connection Failures**:
```bash
# Test basic connectivity
ansible windows -i inventory.yml -m win_ping -vvv

# Check SSH configuration
ssh username@windows-host

# Verify OpenSSH service on Windows
Get-Service -Name sshd
```

**Software Installation Failures**:
```bash
# Check Chocolatey status
ansible windows -i inventory.yml -m win_shell -a "choco --version"

# Test manual installation
ansible windows -i inventory.yml -m win_chocolatey -a "name=firefox state=present"
```

**Network Drive Issues**:
```bash
# Test network connectivity
ansible windows -i inventory.yml -m win_shell -a "Test-NetConnection fileserver -Port 445"

# Check current mapped drives
ansible windows -i inventory.yml -m win_shell -a "Get-PSDrive -PSProvider FileSystem"
```

### Debug Mode
Run with maximum verbosity:
```bash
ansible-playbook -i inventory.yml main_playbook.yml -vvv
```

### Dry Run Mode
Preview changes without execution:
```bash
ansible-playbook -i inventory.yml main_playbook.yml --check --diff
```

## 📊 Monitoring and Validation

### Post-Installation Verification
```bash
# Check installed software
ansible windows -i inventory.yml -m win_shell -a "choco list --local-only"

# Verify Python installation
ansible windows -i inventory.yml -m win_shell -a "python --version"

# Check mapped drives
ansible windows -i inventory.yml -m win_shell -a "Get-PSDrive"
```

### Health Checks
Create validation playbook:
```yaml
---
- name: System Health Check
  hosts: windows
  tasks:
    - name: Check disk space
      win_shell: Get-WmiObject -Class Win32_LogicalDisk | Select-Object Size,FreeSpace,DeviceID
      
    - name: Verify services
      win_service_info:
        name: "{{ item }}"
      loop:
        - sshd
        - Themes
        - AudioSrv
```

## 🔗 Related Documentation

- [Ansible Windows Documentation](https://docs.ansible.com/ansible/latest/user_guide/windows.html)
- [Chocolatey Package Repository](https://community.chocolatey.org/packages)
- [Main Windows Setup Guide](../README.md)
- [AutoOS Project Overview](../../README.md)

## 💡 Best Practices

1. **Version Control**: Track all configuration changes in Git
2. **Testing**: Test playbooks in virtual machines first
3. **Idempotency**: Ensure playbooks can be run multiple times safely
4. **Documentation**: Document custom variables and requirements
5. **Security**: Use Ansible Vault for passwords and sensitive data
6. **Modular Design**: Keep playbooks focused on single responsibilities
7. **Error Handling**: Add proper error handling and rollback procedures

---

**Security Note**: Always review playbook contents before execution. Use Ansible Vault for sensitive information and never commit passwords to version control.