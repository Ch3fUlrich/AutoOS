# Windows Setup (AutoOS)

This folder contains Ansible playbooks, PowerShell scripts, and configuration files for automating Windows post-installation setup. Windows automation is performed via WSL (Windows Subsystem for Linux) and SSH.

## Structure
- **ansible/**: Inventory and playbooks for Windows automation
- **powershell/**: PowerShell scripts and Oh My Posh configuration

## Getting Started
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
- See `powershell/oh-my-posh/README.md` for shell customization instructions

## Upcoming Improvements
- Add troubleshooting and FAQ section
- Expand documentation for each playbook and script
- Integrate CI for playbook validation

---
See the main [README.md](../README.md) for repository overview.
