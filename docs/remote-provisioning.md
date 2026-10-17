# Remote provisioning

`setup.ps1` and `setup.sh` set up **the machine you are on**. Two other paths
exist for machines you are not.

## Ansible — an existing Windows machine over the network

`Windows/ansible/` is separate from the local entry points and is not used by
`setup.ps1`.

```bash
ansible-galaxy collection install -r Windows/ansible/requirements.yml
cp Windows/ansible/inventory.example.yml Windows/ansible/inventory.yml   # then edit
ansible-vault create Windows/ansible/group_vars/windows/vault.yml
ansible-playbook -i Windows/ansible/inventory.yml \
                 Windows/ansible/main_playbook.yml --ask-vault-pass
```

`inventory.yml` and `vault.yml` are git-ignored. Only `.example` templates are
tracked. See [Security](security.md).

### Target requirements

The `win_*` modules are PowerShell-based, so the target's OpenSSH default shell
must **be** PowerShell. Once, on the target:

```powershell
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell `
  -Value 'C:\Program Files\PowerShell\7\pwsh.exe' -PropertyType String -Force
```

Then `ansible_shell_type: powershell` in the inventory.

### Playbooks

| Playbook | Does |
|---|---|
| `chocolatey_software.yml` | Baseline packages via Chocolatey |
| `python_miniconda_suite2p.yml` | Miniconda + an isolated `suite2p` environment |
| `down_install_software.yml` | Downloads vendor installers and runs them |
| `terminal_setup.yml` | oh-my-posh theme into the right shell's profile |
| `add_drives.yml` | Maps network drives from the vault |

## Unattended Ubuntu install

`Linux/ubuntu_autoinstall/autoinstall.yml` is a Subiquity profile for
**Ubuntu 24.04 LTS**. It installs a base set, enables OpenSSH, then clones this
repository and hands off to `setup.sh --profile server` — the package list is
not duplicated.

Two values **must** be replaced before use or the machine is unusable:

```bash
mkpasswd -m sha-512     # -> identity.password
```

plus your SSH public key in `ssh.authorized-keys`. The committed values are
deliberate placeholders.

Serve it as `user-data` alongside an empty `meta-data`:

```bash
python3 -m http.server 3003
```

then boot the installer with:

```
autoinstall ds=nocloud-net;s=http://<your-ip>:3003/
```

Full detail and the list of what was wrong with the previous version:
[Linux/ubuntu_autoinstall/README.md](../Linux/ubuntu_autoinstall/README.md).

## Which one do I want?

| Situation | Use |
|---|---|
| The machine in front of me | `setup.ps1` / `setup.sh` |
| A headless box I can reach | `setup.sh --serve` ([browser UI](web-ui.md)) |
| Repeat a setup I already did | `--from-state` ([replay](state-and-undo.md)) |
| An existing Windows machine over SSH | `Windows/ansible/` |
| A machine that has no OS yet | `Linux/ubuntu_autoinstall/` |
