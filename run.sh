#!/bin/sh
# Start the NCDXF beacon monitor (extra options are passed on, e.g. --autostart cycle).
cd "$(dirname "$0")"
exec python3 monitor.py "$@"
