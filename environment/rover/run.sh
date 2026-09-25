#!/bin/bash
# Builds the survey tool and runs it against the archive in this environment.
set -e
/app/rover/build.sh
cd /app/rover
exec /app/rover/bin/survey
