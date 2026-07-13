# Automated Ubuntu Installation
This Folder contains files to automate the installation of Ubuntu 24.01 LTS.
On a fresh installation of Ubuntu a yaml file can be provided to automate the installation of the OS.

## Usage

To avoid hardcoding sensitive information like passwords in the `autoinstall.yml` file, the password field uses a placeholder (`${UBUNTU_PASSWORD_HASH}`). You must generate the final configuration file before use.

1. Generate a password hash (e.g., using `mkpasswd -m sha-512`).
2. Set the environment variable and use `envsubst` to create the final `user-data` file:

```bash
export UBUNTU_PASSWORD_HASH='$6$rounds=4096$somesalt$hashedpassword'
envsubst < autoinstall.yml > user-data
```

#TODO: Create small bash files to automate the installation of specific software. LIke
- Terminal Design
- Programming Software
- Dotfiles

#TODO: change current autoinstall.yaml to download from AutoOS repo and use the small bash files to install software.

