# nest-debserver

Docker Compose stacks for my Hack Club Nest container (Debian 13).

## Layout
- `stacks/<name>/compose.yaml`: one folder per service
- `stacks/<name>/.env.example`: documents the required variables (no real values)
- `stacks/<name>/.env`: the real secrets, which exist only on the server and are gitignored
- `stacks/<name>/data/`: persistent volumes, gitignored

## Workflow
Edit on the laptop, then commit and push, then `git pull` on the server, then `docker compose up -d`.
