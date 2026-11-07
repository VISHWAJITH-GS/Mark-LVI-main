import asyncio
import threading

ready = threading.Event()
loop = None

def run():
    global loop
    print("In run()")
    try:
        loop = asyncio.new_event_loop()
        print("Loop created:", loop)
        asyncio.set_event_loop(loop)
        ready.set()
        print("Ready set")
        loop.run_forever()
    except Exception as e:
        print("Exception in run:", e)

t = threading.Thread(target=run, daemon=True)
t.start()
print("Thread started, waiting...")
res = ready.wait(timeout=5)
print("Result of wait:", res)
print("Loop:", loop)

