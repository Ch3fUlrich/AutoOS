# Post OS installation Setup
This Repository is for setting up a newly installed OS with all the necessary tools and configurations. For this process we will use [Ansible](https://docs.ansible.com/ansible/latest/getting_started/index.html) was born in the Linux world, support and capabilities for managing Windows environments have improved significantly.
Automations are done using [Ansible](https://docs.ansible.com/ansible/latest/getting_started/index.html).

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
Get-WindowsCapability -Online | ? Name -like 'OpenSSH*'
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
```bash
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
2. After the installation, you can clone this repository.
```bash
git clone https://github.com/Ch3fUlrich/Ansible-Setup-OS.git
```
3. Get your windows ip address by running the following command in powershell.
```powershell
# the ip address should look like 192.156.125.132
ipconfig
```
4. Change the **IP addresses** in the inventory file the windows IP from Steop 3. In this case we will use the localhost.
```bash
nano inventory.yml
```
5. Ctrl + X to exit and save the file, then press Y to confirm.

#TODO: is this correct?
```yaml
all:
  hosts:
    localhost:
      ansible_connection: local
```
#TODO: create an automated script for doing the above steps

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