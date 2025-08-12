# Linux Setup (AutoOS)

This folder contains scripts and modules for automating the setup of a newly installed Linux system (primarily Ubuntu) using Bash and Ansible.

## Structure
- **bash/**: Main bash scripts for Linux automation
- **modules/**: Modular scripts for packages, GNOME setup, programming tools, shell customization, and utilities
- **extensions/**: GNOME extensions and utilities

## Getting Started
1. Update and upgrade your system:
   ```bash
   sudo apt update -y
   sudo apt upgrade -y
   ```
2. Install Ansible and dependencies:
   ```bash
   sudo apt install git sshpass ansible
   ansible --version
   ```
3. Clone this repository and navigate to the Linux folder:
   ```bash
   git clone https://github.com/Ch3fUlrich/AutoOS.git
   cd AutoOS/Linux/bash
   ```
4. Run the main setup script:
   ```bash
   bash main.sh
   ```

## Modules
- **core_packages.sh**: Installs essential packages
- **gnome_setup.sh**: Configures GNOME desktop
- **programming.sh**: Installs programming tools
- **shell_setup.sh**: Customizes shell environment
- **utils.sh**: Miscellaneous utilities

## GNOME Extensions
- Use scripts in `extensions/` to install and manage GNOME extensions.

## Upcoming Improvements
- Add troubleshooting and FAQ section
- Expand documentation for each module
- Integrate CI for script linting

---
See the main [README.md](../README.md) for repository overview.
