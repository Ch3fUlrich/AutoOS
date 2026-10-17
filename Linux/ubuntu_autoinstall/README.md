# Unattended Ubuntu install

`autoinstall.yml` is a Subiquity autoinstall profile for **Ubuntu 24.04 LTS**
(the previous README said "24.01", which is not a release).

## Before you use it

Two values must be replaced or the installed machine is unusable:

```bash
mkpasswd -m sha-512          # put the result in identity.password
```

and your own SSH public key in `ssh.authorized-keys`. The committed values are
deliberate placeholders, not working defaults.

## Serving it

```bash
# in a directory containing this file as `user-data` plus an empty `meta-data`
python3 -m http.server 3003
```

Then boot the installer with:

```
autoinstall ds=nocloud-net;s=http://<your-ip>:3003/
```

## What it does

Installs a base package set, enables OpenSSH, then clones this repository and
hands off to `setup.sh --profile server`. The package list and shell setup are
**not** duplicated here — one implementation, in `setup.sh`.

## What was wrong with the previous version

- `storage:` declared both `layout:` and `config:`, which are mutually
  exclusive; the installer stopped at the storage stage.
- `initx` is not a package (it is `xinit`), which aborted the package stage.
- `ethernets: eth0` matches nothing on 24.04's predictable interface names, so
  the installed system booted with no network.
- No `ssh:` section at all, on a machine whose entire purpose is remote access.
- `runcmd` ran the oh-my-zsh installer as **root**, so it landed in
  `/root/.oh-my-zsh` while the plugin clones targeted `/home/ubuntu/...` — the
  theme and plugins were never loaded.
- `bash <(curl ...)` is a bash-only construct, but `runcmd` string entries run
  under `sh`, so the xpipe install silently never happened.
