# The model tunnel

**Only for `PORTER_PROVIDER=ollama`.** With OpenAI (the default), the deployment calls OpenAI's
API directly, and nothing here is needed.

The EC2 deployment has to call a model, and a small CPU instance cannot run `gpt-oss:20b`.
So the model stays on this machine, in Ollama, and a tunnel gives it a public HTTPS URL.
The same URL is the endpoint for anybody following along without Ollama of their own, with
`PORTER_PROVIDER=ollama`, `PORTER_BASE_URL=https://<the domain>/v1` and the token as
`PORTER_API_KEY`.

```
EC2 · porter ──HTTPS──► ngrok edge ──(token + /v1/* only)──► this laptop · Ollama :11434
```

## Once

1. `ngrok config add-authtoken <token>` — from dashboard.ngrok.com (already done on this machine).
2. dashboard.ngrok.com → **Domains** → claim the free static domain. Put it in `.env`:
   `NGROK_DOMAIN=something-something.ngrok-free.app`
3. Make a token and put it in `.env`:
   `PORTER_MODEL_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")`

## Every time

```bash
./ngrok/start_model_tunnel.sh      # leave it running; Ctrl+C closes it
```

Check it from anywhere:

```bash
curl -s https://$NGROK_DOMAIN/v1/models -H "Authorization: Bearer $PORTER_MODEL_TOKEN"   # 200, model list
curl -s -o /dev/null -w "%{http_code}\n" https://$NGROK_DOMAIN/v1/models                   # 401
curl -s -o /dev/null -w "%{http_code}\n" https://$NGROK_DOMAIN/api/tags                    # 404
```

## What it costs you

- **Capacity.** Every caller shares one laptop's GPU. Measured here: about 2–3 raw model
  calls a second at best; agent requests queue behind each other.
- **Availability.** The URL answers while this laptop is awake and online, and not otherwise.
  A deployment that depends on it is up exactly when the laptop is.
- **The token is shared.** Anyone who has it can use the model. Rotate it after a session:
  change `PORTER_MODEL_TOKEN`, restart the tunnel, update the deployment's `.env`.
