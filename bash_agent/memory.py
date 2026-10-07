"""Replace this module's policy without changing the agent or environment."""
from abc import ABC, abstractmethod
from copy import deepcopy


class Memory(ABC):
    @abstractmethod
    def reset(self): ...

    @abstractmethod
    def update(self, message): ...

    @abstractmethod
    def render(self):
        """Return a valid Chat Completions message list for the next request."""
        ...


class IdentityMemory(Memory):
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = []

    def update(self, message):
        self.history.append(deepcopy(message))

    def render(self):
        return deepcopy(self.history)


class RecentTurnsMemory(IdentityMemory):
    """Limit model input by USER turns, retaining complete assistant/tool exchanges.

    Stores full history internally; this is a context-window policy, not a
    bounded-storage implementation. Never slice blindly by individual messages.
    """
    def __init__(self, turns=1):
        if turns < 1:
            raise ValueError("turns must be positive")
        self.turns = turns
        super().__init__()

    def render(self):
        starts = [i for i, m in enumerate(self.history) if m["role"] == "user"]
        if len(starts) <= self.turns:
            return super().render()
        system = [m for m in self.history if m["role"] == "system"]
        return deepcopy(system + self.history[starts[-self.turns]:])
