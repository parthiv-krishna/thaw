# Thaw 🌨️

**Wake sleeping machines with a simple web interface**

Thaw is a lightweight web application that monitors your network machines and allows you to wake them up using Wake-on-LAN packets. It provides a web interface to see which machines are awake or asleep and wake them with a single click.

## Quick Start

### Using Nix

```bash
# Run directly with nix
nix run github:parthiv-krishna/thaw -- --machines /abspath/to/machines.json --port PORT

# Or clone and run locally
git clone https://github.com/parthiv-krishna/thaw.git
cd thaw
nix run .# -- --machines /abspath/to/machines.json --port PORT
```

### Manual Installation

```bash
# Install dependencies
pip install flask

# Run the application
python thaw.py --machines machines.json --port 8080
```

## Configuration

Create a `machines.json` file with your machine configurations:

```json
{
    "server1": {
        "ip": "192.168.1.100",
        "mac": "aa:bb:cc:dd:ee:ff",
        "broadcast_ip": "192.168.1.255",
        "display_name": "Main Server",
        "timeout_seconds": 1,
        "wake_port": 9
    },
    "workstation": {
        "ip": "192.168.1.101",
        "mac": "11:22:33:44:55:66",
        "broadcast_ip": "192.168.1.255",
        "display_name": "Development Workstation"
    }
}
```

### Configuration Options

- **ip** (required): IP address to ping for status checks
- **mac** (required): MAC address for Wake-on-LAN packets
- **broadcast_ip** (required): Broadcast IP for sending WoL packets
- **display_name** (optional): Human-readable name (defaults to machine key)
- **timeout_seconds** (optional): Ping timeout in seconds (default: 1)
- **wake_port** (optional): UDP port for WoL packets (default: 9)

## Command Line Options

```bash
python thaw.py [OPTIONS]

Options:
  --machines PATH    Path to machines configuration file (default: machines.json)
  --port PORT       Port to listen on (default: 8080)
  --help           Show help message
```

## NixOS Integration

Add thaw as a system service in your NixOS configuration:

```nix
{
  inputs.thaw.url = "github:parthiv-krishna/thaw";
  
  outputs = { nixpkgs, thaw, ... }: {
    nixosConfigurations.yourhost = nixpkgs.lib.nixosSystem {
      modules = [
        thaw.nixosModules.thaw
        {
          services.thaw = {
            enable = true;
            port = 8080;
            machines = {
              server1 = {
                ip = "192.168.1.100";
                mac = "aa:bb:cc:dd:ee:ff";
                broadcast_ip = "192.168.1.255";
                display_name = "Main Server";
              };
              workstation = {
                ip = "192.168.1.101";
                mac = "11:22:33:44:55:66";
                broadcast_ip = "192.168.1.255";
              };
            };
          };
        }
      ];
    };
  };
}
```

## Development

```bash
# Enter development shell
nix develop

# Run in development mode
python thaw.py

# Test with example configuration
python thaw.py --machines machines.json --port 8080
```

## API

Thaw provides a simple REST API:

- `GET /` - Web interface
- `GET /status/<machine>` - Get machine status (awake/asleep/unknown)
- `POST /wake/<machine>` - Send wake-on-LAN packet

Example API usage:

```bash
# Check status
curl http://localhost:8080/status/server1

# Wake machine
curl -X POST http://localhost:8080/wake/server1
```

## Requirements

- Python 3.7+
- Flask
- Linux system with `ping` command
- Network permissions to send broadcast UDP packets

## Security Considerations

- Thaw binds only to localhost (127.0.0.1) by default
- Designed to work behind a reverse proxy with authentication (e.g., Authelia)
- The NixOS module runs with minimal privileges and security hardening
- Consider network segmentation and firewall rules for WoL broadcast traffic

## Troubleshooting

### Wake-on-LAN not working

1. Ensure the target machine has WoL enabled in BIOS/UEFI
2. Check that the network interface supports WoL: `ethtool eth0`
3. Enable WoL on the interface: `ethtool -s eth0 wol g`
4. Verify firewall allows UDP broadcast traffic on the WoL port

### Ping issues

1. Ensure the thaw service has appropriate network permissions
2. Check that ICMP packets are not blocked by firewalls
3. Verify the target IP addresses are correct

### Permission errors

1. Ensure the user running thaw has CAP_NET_RAW capability for raw sockets
2. On NixOS, the systemd service is configured with the necessary capabilities
