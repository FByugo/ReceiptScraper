# shared helper: show a command as if typed, then run it
run() { printf '\033[1;32m$\033[0m '; for ((i=0;i<${#1};i++)); do printf '%s' "${1:i:1}"; sleep 0.03; done; echo; sleep 0.4; eval "$1"; sleep 1.6; }
note() { printf '\033[2m# %s\033[0m\n' "$1"; sleep 1.2; }
