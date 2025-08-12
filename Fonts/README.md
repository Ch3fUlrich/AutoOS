# Font Resources

This directory contains essential font files used by various AutoOS automation scripts, particularly for terminal and development environment customization.

## 📁 Directory Contents

```
Fonts/
├── MesloLGSNerdFontMono-Regular.ttf  # Meslo Nerd Font for terminals
└── README.md                         # This file
```

## 🎯 Purpose

The fonts in this directory are specifically chosen to enhance the developer experience by providing:
- **Icon Support**: Rich icon sets for modern terminal themes
- **Programming Ligatures**: Enhanced readability for code
- **Unicode Coverage**: Extensive character set support
- **Terminal Optimization**: Designed for command-line interfaces

## 🔤 Available Fonts

### MesloLGS Nerd Font Mono
**File**: `MesloLGSNerdFontMono-Regular.ttf`

**Features**:
- **Nerd Font Patched**: Includes thousands of glyphs and icons
- **Monospace**: Fixed-width for perfect terminal alignment
- **Programming Focus**: Optimized for code display
- **Oh My Posh Compatible**: Required for many terminal themes

**Use Cases**:
- Terminal applications (PowerShell, Bash, Zsh)
- Code editors (VS Code, Vim, Emacs)
- Oh My Posh themes
- Development environments

**Character Sets**:
- Standard ASCII characters
- Programming symbols and ligatures
- Powerline symbols
- Font Awesome icons
- Material Design icons
- Weather icons
- File type icons

## 🚀 Installation

### Automatic Installation
The font is automatically installed by various AutoOS scripts:
- **Windows**: Oh My Posh setup script installs automatically
- **Linux**: Terminal setup scripts include font installation
- **Manual**: Use the installation methods below

### Windows Installation
#### Method 1: Automated (Recommended)
```powershell
cd Windows/powershell/oh-my-posh
.\setup_ohmypsh.ps1
```

#### Method 2: Manual Installation
1. Download or locate the font file
2. Right-click on `MesloLGSNerdFontMono-Regular.ttf`
3. Select "Install" from the context menu
4. Or double-click and click "Install" button

#### Method 3: PowerShell
```powershell
# Copy font to Windows Fonts directory
Copy-Item "MesloLGSNerdFontMono-Regular.ttf" "$env:SystemRoot\Fonts\"

# Register font in registry
$fontName = "MesloLGS NF"
$fontFile = "MesloLGSNerdFontMono-Regular.ttf"
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts" -Name "$fontName (TrueType)" -Value $fontFile
```

### Linux Installation
#### Method 1: User Installation
```bash
# Create user fonts directory
mkdir -p ~/.local/share/fonts

# Copy font file
cp MesloLGSNerdFontMono-Regular.ttf ~/.local/share/fonts/

# Update font cache
fc-cache -fv
```

#### Method 2: System-wide Installation
```bash
# Copy to system fonts directory
sudo cp MesloLGSNerdFontMono-Regular.ttf /usr/share/fonts/truetype/

# Update font cache
sudo fc-cache -fv
```

#### Method 3: Package Manager (Ubuntu/Debian)
```bash
# Alternative: Install via package manager
sudo apt update
sudo apt install fonts-firacode fonts-hack
```

### macOS Installation
```bash
# Copy to user fonts directory
cp MesloLGSNerdFontMono-Regular.ttf ~/Library/Fonts/

# Or use Font Book application
open MesloLGSNerdFontMono-Regular.ttf
```

## ⚙️ Configuration

### Terminal Applications

#### Windows Terminal
```json
{
    "profiles": {
        "defaults": {
            "fontFace": "MesloLGS NF",
            "fontSize": 12
        }
    }
}
```

#### PowerShell (with Oh My Posh)
```powershell
# Font is automatically configured by setup script
# Manual configuration in terminal settings:
# Font Family: MesloLGS NF
```

#### VS Code Terminal
```json
{
    "terminal.integrated.fontFamily": "MesloLGS NF",
    "terminal.integrated.fontSize": 14,
    "editor.fontFamily": "MesloLGS NF, Consolas, monospace"
}
```

#### Gnome Terminal (Linux)
```bash
# Set font via command line
gsettings set org.gnome.desktop.interface monospace-font-name 'MesloLGS NF 12'

# Or use GUI: Preferences > Profiles > Text > Custom font
```

### Code Editors

#### Visual Studio Code
Add to `settings.json`:
```json
{
    "editor.fontFamily": "MesloLGS NF",
    "editor.fontSize": 14,
    "editor.fontLigatures": true,
    "terminal.integrated.fontFamily": "MesloLGS NF"
}
```

#### Vim/Neovim
```vim
" In .vimrc or init.vim
set guifont=MesloLGS\ NF:h12
```

## 🔍 Troubleshooting

### Common Issues

**Font Not Appearing in Application**:
- Restart the application after font installation
- Check if application requires specific font name format
- Verify font is installed in correct directory

**Icons Not Displaying Correctly**:
- Ensure you're using "MesloLGS NF" (with NF suffix)
- Check terminal/application Unicode support
- Verify Oh My Posh theme compatibility

**Font Installation Fails**:
```bash
# Linux: Check font cache
fc-cache -fv
fc-list | grep -i meslo

# Windows: Check fonts directory
dir "$env:SystemRoot\Fonts" | findstr Meslo
```

### Verification Commands

#### Linux
```bash
# List installed fonts
fc-list | grep -i meslo

# Test font availability
fc-match "MesloLGS NF"
```

#### Windows
```powershell
# Check installed fonts
Get-ChildItem "$env:SystemRoot\Fonts" | Where-Object {$_.Name -like "*Meslo*"}

# Registry check
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts" | Where-Object {$_ -like "*Meslo*"}
```

## 🎨 Font Features

### Nerd Font Additions
The Meslo Nerd Font includes additional glyphs:
- **File type icons**: Specific icons for different file extensions
- **Git status icons**: Visual indicators for version control
- **System icons**: Battery, network, CPU indicators
- **Weather symbols**: For weather-aware prompts
- **Programming symbols**: Enhanced operators and symbols

### Character Map
Common Nerd Font symbols:
- `` (Branch symbol)
- `` (Folder icon)
- `` (File icon)
- `` (Lock icon)
- `` (Lightning bolt)
- `` (Gear icon)

## 🔗 Related Resources

- [Nerd Fonts Official Site](https://www.nerdfonts.com/)
- [Oh My Posh Documentation](https://ohmyposh.dev/)
- [Powerline Documentation](https://powerline.readthedocs.io/)
- [Font Installation Guide](https://github.com/ryanoasis/nerd-fonts#option-4-manual)

## 📦 Alternative Fonts

If MesloLGS doesn't suit your preferences, consider these alternatives:
- **FiraCode Nerd Font**: Programming ligatures
- **JetBrains Mono Nerd Font**: Modern programming font
- **Hack Nerd Font**: Clean, readable monospace
- **Source Code Pro Nerd Font**: Adobe's programming font

## 💡 Tips

1. **Consistent Setup**: Use the same font across all terminal applications
2. **Size Matters**: Adjust font size for comfortable reading (12-14pt recommended)
3. **Backup Fonts**: Specify fallback fonts in configurations
4. **Theme Compatibility**: Ensure font supports your terminal theme requirements
5. **Regular Updates**: Check for Nerd Font updates periodically

---

**Note**: Font installation may require administrative privileges on some systems. Always verify font licensing for commercial use.