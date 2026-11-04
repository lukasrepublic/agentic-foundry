#!/usr/bin/env bash
# FG-LIC-002: REUSE lint (SPDX headers). Header form in each file:
#   SPDX-FileCopyrightText: <year> <holder>
#   SPDX-License-Identifier: <SPDX id>
set -euo pipefail
reuse lint
