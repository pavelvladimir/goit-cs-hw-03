"""Register disposable credentials with pytest while reusing the public runner."""

from collections.abc import Callable, Iterable
from typing import ClassVar

from scripts.stack import ComposeStack as BaseStack
from scripts.stack import clean_environment as clean_environment
from scripts.stack import running_stack as running_stack
from tests.reporting import register_secrets


class ComposeStack(BaseStack):
    secret_observer: ClassVar[Callable[[Iterable[str]], None]] = staticmethod(
        register_secrets
    )
