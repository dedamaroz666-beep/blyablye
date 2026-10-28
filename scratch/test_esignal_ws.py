import asyncio
import websockets

async def test_ws():
    uri = 'ws://127.0.0.1:7789/'
    try:
        async with websockets.connect(uri) as websocket:
            print('Successfully connected to eSignal WebSocket on port 7789!')
            await websocket.send('{"action": "ping"}')
            resp = await asyncio.wait_for(websocket.recv(), timeout=3.0)
            print('Response from eSignal:', resp)
    except Exception as e:
        print('WebSocket connection error:', type(e), e)

if __name__ == '__main__':
    asyncio.run(test_ws())
