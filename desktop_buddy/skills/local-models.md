Manage existing LM Studio and Ollama services without downloading or duplicating runtimes.

1. Inspect system and the configured provider. Prefer its existing localhost API.
2. Discover models in Connection settings. Buddy tries tool calling and image input automatically unless explicitly disabled. Use actual service responses to identify unsupported features; an optional probe is not required.
3. LM Studio uses the configured `/v1/models` compatibility endpoint. Its native `/api/v1/models/load` and `/api/v1/models/unload` routes can manage models after explicit approval; consult the installed server's documented schema before posting.
4. Ollama uses `/api/tags`, `/api/show` and `/api/chat`. A chat request loads the selected local model when needed. Avoid `pull` or new model downloads unless requested.
5. For GUI work, launch its registered desktop ID, inspect accessibility/screenshot, then perform one approved action and observe the result.
6. Local-only mode requires an actual local model, not Ollama cloud delegation. If the service or app is absent, report that and ask for installation authorization. Do not silently switch to a cloud model.
