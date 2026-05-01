rsync -rzuP \
	--exclude .git \
	--exclude .venv \
	--exclude __pycache__ \
	--exclude node_modules \
	--exclude dist \
	--exclude nanobot \
	lemon:ldq/nanobot-soulboard/. .
