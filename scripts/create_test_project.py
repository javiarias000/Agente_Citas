#!/usr/bin/env python3
"""
Create test project with API key
"""

import asyncio
import sys
import os

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.models import Project
import hashlib
import uuid

async def create_test_project(api):
    """Create test project using existing ArcadiumAPI instance"""
    async with api.db.get_session() as session:
        # Check if test project exists
        from sqlalchemy import select
        stmt = select(Project).where(Project.slug == "test-project")
        result = await session.execute(stmt)
        project = result.scalar_one_or_none()

        if project:
            print(f"Project 'test-project' already exists (ID: {project.id})")
            print(f"API Key (plain): test-key-123")
            print(f"API Key (hashed): {project.api_key}")
        else:
            # Create new project
            api_key_plain = "test-key-123"
            api_key_hash = hashlib.sha256(api_key_plain.encode()).hexdigest()

            project = Project(
                id=uuid.uuid4(),
                name="Test Project",
                slug="test-project",
                api_key=api_key_hash,
                whatsapp_webhook_url="https://example.com/webhook",
                is_active=True,
                settings={}
            )
            session.add(project)
            await session.commit()
            print(f"✓ Created test project (ID: {project.id})")
            print(f"API Key (plain): {api_key_plain}")
            print(f"API Key (hashed): {project.api_key}")

async def main():
    from core.orchestrator import ArcadiumAPI
    from core.config import Settings

    # Initialize API
    settings = Settings()
    api = ArcadiumAPI(settings)
    await api.initialize()

    await create_test_project(api)
    # No need to close, ArcadiumAPI doesn't have close method

if __name__ == "__main__":
    asyncio.run(main())
