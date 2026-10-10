import asyncio

import main


def test_python_310_task_group_cancels_siblings_and_propagates_reconnect():
    async def exercise():
        sibling_stopped = asyncio.Event()

        async def reconnect():
            raise main._ReconnectSignal(keep_context=False)

        async def sibling():
            try:
                await asyncio.Future()
            finally:
                sibling_stopped.set()

        try:
            async with main._CompatTaskGroup() as group:
                group.create_task(sibling())
                group.create_task(reconnect())
        except BaseException as exc:
            assert main._is_reconnect_signal(exc)
            assert main._keep_context_of(exc) is False
        else:
            raise AssertionError("reconnect signal should escape the task group")

        await asyncio.wait_for(sibling_stopped.wait(), timeout=1)

    asyncio.run(exercise())