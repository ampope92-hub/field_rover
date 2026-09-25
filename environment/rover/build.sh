#!/bin/bash
# Compiles the survey tool the way the grader does: src/main.ml alone, static, unix library.
set -e
cd /app/rover
mkdir -p bin
ocamlopt -ccopt -static unix.cmxa /app/rover/src/main.ml -o /app/rover/bin/survey
