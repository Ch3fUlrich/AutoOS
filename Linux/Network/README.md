# Network Configuration Tools

This directory contains automated network configuration scripts, primarily focused on enterprise WiFi setup and university network connections.

## 📁 Directory Contents

```
Network/
├── eduroam-linux-Universitat_Basel-UniBasel.py  # Eduroam setup script
└── README.md                                     # This file
```

## 🎯 Purpose

The network configuration tools automate the complex process of connecting to enterprise-grade wireless networks, particularly eduroam (education roaming) networks used by universities and research institutions worldwide.

## 🌐 Eduroam Configuration

### What is Eduroam?
Eduroam is a secure, worldwide roaming access service developed for the international research and education community. It allows students, researchers, and staff to access the internet at any participating institution using their home institution credentials.

### Features
- **Automated Certificate Installation**: Downloads and installs required security certificates
- **Network Profile Creation**: Creates properly configured WiFi profiles
- **Credential Management**: Securely stores and manages authentication credentials
- **Institution-Specific Settings**: Preconfigured for specific university requirements

## 🚀 Usage

### Prerequisites
- Linux system (Ubuntu, Debian, etc.)
- Python 3.x installed
- Administrator/sudo privileges
- Valid university credentials

### Running the Script
```bash
cd AutoOS/Linux/Network
python3 eduroam-linux-Universitat_Basel-UniBasel.py
```

### Interactive Setup Process
1. **Launch Script**: The script will start with a graphical interface
2. **Enter Credentials**: Provide your university username and password
3. **Certificate Verification**: Script downloads and verifies security certificates
4. **Network Configuration**: Automatically configures WiFi settings
5. **Connection Testing**: Verifies successful network connection

## 🔧 Configuration Details

### Supported Institutions
- **University of Basel (UniBasel)**: Fully configured and tested
- **Other Institutions**: Script can be adapted for other eduroam providers

### Security Features
- **WPA2-Enterprise**: Uses enterprise-grade security protocols
- **EAP-TTLS/PAP**: Secure authentication method
- **Certificate Validation**: Verifies server certificates for security
- **Encrypted Credentials**: Securely stores authentication information

### Network Settings
- **SSID**: eduroam
- **Security**: WPA2-Enterprise
- **Authentication**: EAP-TTLS with PAP
- **Certificate Authority**: Institution-specific CA certificates

## 🛠️ Customization

### Adapting for Other Institutions
To configure the script for a different university:

1. **Download Institution Script**: Get the eduroam configuration script from your institution
2. **Certificate URLs**: Update certificate download URLs
3. **Server Settings**: Modify authentication server addresses
4. **Institution Suffix**: Update username suffix (e.g., @university.edu)

### Example Configuration Elements
```python
# Institution-specific settings
INSTITUTION_NAME = "Your University"
REALM = "@your-university.edu"
SERVER_NAME = "radius.your-university.edu"
CA_CERT_URL = "https://your-university.edu/certificates/ca.crt"
```

## 🔍 Troubleshooting

### Common Issues

**Script Fails to Run**:
```bash
# Check Python installation
python3 --version

# Install required packages
sudo apt update
sudo apt install python3 python3-pip

# Check file permissions
chmod +x eduroam-linux-*.py
```

**Connection Authentication Fails**:
- Verify username and password are correct
- Check if account is activated for WiFi access
- Ensure you're using the correct username format (with or without @domain)

**Certificate Issues**:
- Clear existing certificates and re-run script
- Check if institution has updated their certificates
- Verify internet connectivity for certificate download

**Network Manager Issues**:
```bash
# Restart Network Manager
sudo systemctl restart NetworkManager

# Check Network Manager status
sudo systemctl status NetworkManager

# View available connections
nmcli connection show
```

### Manual Network Removal
If you need to remove the eduroam configuration:
```bash
# List WiFi connections
nmcli connection show

# Remove eduroam connection
sudo nmcli connection delete eduroam

# Clear certificates (if needed)
sudo rm -rf /etc/ssl/certs/eduroam*
```

## 🔒 Security Considerations

### Best Practices
- **Run from Trusted Source**: Only use scripts provided by your institution
- **Verify Script Integrity**: Check script authenticity before running
- **Secure Credentials**: Never hardcode passwords in scripts
- **Regular Updates**: Update certificates when institution requires

### Privacy Notes
- Scripts typically store minimal information locally
- Credentials are stored in system keyring when possible
- No credentials are transmitted to third parties

## 📋 Supported Operating Systems

| OS | Status | Notes |
|----|--------|-------|
| Ubuntu 20.04+ | ✅ Fully Supported | Tested and verified |
| Debian 10+ | ✅ Fully Supported | Compatible |
| Fedora 30+ | 🔄 Partial Support | May require adaptation |
| Arch Linux | 🔄 Partial Support | Manual configuration needed |

## 🔗 Related Resources

- [Eduroam Official Website](https://www.eduroam.org/)
- [Institution WiFi Setup Guides](https://www.your-university.edu/it/wifi)
- [NetworkManager Documentation](https://networkmanager.dev/)
- [Main AutoOS Documentation](../README.md)

## 💡 Tips

1. **Test Connection**: Always test in a location with good eduroam signal
2. **Backup Configuration**: Save working configurations before changes
3. **Multiple Profiles**: You can configure multiple institution profiles
4. **Guest Networks**: Use institution guest networks as backup
5. **VPN Compatibility**: Ensure VPN software doesn't interfere

---

**Note**: Network configuration scripts require appropriate permissions and may modify system settings. Always review and understand the script before execution.