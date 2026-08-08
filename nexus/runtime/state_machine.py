"""
State machine for the Nexus execution engine.
"""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class RunState(str, Enum):
    """Execution states for an agent run."""

    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"


class StateMachine:
    """Manages state transitions for the execution engine."""

    # Valid transitions from each state
    _transitions = {
        RunState.IDLE: {RunState.PLANNING, RunState.EXECUTING, RunState.FAILED},
        RunState.PLANNING: {RunState.EXECUTING, RunState.FAILED},
        RunState.EXECUTING: {RunState.WAITING_FOR_USER, RunState.COMPLETED, RunState.FAILED},
        RunState.WAITING_FOR_USER: {RunState.EXECUTING, RunState.COMPLETED, RunState.FAILED},
        RunState.COMPLETED: {RunState.IDLE},
        RunState.FAILED: {RunState.IDLE},
    }

    def __init__(self):
        self._state = RunState.IDLE

    @property
    def state(self) -> RunState:
        return self._state

    def transition_to(self, new_state: RunState) -> bool:
        """
        Transition to a new state.

        Returns True if successful, False if invalid transition.
        """
        if new_state not in self._transitions[self._state]:
            logger.error("Invalid state transition: %s -> %s", self._state, new_state)
            return False

        logger.debug("State transition: %s -> %s", self._state, new_state)
        self._state = new_state
        return True

    def is_terminal(self) -> bool:
        return self._state in (RunState.COMPLETED, RunState.FAILED)
