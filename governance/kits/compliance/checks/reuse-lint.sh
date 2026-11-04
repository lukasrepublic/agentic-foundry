#!/usr/bin/env bash
# FG-LIC-002: REUSE lint (SPDX headers). Header form in each file:
#   SPDX-FileCopyrightText: <year> <holder>
#   SPDX-License-Identifier: <SPDX id>
# Pin reuse per the stack kit's version-matrix template (governance/kits/stack/templates/version-matrix.md).
# Pinned-install form (placeholder):  pip install reuse==<version>
set -euo pipefail
reuse lint
