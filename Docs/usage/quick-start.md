# Quick Start

## Prerequisites
- Python 3.11+
- Node.js 18+
- A configured .env file (see .env.example)

## Install

```
uv sync
cd frontend
npm install
cd ..
```

## Initialize

```
semabridge init
```

## Validate Connections

```
semabridge validate
```

## Run a Sync

```
# Forward or reverse sync is inferred from your config
semabridge semantic sync
```

## Compare Models

```
semabridge diff compare -d <id> --from <v1> --to <v2>
```

## Logs

```
semabridge logs list
```
