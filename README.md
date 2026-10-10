# postil-chat

A model-agnostic, self-hostable chat UI where you can reply inline to a specific part of an AI response.

## Development

Requirements: [uv](https://docs.astral.sh/uv/), Node.js, and [Ollama](https://ollama.com) running locally.

```sh
ollama pull qwen3:1.7b      # or change the model in config.yaml
sudo systemctl edit ollama  # add: [Service] Environment=OLLAMA_CONTEXT_LENGTH=16384
cp .env.example .env        # optional: API keys for hosted providers
make install
make dev                    # backend on :8000, UI on http://127.0.0.1:5173
```

`make test` runs the backend and frontend tests; `make lint` runs ruff, mypy, oxlint and tsc.

Configuration (models, default model, token budget, database path) lives in `config.yaml`.
Each model's `context_window` should match what the server really allocates: Ollama defaults
to 4096 tokens and silently drops the start of longer prompts (check with `ollama ps`).
