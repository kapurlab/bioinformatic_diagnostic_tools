#!/bin/bash
# ============================================================================
#  Kapur Lab Diagnostic Tools — double-click to open the dashboard.
#
#  macOS:  Double-click this file. (The FIRST time only, macOS may say it
#          "cannot be opened because it is from an unidentified developer" —
#          right-click the file, choose Open, then click Open again. After that
#          a normal double-click works.)
#  Linux:  Double-click and choose "Run", or run it from a terminal.
#
#  A small window opens and your browser shows the dashboard. Pick a tool to
#  launch it. Keep the window open while you work; close it (or restart your
#  computer) to stop. Just open this file again to start the dashboard later.
# ============================================================================
cd "$(dirname "$0")" || exit 1
exec bin/bdtools dashboard
