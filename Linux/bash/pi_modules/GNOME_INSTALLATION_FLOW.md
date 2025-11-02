# GNOME on Raspberry Pi - Installation Flow

## Overview

AutoOS now supports optional GNOME Desktop installation on Raspberry Pi with detailed resource warnings and an interactive installation process.

## Installation Flow

### 1. Detection Phase
When running the GNOME extensions installer on Raspberry Pi without GNOME:

```
┌─────────────────────────────────────────────────┐
│  GNOME Extensions Installer Started             │
└─────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────┐
│  Detects: Raspberry Pi + No GNOME Shell         │
└─────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────┐
│  ⚠️  Warning: GNOME Shell not installed         │
└─────────────────────────────────────────────────┘
```

### 2. Resource Information Display

The installer shows detailed resource requirements:

```
📊 GNOME Resource Requirements for Raspberry Pi:

  RAM:  1.0-1.5 GB idle (vs ~400MB for PIXEL)
        2.5-4.0 GB with applications

  CPU:  5-15% idle, 20-40% during use
        May cause thermal throttling

  GPU:  Continuous usage for compositing
        May impact video playback

  Storage: ~1.5 GB for GNOME packages

  ✓ Recommended: Pi 5 with 8GB RAM
  ✗ Not Recommended: Pi 4 with 4GB or less
```

### 3. User Decision Point

```
Would you like to install GNOME Desktop on this Raspberry Pi? [y/N]
```

**If NO:** Exit gracefully with tip about PIXEL desktop
**If YES:** Proceed to GNOME installation

### 4. GNOME Installation Process

The installer (`install_gnome_pi.sh`) performs:

1. **Verification**
   - Confirms Raspberry Pi hardware
   - Checks if GNOME already installed

2. **Resource Warning Info Box**
   - Detailed breakdown of RAM, CPU, GPU usage
   - Comparison with PIXEL desktop
   - Storage requirements
   - Performance impact notes
   - Recommendations based on Pi model

3. **First Confirmation**
   ```
   ⚠️  Are you sure you want to install GNOME on Raspberry Pi?
   Continue with GNOME installation? [y/N]
   ```

4. **RAM Check & Second Confirmation** (if < 6GB RAM)
   ```
   ⚠️  Your Raspberry Pi has less than 6GB RAM (detected: 4096MB)
   
   GNOME will use a significant portion of your available memory!
   
   Are you ABSOLUTELY sure you want to continue? [y/N]
   ```

5. **Installation Steps**
   - Update package list
   - Install GNOME Shell core (gnome-shell, gnome-session, gdm3, etc.)
   - Install recommended utilities (gnome-tweaks, file-roller, etc.)
   - Configure GDM3 as display manager
   - Apply Pi-specific performance optimizations:
     - Disable animations
     - Reduce thumbnail sizes
     - Disable file indexing
     - Conservative power profile
   - Optional: Install Extension Manager via Flatpak

6. **Post-Installation**
   - Success message with next steps
   - Instructions for switching sessions
   - Troubleshooting tips
   - Optional reboot prompt

### 5. Extensions Installation (After GNOME)

If GNOME installation succeeds:

```
✅ SUCCESS: GNOME installed successfully!

Continue with GNOME extensions installation? [Y/n]
```

**If YES:** Proceeds with normal grouped extensions installer
**If NO:** Exit with note to run installer again later

If reboot is required:
```
⚠️  WARNING: GNOME Shell not available yet - reboot may be required
After rebooting into GNOME, run this installer again for extensions
```

## Command-Line Usage

### Direct GNOME Installation on Pi
```bash
bash Linux/bash/pi_modules/install_gnome_pi.sh
```

### Extensions Installer (with GNOME installation offer)
```bash
bash Linux/bash/modules/gnome-extensions_installer.sh
```

### Via Main Menu
```bash
cd Linux/bash
./main.sh
# Select "GNOME Desktop Setup"
```

## User Experience Examples

### Scenario 1: Pi 5 with 8GB RAM
✅ Recommended configuration
- Shows resource warnings
- Single confirmation required
- Installation proceeds smoothly
- Extensions can be installed immediately

### Scenario 2: Pi 4 with 4GB RAM
⚠️ Not recommended
- Shows resource warnings
- **Two confirmations required** (extra warning about RAM)
- Installation allowed but discouraged
- User fully informed of performance impact

### Scenario 3: User Declines GNOME
💡 Helpful guidance
- No installation
- Tip about PIXEL desktop being optimized for Pi
- Clean exit

### Scenario 4: GNOME Already Installed
✅ Smart detection
- Detects existing GNOME Shell
- Shows current version
- Offers reinstall/update option
- Skips to extensions if declined

## Files Modified

1. **`Linux/bash/pi_modules/install_gnome_pi.sh`** (NEW)
   - Complete GNOME installation script for Pi
   - Resource warnings and confirmations
   - Performance optimizations
   - Post-installation guidance

2. **`Linux/bash/modules/gnome-extensions_installer.sh`**
   - Added Pi detection with GNOME offer
   - Integrated resource information display
   - Seamless flow to extensions after GNOME install
   - Colorized output for readability

3. **`Linux/bash/README.md`**
   - Documented GNOME on Pi option
   - Resource requirements listed
   - Usage examples added
   - Pi module documentation updated

## Safety Features

- ✅ Multiple confirmations for low-RAM systems
- ✅ RAM detection and warnings
- ✅ Dry-run support for testing
- ✅ Ability to cancel at any point
- ✅ Instructions for switching back to PIXEL
- ✅ Performance optimizations applied automatically
- ✅ Detailed logging of all actions

## Performance Optimizations Applied

Automatically configured for Pi hardware:
- Animations disabled (`enable-animations false`)
- Thumbnail size reduced
- File indexing disabled
- Power saver profile enabled
- Systemwide optimization script installed

## Troubleshooting Built-In

The installer provides:
- Instructions for switching desktop environments
- Tips for improving performance
- Resource monitoring commands
- Rollback information (switch back to PIXEL)

## Future Enhancements

Potential additions:
- [ ] Wayland vs X11 session selection
- [ ] More aggressive performance mode for Pi 4
- [ ] Automatic memory monitoring/warnings
- [ ] Swap file size recommendations
- [ ] Performance benchmarking tool
