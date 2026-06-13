"""Provider factory wrapper for nanobot-soulboard.

Upstream's ``OpenAICompatProvider._parse()`` returns an ``LLMResponse`` with
``finish_reason="error"`` when the API hands back an empty ``choices`` array,
but it leaves the structured error metadata unset. As a result the base retry
policy in ``LLMProvider._run_with_retry()`` runs ``_is_transient_response()``,
finds no status code / kind / matching text marker, classifies it as a
*non-transient* error, and gives up immediately — surfacing the response as a
terminal "model error" with no retry. (Verified against upstream's
``_TRANSIENT_ERROR_MARKERS``: "empty choices" matches none of them, so upstream
still does not retry this case on its own.)

Empty choices is almost always a transient upstream hiccup (gateway blip,
truncated stream, momentary backend error) and should go through the same
backoff/retry path as a 5xx. Rather than patch the upstream submodule, we
subclass the provider and override ``_parse`` to re-mark that specific failure
as retryable. The actual retry/backoff loop still lives entirely upstream.

Upstream's ``make_provider`` may wrap the real provider in a
``FallbackProvider`` when ``agents.defaults.fallback_models`` is configured. In
that case the wrapper is not an ``OpenAICompatProvider``, so we also reach into
its primary provider and its lazy fallback factory to make sure the fix is not
silently skipped for fallback-enabled souls.
"""

from typing import Any

from nanobot.config.schema import Config
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.factory import make_provider as _upstream_make_provider
from nanobot.providers.fallback_provider import FallbackProvider
from nanobot.providers.openai_compat_provider import OpenAICompatProvider

# Must match the sentinel produced by upstream OpenAICompatProvider._parse().
_EMPTY_CHOICES_ERROR = "error: api returned empty choices."


def _mark_empty_choices_retryable(parsed: LLMResponse) -> LLMResponse:
    """Flip the upstream "empty choices" parse error to a transient error.

    Setting ``error_should_retry=True`` is enough — it is the first field
    ``LLMProvider._is_transient_response()`` consults, so the response now
    flows into the standard backoff/retry loop instead of being surfaced as a
    terminal "model error".
    """
    if (
        parsed.finish_reason == "error"
        and not parsed.tool_calls
        and parsed.error_should_retry is None
        and (parsed.content or "").strip().lower() == _EMPTY_CHOICES_ERROR
    ):
        parsed.error_should_retry = True
        parsed.error_kind = "empty_choices"
    return parsed


_retrying_subclass_cache: dict[type, type] = {}


def _retrying_subclass(cls: type) -> type:
    """Return (and cache) a single-inheritance subclass of *cls* whose
    ``_parse`` post-processes empty-choices errors into retryable ones.

    Single inheritance keeps the instance layout identical to ``cls`` (the
    provider base classes use ``__slots__`` + a managed dict), which is what
    lets us reclass an already-constructed instance via ``__class__``. Built
    dynamically so any OpenAICompat-derived provider (e.g.
    ``GitHubCopilotProvider``) keeps its own ``_parse`` specialization while
    still gaining the fix.
    """
    sub = _retrying_subclass_cache.get(cls)
    if sub is None:
        base_parse = cls._parse  # capture original to avoid self-recursion

        def _parse(self: Any, response: Any) -> LLMResponse:
            return _mark_empty_choices_retryable(base_parse(self, response))

        sub = type(f"Retrying{cls.__name__}", (cls,), {"_parse": _parse})
        _retrying_subclass_cache[cls] = sub
    return sub


def _enable_empty_choices_retry(provider: LLMProvider) -> LLMProvider:
    """Reclass an OpenAI-compatible *provider* in place so empty-choices errors
    become retryable, then return it.

    Only OpenAI-compatible providers emit the "empty choices" sentinel; the
    Anthropic/Bedrock/Azure/Codex backends populate error metadata themselves,
    so they are returned unchanged.
    """
    if isinstance(provider, OpenAICompatProvider):
        provider.__class__ = _retrying_subclass(type(provider))
    return provider


def make_provider(config: Config) -> LLMProvider:
    """Create the provider via upstream, then enable empty-choices retry.

    When fallback models are configured upstream returns a ``FallbackProvider``
    instead of a bare provider; patch its primary and wrap its lazy fallback
    factory so every underlying OpenAI-compatible provider gains the fix.
    """
    provider = _upstream_make_provider(config)
    if isinstance(provider, FallbackProvider):
        _enable_empty_choices_retry(provider._primary)
        _base_factory = provider._provider_factory
        provider._provider_factory = lambda fb: _enable_empty_choices_retry(
            _base_factory(fb)
        )
        return provider
    return _enable_empty_choices_retry(provider)
