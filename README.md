# Post OS installation Setup
This Repository is for setting up a newly installed OS with all the necessary tools and configurations. For this process we will use [Ansible](https://docs.ansible.com/ansible/latest/getting_started/index.html) was born in the Linux world, support and capabilities for managing Windows environments have improved significantly.
Automations are done using [Ansible](https://docs.ansible.com/ansible/latest/getting_started/index.html).

# AutoOS: Automated Post-OS Installation Setup

AutoOS is a cross-platform automation toolkit for setting up newly installed operating systems (Windows, Linux, MacOS) with all necessary tools, configurations, and customizations. It leverages [Ansible](https://docs.ansible.com/ansible/latest/getting_started/index.html) for automation, supporting both Linux and Windows environments (via WSL for Windows).

## Repository Structure

- **Linux/**: Bash scripts and modules for configuring Ubuntu and other Linux distributions.
- **Windows/**: Ansible playbooks, PowerShell scripts, and Oh My Posh setup for Windows environments.
- **Network/**: Network configuration scripts (e.g., Eduroam setup for Linux).
- **Fonts/**: Recommended fonts for terminal and shell customization.
- **ubuntu_autoinstall/**: Ubuntu autoinstall configuration and documentation.

## Completed Steps

- Windows setup instructions (WSL, SSH server, Ansible configuration)
- Linux setup instructions (Ansible installation, playbook usage)
- Initial Ansible playbooks and inventory files for Windows automation
- Oh My Posh configuration for PowerShell
- Eduroam network setup script for Linux

## Upcoming Steps & Suggestions

- Add detailed setup instructions for each OS (Linux, Windows, MacOS)
- Automate more installation/configuration tasks (network, fonts, shell customization)
- Add troubleshooting and FAQ sections
- Integrate CI for script linting and validation
- Expand documentation for each module and script
- Add MacOS support and documentation

## Getting Started

See the subproject README files for OS-specific instructions:
- [Linux/README.md](Linux/README.md)
- [Windows/README.md](Windows/README.md)
- [Network/README.md](Network/README.md)
- [ubuntu_autoinstall/README.md](ubuntu_autoinstall/README.md)
- [Fonts/README.md](Fonts/README.md)

## Linux: CLI usage (bash modules)

This repository contains a set of bash modules under `Linux/bash/` used to perform post-install configuration on Ubuntu and related distributions. The GNOME extensions installer has been refactored into a small set of modules and supports non-interactive and dry-run modes.

Quick notes:
- Modules entrypoint: `Linux/bash/main.sh` and module files live under `Linux/bash/modules/`.
- GNOME extensions metadata: `Linux/bash/modules/extensions.json` (JSON is the canonical source; `extensions.yaml` is deprecated/removed).
- Key environment variables / CLI flags used by the bash modules:
    - `--dry-run` or set `DRY_RUN=true` — run the flow without performing mutating actions; commands are printed/logged but not executed.
    - `--non-interactive` / `--yes` / `-y` or set `AUTO_CONFIRM=true` — skip interactive prompts and accept defaults.
    - The GNOME installer exposes a thin loader: `Linux/bash/modules/gnome-extensions_installer.sh` which sources the core and platform helpers.

Module layout (important files):
- `Linux/bash/modules/utils.sh` — shared helpers: colored output (`cecho`), `safe_run()` (respects `DRY_RUN`), `confirm()`/`select_from_list()` (respect `AUTO_CONFIRM`), system helpers and package helpers.
- `Linux/bash/modules/gnome-extensions-core.sh` — core installer logic: JSON parsing, grouping, compatibility checks, install engine.
- `Linux/bash/modules/gnome-extensions-platform.sh` — platform-specific behavior (Raspberry Pi handling, GNOME-on-Pi helpers).
- `Linux/bash/pi_modules/install_gnome_pi.sh` — legacy shim that delegates to the canonical installer.

Examples:

Run a dry-run of the GNOME extensions installer (no changes):
```bash
# from repository root
DRY_RUN=true AUTO_CONFIRM=true bash Linux/bash/modules/gnome-extensions_installer.sh --dry-run --non-interactive
```

Run the full installer interactively (will prompt):
```bash
bash Linux/bash/main.sh
```

If you're running on a Raspberry Pi and GNOME is not installed, the platform helper will skip incompatible extensions and offers an optional flow to install GNOME (see `pi_modules/install_gnome_pi.sh`).


---
Below are the original setup instructions for Windows and Linux. These will be further improved and split into their respective subproject README files.

## Windows (10)
Since Ansible is not natively supported on Windows, we will use Windows Subsystem for Linux (WSL) to run Ansible. WSL allows you to run a Linux distribution on Windows without the need for a virtual machine or dual booting.

1. [Set Up Windows Subsystem for Linux](#set-up-windows-subsystem-for-linux)
2. [Install Orchestrator](#install-ansible).
3. [Setup SSH server on Windows](#setup-ssh-server-on-windows).
4. Test the connection to Windows from Ubuntu by running the following command:
```bash
ssh <windows-ip>
```
5. [Configure Ansible for Windows](#configure-ansible-for-windows).
6. [Setup Ansible scripts](#setup-ansible-scripts).
7. [Run the main playbook in the Ubuntu terminal](#run-playbooks).

### Set Up Windows Subsystem for Linux
1. Open PowerShell as Administrator and run the following line to install Ubuntu:
```powershell
wsl --install
```
2. **Restart** your computer when prompted.
   1. If the installation failed set the **virtualization** flag in the **BIOS** to **enabled**.
3. **Start** **Ubuntu** installation by searching for Ubuntu in the start menu.
4. Complete the installation and setup of the Linux distribution.

### Setup SSH Server on Windows
Since Ansible uses SSH to connect to Windows machines, we need to set up an SSH server on Windows. The following commands will install the OpenSSH server, start the service, set it to start automatically, and allow it through the firewall.

Open PowerShell as **Administrator** and run the following commands:

```powershell
# check if OpenSSH is installed
Get-WindowsCapability -Online | ? Name -like 'OpenSSH*'
# install the OpenSSH server (client is typically already installed)
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
# start the ssh server
Start-Service sshd
# Set SSH server to start automatically
Set-Service -Name sshd -StartupType 'Automatic'
# Allow SSH server through the firewall
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Protocol TCP -Action Allow -LocalPort 22
# verify ssh server is running
Get-Service -Name sshd
```


### Configure Ansible for Windows
Ansible uses an inventory file (ansible hosts) to specify which Windows machines to connect to and manage with Ansible playbooks.

Let‘s create an inventory file and set up key-based SSH authentication for passwordless connections:
1. **Start Ubuntu** installation by searching for Ubuntu in the start menu.
2. **Set defaults** for Ansible on Windows: Create a .ansible.cfg file in your home directory by running `nano ~/.ansible.cfg` and add the following lines:
```yaml
[defaults]
interpreter_python = auto_silent
host_key_checking = False
```
3. Press Ctrl + X to exit and save the file, then press Y to confirm.

### The Ansible setup will 

## Linux (Ubuntu)
### Install [Ansible](https://docs.ansible.com/ansible/latest/getting_started/index.html)
1. Update the package list and install [Ansible](https://docs.ansible.com/ansible/latest/getting_started/index.html): The packages that will be installed are:
    - python – Ansible is written in Python
    - python-pip – Used to install Ansible dependencies
    - software-properties-common – Allows apt-add-repository command
    - ansible – The latest Ansible release
```bash
sudo apt update -y
sudo apt upgrade -y
#sudo apt install software-properties-common
#sudo apt-add-repository --yes --update ppa:ansible/ansible
sudo apt install git sshpass ansible
# verify installation
ansible --version
```

### Setup Ansible scripts
1. After the (installation)[#install-ansible] you can clone this repository and go into the wanted directory e.g. **Windows**. 
```bash
git clone https://github.com/Ch3fUlrich/AutoOS.git
cd AutoOS/Windows/anisble
```
2. Get your windows username and ip address by running the following command in powershell.
```powershell
# get the username (this will be the username for the inventory file)
$Env:UserName
# the ip address should one of the ip addresses that pop up. Typically the top one. 
ipconfig | Select-String "IPv4"
```
3. Change the **windows_ip**, **windows_user** and **windows_password** in the inventory file from the step before. The password is the password of the user that is logged in.
```bash
nano inventory.yml
```
4. Ctrl + X to exit and save the file, then press Y to confirm.

### Configure Ansible for Linux
#TODO: Add Linux setup instructions

### Run Playbooks
To run all playbooks, use the following command:
```bash
ansible-playbook -i inventory.yml main_playbook.yml
```

## MacOS
#TODO: Add MacOS setup instructions


# Why Ansible?
- **Agentless**: Ansible doesn’t require any agent to be installed on the client machine. It uses SSH for connecting to the client machines.
- **Simple**: Ansible uses simple YAML syntax for writing playbooks.
- **Powerful**: Ansible can automate complex multi-tier IT application environments.
- **Flexible**: Ansible can be used to automate the configuration of a wide range of systems and devices such as servers, switches, storage, and cloud providers.
- **Efficient**: Ansible uses a push-based mechanism for executing tasks on client machines. It can manage thousands of client machines from a single control node.
- **Extensible**: Ansible can be extended by writing custom modules in any programming language.
- **Secure**: Ansible uses SSH for connecting to the client machines. It can also be integrated with enterprise authentication systems such as LDAP, Active Directory, and Kerberos.