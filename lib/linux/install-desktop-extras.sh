#!/usr/bin/env bash
# Desktop fonts and shell configuration; sourced by install.sh for every loader.
# Uses its caller's shell options, ui_*, has_cmd and idempotent editing helpers.
# shellcheck shell=bash

# Pure inspection hook, also loaded lazily by standalone detect.sh callers.
desktop_script_is_installed() {
    case "$1" in
        jetbrains-mono-nerd-font)
            [[ -f "$SYS_HOME/.local/share/fonts/JetBrainsMonoNerdFont/JetBrainsMonoNerdFont-Regular.ttf" ||
               -f /usr/local/share/fonts/JetBrainsMonoNerdFont/JetBrainsMonoNerdFont-Regular.ttf ]] ;;
        moonlight) has_bin moonlight-qt || has_bin moonlight ;;
        *) return 1 ;;
    esac
}

install_zsh_plugins() {
    local custom="$SYS_HOME/.oh-my-zsh/custom/plugins"
    clone_or_update https://github.com/zsh-users/zsh-autosuggestions      "$custom/zsh-autosuggestions" 1
    clone_or_update https://github.com/zsh-users/zsh-syntax-highlighting  "$custom/zsh-syntax-highlighting" 1
    clone_or_update https://github.com/zsh-users/zsh-completions          "$custom/zsh-completions" 1
    clone_or_update https://github.com/Aloxaf/fzf-tab                     "$custom/fzf-tab" 1
    clone_or_update https://github.com/zsh-users/zsh-history-substring-search "$custom/zsh-history-substring-search" 1
    append_line_once "$SYS_HOME/.zshrc" "AutoOS:plugins" \
        'plugins=(git z sudo copypath copyfile extract colored-man-pages zsh-completions fzf-tab zsh-autosuggestions zsh-syntax-highlighting zsh-history-substring-search)  # AutoOS:plugins'
    append_line_once "$SYS_HOME/.zshrc" "AutoOS:history" \
        'HISTSIZE=500000  # AutoOS:history'$'\n''SAVEHIST=100000'
    append_line_once "$SYS_HOME/.zshrc" "AutoOS:fzf-tab" \
        'zstyle ":completion:*" list-colors "${(s.:.)LS_COLORS}"  # AutoOS:fzf-tab'$'\n''zstyle ":fzf-tab:complete:cd:*" fzf-preview "lsd -1 --color=always \$realpath 2>/dev/null || ls -1 --color=always \$realpath"'
    append_line_once "$SYS_HOME/.zshrc" "AutoOS:history-search" \
        'bindkey "^[[A" history-substring-search-up  # AutoOS:history-search'$'\n''bindkey "^[[B" history-substring-search-down'
}

install_jetbrains_mono_font() {
    local dir="$SYS_HOME/.local/share/fonts/JetBrainsMonoNerdFont"
    local target="$dir/JetBrainsMonoNerdFont-Regular.ttf"
    [[ -f "$target" ]] && { ui_muted "JetBrains Mono Nerd Font already installed"; return 0; }
    if [[ -f "/usr/local/share/fonts/JetBrainsMonoNerdFont/JetBrainsMonoNerdFont-Regular.ttf" ]]; then
        ui_muted "JetBrains Mono Nerd Font already installed system-wide"
        return 0
    fi
    if (( AUTOOS_DRY_RUN )); then ui_muted "would download JetBrains Mono Nerd Font to $dir"; return 0; fi
    mkdir -p "$dir"
    local tmp; tmp="$(mktemp -d)"
    if curl -fsSL -o "$tmp/jb.tar.xz" "https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.tar.xz"; then
        tar -xf "$tmp/jb.tar.xz" -C "$dir"
        has_cmd fc-cache && fc-cache -f "$dir" >/dev/null 2>&1 || true
        ui_ok "installed JetBrains Mono Nerd Font"
    else
        ui_warn "failed to download JetBrains Mono Nerd Font"
    fi
    rm -rf "$tmp"
}

setup_kitty_config() {
    local cfg_dir="$SYS_HOME/.config/kitty"
    local cfg_file="$cfg_dir/kitty.conf"
    if [[ ! -f "$cfg_file" ]]; then
        if (( AUTOOS_DRY_RUN )); then
            ui_muted "would create $cfg_file"
        else
            mkdir -p "$cfg_dir"
            cat > "$cfg_file" <<'EOF'
# Font settings (Nerd Font)
font_family      JetBrainsMono Nerd Font
bold_font        auto
italic_font      auto
bold_italic_font auto
font_size        11.0

# Shell
shell /usr/bin/zsh

# Window layout & padding
window_padding_width 8
remember_window_size  yes

# Tab bar appearance
tab_bar_edge bottom
tab_bar_style powerline
tab_powerline_style slanted

# Cursor & Bell
cursor_shape beam
cursor_blink_interval 0.5
enable_audio_bell no
EOF
            chown -R "${SUDO_USER:-$USER}:${SUDO_USER:-$USER}" "$cfg_dir" 2>/dev/null || true
        fi
    fi
    # If running on Raspberry Pi, ensure OpenGL 3.3 Mesa wrapper exists
    if [[ -f /proc/device-tree/model ]] && grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
        if [[ ! -f /usr/local/bin/kitty ]]; then
            if (( AUTOOS_DRY_RUN )); then
                ui_muted "would create /usr/local/bin/kitty wrapper with MESA_GL_VERSION_OVERRIDE=3.3"
            else
                $AUTOOS_SUDO tee /usr/local/bin/kitty >/dev/null <<'EOF'
#!/bin/sh
export MESA_GL_VERSION_OVERRIDE=3.3
exec /usr/bin/kitty "$@"
EOF
                $AUTOOS_SUDO chmod +x /usr/local/bin/kitty
            fi
        fi
        if has_cmd update-alternatives; then
            if (( AUTOOS_DRY_RUN )); then
                ui_muted "would set x-terminal-emulator alternative to /usr/local/bin/kitty"
            else
                $AUTOOS_SUDO update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/local/bin/kitty 60 2>/dev/null || true
                $AUTOOS_SUDO update-alternatives --set x-terminal-emulator /usr/local/bin/kitty 2>/dev/null || true
            fi
        fi
    fi
}

setup_lsd_aliases() {
    local shrc
    for shrc in "$SYS_HOME/.zshrc" "$SYS_HOME/.bashrc"; do
        [[ -f "$shrc" ]] || continue
        append_line_once "$shrc" "AutoOS:lsd" \
'alias ls="lsd --group-dirs first"  # AutoOS:lsd
alias ll="lsd -la --group-dirs first"
alias la="lsd -a --group-dirs first"
alias lt="lsd --tree"'
    done
}

setup_bat_alias() {
    local shrc
    for shrc in "$SYS_HOME/.zshrc" "$SYS_HOME/.bashrc"; do
        [[ -f "$shrc" ]] || continue
        append_line_once "$shrc" "AutoOS:bat" 'alias bat="batcat"  # AutoOS:bat'
    done
}

setup_agy_pi_config() {
    local zshrc="$SYS_HOME/.zshrc"
    if [[ -f "$zshrc" ]]; then
        append_line_once "$zshrc" "AutoOS:agy-pi" \
'# >>> agy-pi configuration >>>  # AutoOS:agy-pi
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"
# <<< agy-pi configuration <<<'
    fi
    if has_cmd pi; then
        if (( AUTOOS_DRY_RUN )); then
            ui_muted "would register agy-pi with pi"
        else
            pi install npm:agy-pi 2>/dev/null || true
        fi
    fi
}
