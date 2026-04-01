import asyncio
import json
import websockets
import os

async def test_broadcast():
    uri = "ws://127.0.0.1:9876"
    
    sock_path = "/tmp/driftcop_trace.sock"
    if os.path.exists(sock_path):
        os.remove(sock_path)
        
    async def handle_ipc(reader, writer):
        data = await reader.readline()
        print(f"✅ RECEIVED AT DRIFTCOP IPC SOCKET: {data.decode('utf-8').strip()}")
        writer.close()
        await writer.wait_closed()
        
    server = await asyncio.start_unix_server(handle_ipc, sock_path)
    print(f"Fake DriftCop IPC Listening at {sock_path}")
    
    try:
        async with websockets.connect(uri) as websocket:
            print(f"Connected to Bridge Server. Sending prompt event...")
            payload = json.dumps({
                "action": "start_prompt_trace",
                "request_id": "test-123",
                "prompt": "Deploy my code to AWS Lambda"
            })
            await websocket.send(payload)
            response = await websocket.recv()
            print(f"✅ RECEIVED FROM BRIDGE SERVER: {response}")
            
            # Wait a tick for IPC to process
            await asyncio.sleep(0.5)
    except Exception as e:
        print(f"Error connecting: {e}")
        
    server.close()
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(test_broadcast())
