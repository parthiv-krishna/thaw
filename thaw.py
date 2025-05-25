#!/usr/bin/env python3

import argparse
import json
import logging
import socket
import struct
import subprocess
import time
from datetime import datetime
from flask import Flask, jsonify, render_template
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class MachineMonitor:
    def __init__(self, machines_config):
        self.machines = machines_config
        self.status_cache = (
            {}
        )  # {machine_name: {'status': 'awake/asleep', 'timestamp': time.time()}}
        self.cache_duration = 1.0  # Cache status for 5 seconds

    def ping_machine(self, ip, timeout_seconds=1):
        """Ping a machine to check if it's awake. Returns True if awake, False if asleep."""
        try:
            # Use ping command with specified timeout
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(timeout_seconds), ip],
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 1,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning(f"Ping timeout for {ip}")
            return False
        except Exception as e:
            logger.error(f"Ping error for {ip}: {e}")
            return False

    def wake_on_lan(self, mac_address, broadcast_ip, port=9):
        """Send a Wake-on-LAN packet to wake up a machine."""
        try:
            # Remove any separators from MAC address and convert to bytes
            mac_clean = mac_address.replace(":", "").replace("-", "").lower()
            if len(mac_clean) != 12:
                raise ValueError(f"Invalid MAC address: {mac_address}")

            mac_bytes = bytes.fromhex(mac_clean)

            # Create magic packet: 6 bytes of 0xFF followed by 16 repetitions of MAC
            magic_packet = b"\xff" * 6 + mac_bytes * 16

            # Send the packet
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(magic_packet, (broadcast_ip, port))
            sock.close()

            logger.info(
                f"Wake-on-LAN packet sent to {mac_address} at {broadcast_ip}:{port}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to send Wake-on-LAN packet to {mac_address}: {e}")
            return False

    def get_status(self, machine_name):
        """Get the current status of a machine, using cache if recent enough."""
        if machine_name not in self.machines:
            return "unknown"

        current_time = time.time()

        # Check if we have a recent cached status
        if machine_name in self.status_cache:
            cached_entry = self.status_cache[machine_name]
            if current_time - cached_entry["timestamp"] < self.cache_duration:
                return cached_entry["status"]

        # Cache is stale or doesn't exist, ping the machine
        config = self.machines[machine_name]
        ip = config["ip"]
        timeout_seconds = config.get("timeout_seconds", 1)

        try:
            is_awake = self.ping_machine(ip, timeout_seconds)
            status = "awake" if is_awake else "asleep"

            # Update cache
            self.status_cache[machine_name] = {
                "status": status,
                "timestamp": current_time,
            }

            # Log status changes
            if machine_name in self.status_cache:
                old_status = self.status_cache[machine_name].get("status")
                if old_status and old_status != status:
                    logger.info(
                        f"{machine_name} status changed: {old_status} -> {status}"
                    )

            return status

        except Exception as e:
            logger.error(f"Error checking status for {machine_name}: {e}")
            # Cache the error state
            self.status_cache[machine_name] = {
                "status": "asleep",
                "timestamp": current_time,
            }
            return "asleep"


# Global monitor instance
monitor = None


def load_machines_config(config_file):
    """Load machines configuration from JSON file."""
    try:
        with open(config_file, "r") as f:
            machines = json.load(f)

        # Validate and set defaults for each machine
        for name, config in machines.items():
            required_fields = ["ip", "mac", "broadcast_ip"]
            for field in required_fields:
                if field not in config:
                    raise ValueError(
                        f"Missing required field '{field}' for machine '{name}'"
                    )

            # Set defaults for optional fields
            config.setdefault("timeout_seconds", 1)
            config.setdefault("wake_port", 9)
            config.setdefault("display_name", name)

        logger.info(f"Loaded configuration for {len(machines)} machines")
        return machines

    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_file}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        raise
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        raise


def create_app(machines_config):
    """Create and configure the Flask application."""
    app = Flask(__name__)

    @app.route("/")
    def index():
        """Serve the main page."""
        return render_template("index.html", machines=machines_config)

    @app.route("/status/<machine>")
    def get_status(machine):
        """Get the current status of a machine."""
        if machine not in machines_config:
            return jsonify({"error": "Machine not found"}), 404

        status = monitor.get_status(machine)
        return jsonify(
            {
                "machine": machine,
                "status": status,
                "timestamp": datetime.now().isoformat(),
            }
        )

    @app.route("/wake/<machine>", methods=["POST"])
    def wake_machine(machine):
        """Send a wake-on-LAN packet to a machine."""
        if machine not in machines_config:
            return jsonify({"error": "Machine not found"}), 404

        config = machines_config[machine]
        success = monitor.wake_on_lan(
            config["mac"], config["broadcast_ip"], config.get("wake_port", 9)
        )

        if success:
            return jsonify(
                {
                    "success": True,
                    "message": f'Wake-on-LAN packet sent to {config["display_name"]}',
                    "machine": machine,
                }
            )
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": f'Failed to send wake-on-LAN packet to {config["display_name"]}',
                        "machine": machine,
                    }
                ),
                500,
            )

    return app


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Thaw - Wake sleeping machines")
    parser.add_argument(
        "--machines",
        default="machines.json",
        help="Path to machines configuration file (default: machines.json)",
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port to listen on (default: 8080)"
    )

    args = parser.parse_args()

    try:
        # Load configuration
        machines_config = load_machines_config(args.machines)

        # Create global monitor
        global monitor
        monitor = MachineMonitor(machines_config)

        # Create Flask app
        app = create_app(machines_config)

        logger.info(f"Starting Thaw server on localhost:{args.port}")
        logger.info(f"Monitoring {len(machines_config)} machines on-demand")

        # Run the Flask app
        app.run(host="127.0.0.1", port=args.port, debug=False)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
