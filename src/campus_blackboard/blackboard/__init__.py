"""Persistent shared Blackboard and client interface."""

from campus_blackboard.blackboard.client import BlackboardClient
from campus_blackboard.blackboard.repository import Repository

__all__ = ["BlackboardClient", "Repository"]

