# Mock Enterprise LiteLLM Proxy

A stand-in for an enterprise [LiteLLM proxy](https://docs.litellm.ai/docs/simple_proxy)
whose models are scoped to teams, for exercising Atlas's
[team gateways](../../docs/admin/litellm-team-gateways.md) without a real proxy.

It serves the three calls Atlas makes, with LiteLLM's request and response
shapes:

| Call | Mock behavior |
|---|---|
| `GET /team/list?user_id=<id>` | The user's teams (`team_id`, `team_alias`, `models`, `members_with_roles`). `user_id` may be the email or the object id. |
| `GET /models?team_id=<id>` (also `/v1/models`) | `{"object": "list", "data": [{"id": ...}]}` for that team. |
| `POST /chat/completions` (also `/v1/...`) | Requires `x-litellm-team-id` (team id or alias). Refuses a missing header (400), a team the caller is not in (401), or a model outside the team (401). Supports `stream: true`. The reply starts with `[<team alias> / <model>]`. |

Test support (not LiteLLM APIs): `GET /mock/requests` lists the chat requests
received (model, team header, caller, outcome); `DELETE /mock/requests` clears
it; `GET /health`.

## Credentials

- **Master key** `sk-mock-litellm-master` (override with
  `MOCK_LITELLM_MASTER_KEY`): a service key that may act for any user. Matches
  an Atlas gateway with `auth_type: "system"`.
- **JWT with an `oid` claim**: a delegated (Entra On-Behalf-Of style) user
  token. Signatures are not checked; the `oid` must be one of the users below,
  and such a caller can only see and use its own teams.

## Data

| Team id | Alias | Models | Members |
|---|---|---|---|
| `team-alpha-7f3a` | Project Alpha | `gpt-4o-mini`, `claude-sonnet` | test@test.com, alice@example.com |
| `team-beta-19c2` | Project Beta | `llama-3.3-70b` | test@test.com, bob@example.com |
| `team-gamma-5d81` | Project Gamma | `gpt-4o-mini` | bob@example.com |

Edit `USERS` and `TEAMS` in `main.py` to change them.

## Try It

```bash
# 1. Start the mock (port 4010; override with MOCK_LITELLM_PORT)
python mocks/litellm-mock/main.py &

# 2. Check it
python mocks/litellm-mock/smoke_test.py

# 3. Point Atlas at it: add to config/llmconfig.yml
litellm_gateways:
  enterprise:
    display_name: "Enterprise LiteLLM (mock)"
    base_url: "http://127.0.0.1:4010"
    api_key: "sk-mock-litellm-master"
    model_defaults:
      supports_tools: false

# 4. Chat through a team model from the CLI (test@test.com is in Alpha and Beta)
atlas-chat "hello" --model "enterprise::team-beta-19c2::llama-3.3-70b"
curl -s http://127.0.0.1:4010/mock/requests   # team_header: team-beta-19c2
```

In the web UI, open the model picker under the message box: the
"Enterprise LiteLLM (mock)" section lists the teams, and choosing a team lists
its models.
