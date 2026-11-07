import threading
import time

print("Main starting")

def worker():
    print("Worker running!")

t = threading.Thread(target=worker)
t.start()
t.join(timeout=5)
print("Main done, worker alive:", t.is_alive())

