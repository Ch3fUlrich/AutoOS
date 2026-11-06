# OmniRoute and Qoder (empty gateway home)

D-4214 restores a writable, initially empty gateway home at `omniroute-home`,
mounted as the home directory of the image's `node` user with the root filesystem still read-only. The old
`qoder-home` directory is not reused. The compose image built from this repo
still has no `qodercli` layer; a fork image carrying it uses the new home.
Seat routing is unchanged (D-4250), including the host's `qodercli` ACP client
(`clients.qoder`, `gateway_mode: none`).
With the optional `compose.zcode.yml` overlay, Docker creates an empty
root-owned `.zcode` mountpoint inside `omniroute-home`; it stays after the
overlay is dropped and is harmless.
`migrate` calls `init`, which creates `omniroute-home` and sets mode 700 even
if the directory already exists, but copies only the separate `omniroute/`
data; neither migration nor rollback copies or removes the home or any
spawned-tool state written there, and rollback leaves it in place.
