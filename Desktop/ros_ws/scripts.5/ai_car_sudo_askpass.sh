#!/bin/sh
# SUDO_ASKPASS helper — password from AI_CAR_SUDO_PASS (default: rock)
printf '%s\n' "${AI_CAR_SUDO_PASS:-rock}"
