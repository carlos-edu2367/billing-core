from app.application.interfaces.uow_provider import UowProvider
from sqlalchemy.ext.asyncio import AsyncSession

class UowProvider(UowProvider):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def commit(self):
        try:
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise e

    async def rollback(self):
        await self.session.rollback()

