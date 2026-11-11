"""The queue of planned calls and the token bucket of each Region. Spec 0003 section 6A."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# The `DescribeCapacityBlockOfferings` bucket of the AWS API in each Region: 10 tokens, refill
# 0.15 per second. Spec 0008 section 3.3.
BUCKET_SIZE = 10
REFILL_PER_SECOND = 0.15


@dataclass(frozen=True)
class Call:
    """One planned call. `probe_type`, `region`, and `args` are the key of the queue, unless
    `key` is set.

    `key` gives a call a queue identity apart from its `args`: a probe whose calls are the steps
    of a search (`aws_cb_longest`, spec 0008 section 3B) sets it to something stable across the
    steps (for example the instance type), so the queue sees 1 search, not 1 call per step. The
    default, `None`, keeps the 3-field identity of every other probe.
    """

    probe_type: str
    region: str
    args: tuple[Any, ...] = ()
    key: Any = None


# The model starts with half a bucket: the real bucket can be partly drained by other clients.
START_TOKENS = 5


class TokenBucket:
    """A model of the token bucket of each Region, on a monotonic clock."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.tokens: dict[str, float] = {}
        self.last: dict[str, float] = {}

    def available(self, region: str) -> int:
        """The whole tokens in the bucket now."""
        now = self.clock()
        tokens = self.tokens.get(region, float(START_TOKENS))
        tokens += (now - self.last.get(region, now)) * REFILL_PER_SECOND
        self.tokens[region] = min(float(BUCKET_SIZE), tokens)
        self.last[region] = now
        return int(self.tokens[region])

    def take(self, region: str) -> None:
        self.available(region)
        self.tokens[region] = max(0.0, self.tokens[region] - 1)


@dataclass
class Entry:
    """The state of a waiting call: throttled attempts so far, and the time not to run before."""

    attempts: int = 0
    not_before: float = 0.0


class CallQueue:
    """One FIFO queue of calls for each Region. A call with a key that waits is replaced."""

    def __init__(self) -> None:
        self.items: dict[str, dict[Call, Entry]] = {}

    def add(self, call: Call) -> None:
        """Add `call` at the end. The same key that still waits stays as it is: it keeps its place,
        its attempts, and its not-before time, so a new cycle does not cancel a backoff.

        With `call.key` set, "the same key" means the same `(probe_type, key)`, not the same
        `args`: a search already in the queue (at any step) is not joined by a 2nd one for the
        same type when a new cycle plans it again (spec 0008, section 3.3, rule 8).
        """
        queue = self.items.setdefault(call.region, {})
        if call.key is not None and any(
            existing.probe_type == call.probe_type and existing.key == call.key
            for existing in queue
        ):
            return
        queue.setdefault(call, Entry())

    def requeue(self, call: Call, attempts: int, not_before: float) -> None:
        """Put a throttled call back at the end."""
        self.items.setdefault(call.region, {})[call] = Entry(attempts, not_before)

    def take(self, region: str, n: int, now: float = 0.0) -> list[tuple[Call, int]]:
        """The first `n` calls of the Region that are ready at `now`, with their attempts."""
        queue = self.items.get(region, {})
        ready = [(call, e.attempts) for call, e in queue.items() if e.not_before <= now][:n]
        for call, _ in ready:
            del queue[call]
        return ready

    def regions(self) -> list[str]:
        return [region for region, queue in self.items.items() if queue]

    def __len__(self) -> int:
        return sum(len(queue) for queue in self.items.values())
