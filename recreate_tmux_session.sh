# Windows:
#   0 bash                -> bash
#   1 config              -> cd ~/.nanobot
#   2 deploy-backend      -> python -m nanobot_soulboard
#   3 deploy-frontend     -> cd frontend && pnpm preview
#   4 ttyd                -> ttyd -i lo -W bash

SESSION_NAME="soulboard"

# If session exists, just attach to it.
if tmux has-session -t "$SESSION_NAME"; then
	exec tmux attach-session -t "$SESSION_NAME"
fi

# Otherwise, create a new detached session with window 0.
tmux new-session -d -s "$SESSION_NAME"
# Create the remaining windows.
tmux new-window -d -t "${SESSION_NAME}:1" -n config ~/.nanobot
tmux new-window -d -t "${SESSION_NAME}:2" -n deploy-backend
tmux new-window -d -t "${SESSION_NAME}:3" -n deploy-frontend -c ./frontend
tmux new-window -d -t "${SESSION_NAME}:4" -n ttyd
# Send the commands.
tmux send-keys -t "${SESSION_NAME}:2" "python -m nanobot_soulboard" C-m
tmux send-keys -t "${SESSION_NAME}:3" "pnpm preview" C-m
tmux send-keys -t "${SESSION_NAME}:4" "ttyd -i lo -W bash" C-m

tmux select-window -t "${SESSION_NAME}:0"
exec tmux attach-session -t "$SESSION_NAME"
