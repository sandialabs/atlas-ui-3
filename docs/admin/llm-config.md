# LLM Configuration

Last updated: 2026-02-08

The `llmconfig.yml` file is where you define all the Large Language Models that the application can use. The application uses the `LiteLLM` library, which allows it to connect to a wide variety of LLM providers.

*   **Location**: The package default is at `atlas/config/llmconfig.yml`. Place your instance-specific configuration in `config/llmconfig.yml`.

## Comprehensive Example

Here is an example of a model configuration that uses all available options.

```yaml
models:
  MyCustomGPT:
    model_name: openai/gpt-4-turbo-preview
    model_url: https://api.openai.com/v1/chat/completions
    api_key: "${OPENAI_API_KEY}"
    description: "The latest and most capable model from OpenAI."
    max_tokens: 8000
    temperature: 0.7
    extra_headers:
      "x-my-custom-header": "value"
    compliance_level: "External"

  OpenRouterLlama:
    model_name: meta-llama/llama-3-70b-instruct
    model_url: https://openrouter.ai/api/v1
    api_key: "${OPENROUTER_API_KEY}"
    description: "Llama 3 70B via OpenRouter"
    max_tokens: 4096
    temperature: 0.7
    extra_headers:
      "HTTP-Referer": "${OPENROUTER_SITE_URL}"
      "X-Title": "${OPENROUTER_SITE_NAME}"
    compliance_level: "External"
```

**Note**: The second example demonstrates environment variable expansion in `extra_headers`, which is useful for services like OpenRouter that require site identification headers.

## Environment Variable Expansion in LLM Configs

Similar to MCP server authentication, LLM configurations support environment variable expansion for API keys and header values. This feature provides security and flexibility in managing sensitive credentials.

### Security Best Practice

**Never store API keys directly in configuration files.** Instead, use environment variable substitution:

```yaml
models:
  my-openai-model:
    model_name: openai/gpt-4
    model_url: https://api.openai.com/v1
    api_key: "${OPENAI_API_KEY}"
    extra_headers:
      "X-Custom-Header": "${MY_CUSTOM_HEADER_VALUE}"
```

Then set the environment variables:
```bash
export OPENAI_API_KEY="sk-your-secret-api-key"
export MY_CUSTOM_HEADER_VALUE="your-custom-value"
```

### How It Works

1. **API Key Expansion**: The `api_key` value is processed at runtime. If it contains the `${VAR_NAME}` pattern, it's replaced with the value of the environment variable `VAR_NAME`.
2. **Extra Headers Expansion**: Each value in the `extra_headers` dictionary is also processed for environment variable expansion, allowing you to use dynamic values for headers like `HTTP-Referer` or `X-Title`.
3. **Error Handling**: If a required environment variable is missing, the application will raise a clear error message indicating which variable needs to be set. This prevents silent failures where unexpanded variables might be sent to the API provider.
4. **Literal Values**: You can still use literal string values without environment variables for development or testing purposes (though not recommended for production).

### Common Use Cases

**OpenRouter Configuration:**
```yaml
models:
  openrouter-claude:
    model_name: anthropic/claude-3-opus
    model_url: https://openrouter.ai/api/v1
    api_key: "${OPENROUTER_API_KEY}"
    extra_headers:
      "HTTP-Referer": "${OPENROUTER_SITE_URL}"
      "X-Title": "${OPENROUTER_SITE_NAME}"
```

**Custom LLM Provider with Authentication Headers:**
```yaml
models:
  custom-provider:
    model_name: custom/model-name
    model_url: https://custom-llm.example.com/v1
    api_key: "${CUSTOM_PROVIDER_API_KEY}"
    extra_headers:
      "X-Tenant-ID": "${TENANT_IDENTIFIER}"
      "X-Region": "${DEPLOYMENT_REGION}"
```

### Security Considerations

- **Recommended**: Use environment variables for all production API keys and sensitive header values
- **Alternative**: For development/testing, you can use direct string values (not recommended for production)
- **Never**: Commit API keys to `atlas/config/llmconfig.yml` or any version-controlled files

This environment variable expansion system works identically to the MCP server `auth_token` field, providing consistent behavior across all authentication and configuration mechanisms in the application.

## Per-User API Keys (2026-02-08)

Models can be configured to require users to bring their own API keys instead of using system-managed environment variables. This is useful when admins want to provide curated model endpoints but let individual users supply their own credentials.

### Configuration

Add `api_key_source: "user"` to a model definition:

```yaml
models:
  # System-managed key (default behavior)
  gpt-4.1:
    model_name: gpt-4.1
    model_url: https://api.openai.com/v1/chat/completions
    api_key: "${OPENAI_API_KEY}"
    compliance_level: "External"

  # User-managed key (users must bring their own)
  user-openai:
    model_name: gpt-4.1
    model_url: https://api.openai.com/v1/chat/completions
    api_key: ""
    api_key_source: "user"
    description: "OpenAI GPT-4.1 (bring your own key)"
    compliance_level: "External"
```

### How It Works

1. When `api_key_source` is `"user"`, the model appears in the UI with a key icon
2. Users click the key icon to open a modal and paste their API key
3. The key is encrypted and stored per-user using the same `MCPTokenStorage` as MCP server tokens (with `llm:{model_name}` prefix)
4. When the user sends a chat message with that model, the stored key is used for the LLM API call
5. If no key is stored, the model is disabled in the dropdown

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/llm/auth/status` | Auth status for all user-key models |
| POST | `/api/llm/auth/{model_name}/token` | Upload API key for a model |
| DELETE | `/api/llm/auth/{model_name}/token` | Remove API key for a model |

### Values for `api_key_source`

- `"system"` (default) - API key resolved from environment variables using `${VAR_NAME}` syntax
- `"user"` - API key provided per-user via the UI, stored encrypted on disk

## Configuration Fields Explained

*   **`model_name`**: (string) The identifier for the model that will be sent to the LLM provider. For `LiteLLM`, you often need to prefix this with the provider name (e.g., `openai/`, `anthropic/`).
*   **`model_url`**: (string) The API endpoint for the model.
*   **`api_key`**: (string) The API key for authenticating with the model's provider. **Security Best Practice**: Use environment variable substitution with the `${VAR_NAME}` syntax (e.g., `"${OPENAI_API_KEY}"`). The application will automatically expand these variables at runtime and provide clear error messages if a required variable is not set. This works identically to the `auth_token` field in MCP server configurations. You can also use literal API key values for development/testing (not recommended for production).
*   **`description`**: (string) A short description of the model that will be shown to users in the model selection dropdown.
*   **`max_tokens`**: (integer) The maximum number of tokens to generate in a response.
*   **`temperature`**: (float) A value between 0.0 and 1.0 that controls the creativity of the model's responses. Higher values are more creative.
*   **`extra_headers`**: (dictionary) A set of custom HTTP headers to include in the request, which is useful for some proxy services or custom providers. **Environment Variable Support**: Header values can also use the `${VAR_NAME}` syntax for environment variable expansion. This is particularly useful for services like OpenRouter that require headers like `HTTP-Referer` and `X-Title`. If an environment variable is missing, the application will raise a clear error message.
*   **`api_key_source`**: (string) Controls where the API key comes from. `"system"` (default) resolves from environment variables. `"user"` requires each user to provide their own key via the UI. See [Per-User API Keys](#per-user-api-keys-2026-02-08) above.
*   **`compliance_level`**: (string) The security compliance level of this model (e.g., "Public", "Internal"). This is used to filter which models can be used in certain compliance contexts.
