#!/bin/sh
set -eu
printf 'Hello from the guest\n'
printf 'System: '
uname -s
printf 'Account: '
id -un
printf 'Answer: %s\n' "$((6 * 7))"
