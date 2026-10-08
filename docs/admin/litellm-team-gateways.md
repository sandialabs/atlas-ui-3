# Enterprise LiteLLM Team Gateways

Last updated: 2026-10-07

Some deployments reach their models through an enterprise
[LiteLLM proxy](https://docs.litellm.ai/docs/simple_proxy) where access and
spend are organized by **team** (project). A user may belong to many teams, and
each team may call a different set of models. For such a proxy, Atlas does not
list models in `llmconfig.yml`. Instead the user:

1. picks one of **their LiteLLM teams**, then
2. picks one of the **models that team may call**,

and every LLM request for that model carries the team in the
`x-litellm-team-id` header so LiteLLM routes and charges it to that team.

LiteLLM only acts on that header in its JWT/OIDC auth mode, which is an
Enterprise feature. Against OSS LiteLLM, see
[Enterprise vs. OSS LiteLLM](#enterprise-vs-oss-litellm) before deploying.

## Configuration

Add a `litellm_gateways` section next to `models` in `config/llmconfig.yml`.
Each key is a short gateway name.

```yaml
models:
  gpt-4.1:
    model_url: "https://api.openai.com/v1/chat/completions"
    model_name: "gpt-4.1"
    api_key: "${OPENAI_API_KEY}"

litellm_gateways:
  enterprise:
    display_name: "Enterprise LiteLLM"
    description: "Project-billed models on the enterprise LiteLLM proxy"
    base_url: "${ENTERPRISE_LITELLM_URL}"      # e.g. https://litellm.example.gov/
    auth_type: "delegated"                     # or "system"
    delegation:
      scope: "https://litellm.example.gov/user_impersonation"
    groups: ["staff"]                          # optional; empty = everyone
    compliance_level: "Internal"               # optional; default for every model
    models:                                    # optional allowlist; omit = every team model
      gpt-4.1:
        compliance_level: "SOC2"               # overrides the gateway's level
      claude-sonnet: {}                        # allowed; inherits "Internal"
    model_defaults:                            # applied to every team model
      max_tokens: 4096
      supports_tools: true
      supports_vision: false
```

| Field | Default | Meaning |
|---|---|---|
| `base_url` | required | LiteLLM proxy root. `${ENV_VAR}` is expanded. |
| `display_name`, `description` | gateway name | Shown in the model picker. |
| `auth_type` | `system` | How Atlas authenticates to LiteLLM (see below). |
| `api_key` | required for `system` | Service key for `auth_type: system` (`${ENV_VAR}` expanded). Required so LiteLLM's SDK never falls back to the server's own provider key. |
| `delegation.scope` / `audience` / `resource` | - | Target of the delegated token for `auth_type: delegated`. One is required. |
| `user_id_source` | `email` for `system`, `token_claim` for `delegated` | How the LiteLLM `user_id` for `/team/list` is derived. |
| `user_id_claim` | `oid` | Claim read from the delegated token when `user_id_source: token_claim`. Entra puts the user's object id in `oid`. |
| `user_id_strip_suffix` | - | With `user_id_source: email`, strip this suffix (e.g. `@example.gov`) from the Atlas user. |
| `team_header` | `x-litellm-team-id` | Header that carries the selected team on chat requests. |
| `team_list_path` | `team/list` | Team listing endpoint, called with `?user_id=`. |
| `models_path` | `models` | Model listing endpoint, called with `?team_id=`. |
| `discovery_timeout_seconds` | `30` | Timeout for team and model listing. |
| `discovery_cache_seconds` | `300` | How long a user's team and model lists are cached. A forced refresh (`?refresh=true`, or a cache miss on a team) is only honored once the cached list is 10 seconds old. |
| `groups` | `[]` | Atlas groups allowed to use the gateway, as for a model's `groups`. |
| `allowed_data_classifications` | - | Data classifications every model reached through the gateway is approved for, unless a `models` entry sets its own. See [Compliance](compliance.md). |
| `compliance_level` | - | Deprecated single level; read as `[compliance_level]` when `allowed_data_classifications` is unset. |
| `models` | `{}` (all) | Admin allowlist of LiteLLM model ids. When set, only these models are offered and callable through the gateway (see [Limiting the models](#limiting-the-models)). Each entry may set `allowed_data_classifications` or `compliance_level`. A plain list (`models: [gpt-4.1, claude-sonnet]`) allows models without per-model settings. |
| `extra_headers` | - | Static headers sent on every chat request. |
| `model_defaults` | `{}` | Any [model field](llm-config.md#configuration-fields-explained) except the identity and access fields (`model_name`, `model_url`, `api_key`, `api_key_source`, `globus_scope`, `groups`, `compliance_level`, `allowed_data_classifications`, `extra_headers`). Validated when the file loads. |

A gateway name must not contain `::`, and no model in the top-level `models`
may start with `<gateway>::`.

### Limiting the models

By default a gateway offers every model a user's team lists. To limit a gateway
to named models and give each its own compliance level, list them under the
gateway's `models`, keyed by the LiteLLM model id (the `id` that
`/models?team_id=` returns):

```yaml
litellm_gateways:
  enterprise:
    compliance_level: "Internal"
    models:
      gpt-4.1:
        compliance_level: "SOC2"
      claude-sonnet: {}
```

- **The allowlist narrows, it never grants.** A model appears for a team only if
  the team lists it *and* it is in `models`. With the example, a team whose
  LiteLLM models are `gpt-4.1` and `llama-3.3-70b` sees only `gpt-4.1`.
- **It is enforced on the server, not just hidden.** A model key naming a model
  outside the allowlist is an unknown model everywhere (chat, `atlas-chat
  --model`, saved conversations), so no request for it reaches LiteLLM. A
  call with such a key, such as a saved selection whose model has since been
  removed from the list, fails with "The selected model is no longer available
  on this LiteLLM gateway".
- **Compliance.** Each model's classifications are, most specific first, its
  entry's `allowed_data_classifications`, its entry's `compliance_level`, the
  gateway's `allowed_data_classifications`, then the gateway's
  `compliance_level`. Levels are validated like any other
  model's. An unknown level (a typo such as `SOC-2`) leaves that model
  unleveled, as it would for a static model. It does not inherit the gateway's level. The level drives the server-side compliance checks. It also drives
  the compliance filter in the model picker: the gateway stays listed while any
  of its models passes the filter, and models that don't pass are hidden.
- Each entry accepts only `allowed_data_classifications` and `compliance_level`; other model settings still come
  from `model_defaults`. Removing a model from the list takes effect when the
  new configuration is loaded.

### Authentication

**`auth_type: delegated`** (recommended for enterprise LiteLLM). Atlas exchanges
the user's OIDC login token for a LiteLLM-scoped token using the existing
[delegation layer](oidc-authentication.md), so LiteLLM sees the real user and
enforces their team membership itself. For Microsoft Entra ID this is the
On-Behalf-Of flow:

```bash
FEATURE_OIDC_AUTH_ENABLED=true
FEATURE_OIDC_DELEGATION_ENABLED=true
OIDC_DELEGATION_PROVIDER=entra_obo
```

The login must request the Atlas API scope (for example
`api://<atlas-client-id>/user_impersonation`), not only Microsoft Graph scopes:
a Graph token cannot be exchanged. Common Entra OBO failures:

- `AADSTS50105` - the user is not assigned to the LiteLLM enterprise application.
- `AADSTS65001` - consent is missing for Atlas to call the LiteLLM scope.
- `AADSTS50013` - the assertion has the wrong audience; check the login scope.

Users without an OIDC session (for example header-authenticated users) see
"Please sign in again" in the picker instead of teams.

**`auth_type: system`**. One service key calls LiteLLM for everyone, and the
user's teams are looked up by their Atlas identity. LiteLLM cannot tell users
apart with a shared key, so Atlas enforces membership (below). Use this when
the proxy trusts Atlas as a service, or for local testing with the mock.

### Enterprise vs. OSS LiteLLM

Per-request team selection is a LiteLLM **Enterprise** feature. LiteLLM reads
`x-litellm-team-id` only in its JWT/OIDC auth mode (`enable_jwt_auth`), and
that mode requires an Enterprise license. With OSS LiteLLM virtual keys, each
key belongs to exactly one team, and LiteLLM ignores the header.

What that means for each auth type against **OSS** LiteLLM:

- **`auth_type: system`** still works, but it doesn't select a team. Atlas's
  own team and model check still passes or refuses the call, but every
  request that goes through is charged to the service key's team, whatever
  team the user picked.
- **`auth_type: delegated`** straight to OSS LiteLLM doesn't work, because OSS
  LiteLLM does not accept a JWT as a bearer credential.

To get per-team routing and spend with OSS LiteLLM, put an identity-aware
proxy in front of it. The proxy should:

- accept the user's delegated token;
- answer `team/list?user_id=` in LiteLLM's shape, and `models?team_id=`;
- on chat requests, check that the user is in the team named by
  `x-litellm-team-id`, then call LiteLLM with a virtual key for that user and
  team.

Point `base_url` at that proxy. If it uses other paths or header names, change
`team_list_path`, `models_path`, and `team_header` to match. No Atlas changes
are needed.

### Keycloak (RFC 8693 token exchange)

`auth_type: delegated` also works with `OIDC_DELEGATION_PROVIDER=token_exchange`.
For Keycloak (standard token exchange v2):

- Set `delegation.audience` to the client ID of a real Keycloak client.
  Otherwise Keycloak answers `Audience not found`.
- Make that audience available to Atlas's client: create a client scope with
  an Audience mapper for the target client, add it to Atlas's client as an
  optional scope, and request it in `delegation.scope`. Otherwise Keycloak
  answers `Requested audience not available`.
- Keycloak tokens have no `oid` claim. Set `user_id_claim` to the claim that
  holds the user's LiteLLM `user_id`, for example `sub`.

When those are in place, the exchanged token has `aud` set to the target
client, `azp` set to Atlas's client, `sub` set to the user, and Keycloak's
default lifetime of 300 s.

### Validation status

Testing of `auth_type: system` ran against the mock proxy, both in automated
tests and in an end-to-end run as two users. `auth_type: delegated` has unit
tests. The token exchange step was also run by hand against a real Keycloak.
No run has yet gone through a real identity provider, then a real LiteLLM
proxy (Enterprise JWT auth or an identity-aware front proxy), then a
completion. Validate that path in staging before relying on it.

## How It Works

- **Discovery.** The picker calls `GET /api/llm/gateways/<gateway>/teams`, which
  calls LiteLLM `GET /team/list?user_id=<id>`, then
  `GET /api/llm/gateways/<gateway>/models?team_id=<team>`, which calls
  `GET /models?team_id=<team>` and reads the OpenAI-style `data[].id`.
- **Selection.** The chosen model is the model name
  `<gateway>::<team_id>::<model_id>`. It is sent with each chat turn, saved with
  conversations, and accepted anywhere a model name is (including
  `atlas-chat --model`).
- **Calls.** For such a model Atlas calls `POST <base_url>/chat/completions`
  with the model id, the gateway credential as the bearer token, and
  `x-litellm-team-id: <team_id>`.
- **Team and model are checked on every call.** Before any request for a team
  model, Atlas confirms the gateway lists that team for the user and that the
  model is among the team's models (both cached for `discovery_cache_seconds`,
  re-checked once on a miss so a newly granted team or model works). A
  hand-crafted model name for someone else's team, or for a model outside the
  team, is refused ("You are not a member of the selected LiteLLM team" /
  "The selected model is not available to the selected LiteLLM team") and no
  request reaches LiteLLM. With a shared service key this check is what keeps
  users inside their teams, so removing a user from a team takes effect within
  `discovery_cache_seconds`.
- **Not yet covered:** `atlas_launch` sub-conversations and MCP sampling
  choose from the statically configured models only.
- **Access control** (`groups`) and **compliance** (`compliance_level`) apply
  to every gateway model exactly as they do to configured models. A gateway a
  user may not use is absent from `/api/config` and its endpoints answer 404.

### REST API

| Endpoint | Returns |
|---|---|
| `GET /api/config`, `GET /api/config/shell` | `llm_gateways`: `[{name, display_name, description, supports_tools, supports_vision, supports_pdf, compliance_level?, compliance_levels?, model_compliance_levels?}]`. With compliance levels on, `compliance_levels` lists every level a gateway model can have, and `model_compliance_levels` maps each allowlisted model id to its level (`null` when the model is unleveled). `model_allowed_data_classifications` maps each allowlisted model id to its classifications and `model_classifications` lists them, which is what the picker filters on. |
| `GET /api/llm/gateways/{gateway}/teams[?refresh=true]` | `{gateway, teams: [{team_id, label}]}` |
| `GET /api/llm/gateways/{gateway}/models?team_id=...[&refresh=true]` | `{gateway, team_id, team_label, models: [{name, model_id, label, compliance_level?, allowed_data_classifications?}]}`, limited to the allowlist when one is set |

Errors: `401` the gateway could not authenticate the user, `403` not a member
of the team, `404` unknown or restricted gateway, `502` LiteLLM unreachable.

## Local Testing with the Mock

`mocks/litellm-mock/` is a LiteLLM stand-in with three teams, a master key,
and JWT (`oid`) callers. It refuses chat requests without a valid team header
and records what it received at `GET /mock/requests`. See its
[README](../../mocks/litellm-mock/README.md) for the config snippet and a
walkthrough.
