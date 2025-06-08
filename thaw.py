#!/usr/bin/env python3

import argparse
import json
import logging
import socket
import struct
import subprocess
import time
import threading
import re
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class CronScheduler:
    """A simple scheduler that supports human-readable expressions."""
    
    def __init__(self):
        self.jobs = []
        self.running = False
        self.thread = None
        self.weekday_map = {
            'sun': 0, 'sunday': 0,
            'mon': 1, 'monday': 1,
            'tue': 2, 'tuesday': 2,
            'wed': 3, 'wednesday': 3,
            'thu': 4, 'thursday': 4,
            'fri': 5, 'friday': 5,
            'sat': 6, 'saturday': 6
        }
    
    def parse_schedule_expression(self, schedule_expr):
        """Parse a schedule expression in the format: day month weekday time
        Examples:
        - "* * Sun 07:00" - Every Sunday at 7:00 AM
        - "15 * Mon 09:30" - Every Monday on the 15th at 9:30 AM
        - "* 6 * 12:00" - Every day in June at 12:00 PM
        - "1 1 * 00:00" - January 1st at midnight
        """
        parts = schedule_expr.strip().split()
        if len(parts) != 4:
            raise ValueError(f"Invalid schedule expression: {schedule_expr}. Expected format: 'day month weekday time'")
        
        day_part, month_part, weekday_part, time_part = parts
        
        # Parse time (HH:MM)
        if ':' not in time_part:
            raise ValueError(f"Invalid time format: {time_part}. Expected HH:MM format")
        
        try:
            hour_str, minute_str = time_part.split(':')
            hour = int(hour_str)
            minute = int(minute_str)
            
            if not (0 <= hour <= 23) or not (0 <= minute <= 59):
                raise ValueError(f"Invalid time: {time_part}. Hour must be 0-23, minute must be 0-59")
        except ValueError as e:
            raise ValueError(f"Invalid time format: {time_part}. {e}")
        
        return {
            'minute': [minute],
            'hour': [hour],
            'day': self._parse_field(day_part, 1, 31),
            'month': self._parse_field(month_part, 1, 12),
            'weekday': self._parse_weekday(weekday_part)
        }
    
    def _parse_field(self, field, min_val, max_val):
        """Parse a single field (supports *, numbers, and comma-separated values)"""
        if field == '*':
            return list(range(min_val, max_val + 1))
        elif ',' in field:
            return [int(x.strip()) for x in field.split(',') if x.strip()]
        else:
            return [int(field)]
    
    def _parse_weekday(self, weekday_field):
        """Parse weekday field (supports *, day names, and comma-separated values)"""
        if weekday_field == '*':
            return list(range(0, 7))  # All days
        
        weekdays = []
        for day in weekday_field.lower().split(','):
            day = day.strip()
            if day in self.weekday_map:
                weekdays.append(self.weekday_map[day])
            else:
                try:
                    # Try parsing as number (0-6)
                    day_num = int(day)
                    if 0 <= day_num <= 6:
                        weekdays.append(day_num)
                    else:
                        raise ValueError(f"Invalid weekday: {day}")
                except ValueError:
                    raise ValueError(f"Invalid weekday: {day}. Use day names (Sun, Mon, etc.) or numbers 0-6")
        
        return weekdays
    
    def should_run(self, schedule, now=None):
        """Check if a schedule should run at the given time"""
        if now is None:
            now = datetime.now()
        
        # Convert Python weekday (Monday=0) to our weekday (Sunday=0)
        python_weekday = now.weekday()  # Monday=0, Sunday=6
        our_weekday = (python_weekday + 1) % 7  # Sunday=0, Saturday=6
        
        return (
            now.minute in schedule['minute'] and
            now.hour in schedule['hour'] and
            now.day in schedule['day'] and
            now.month in schedule['month'] and
            our_weekday in schedule['weekday']
        )
    
    def add_job(self, schedule_expr, callback, *args, **kwargs):
        """Add a scheduled job"""
        try:
            schedule = self.parse_schedule_expression(schedule_expr)
            self.jobs.append({
                'schedule': schedule,
                'callback': callback,
                'args': args,
                'kwargs': kwargs,
                'schedule_expr': schedule_expr
            })
            logger.info(f"Added scheduled job: {schedule_expr}")
        except Exception as e:
            logger.error(f"Failed to add scheduled job '{schedule_expr}': {e}")
    
    def start(self):
        """Start the scheduler in a background thread"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()
        logger.info("Scheduler started")
    
    def stop(self):
        """Stop the scheduler"""
        self.running = False
        if self.thread:
            self.thread.join()
        logger.info("Scheduler stopped")
    
    def _run_scheduler(self):
        """Main scheduler loop"""
        last_minute = None
        
        while self.running:
            now = datetime.now()
            current_minute = (now.hour, now.minute)
            
            # Only check jobs once per minute
            if current_minute != last_minute:
                last_minute = current_minute
                
                for job in self.jobs:
                    try:
                        if self.should_run(job['schedule'], now):
                            logger.info(f"Executing scheduled job: {job['schedule_expr']}")
                            job['callback'](*job['args'], **job['kwargs'])
                    except Exception as e:
                        logger.error(f"Error executing scheduled job '{job['schedule_expr']}': {e}")
            
            # Sleep for a short time to avoid busy waiting
            time.sleep(1)


class MachineMonitor:
    def __init__(self, machines_config):
        self.machines = machines_config
        self.status_cache = (
            {}
        )  # {machine_name: {'status': 'awake/asleep', 'timestamp': time.time()}}
        self.cache_duration = 1.0  # Cache status for 1 second
        self.scheduler = CronScheduler()

    def setup_wakeup_schedules(self):
        """Set up wakeup schedules for all machines"""
        for machine_name, config in self.machines.items():
            wakeup_schedules = config.get('wakeup_schedules', [])
            for schedule in wakeup_schedules:
                if schedule and schedule.strip():  # Skip empty schedules
                    self.scheduler.add_job(
                        schedule, 
                        self.scheduled_wake, 
                        machine_name
                    )
        
        if self.scheduler.jobs:
            self.scheduler.start()
            logger.info(f"Started scheduler with {len(self.scheduler.jobs)} wakeup schedules")

    def scheduled_wake(self, machine_name):
        """Wake a machine as part of a scheduled job"""
        if machine_name not in self.machines:
            logger.error(f"Scheduled wake failed: machine '{machine_name}' not found")
            return
        
        config = self.machines[machine_name]
        display_name = config.get('display_name', machine_name)
        
        logger.info(f"Scheduled wake initiated for {display_name} ({machine_name})")
        
        success = self.wake_on_lan(
            config["mac"], 
            config["broadcast_ip"], 
            config.get("wake_port", 9)
        )
        
        if success:
            logger.info(f"Scheduled wake-on-LAN packet sent to {display_name}")
        else:
            logger.error(f"Failed to send scheduled wake-on-LAN packet to {display_name}")

    def shutdown(self):
        """Clean shutdown of the monitor"""
        if hasattr(self, 'scheduler'):
            self.scheduler.stop()

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
        """Send a Wake-on-LAN packet to wake up a machine using the system wakeonlan command."""
        try:
            cmd = [
                "wakeonlan",
                "-i", broadcast_ip,
                "-p", str(port),
                mac_address
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info(f"wakeonlan command succeeded: {result.stdout.strip()}")
                return True
            else:
                logger.error(f"wakeonlan command failed: {result.stderr.strip()}")
                return False
        except Exception as e:
            logger.error(f"Failed to run wakeonlan command: {e}")
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
            config.setdefault("wakeup_schedules", [])
            
            # Validate wakeup_schedules is a list
            if not isinstance(config["wakeup_schedules"], list):
                raise ValueError(
                    f"'wakeup_schedules' must be a list for machine '{name}'"
                )

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
        
        # Set up wakeup schedules
        monitor.setup_wakeup_schedules()

        # Create Flask app
        app = create_app(machines_config)

        logger.info(f"Starting Thaw server on localhost:{args.port}")
        logger.info(f"Monitoring {len(machines_config)} machines on-demand")

        # Run the Flask app
        app.run(host="127.0.0.1", port=args.port, debug=False)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        if monitor:
            monitor.shutdown()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if monitor:
            monitor.shutdown()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
