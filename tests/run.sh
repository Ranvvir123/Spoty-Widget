#!/usr/bin/env bash
# Runs the whole test-suite on a private D-Bus session (does not touch your real session or config).
cd "$(dirname "$0")/.." && exec dbus-run-session -- "${PYTHON:-/usr/bin/python3}" -W ignore -m unittest discover -s tests -p 'test_*.py' -v
