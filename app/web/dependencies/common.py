from fastapi import Request


async def get_redis_pool(request: Request):
    return request.app.state.redis_pool
