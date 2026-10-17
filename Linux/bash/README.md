# Deprecated

`install.sh` here is a thin shim that forwards to the repository's single Linux
entry point:

```bash
./setup.sh
```

Run that instead. It detects the machine, lets you tick exactly what you want,
shows the plan before touching anything, and is safe to run twice.
