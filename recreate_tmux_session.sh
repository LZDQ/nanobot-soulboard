#!/bin/sh

# Usage: ./recreate_tmux_session.sh [--no-attach]

# tmux windows:
#   0 bash                -> bash
#   1 config              -> cd ~/.nanobot
#   2 deploy-backend      -> python -m nanobot_soulboard
#   3 deploy-frontend     -> cd frontend && pnpm preview
#   4 ttyd                -> ttyd -i lo -W bash

set -eu

SESSION_NAME="soulboard"
ATTACH=1

if [ "${1-}" = "--no-attach" ]; then
	ATTACH=0
fi

if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
	# Create detached session with window 0.
	tmux new-session -d -s "$SESSION_NAME" -n bash

	# Create remaining windows.
	tmux new-window -d -t "${SESSION_NAME}:1" -n config -c "$HOME/.nanobot"
	tmux new-window -d -t "${SESSION_NAME}:2" -n deploy-backend
	tmux new-window -d -t "${SESSION_NAME}:3" -n deploy-frontend -c "$HOME/frontend"
	tmux new-window -d -t "${SESSION_NAME}:4" -n ttyd

	# Send commands.
	tmux send-keys -t "${SESSION_NAME}:2" "python -m nanobot_soulboard" C-m
	tmux send-keys -t "${SESSION_NAME}:3" "pnpm preview" C-m
	tmux send-keys -t "${SESSION_NAME}:4" "ttyd -i lo -W bash" C-m

	tmux select-window -t "${SESSION_NAME}:0"
fi

if [ "$ATTACH" -eq 1 ]; then
	exec tmux attach-session -t "$SESSION_NAME"
fi

exit 0
