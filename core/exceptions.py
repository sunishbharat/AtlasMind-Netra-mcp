"""Exception hierarchy rooted at NetraError (Coding Guidelines Rule 5).

Third-party exceptions are translated into this hierarchy at the boundary where they occur
(httpx errors in the backend client, PydanticAI errors in the clarifier, JSON errors in the
config loaders). Code above those boundaries only ever catches NetraError subclasses.
"""


class NetraError(Exception):
    """Base class for all AtlasMind-Netra-mcp errors."""


class ConfigError(NetraError):
    """A config file is missing, malformed, or fails validation. Raised fail-fast at startup."""


class LiteBackendError(NetraError):
    """The atlasMind backend is unreachable, returned an error status, or an in-band error."""


class ClarificationError(NetraError):
    """The clarifier LLM call failed or produced unusable output after retries."""


class StateTransitionError(NetraError):
    """An illegal clarification state machine transition was attempted (programming error)."""


class AnalysisError(NetraError):
    """IssueAnalyser LLM call failed or produced unusable output after retries."""
