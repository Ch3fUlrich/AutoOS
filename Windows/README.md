# Windows Setup (AutoOS)

This folder contains automated setup tools for Windows post-installation. Choose between the modern PowerShell-based approach or the traditional Ansible method.

## Structure
- **shell/**: **[NEW]** Modern PowerShell automation scripts with UniGetUI (recommended)
- **ansible/**: Ansible playbooks for Windows automation via WSL
- **powershell/**: PowerShell scripts and Oh My Posh configuration

## Quick Start (Recommended)

### Modern Approach - PowerShell Shell Scripts

The new `shell/` directory provides a streamlined, Windows-native automation experience:

1. **Clone the repository**:
   ```powershell
   git clone https://github.com/Ch3fUlrich/AutoOS.git
   cd AutoOS/Windows/shell
   ```

2. **Run the automated setup**:
   ```powershell
   .\Setup-Windows.ps1
   ```

This will:
- Install UniGetUI (modern package manager)
- Install all software packages via WinGet
- Set up Oh My Posh terminal customization
- Optionally run Ansible playbooks

**See [shell/README.md](shell/README.md) for detailed documentation.**

## Alternative Approach - Ansible + WSL

For advanced users who prefer Ansible automation:

### Getting Started
1. Install WSL and Ubuntu:
   ```powershell
   wsl --install
   ```
2. Set up OpenSSH server on Windows (see main README for details)
3. Install Ansible in Ubuntu (WSL):
   ```bash
   sudo apt update -y
   sudo apt install git sshpass ansible
   ansible --version
   ```
4. Clone this repository and navigate to the Windows/ansible folder:
   ```bash
   git clone https://github.com/Ch3fUlrich/AutoOS.git
   cd AutoOS/Windows/ansible
   ```
5. Edit `inventory.yml` with your Windows IP, username, and password
6. Run the main playbook:
   ```bash
   ansible-playbook -i inventory.yml main_playbook.yml
   ```

## PowerShell & Oh My Posh
- Integrated into the shell scripts (recommended)
- Or use standalone: `powershell/oh-my-posh/main.ps1`

## Features Comparison

| Feature | Shell Scripts (New) | Ansible (Traditional) |
|---------|-------------------|----------------------|
| Package Manager | UniGetUI/WinGet | Chocolatey |
| Requires WSL | Optional | Yes |
| Setup Complexity | Simple | Moderate |
| User Experience | Modern GUI + CLI | Terminal-based |
| Windows Integration | Native | Via SSH |
| Recommended For | All users | Advanced automation |

## Documentation

- **Shell Scripts**: [shell/README.md](shell/README.md) - Comprehensive guide for the new setup
- **Ansible**: Current documentation below
- **Oh My Posh**: [powershell/oh-my-posh/](powershell/oh-my-posh/)

---
See the main [README.md](../README.md) for repository overview.
