class Configuration:
    def __init__(self, *, host: str, api_key: dict[str, str]) -> None: ...

class ApiClient:
    def __init__(self, configuration: Configuration) -> None: ...

class Environment:
    Production: str
    Sandbox: str
