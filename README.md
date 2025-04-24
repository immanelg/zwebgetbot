
A Telegram bot for downloading a page from a web url and bundling everything into one index.html

# Run
Obtain bot token from Bot Father.

Create env
```
cp .env.json.example .env.json
```

Run with uv
```
uv run main.py
```

Or 
```
uv sync
source .venv/bin/activate
python main.py
```

# Develop

```
uv tool install ruff@latest
ruff format main.py webpage2html/
```

