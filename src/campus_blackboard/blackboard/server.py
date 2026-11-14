"""ASGI entry point for the persistent A2A Blackboard service."""

from campus_blackboard.blackboard.repository import Repository
from campus_blackboard.config import get_settings
from campus_blackboard.transport.service import create_a2a_app


settings = get_settings()
repository = Repository(settings.database_path)
app = create_a2a_app("blackboard", settings=settings, repository=repository)

