"""
LLM fallback chain manager.

Tries each configured LLM provider in order. Each provider internally
cycles through its own model variants before the chain moves on.

Fallback cascade:
  Provider 1 (model A → B → C) → Provider 2 (model D → E) → …

Usage:
    chain = LLMFallbackChain()
    result = chain.generate(system_prompt, user_prompt)
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich import box

from sonar_agent.llm.llm_providers import (
    ALL_PROVIDERS,
    LLMProvider,
    QuotaExhaustedError,
)

console = Console()


class LLMFallbackChain:
    """
    Manages a chain of LLM providers with automatic failover.

    Providers are tried in the order defined by ALL_PROVIDERS.
    Only providers whose API key is configured are included.
    Each provider internally tries all its model variants.
    """

    def __init__(self) -> None:
        self._providers: list[LLMProvider] = [
            cls() for cls in ALL_PROVIDERS if cls().is_configured()
        ]
        self._exhausted: set[str] = set()
        self.last_used: str | None = None
        self.last_model: str | None = None

    @property
    def available_providers(self) -> list[str]:
        return [p.name for p in self._providers]

    @property
    def active_providers(self) -> list[str]:
        return [p.name for p in self._providers if p.name not in self._exhausted]

    @property
    def total_models(self) -> int:
        return sum(len(p.MODELS) for p in self._providers)

    @property
    def active_models(self) -> int:
        return sum(
            len(p.active_models)
            for p in self._providers
            if p.name not in self._exhausted
        )

    def generate(self, system_prompt: str, user_prompt: str) -> str | None:
        """
        Try each active provider in order until one succeeds.

        Returns the generated text, or None if all providers failed.
        """
        for provider in self._providers:
            if provider.name in self._exhausted:
                continue

            try:
                result = provider.generate(system_prompt, user_prompt)
                if result:
                    self.last_used = provider.name
                    self.last_model = getattr(provider, "last_model_used", None)
                    return result
                else:
                    console.print(
                        f"  [dim]⚠ {provider.name} returned empty, "
                        f"trying next…[/dim]"
                    )
                    continue

            except QuotaExhaustedError as exc:
                self._exhausted.add(provider.name)
                remaining = self.active_providers
                if remaining:
                    console.print(
                        f"  [yellow]⚠ {provider.name} fully exhausted "
                        f"(all models) → falling back to {remaining[0]}[/yellow]"
                    )
                else:
                    console.print(
                        f"  [red]✘ {provider.name} exhausted — "
                        f"no more providers available.[/red]"
                    )
                continue

            except Exception as exc:
                console.print(
                    f"  [yellow]⚠ {provider.name} error: {exc} → "
                    f"trying next…[/yellow]"
                )
                continue

        return None

    def reset(self) -> None:
        self._exhausted.clear()
        for p in self._providers:
            p._exhausted_models.clear()

    def print_status(self) -> None:
        """Print a rich table showing all providers and their models."""
        table = Table(
            title="LLM Fallback Chain",
            box=box.ROUNDED,
            title_style="bold cyan",
            show_lines=False,
        )
        table.add_column("Provider", style="bold", width=10)
        table.add_column("Status", width=12)
        table.add_column("Models", style="dim")

        for p in self._providers:
            if p.name in self._exhausted:
                status = "[red]🔴 exhausted[/red]"
            else:
                status = "[green]🟢 ready[/green]"

            model_list = []
            for m in p.MODELS:
                if m in p._exhausted_models:
                    model_list.append(f"[dim strikethrough]{m}[/dim strikethrough]")
                else:
                    model_list.append(f"[cyan]{m}[/cyan]")

            table.add_row(
                p.name,
                status,
                ", ".join(model_list),
            )

        console.print(table)
        console.print(
            f"  [bold]{self.active_models}[/bold] of "
            f"[bold]{self.total_models}[/bold] models available\n"
        )

    def status_table(self) -> list[tuple[str, str, int]]:
        """Return (provider_name, status, model_count) for display."""
        rows = []
        for p in self._providers:
            if p.name in self._exhausted:
                status = "🔴 exhausted"
            else:
                status = "🟢 ready"
            rows.append((p.name, status, len(p.MODELS)))
        return rows
