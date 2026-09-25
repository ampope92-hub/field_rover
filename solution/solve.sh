#!/bin/bash
# Reference solution: puts the finished survey tool in place, compiles it the way the grader
# does, then reads the archive here once so more than the compile is shown working. The read is
# time bounded in case the archive is not up yet.
set -e

cp /solution/src/main.ml /app/rover/src/main.ml

/app/rover/build.sh

echo "built; reading the archive once"
cd /app/rover
timeout 120 /app/rover/bin/survey > /tmp/survey_out.json \
    && echo "report: $(head -c 240 /tmp/survey_out.json)..." \
    || echo "the archive did not answer from here, which is fine at build time"
